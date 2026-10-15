"""Maintainer catalog retirement; historical records remain readable."""
import json
from pathlib import Path
from .errors import GatewayError

CATALOG=Path(__file__).resolve().parents[4]/'capabilities/public-catalog.json'
REPLACEMENTS={'sfc_history':'cd80/cd80-history','sfc-history':'cd80/cd80-history',
              'sfc_announcements':'cd80/cd80-announcement','sfc-announcement':'cd80/cd80-announcement'}


def retired_names():
    if not CATALOG.exists():return set()
    return set(json.loads(CATALOG.read_text(encoding='utf-8')).get('retiredCapabilities',[])) & REPLACEMENTS.keys()


def check(name):
    if isinstance(name,str) and name in retired_names():
        raise GatewayError('此南财能力已退役，请改用'+REPLACEMENTS[name]+'并重新确认应用；历史结果仍可查看。',409,'CAPABILITY_RETIRED')


def check_spec(spec):
    for value in spec.get('tools',[]):check(value.get('registeredTool'))
    for value in spec.get('skills',[]):check(value.get('registeredSkill'))
