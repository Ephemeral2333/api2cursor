"""持久化配置管理。

从 data/settings.json 读取并保存运行时配置：
  - proxy_target_url / proxy_api_key: 默认上游中转站地址和密钥
  - relay_profiles / active_relay: 多中转站配置和当前激活项
  - model_mappings: Cursor 模型名 -> {upstream_model, backend, relay_profile, ...}
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
    'upstream_proxy': '',
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
    """从磁盘加载配置并刷新内存缓存。

    如果配置文件不存在、无法读取或 JSON 无效，则回退到默认配置。
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
    """保存配置到磁盘并同步更新内存缓存。

    保存前会先规范化配置结构，避免无效中转站或脏字段进入缓存。
    """
    global _cache
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        _cache = _normalize_settings(data)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)


def get():
    """获取当前配置副本，必要时自动加载。"""
    with _lock:
        if _cache is not None:
            return copy.deepcopy(_cache)
    return load()


def get_relay_profiles():
    """返回所有中转站配置。"""
    return get().get('relay_profiles', {})


def get_active_relay_name():
    """返回当前激活的中转站名称。"""
    return str(get().get('active_relay', '') or '').strip()


def get_active_relay():
    """返回当前激活的中转站配置；未配置时返回 None。"""
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
    """返回当前生效的上游 URL。"""
    return _resolve_global_target(get())[0]


def get_key():
    """返回当前生效的上游 API 密钥。"""
    return _resolve_global_target(get())[1]


def get_debug_mode():
    """返回当前生效的调试日志模式。"""
    mode = (get().get('debug_mode') or '').strip().lower()
    return mode if mode in ('off', 'simple', 'verbose') else Config.DEBUG_MODE


def get_upstream_proxy() -> str:
    """返回当前生效的上游代理，直接读内存缓存，不做深拷贝。"""
    with _lock:
        if _cache is None:
            return Config.UPSTREAM_PROXY
        return str(_cache.get('upstream_proxy', '') or '').strip() or Config.UPSTREAM_PROXY


def resolve_model(model_name):
    """根据客户端模型名解析完整的上游路由配置。"""
    current = get()
    mappings = current.get('model_mappings', {})
    relay_profiles = current.get('relay_profiles', {})
    base_url, base_key = _resolve_global_target(current)
    active_relay = str(current.get('active_relay', '') or '').strip()

    if model_name in mappings:
        m = mappings[model_name]
        backend = m.get('backend')
        if backend in ('', None, 'auto'):
            backend = _auto_detect(model_name)
        relay_name = str(m.get('relay_profile', '') or '').strip()
        relay = relay_profiles.get(relay_name, {}) if relay_name else {}
        has_custom_target = bool(str(m.get('target_url', '') or '').strip())
        has_custom_key = bool(str(m.get('api_key', '') or ''))
        if has_custom_target or has_custom_key:
            relay_label = '自定义地址/密钥'
            relay_source = 'mapping_custom'
        elif relay_name in relay_profiles:
            relay_label = relay_name
            relay_source = 'mapping_relay'
        elif active_relay in relay_profiles:
            relay_label = active_relay
            relay_source = 'active_relay'
        else:
            relay_label = '全局默认'
            relay_source = 'global_default'
        return {
            'upstream_model': m.get('upstream_model') or model_name,
            'backend': backend,
            'target_url': str(m.get('target_url') or relay.get('base_url') or base_url).strip(),
            'api_key': str(m.get('api_key') or relay.get('api_key') or base_key),
            'relay_profile': relay_name if relay_name in relay_profiles else '',
            'active_relay': active_relay,
            'relay_label': relay_label,
            'relay_source': relay_source,
            'custom_instructions': m.get('custom_instructions') or '',
            'instructions_position': m.get('instructions_position') or 'prepend',
            'body_modifications': m.get('body_modifications') or {},
            'header_modifications': m.get('header_modifications') or {},
        }

    if active_relay in relay_profiles:
        relay_label = active_relay
        relay_source = 'active_relay'
    else:
        relay_label = '全局默认'
        relay_source = 'global_default'

    return {
        'upstream_model': model_name,
        'backend': _auto_detect(model_name),
        'target_url': base_url,
        'api_key': base_key,
        'relay_profile': '',
        'active_relay': active_relay,
        'relay_label': relay_label,
        'relay_source': relay_source,
        'custom_instructions': '',
        'instructions_position': 'prepend',
        'body_modifications': {},
        'header_modifications': {},
    }


def _auto_detect(name):
    """根据模型名自动推断后端协议。

    名称包含 `claude` 或 `anthropic` 时走 Anthropic；
    名称包含 `gemini` 时走 Gemini；否则默认走 OpenAI 兼容接口。
    """
    lower = (name or '').lower()
    if 'claude' in lower or 'anthropic' in lower:
        return 'anthropic'
    if 'gemini' in lower:
        return 'gemini'
    return 'openai'
