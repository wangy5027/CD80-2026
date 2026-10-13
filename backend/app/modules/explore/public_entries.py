"""Server-approved public entry points, never derived from private file text."""

import json
import ipaddress
from urllib.parse import urlsplit

from .public_reader import _public_address, _validate_url
from ..workbench.errors import GatewayError


def parse_entry_catalog(value):
    try:
        rows = json.loads(value) if isinstance(value, str) else value
        if not isinstance(rows, list) or len(rows) > 20:
            raise ValueError
        catalog = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'keywords', 'urls'}:
                raise ValueError
            keywords, urls = row['keywords'], row['urls']
            if (not isinstance(keywords, list) or not 1 <= len(keywords) <= 12
                    or any(not isinstance(x, str) or not 2 <= len(x.strip()) <= 80 for x in keywords)
                    or not isinstance(urls, list) or not 1 <= len(urls) <= 16):
                raise ValueError
            approved = []
            for url in urls:
                scheme, host, _, path = _validate_url(url)
                parsed = urlsplit(url)
                # Only exact, operator-approved website roots. The reader still
                # validates DNS and each redirect at request time.
                if (path != '/' or parsed.query or parsed.fragment or '.' not in host
                        or host == 'localhost' or host.endswith(('.local', '.localhost', '.internal'))):
                    raise ValueError
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    pass
                else:
                    if not _public_address(host):
                        raise ValueError
                approved.append(f'{scheme}://{parsed.netloc}/')
            catalog.append({'keywords': tuple(x.strip().casefold() for x in keywords),
                            'urls': tuple(dict.fromkeys(approved))})
        return catalog
    except (ValueError, TypeError, GatewayError):
        raise ValueError('Explore 公开入口配置无效；只接受服务端批准的网站首页。') from None


def entries_for_question(catalog, question):
    question = question.casefold()
    return list(dict.fromkeys(url for row in catalog
                if any(keyword in question for keyword in row['keywords'])
                for url in row['urls']))[:16]
