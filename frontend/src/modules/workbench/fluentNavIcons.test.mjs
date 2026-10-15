import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fluentNavGlyphs, fluentNavNames, resolveFluentNavGlyph } from './assets/fluent-nav/index.js';

const assetUrl = new URL('./assets/fluent-nav/', import.meta.url);
const read = name => readFileSync(new URL(name, assetUrl), 'utf8');
const mapping = JSON.parse(read('icon-map.json'));
const sources = JSON.parse(read('sources.json'));

test('22 known names; every mapping has a real Regular glyph', () => {
  assert.equal(fluentNavNames.length, 22);
  assert.deepEqual(mapping.map(item => item.key), fluentNavNames);
  for (const name of fluentNavNames) {
    assert.ok(fluentNavGlyphs[name].regular.startsWith('<path '), name);
    assert.equal(resolveFluentNavGlyph(name).viewBox, '0 0 20 20');
  }
});

test('all navigation glyphs and settings have a distinct official Filled variant', () => {
  const paired = mapping.filter(item => item.paired);
  assert.equal(paired.length, 12);
  for (const item of paired) {
    const regular = resolveFluentNavGlyph(item.key);
    const active = resolveFluentNavGlyph(item.key, true);
    assert.equal(active.variant, 'filled', item.key);
    assert.notEqual(regular.body, active.body, item.key);
  }
  assert.ok(mapping.filter(item => ['main', 'child'].includes(item.group)).every(item => item.paired));
});

test('stateless utilities deliberately stay Regular and unknown names never become info/grid', () => {
  for (const item of mapping.filter(item => !item.paired)) {
    assert.equal(resolveFluentNavGlyph(item.key, true).variant, 'regular');
  }
  for (const name of ['grid', 'info', 'extensions', 'constructor', '__proto__', undefined]) {
    assert.throws(() => resolveFluentNavGlyph(name), RangeError);
  }
});

test('all 34 adapted assets, their registry bodies and their SHA-256 values agree', () => {
  assert.equal(sources.icons.length, 34);
  assert.equal(readdirSync(new URL('svg/', assetUrl)).filter(name => name.endsWith('.svg')).length, 34);
  for (const item of sources.icons) {
    const svg = read(item.file);
    assert.equal(createHash('sha256').update(svg).digest('hex'), item.sha256, item.file);
    assert.match(svg, /viewBox="0 0 20 20"/);
    assert.ok(svg.includes(fluentNavGlyphs[item.key][item.variant]), item.file);
    assert.match(svg, /fill="currentColor"/);
    assert.doesNotMatch(svg, /<script|<foreignObject|<image|<use|\son[a-z]+=|href=|url\(|fill="#/i);
  }
});

test('original bytes reproduce GitHub blob hashes; adapted SVG keeps the exact path data', () => {
  for (const item of sources.icons) {
    const raw = read(item.upstream_file);
    const bytes = Buffer.from(raw);
    const sha = createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');
    assert.equal(sha, item.upstream_git_blob_sha1, item.upstream_file);
    const originalPath = raw.match(/<path d="([^"]+)"/)[1];
    const adaptedPath = read(item.file).match(/<path d="([^"]+)"/)[1];
    assert.equal(adaptedPath, originalPath, item.file);
  }
});

test('the new component is isolated from legacy .icon strokes and carries decorative SVG semantics', () => {
  const component = readFileSync(new URL('./components/SidebarIcon.vue', import.meta.url), 'utf8');
  assert.match(component, /class="fluent-nav-icon"/);
  assert.doesNotMatch(component, /class="icon(?:[ "])/);
  assert.match(component, /aria-hidden="true"/);
  assert.match(component, /focusable="false"/);
  assert.match(component, /stroke="none"/);
  assert.match(component, /resolveFluentNavGlyph/);
});

test('no new icon-specific background, glow or motion; existing layout is not restyled', () => {
  const css = readFileSync(new URL('./pages/sidebar-fluent-icons.css', import.meta.url), 'utf8');
  assert.doesNotMatch(css, /background\s*:|box-shadow\s*:|filter\s*:|transform\s*:|animation\s*:/);
  assert.match(css, /prefers-reduced-motion/);
});
