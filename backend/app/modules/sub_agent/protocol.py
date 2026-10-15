"""Bounded, value-free diagnostics for the coordinator's model protocol."""
import json
import re

from ..agent_platform.planner import _json_object
from ..workbench.errors import GatewayError


STAGES = frozenset({'plan', 'act', 'approve', 'review'})
MAX_VALIDATION_ERRORS = 8


def _error(stage, code, errors, **diagnostics):
    message = ('模型响应不是有效的JSON对象。' if code == 'MODEL_JSON_INVALID'
               else '模型响应不符合当前阶段的格式要求。')
    error = GatewayError(message, 502, code)
    error.validationErrors = errors[:MAX_VALIDATION_ERRORS]
    error.diagnostics = {'stage': stage if stage in STAGES else 'unknown',
                         **diagnostics, 'validationErrors': error.validationErrors}
    return error


def parse_response(stage, raw):
    """Use the existing strict parser; never guess missing syntax or values."""
    length = len(raw) if isinstance(raw, str) else 0
    try:
        value = _json_object(raw) if isinstance(raw, str) else None
    except (GatewayError, RecursionError):
        diagnostics = {'responseLength': length}
        # Only locate errors in the original text. This secondary check never
        # supplies an executable result or repairs the shared parser's output.
        fenced = re.fullmatch(r'```json[ \t]*\r?\n([\s\S]*?)\r?\n```', raw.strip())
        body = fenced.group(1) if fenced else raw
        offset = raw.find(body) if fenced else 0
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            position = offset + error.pos
            diagnostics.update(parsePosition=position, parseLine=raw.count('\n', 0, position) + 1,
                               parseColumn=position - raw.rfind('\n', 0, position),
                               parsePositionBasis='original_response')
        except (ValueError, TypeError, RecursionError):
            pass
        else:
            # The shared parser requires an object. A valid scalar/array is a
            # shape failure, not a syntax failure. Reject non-finite values and
            # duplicate keys as JSON errors even when Python's decoder accepts them.
            try:
                _json_object('{"value":' + body + '}')
            except (GatewayError, RecursionError):
                pass
            else:
                if not isinstance(parsed, dict):
                    raise _error(stage, 'MODEL_SCHEMA_INVALID',
                                 [{'path': '$', 'rule': 'must be an object'}], **diagnostics) from None
        raise _error(stage, 'MODEL_JSON_INVALID',
                     [{'path': '$', 'rule': 'must contain one valid JSON object'}], **diagnostics) from None
    if not isinstance(raw, str):
        raise _error(stage, 'MODEL_JSON_INVALID',
                     [{'path': '$', 'rule': 'response must be text'}], responseLength=length)
    return value


def validate_response(stage, value):
    """Validate one stage without executing, normalizing, or granting anything."""
    errors = []

    def add(path, rule):
        if len(errors) < MAX_VALIDATION_ERRORS:
            errors.append({'path': path, 'rule': rule})

    def text(doc, field, prefix='$', required=False):
        if field not in doc and not required:
            return
        item = doc.get(field)
        if not isinstance(item, str) or (required and not item.strip()):
            add(prefix + '.' + field, 'must be a non-empty string' if required else 'must be a string')

    def strings(doc, field, prefix='$', maximum=None):
        if field not in doc:
            return
        items = doc[field]
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            add(prefix + '.' + field, 'must be an array of strings')
        elif maximum is not None and len(items) > maximum:
            add(prefix + '.' + field, 'must contain at most ' + str(maximum) + ' items')

    def choice(doc, field, choices, prefix='$'):
        item = doc.get(field)
        if not isinstance(item, str) or item not in choices:
            add(prefix + '.' + field, 'must be one of: ' + ', '.join(choices))
            return None
        return item

    def action(doc, prefix='$', direct=False):
        if not isinstance(doc, dict):
            add(prefix, 'must be an object')
            return
        kind = choice(doc, 'type', ('tool', 'read') if direct else ('tool', 'read', 'code', 'ask', 'finish'), prefix)
        text(doc, 'purpose', prefix)
        strings(doc, 'inputRefs', prefix, 16)
        if kind == 'tool':
            text(doc, 'name', prefix, True)
            if not isinstance(doc.get('arguments'), dict):
                add(prefix + '.arguments', 'must be an object')
        elif kind == 'read':
            text(doc, 'artifactId', prefix, True)
        elif kind == 'code':
            text(doc, 'code', prefix, True)
            text(doc, 'reason', prefix, True)
        elif kind == 'ask':
            text(doc, 'question', prefix, True)
        elif kind == 'finish':
            text(doc, 'answer', prefix, True)
            strings(doc, 'artifactIds', prefix)
            completion(doc, prefix)

    def completion(doc, prefix='$'):
        if 'completion' in doc:
            choice(doc, 'completion', ('complete', 'partial'), prefix)
        strings(doc, 'missingDeliverables', prefix, 32)
        strings(doc, 'adoptedStepIds', prefix, 64)
        strings(doc, 'resultArtifactIds', prefix, 64)

    if stage not in STAGES:
        add('stage', 'must be a supported model stage')
    if not isinstance(value, dict):
        add('$', 'must be an object')
    else:
        # Stubs also pass through this entry point, so reject Python-only values
        # without including their repr, object keys, or business data in errors.
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            add('$', 'must contain only finite JSON values')
        if 'assessment' in value and value['assessment'] is not None:
            assessment = value['assessment']
            if not isinstance(assessment, dict):
                add('$.assessment', 'must be an object')
            else:
                for field in ('needsInformation', 'dependentProcessing', 'explicitApplication'):
                    if field in assessment and type(assessment[field]) is not bool:
                        add('$.assessment.' + field, 'must be a boolean')
                text(assessment, 'reason', '$.assessment')
        if stage == 'plan':
            mode = choice(value, 'mode', ('chat', 'answer', 'direct', 'ask', 'delegate', 'application', 'authoring', 'asset_app'))
            if mode == 'authoring':
                spec = value.get('authoring')
                if not isinstance(spec, dict):
                    add('$.authoring', 'must be an object')
                else:
                    choice(spec, 'kind', ('app', 'tool', 'skill'), '$.authoring')
                    choice(spec, 'intent', ('create', 'capture', 'edit', 'prepare_import', 'upgrade', 'extract'), '$.authoring')
                    if 'mode' in spec:
                        choice(spec, 'mode', ('prepare', 'draft_only'), '$.authoring')
            if mode == 'asset_app':
                spec = value.get('assetApp')
                if not isinstance(spec, dict):
                    add('$.assetApp', 'must be an object')
                else:
                    text(spec, 'assetId', '$.assetApp', True)
                    text(spec, 'versionId', '$.assetApp', True)
                    if not isinstance(spec.get('input'), dict):
                        add('$.assetApp.input', 'must be an object')
            strings(value, 'requiredCapabilities')
            if 'preferredCapabilities' in value:
                refs = value['preferredCapabilities']
                if not isinstance(refs, list):
                    add('$.preferredCapabilities', 'must be an array of capability references')
                else:
                    for index, ref in enumerate(refs):
                        prefix = '$.preferredCapabilities[' + str(index) + ']'
                        if not isinstance(ref, dict):
                            add(prefix, 'must be an object')
                        else:
                            text(ref, 'assetId', prefix, True)
                            text(ref, 'versionId', prefix, True)
                        if len(errors) >= MAX_VALIDATION_ERRORS:
                            break
            if mode == 'answer':
                text(value, 'answer', required=True)
                completion(value)
            elif mode == 'ask':
                text(value, 'question', required=True)
            elif mode == 'direct':
                action(value.get('action'), '$.action', direct=True)
            elif mode == 'delegate':
                text(value, 'goal', required=True)
                text(value, 'contextSummary')
                for field in ('deliverables', 'acceptanceCriteria', 'constraints', 'requiredExtensions'):
                    strings(value, field)
                if 'allowTemporaryCode' in value and type(value['allowTemporaryCode']) is not bool:
                    add('$.allowTemporaryCode', 'must be a boolean')
        elif stage == 'act':
            action(value)
        elif stage == 'approve':
            decision = choice(value, 'decision', ('approve', 'reject', 'ask'))
            if decision == 'ask':
                text(value, 'question', required=True)
            elif decision:
                text(value, 'reason', required=True)
        elif stage == 'review':
            completion(value)
            text(value, 'answer')
            decision = choice(value, 'decision', ('accept', 'revise', 'fail'))
            if decision == 'accept':
                text(value, 'answer', required=True)
            elif decision:
                text(value, 'reason', required=True)
    if errors:
        raise _error(stage, 'MODEL_SCHEMA_INVALID', errors)
    return value
