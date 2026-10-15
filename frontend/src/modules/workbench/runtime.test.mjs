import test from 'node:test';
import assert from 'node:assert/strict';
import { publicRuntime } from './useWorkbench.js';

test('runtime state keeps only public status and never stores credential fields', () => {
  const status = publicRuntime({
    provider: 'Gitee AI', model: 'GLM-5.3', configured: true,
    message: '服务端已配置。', apiKey: 'should-not-reach-ui', baseUrl: 'internal-url',
  });
  assert.deepEqual(status, {
    provider: 'Gitee AI', model: 'GLM-5.3', configured: true, subAgentAvailable: false, message: '服务端已配置。',
    searchAvailable: false, skills: [], skillWarnings: [], tools: [],
  });
  assert.equal(JSON.stringify(status).includes('should-not-reach-ui'), false);
});

test('runtime shows only public Skill and tool metadata', () => {
  const status = publicRuntime({
    searchAvailable: true,
    skills: [{ name: 'sfc-news', description: '查询新闻', content: 'private skill body' }],
    skillWarnings: [{ path: '/private/path', reason: 'secret' }],
    tools: [{ name: 'finance_news', description: '查询财经新闻', available: true, capabilities: ['finance', 'search'], apiKey: 'private-key' }],
    apiKey: 'private-key',
  });
  assert.deepEqual(status.skills, [{ name: 'sfc-news', description: '查询新闻' }]);
  assert.deepEqual(status.tools, [{ name: 'finance_news', description: '查询财经新闻', available: true, capabilities: ['finance', 'search'] }]);
  assert.deepEqual(status.skillWarnings, ['部分技能未能加载。']);
  assert.equal(status.searchAvailable, true);
  assert.equal(JSON.stringify(status).includes('private-key'), false);
  assert.equal(JSON.stringify(status).includes('private skill body'), false);
});

test('runtime treats only a boolean true as configured', () => {
  assert.equal(publicRuntime({ configured: 'true' }).configured, false);
  assert.equal(publicRuntime({ configured: false }).configured, false);
});
