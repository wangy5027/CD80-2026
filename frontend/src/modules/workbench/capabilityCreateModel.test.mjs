import test from 'node:test';
import assert from 'node:assert/strict';
import { checkOriginalFile, normalizeOriginalPreview, originalReadiness, rawOriginalFile } from './capabilityCreateModel.js';

test('complete Skill and Tool originals remain byte-for-byte in File payloads', async () => {
  const skill = '---\r\nname: review-source\r\ndescription: Check the source.\r\n# retained comment\r\n---\r\n\r\n# Steps\r\n';
  const tool = '{\n  "name": "review_source",\n  "description": "Review source",\n  "input_schema": { "type": "object" }\n}\n';
  const skillFile = rawOriginalFile('skill', skill), toolFile = rawOriginalFile('tool', tool);
  assert.equal(skillFile.name, 'SKILL.md');
  assert.equal(toolFile.name, 'tool.json');
  assert.deepEqual(new Uint8Array(await skillFile.arrayBuffer()), new TextEncoder().encode(skill));
  assert.deepEqual(new Uint8Array(await toolFile.arrayBuffer()), new TextEncoder().encode(tool));
});

test('file and response checks reject mismatched kinds, unsupported extensions, and partial previews', () => {
  assert.throws(() => rawOriginalFile('tool', '  '), /完整的 tool.json/);
  assert.throws(() => checkOriginalFile('tool', new File(['x'], 'SKILL.md')), /Tool 请上传/);
  assert.throws(() => checkOriginalFile('skill', new File(['x'], '../tool.json')), /Skill 请上传/);
  assert.throws(() => normalizeOriginalPreview({ kind: 'skill', sha256: 'short', files: [], issues: [], manifest: {} }, 'skill'), /完整的原件预览/);
  assert.equal(originalReadiness('needs_configuration'), '待配置');
});
