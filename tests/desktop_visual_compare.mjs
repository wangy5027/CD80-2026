/** Compare original and synced screenshots of the unchanged main panels. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const moduleDir = process.env.PLAYWRIGHT_MODULE_DIR;
const { PNG } = require(moduleDir ? path.join(moduleDir, 'pngjs') : 'pngjs');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'docs', 'sqlite-sync-qa');
const panels = ['home', 'schedule', 'applications', 'extensions', 'files', 'settings'];
const summary = { sameViewport: true, panels: {}, totalChangedPixels: 0 };

for (const name of panels) {
  const before = PNG.sync.read(await readFile(path.join(output, 'before', `${name}.png`)));
  const after = PNG.sync.read(await readFile(path.join(output, 'after', `${name}.png`)));
  const sameSize = before.width === after.width && before.height === after.height;
  summary.sameViewport &&= sameSize;
  const width = Math.min(before.width, after.width);
  const height = Math.min(before.height, after.height);
  let changedPixels = 0;
  const box = { left: width, top: height, right: -1, bottom: -1 };
  const diff = new PNG({ width, height });
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const p = (y * before.width + x) * 4;
    const q = (y * after.width + x) * 4;
    const d = (y * width + x) * 4;
    let different = false;
    for (let channel = 0; channel < 4; channel++) different ||= before.data[p + channel] !== after.data[q + channel];
    if (different) {
      changedPixels++;
      box.left = Math.min(box.left, x); box.right = Math.max(box.right, x);
      box.top = Math.min(box.top, y); box.bottom = Math.max(box.bottom, y);
      diff.data[d] = 255; diff.data[d + 1] = 0; diff.data[d + 2] = 76; diff.data[d + 3] = 255;
    } else {
      for (let channel = 0; channel < 3; channel++) diff.data[d + channel] = Math.round(after.data[q + channel] * 0.25 + 191);
      diff.data[d + 3] = 255;
    }
  }
  if (changedPixels) await writeFile(path.join(output, `diff-${name}.png`), PNG.sync.write(diff));
  summary.panels[name] = {
    before: `${before.width}x${before.height}`,
    after: `${after.width}x${after.height}`,
    changedPixels,
    changedPercent: Number((changedPixels / (width * height) * 100).toFixed(6)),
    bounds: changedPixels ? box : null,
  };
  summary.totalChangedPixels += changedPixels;
}
summary.pixelIdentical = summary.sameViewport && summary.totalChangedPixels === 0;
await writeFile(path.join(output, 'visual-comparison.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify({ sameViewport: summary.sameViewport, totalChangedPixels: summary.totalChangedPixels,
  byPanel: Object.fromEntries(Object.entries(summary.panels).map(([name, value]) => [name, value.changedPercent])) }));
assert.equal(summary.sameViewport, true, 'Main panel dimensions changed.');
