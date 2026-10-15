import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer } from 'vue';
import { useExploreMotion } from './useExploreMotion.js';

function mountMotion({ stored = null, oldStored = null, reduced = false, legacy = false, blockedStorage = false, sharedStorage = null } = {}) {
  const old = { window: globalThis.window, document: globalThis.document };
  const windowEvents = new Map();
  const documentEvents = new Map();
  const mediaEvents = new Map();
  const storage = sharedStorage || new Map();
  if (stored !== null) storage.set('ficagent.explore.motionEnabled', stored);
  if (oldStored !== null) storage.set('ficagent.explore.backgroundPaused', oldStored);
  const media = { matches: reduced };
  if (legacy) {
    media.addListener = fn => mediaEvents.set('change', fn);
    media.removeListener = fn => { if (mediaEvents.get('change') === fn) mediaEvents.delete('change'); };
  } else {
    media.addEventListener = (name, fn) => mediaEvents.set(name, fn);
    media.removeEventListener = (name, fn) => { if (mediaEvents.get(name) === fn) mediaEvents.delete(name); };
  }
  globalThis.window = {
    matchMedia: () => media,
    localStorage: { getItem: key => { if (blockedStorage) throw new Error('disabled'); return storage.get(key); }, setItem: (key, value) => { if (blockedStorage) throw new Error('disabled'); storage.set(key, value); } },
    addEventListener: (name, fn) => windowEvents.set(name, fn),
    removeEventListener: (name, fn) => { if (windowEvents.get(name) === fn) windowEvents.delete(name); },
  };
  globalThis.document = {
    hidden: false,
    addEventListener: (name, fn) => documentEvents.set(name, fn),
    removeEventListener: (name, fn) => { if (documentEvents.get(name) === fn) documentEvents.delete(name); },
  };
  const renderer = createRenderer({ createElement: () => ({}), createText: () => ({}), createComment: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode() { return null; }, nextSibling() { return null; }, patchProp() {} });
  let motion;
  const app = renderer.createApp({ setup() { motion = useExploreMotion(); return () => null; } });
  app.mount({});
  return { motion, windowEvents, documentEvents, mediaEvents, storage, close() { app.unmount(); Object.assign(globalThis, old); } };
}

test('hidden pages pause and visible pages resume with listeners cleaned up', () => {
  const h = mountMotion();
  try {
    assert.equal(h.motion.enabled.value, true);
    assert.equal(h.motion.effectivePaused.value, false);
    h.windowEvents.get('pagehide')();
    assert.equal(h.motion.effectivePaused.value, true);
    h.windowEvents.get('pageshow')();
    assert.equal(h.motion.effectivePaused.value, false);
    globalThis.document.hidden = true;
    h.documentEvents.get('visibilitychange')();
    assert.equal(h.motion.effectivePaused.value, true);
    globalThis.document.hidden = false;
    h.documentEvents.get('visibilitychange')();
    assert.equal(h.motion.effectivePaused.value, false);
  } finally { h.close(); }
  assert.equal(h.windowEvents.size + h.documentEvents.size + h.mediaEvents.size, 0);
});

test('system reduced motion changes live and cleans up legacy listeners', () => {
  const h = mountMotion({ reduced: true, legacy: true });
  try {
    assert.equal(h.motion.enabled.value, true);
    assert.equal(h.motion.reducedMotion.value, true);
    assert.equal(h.motion.effectivePaused.value, true);
    h.windowEvents.get('pagehide')();
    h.windowEvents.get('pageshow')();
    assert.equal(h.motion.effectivePaused.value, true);
    h.mediaEvents.get('change')({ matches: false });
    assert.equal(h.motion.effectivePaused.value, false);
    h.motion.setEnabled(false);
    assert.equal(h.motion.effectivePaused.value, true);
    h.mediaEvents.get('change')({ matches: true });
    h.mediaEvents.get('change')({ matches: false });
    assert.equal(h.motion.enabled.value, false);
    assert.equal(h.motion.effectivePaused.value, true);
  } finally { h.close(); }
  assert.equal(h.windowEvents.size + h.documentEvents.size + h.mediaEvents.size, 0);
});

test('manual motion choice persists, while the retired pause preference is ignored', () => {
  const sharedStorage = new Map([['ficagent.explore.backgroundPaused', 'true']]);
  let h = mountMotion({ sharedStorage });
  try {
    assert.equal(h.motion.enabled.value, true);
    assert.equal(h.motion.effectivePaused.value, false);
    h.motion.setEnabled(false);
    assert.equal(h.motion.enabled.value, false);
    assert.equal(h.motion.effectivePaused.value, true);
    assert.equal(sharedStorage.get('ficagent.explore.motionEnabled'), 'false');
  } finally { h.close(); }
  h = mountMotion({ sharedStorage });
  try {
    assert.equal(h.motion.enabled.value, false);
    assert.equal(h.motion.effectivePaused.value, true);
    h.motion.setEnabled(true);
    assert.equal(h.motion.effectivePaused.value, false);
    assert.equal(sharedStorage.get('ficagent.explore.motionEnabled'), 'true');
  } finally { h.close(); }
});

test('blocked preferences still allow the user to switch motion locally', () => {
  const h = mountMotion({ blockedStorage: true });
  try {
    assert.equal(h.motion.enabled.value, true);
    h.motion.setEnabled(false);
    assert.equal(h.motion.enabled.value, false);
    assert.equal(h.motion.effectivePaused.value, true);
    h.motion.setEnabled(true);
    assert.equal(h.motion.effectivePaused.value, false);
  } finally { h.close(); }
});

test('modern media preference and hidden page cannot be overridden by the user switch', () => {
  const h = mountMotion();
  try {
    h.mediaEvents.get('change')({ matches: true });
    assert.equal(h.motion.effectivePaused.value, true);
    h.motion.setEnabled(false);
    h.motion.setEnabled(true);
    assert.equal(h.motion.enabled.value, true);
    assert.equal(h.motion.effectivePaused.value, true);
    h.mediaEvents.get('change')({ matches: false });
    assert.equal(h.motion.effectivePaused.value, false);
    globalThis.document.hidden = true;
    h.documentEvents.get('visibilitychange')();
    h.motion.setEnabled(true);
    assert.equal(h.motion.effectivePaused.value, true);
  } finally { h.close(); }
  assert.equal(h.windowEvents.size + h.documentEvents.size + h.mediaEvents.size, 0);
});
