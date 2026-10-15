"""Downloadable, versioned contracts and examples for external App authors."""
import io
import json
from pathlib import Path
import zipfile


BASE = '/desktop/api/asset-apps'
_SCHEMA_PATH = Path(__file__).with_name('app_definition.schema.json')


def definition_schema():
    return json.loads(_SCHEMA_PATH.read_text(encoding='utf-8'))


def python_example():
    return '''APP_INTERFACE = {
    "name": "两数求和 Python 示例",
    "description": "完整单文件 APP：接收 a 与 b，返回它们的和。",
    "inputSchema": {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "properties": {"sum": {"type": "number"}},
        "required": ["sum"],
    },
    "rules": ["返回 a 与 b 的和。"],
    "testCases": [{
        "label": "原任务用例：1 + 2",
        "input": {"a": 1, "b": 2},
        "fileBindings": {},
        "expectation": {"mode": "json_equals", "value": {"result": {"sum": 3}}},
    }],
}


def run(payload):
    return {"sum": payload["a"] + payload["b"]}
'''


def specification():
    """OpenAPI describes HTTP transport; schema.json describes the saved model."""
    schema = definition_schema()
    schema.pop('$id', None)
    def nested_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == '$ref' and isinstance(child, str) and child.startswith('#/$defs/'):
                    value[key] = child.replace('#/$defs/', '#/components/schemas/AppDefinition/$defs/', 1)
                else:
                    nested_refs(child)
        elif isinstance(value, list):
            for child in value:
                nested_refs(child)
    nested_refs(schema)
    return {
        'openapi': '3.1.0',
        'info': {'title': 'CD80 external App import API', 'version': 'cd80.app/2',
                 'description': 'All requests require the current account session. Preview parses only; import stores an unverified, disabled private App. Verification and enablement are separate operations.'},
        'servers': [{'url': '/'}],
        'security': [{'cookieAuth': []}],
        'paths': {
            BASE + '/import/file-preview': {'post': {
                'summary': 'Preview complete .json, .py or .zip without executing code or saving an App',
                'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': {'type': 'object', 'required': ['file'], 'properties': {'file': {'type': 'string', 'format': 'binary'}}}}}},
                'responses': {'200': {'description': 'Normalized portable package, definition, static issues, warnings, and owner-specific binding requirements',
                                      'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ImportPreview'}}}},
                              '400': {'description': 'Malformed or unsafe package'}, '413': {'description': 'Upload limit'}, '422': {'description': 'Unrepresentable source layout'}}}},
            BASE + '/import/preview': {'post': {
                'summary': 'Preview an existing cd80.app-package/2 JSON body',
                'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'object', 'required': ['package'], 'properties': {'package': {'$ref': '#/components/schemas/AppPackage'}}}}}},
                'responses': {'200': {'description': 'Static import preview'}}}},
            BASE + '/import': {'post': {
                'summary': 'Save a normalized package as a private unverified, disabled App',
                'parameters': [{'name': 'Idempotency-Key', 'in': 'header', 'required': True, 'schema': {'type': 'string'}}],
                'requestBody': {'required': True, 'content': {'application/json': {'schema': {'type': 'object', 'required': ['package'], 'properties': {'package': {'$ref': '#/components/schemas/AppPackage'}, 'bindings': {'type': 'object', 'additionalProperties': {'type': 'string'}}, 'testCases': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'items': {'$ref': '#/components/schemas/AppDefinition/$defs/testCase'}, 'description': 'Optional editable cases. When present, overrides package.definition.testCases without changing the package digest.'}}}}}},
                'responses': {'201': {'description': 'Created or idempotently replayed App detail; verification.status=unverified and enabledVersionId=null'}}}},
            **{BASE + '/import/' + name: {'get': {'summary': 'Download ' + label,
                                                  'responses': {'200': {'description': label}}}}
               for name, label in (
                   ('schema.json', 'canonical cd80.app/2 JSON Schema'),
                   ('specification.json', 'this HTTP OpenAPI specification'),
                   ('template.json', 'complete canonical App example'),
                   ('example.py', 'literal APP_INTERFACE single-file example'),
                   ('template.zip', 'source ZIP with app.json codeFile/resourceFiles'),
                   ('data-interface.zip', 'separate cd80-data-api data-service contract'))},
        },
        'components': {
            'securitySchemes': {'cookieAuth': {'type': 'apiKey', 'in': 'cookie', 'name': 'ficagent_session'}},
            'schemas': {
                'AppDefinition': schema,
                'AppPackage': {'type': 'object', 'required': ['format', 'definition', 'digest'],
                               'properties': {'format': {'const': 'cd80.app-package/2'},
                                              'definition': {'$ref': '#/components/schemas/AppDefinition'},
                                              'digest': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}},
                               'additionalProperties': True},
                'ImportPreview': {'type': 'object', 'required': ['definition', 'package', 'issues', 'warnings', 'bindingRequirements'],
                                  'properties': {'definition': {'$ref': '#/components/schemas/AppDefinition'},
                                                 'package': {'$ref': '#/components/schemas/AppPackage'},
                                                 'issues': {'type': 'array'}, 'warnings': {'type': 'array', 'items': {'type': 'string'}},
                                                 'bindingRequirements': {'type': 'array'}}},
            },
        },
        'x-cd80-transport': {
            'canonical': 'cd80.app/2 JSON with embedded code/files and original testCases metadata, or cd80.app-package/2 with matching SHA-256 canonical digest',
            'zip': {'entry': 'app.json or application.json at root or inside one wrapper directory',
                    'codeFile': 'Only on a code node; relative UTF-8 source file; exclusive with code.',
                    'resourceFiles': 'Only on a code node; array of relative file paths. Each non-Finder ZIP member must be referenced exactly once. ZIP-only fields are replaced by canonical code and files.',
                    'pathRule': 'Forward-slash relative safe path. No traversal, symlink, duplicate casefold/NFC name, unreferenced file, encrypted entry, or path conflict.'},
            'python': {'declaration': 'Exactly one top-level APP_INTERFACE static Python literal dict with name, description, inputSchema, outputSchema, rules; optional testCases.',
                       'inputSchema': 'type object, explicit properties and additionalProperties false; each property maps to an input binding.',
                       'entry': 'Exactly one top-level synchronous def run(payload). Source is parsed with AST but not imported or executed during preview/save.',
                       'mapping': 'One code node main; outputSchema becomes main.delivery.schema; outputs.result binds to the complete step result.'},
        },
    }


def data_interface_zip():
    """Only public static protocol documents; never walk the data directory."""
    root = Path(__file__).resolve().parents[4]
    documents = root / 'cd80-data-api' / 'docs'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ('CONTRACT.md', 'openapi.json', 'data_catalog.json'):
            archive.writestr(name, (documents / name).read_bytes())
    return output.getvalue()
