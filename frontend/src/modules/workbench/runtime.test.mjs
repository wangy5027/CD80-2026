import test from 'node:test';
import assert from 'node:assert/strict';
import { publicRuntime } from './useWorkbench.js';

test('runtime state keeps only public status and never stores credential fields', () => {
  const status = publicRuntime({
    provider: 'Gitee AI', model: 'GLM-5.3', configured: true,
    message: '服务端已配置。', apiKey: 'should-not-reach-ui', baseUrl: 'internal-url',
  });
  assert.deepEqual(status, {
    provider: 'Gitee AI', model: 'GLM-5.3', configured: true, message: '服务端已配置。',
  });
  assert.equal(JSON.stringify(status).includes('should-not-reach-ui'), false);
});

test('runtime treats only a boolean true as configured', () => {
  assert.equal(publicRuntime({ configured: 'true' }).configured, false);
  assert.equal(publicRuntime({ configured: false }).configured, false);
});
