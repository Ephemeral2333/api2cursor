"""???????

?? data/settings.json ???????????????
  - proxy_target_url / proxy_api_key: ????????????
  - relay_profiles / active_relay: ??????????????
  - model_mappings: Cursor ??? -> {upstream_model, backend, relay_profile, ...}
"""

import copy
import json
import os
import threading

from config import Config

_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_ROOT_DIR, 'data')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')

_lock = threading.Lock()
_cache = None

_DEFAULTS = {
    'proxy_target_url': '',
    'proxy_api_key': '',
    'debug_mode': '',
    'active_relay': '',
    'relay_profiles': {},
    'model_mappings': {},
}


def _normalize_settings(data):
    normalized = {**_DEFAULTS, **(data or {})}
    relay_profiles = normalized.get('relay_profiles')
    if not isinstance(relay_profiles, dict):
        relay_profiles = {}

    clean_relays = {}
    for raw_name, raw_profile in relay_profiles.items():
        name = str(raw_name or '').strip()
        if not name or not isinstance(raw_profile, dict):
            continue
        clean_relays[name] = {
            'name': name,
            'base_url': str(raw_profile.get('base_url', '') or '').strip(),
            'api_key': str(raw_profile.get('api_key', '') or ''),
        }

    normalized['relay_profiles'] = clean_relays
    active_relay = str(normalized.get('active_relay', '') or '').strip()
    normalized['active_relay'] = active_relay if active_relay in clean_relays else ''
    return normalized


def load():
    """??????????????????

    ????????????????????????????????????
    """
    global _cache
    with _lock:
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    _cache = _normalize_settings(json.load(f))
            except (json.JSONDecodeError, OSError):
                _cache = copy.deepcopy(_DEFAULTS)
        else:
            _cache = copy.deepcopy(_DEFAULTS)
    return copy.deepcopy(_cache)


def save(data):
    """???????????????????

    ???????????????????????????????
    """
    global _cache
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        _cache = _normalize_settings(data)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)


def get():
    """??????????????????????????"""
    with _lock:
        if _cache is None:
            pass
        else:
            return copy.deepcopy(_cache)
    return load()


def get_relay_profiles():
    """????????????"""
    return get().get('relay_profiles', {})


def get_active_relay_name():
    """?????????????"""
    return str(get().get('active_relay', '') or '').strip()


def get_active_relay():
    """??????????????????? None?"""
    current = get()
    name = str(current.get('active_relay', '') or '').strip()
    relay = current.get('relay_profiles', {}).get(name)
    if not relay:
        return None
    return {'name': name, **relay}


def _resolve_global_target(current=None):
    current = current or get()
    relay_profiles = current.get('relay_profiles', {})
    active_name = str(current.get('active_relay', '') or '').strip()
    active = relay_profiles.get(active_name, {}) if active_name else {}
    url = str(active.get('base_url', '') or current.get('proxy_target_url') or Config.PROXY_TARGET_URL).strip()
    api_key = str(active.get('api_key', '') or current.get('proxy_api_key') or Config.PROXY_API_KEY)
    return url, api_key


def get_url():
    """????????? URL?????????????"""
    return _resolve_global_target(get())[0]


def get_key():
    """??????? API ???????????????"""
    return _resolve_global_target(get())[1]


def get_debug_mode():
    """??????????????????????"""
    mode = (get().get('debug_mode') or '').strip().lower()
    return mode if mode in ('off', 'simple', 'verbose') else Config.DEBUG_MODE


def resolve_model(model_name):
    """???????????????????"""
    current = get()
    mappings = current.get('model_mappings', {})
    relay_profiles = current.get('relay_profiles', {})
    base_url, base_key = _resolve_global_target(current)

    if model_name in mappings:
        m = mappings[model_name]
        backend = m.get('backend')
        if backend in ('', None, 'auto'):
            backend = _auto_detect(model_name)
        relay_name = str(m.get('relay_profile', '') or '').strip()
        relay = relay_profiles.get(relay_name, {}) if relay_name else {}
        return {
            'upstream_model': m.get('upstream_model') or model_name,
            'backend': backend,
            'target_url': str(m.get('target_url') or relay.get('base_url') or base_url).strip(),
            'api_key': str(m.get('api_key') or relay.get('api_key') or base_key),
            'relay_profile': relay_name if relay_name in relay_profiles else '',
            'active_relay': current.get('active_relay', ''),
            'custom_instructions': m.get('custom_instructions') or '',
            'instructions_position': m.get('instructions_position') or 'prepend',
            'body_modifications': m.get('body_modifications') or {},
            'header_modifications': m.get('header_modifications') or {},
        }

    return {
        'upstream_model': model_name,
        'backend': _auto_detect(model_name),
        'target_url': base_url,
        'api_key': base_key,
        'relay_profile': '',
        'active_relay': current.get('active_relay', ''),
        'custom_instructions': '',
        'instructions_position': 'prepend',
        'body_modifications': {},
        'header_modifications': {},
    }


def _auto_detect(name):
    """???????????????????

    ??????????? `claude` ? `anthropic` ? Anthropic?
    ???????? OpenAI ?????
    """
    lower = (name or '').lower()
    if 'claude' in lower or 'anthropic' in lower:
        return 'anthropic'
    if 'gemini' in lower:
        return 'gemini'
    return 'openai'
