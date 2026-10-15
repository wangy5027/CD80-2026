import test from 'node:test';
import assert from 'node:assert/strict';
import { workspaceHash, workspaceView } from './workspaceRoutes.js';

test('old applications bookmarks and persisted views resolve to the skill pool', () => {
  assert.equal(workspaceView('#applications'), 'extensions');
  assert.equal(workspaceView('#applications?app=old'), 'extensions');
  assert.equal(workspaceHash('applications'), '#extensions');
  assert.equal(workspaceHash(workspaceView('#applications')), '#extensions');
});

test('workshop navigation and Explore session links remain intact', () => {
  for (const view of ['extensions', 'strategy-workshop', 'app-workshop', 'community']) {
    assert.equal(workspaceView('#' + view), view);
    assert.equal(workspaceHash(view), '#' + view);
  }
  assert.equal(workspaceHash('explore', '#explore?session=research-1'), '#explore?session=research-1');
  assert.equal(workspaceView('#unknown'), '');
  assert.equal(workspaceHash('home'), '');
});
