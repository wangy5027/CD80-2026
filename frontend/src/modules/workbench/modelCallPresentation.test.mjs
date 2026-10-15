import test from 'node:test';
import assert from 'node:assert/strict';
import { modelCallText, presentModelCall } from './modelCallPresentation.js';
import { USER_FACING_ERROR } from '../../shared/userFacingError.js';

test('failed model summary keeps normal input and duration but hides response and diagnostics', () => {
  const view = presentModelCall({ stage: 'act', model: 'fixture', status: 'failed', startedAt: '2026-10-14T01:00:00Z', completedAt: '2026-10-14T01:00:02Z',
    detail: { value: { request: { payload: { message: '统计', input: { prices: [10, 12] }, availableTools: [{ instructions: 'private-tool-doc' }] } },
      response: { text: 'Traceback: provider-secret', complete: false, diagnostic: 'model-secret' }, transport: { httpStatus: 504, finishReason: 'timeout' } } }, error: { code: 'TIMEOUT', message: '读取模型响应超时' } });
  assert.match(view.input, /统计/); assert.match(view.input, /10/); assert.doesNotMatch(view.input, /private-tool-doc|availableTools/);
  assert.equal(view.partial, true); assert.equal(view.output, '');
  assert.equal(view.metadata.find(item => item.label === '耗时').value, '2.0 秒');
  assert.deepEqual(view.errors, [{ label: '错误', value: USER_FACING_ERROR }]);
  assert.doesNotMatch(JSON.stringify(view), /TIMEOUT|读取模型响应超时|provider-secret|model-secret|504|timeout/);
});

test('nested JSON strings, arrays and privileged message variants are filtered before output limits', () => {
  const input = { messages: [{ role: 'SYSTEM', content: 'secret-system' }, { author: { role: 'developer' }, content: 'secret-developer' },
    { role: 'user', content: JSON.stringify({ nested: JSON.stringify({ system_prompt: 'hidden-system', developerPromptText: 'hidden-developer', answer: 'business-value' }) }) }] };
  const clean = modelCallText(input, 6000);
  assert.doesNotMatch(clean, /secret-system|secret-developer|hidden-system|hidden-developer|system_prompt|developerPrompt/);
  assert.match(clean, /business-value/);
  assert.doesNotMatch(modelCallText('prefix {"role":"system","content":"raw-secret'), /raw-secret/);
  assert.doesNotMatch(modelCallText(JSON.stringify({ nested: JSON.stringify({ role: 'developer', content: 'escaped-private' }) }).slice(0, -5)), /escaped-private/);
  assert.doesNotMatch(modelCallText('system: unstructured-private-prompt'), /unstructured-private/);
  assert.equal(modelCallText('示例 {"role":"user","content":"业务输入"}'), '示例 {"role":"user","content":"业务输入"}');
});

test('retry input keeps business values but hides validation diagnostics and platform instruction', () => {
  const view = presentModelCall({ request: { payload: { message: '分析本次价格', validationFeedback: {
    code: 'MODEL_INVALID_JSON', details: ['缺少type字段'], instruction: 'PLATFORM_RETRY_PROMPT_SENTINEL',
  } } } });
  assert.match(view.input, /分析本次价格/); assert.doesNotMatch(view.input, /MODEL_INVALID_JSON|缺少type字段|validationFeedback/);
  assert.doesNotMatch(view.input, /instruction|PLATFORM_RETRY_PROMPT/);
  const observation = presentModelCall({ request: { payload: { message: '继续处理', observations: [
    { status: 'failed', result: 'secret-provider-reason', detail: { traceback: 'secret-stack' } },
    { status: 'succeeded', result: '正常业务证据' },
  ] } } });
  assert.match(observation.input, /继续处理|正常业务证据/);
  assert.match(observation.input, /当前网络连接失败/);
  assert.doesNotMatch(observation.input, /secret-provider-reason|secret-stack/);
});

test('truncated detail head and tail never become an input or output preview', () => {
  const view = presentModelCall({ model: 'fixture', status: 'failed', detail: { truncated: true, value: { head: 'system raw-secret', tail: 'developer raw-secret' } }, error: { code: 'TIMEOUT' } });
  assert.equal(view.omitted, true); assert.equal(view.input, ''); assert.equal(view.output, '');
  assert.doesNotMatch(JSON.stringify(view), /raw-secret|head|tail|TIMEOUT/); assert.deepEqual(view.errors, [{ label: '错误', value: USER_FACING_ERROR }]);
});

test('output is a marked excerpt and parsed fallback cannot expose prompt fields', () => {
  const view = presentModelCall({ request: { messages: [{ role: 'system', content: 'known-private-system-prompt' }] }, response: { text: 'x'.repeat(4000) } });
  assert.ok(view.output.length < 3100); assert.match(view.output, /仅显示节选/);
  const fallback = presentModelCall({ parsed: { answer: '业务结果', systemPrompt: 'parsed-private', nested: [{ role: 'developer', content: 'nested-private' }] } });
  assert.match(fallback.output, /业务结果/); assert.doesNotMatch(fallback.output, /parsed-private|nested-private/);
  const diagnostic = presentModelCall({ status: 'succeeded', response: { text: JSON.stringify({ answer: '正常输出', diagnostic: 'secret-trace', traceback: 'secret-stack' }) } });
  assert.match(diagnostic.output, /正常输出/); assert.doesNotMatch(diagnostic.output, /secret-trace|secret-stack|diagnostic|traceback/);
});
