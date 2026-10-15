"""Generate local credentials without displaying or overwriting them."""
from pathlib import Path
import os
import secrets

root = Path(__file__).resolve().parents[1]
path = root / '.env'
template = (root / '.env.example').read_text(encoding='utf-8')
try:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print('Existing .env preserved.')
else:
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(template.replace('CD80_ADMIN_API_KEY=\n', f'CD80_ADMIN_API_KEY={secrets.token_urlsafe(32)}\n'))
    print('Created local .env (permissions 600); key was not printed.')

