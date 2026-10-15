import test from 'node:test';
import assert from 'node:assert/strict';
import { exampleAppDefinition, importPreviewValue, initialAppText, isKnownFailure, parseManualApp } from './appCreateModel.js';

test('manual example is a full code-and-resource cd80.app/2 definition with connected output', () => {
  const blank = JSON.parse(initialAppText());
  assert.equal(blank.format, 'cd80.app/2');
  assert.deepEqual(blank.nodes, []);
  const sample = exampleAppDefinition();
  assert.deepEqual(parseManualApp(JSON.stringify(sample)), sample);
  assert.match(sample.nodes[0].code, /def run\(payload\)/);
  assert.equal(sample.nodes[0].files[0].path, 'references/README.md');
  assert.deepEqual(sample.nodes[0].inputBindings.values, { from: 'input', path: 'values' });
  assert.deepEqual(sample.outputs.result, { from: 'step', stepId: 'calculate', path: '' });
});

test('manual parser and upload preview reject incomplete packages before save', () => {
  assert.throws(() => parseManualApp('{oops'), /JSON/);
  assert.throws(() => parseManualApp('{"format":"other","nodes":[]}'), /cd80.app\/2/);
  assert.throws(() => parseManualApp('{"format":"cd80.app\/2","nodes":{}}'), /nodes/);
  assert.throws(() => importPreviewValue({ definition: exampleAppDefinition() }), /完整应用包/);
  const preview = importPreviewValue({ package: { format: 'cd80.app-package/2', digest: 'abc' },
    definition: exampleAppDefinition(), issues: [], warnings: ['须重新验证'], bindingRequirements: [] });
  assert.equal(preview.definition.nodes[0].id, 'calculate');
  assert.deepEqual(preview.warnings, ['须重新验证']);
  assert.equal(isKnownFailure({ status: 422, code: 'INVALID_PACKAGE' }), true);
  assert.equal(isKnownFailure({ status: 409, code: 'OUTCOME_UNKNOWN' }), false);
  assert.equal(isKnownFailure({ status: 503 }), false);
});
