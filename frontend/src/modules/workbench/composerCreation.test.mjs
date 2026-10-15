import test from 'node:test';
import assert from 'node:assert/strict';
import { component, content, find, hostNode, renderer, flush } from './assetComponentHarness.mjs';

const Composer = await component('./components/Composer.vue');
const get = (root, label) => find(root, node => node.tag === 'button' && node.props['aria-label'] === label);

test('both composers expose an off-by-default creation switch and route Enter through send', async () => {
  for (const variant of ['home', 'compact']) {
    const sent = [], modes = [], root = hostNode('root');
    const app = renderer.createApp(Composer, { variant, draft: '完整创建需求', agent: { name: 'Ficagent助手' },
      hasModels: true, onSend: () => sent.push('send'), 'onUpdate:create-mode': value => modes.push(value) });
    try {
      app.mount(root); await flush();
      assert.equal(get(root, '创建模式').props['aria-pressed'], false);
      get(root, '创建模式').props.onClick();
      assert.deepEqual(modes, [true]);
      let prevented = false;
      find(root, node => node.tag === 'textarea').props.onKeydown({ key: 'Enter', keyCode: 13, shiftKey: false,
        isComposing: false, preventDefault() { prevented = true; } });
      assert.equal(prevented, true);
      assert.deepEqual(sent, ['send']);
    } finally { app.unmount(); }
  }
});

test('creation hides old run selections without destroying them and exposes only file choice', async () => {
  const root = hostNode('root'), app = renderer.createApp(Composer, {
    draft: '创建一个 APP', agent: { name: 'Ficagent助手' }, createMode: true,
    application: { spec: { name: '旧运行应用' } }, capabilityRefs: [{ assetId: 'tool', kind: 'tool', name: '旧工具' }],
    skillNames: ['旧技能'], hasModels: false,
  });
  try {
    app.mount(root); await flush();
    assert.equal(get(root, '创建模式').props['aria-pressed'], true);
    assert.equal(get(root, '创建并保存 APP').props.disabled, false);
    assert.doesNotMatch(content(root), /旧运行应用|旧工具|旧技能/);
    get(root, '添加文件').props.onClick(); await flush();
    assert.match(content(root), /上传或选择文件/);
    assert.doesNotMatch(content(root), /选择 Skill|选择 Tool|选择 Application/);
    assert.match(content(root), /未验证/);
  } finally { app.unmount(); }
});
