"""離線由真實路由生成OpenAPI，再由公開OpenAPI產生前端型別；不讀業務資料。"""
from pathlib import Path
import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def typescript_type(schema):
    """只支援目前契約用到的JSON Schema；遇到未知型別就失敗，避免產生any。"""
    if '$ref' in schema: return schema['$ref'].split('/')[-1]
    if 'const' in schema: return json.dumps(schema['const'])
    if 'enum' in schema: return ' | '.join(json.dumps(item) for item in schema['enum'])
    if 'anyOf' in schema: return ' | '.join(typescript_type(item) for item in schema['anyOf'])
    if schema.get('type') == 'array': return f"Array<{typescript_type(schema['items'])}>"
    if schema.get('type') == 'object':
        return '{' + '; '.join(f'{key}: {typescript_type(value)}' for key, value in schema['properties'].items()) + '}'
    kinds = {'integer': 'number', 'number': 'number', 'boolean': 'boolean', 'null': 'null', 'string': 'string'}
    return kinds[schema['type']]


def public_types(spec):
    """回應型別全部欄位都會輸出；ApiError保留可選fields，不把輸入預設誤當可省略。"""
    lines = [f"// 自動產生自docs/public-openapi.json，契約{spec['info']['version']}；請勿手改。"]
    for name, schema in sorted(spec['components']['schemas'].items()):
        lines.append(f'export interface {name} {{')
        for key, field in schema.get('properties', {}).items():
            optional = '?' if name == 'ApiError' and key not in schema.get('required', []) else ''
            readonly = 'readonly ' if field.get('readOnly') else ''
            lines.append(f'  {readonly}{key}{optional}: {typescript_type(field)};')
        lines.append('}\n')
    return '\n'.join(lines) + '\n'


def sync(check=False):
    """只建立路由表，不進lifespan；check模式比對差異，不寫任何檔案。"""
    os.environ['LAB_ADMIN_ORIGIN'] = 'https://admin.lab.example'
    os.environ['LAB_DEV'] = 'false'
    from lab_backend.application import public_app, admin_app
    specs = {'public': public_app().openapi(), 'admin': admin_app().openapi()}
    files = {ROOT / f'docs/{name}-openapi.json': json.dumps(spec, ensure_ascii=False, indent=2) + '\n'
             for name, spec in specs.items()}
    files[ROOT / 'public_frontend/contracts/public-api.d.ts'] = public_types(specs['public'])
    for path, text in files.items():
        if check:
            if not path.exists() or path.read_text() != text:
                raise ValueError(f'契約過期：{path.relative_to(ROOT)}')
        else:
            path.write_text(text)
    return specs


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    sync(parser.parse_args().check)
    print('PASS: public/admin OpenAPI and frontend types synchronized.')
