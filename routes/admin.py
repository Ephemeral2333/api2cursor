"""路由：管理面板

提供 Web 管理界面和 API：
  - /admin         — 返回管理界面 HTML
  - /v1/models     — 返回模型列表供 Cursor 使用
  - /api/admin/*   — 管理面板 CRUD 接口（设置、中转站 CRUD、模型映射 CRUD）
"""

import hmac
import json
import os
import logging

from flask import Blueprint, request, jsonify, send_from_directory

import settings
from config import Config

logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')

bp = Blueprint('admin', __name__)


# ─── 静态页面 ─────────────────────────────────────


@bp.route('/admin')
@bp.route('/admin/')
def admin_page():
    """返回管理面板 HTML，由 React/原生 JS 渲染完整界面。"""
    return send_from_directory(_STATIC_DIR, 'admin.html')


@bp.route('/static/<path:filename>')
def static_files(filename):
    """提供管理面板所需的静态资源文件。"""
    return send_from_directory(_STATIC_DIR, filename)


# ─── 模型列表 ─────────────────────────────────────


@bp.route('/v1/models', methods=['GET'])
def list_models():
    """返回已配置的模型映射列表，供 Cursor 下拉选择使用。"""
    mappings = settings.get().get('model_mappings', {})
    models = [{
        'id': name,
        'object': 'model',
        'owned_by': info.get('backend', 'custom'),
    } for name, info in mappings.items()]

    if not models:
        models.append({
            'id': 'claude-sonnet-4-5-20250929',
            'object': 'model',
            'owned_by': 'anthropic',
        })
    return jsonify({'object': 'list', 'data': models})


# ─── 鉴权登录 ─────────────────────────────────────


@bp.route('/api/admin/login', methods=['POST'])
def admin_login():
    """验证管理面板访问密钥，返回登录结果。"""
    data = request.get_json(force=True)
    if not Config.ACCESS_API_KEY:
        return jsonify({'ok': True, 'message': '未设置访问密钥'})
    if hmac.compare_digest(data.get('key', ''), Config.ACCESS_API_KEY):
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'message': '密钥错误'}), 401


# ─── 全局设置 ─────────────────────────────────────


@bp.route('/api/admin/settings', methods=['GET'])
def get_settings():
    """读取当前全局配置并返回。"""
    err = _check_auth()
    if err:
        return err
    s = settings.get()
    active_relay = settings.get_active_relay()
    effective_url = settings.get_url()
    return jsonify({
        'proxy_target_url': s.get('proxy_target_url', ''),
        'proxy_api_key': s.get('proxy_api_key', ''),
        'upstream_proxy': s.get('upstream_proxy', '') or Config.UPSTREAM_PROXY,
        'debug_mode': s.get('debug_mode', '') or Config.DEBUG_MODE,
        'active_relay': s.get('active_relay', ''),
        'effective_target_url': effective_url,
        'effective_api_key': '***' if settings.get_key() else '',
        'active_relay_label': active_relay.get('name', '') if active_relay else '',
        'env_target_url': Config.PROXY_TARGET_URL,
        'env_api_key': '***' if Config.PROXY_API_KEY else '',
    })


@bp.route('/api/admin/settings', methods=['PUT'])
def update_settings():
    """更新全局配置，支持部分字段覆盖。"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    s = settings.get()
    for key in ('proxy_target_url', 'proxy_api_key', 'upstream_proxy', 'debug_mode'):
        if key in data:
            s[key] = data[key]

    if 'active_relay' in data:
        active_relay = str(data.get('active_relay', '') or '').strip()
        relay_profiles = s.get('relay_profiles', {})
        if active_relay and active_relay not in relay_profiles:
            return jsonify({'error': '中转站不存在'}), 400
        s['active_relay'] = active_relay
    return _save_and_respond(s, '设置已更新')


# ─── 中转站管理 ───────────────────────────────────


@bp.route('/api/admin/relays', methods=['GET'])
def list_relays():
    """返回所有中转站配置。"""
    err = _check_auth()
    if err:
        return err
    current = settings.get()
    return jsonify({
        'active_relay': current.get('active_relay', ''),
        'relay_profiles': current.get('relay_profiles', {}),
    })


@bp.route('/api/admin/relays', methods=['POST'])
def add_relay():
    """新增一个中转站配置。"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    name = str(data.get('name', '') or '').strip()
    if not name:
        return jsonify({'error': '中转站名称不能为空'}), 400

    s = settings.get()
    relays = s.setdefault('relay_profiles', {})
    if name in relays:
        return jsonify({'error': '名称已存在'}), 400

    relays[name] = {
        'name': name,
        'base_url': str(data.get('base_url', '') or '').strip(),
        'api_key': str(data.get('api_key', '') or ''),
    }
    return _save_and_respond(s, f'添加中转站: {name}')


@bp.route('/api/admin/relays/<path:name>', methods=['PUT'])
def update_relay(name):
    """更新指定中转站配置，支持重命名。"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    s = settings.get()
    relays = s.get('relay_profiles', {})
    if name not in relays:
        return jsonify({'error': '中转站不存在'}), 404

    new_name = str(data.get('name', name) or '').strip()
    if not new_name:
        return jsonify({'error': '名称不能为空'}), 400
    if new_name != name and new_name in relays:
        return jsonify({'error': '新名称已被占用'}), 400

    entry = {
        'name': new_name,
        'base_url': str(data.get('base_url', '') or '').strip(),
        'api_key': str(data.get('api_key', '') or ''),
    }

    if new_name != name:
        del relays[name]
    relays[new_name] = entry

    if s.get('active_relay') == name:
        s['active_relay'] = new_name

    mappings = s.get('model_mappings', {})
    for mapping in mappings.values():
        if mapping.get('relay_profile') == name:
            mapping['relay_profile'] = new_name

    s['relay_profiles'] = relays
    s['model_mappings'] = mappings
    return _save_and_respond(s, f'更新中转站: {name} → {new_name}')


@bp.route('/api/admin/relays/<path:name>', methods=['DELETE'])
def delete_relay(name):
    """删除指定中转站配置。"""
    err = _check_auth()
    if err:
        return err
    s = settings.get()
    relays = s.get('relay_profiles', {})
    if name not in relays:
        return jsonify({'ok': True})

    mappings = s.get('model_mappings', {})
    refs = [model for model, mapping in mappings.items() if mapping.get('relay_profile') == name]
    if refs:
        return jsonify({'error': f'以下模型映射仍在使用此中转站: {", ".join(refs[:5])}'}), 400

    del relays[name]
    s['relay_profiles'] = relays
    if s.get('active_relay') == name:
        s['active_relay'] = ''
    return _save_and_respond(s, f'删除中转站: {name}')


# ─── 模型映射 CRUD ────────────────────────────────


@bp.route('/api/admin/mappings', methods=['GET'])
def list_mappings():
    """返回所有模型映射配置，键为 Cursor 侧模型名。"""
    err = _check_auth()
    if err:
        return err
    return jsonify(settings.get().get('model_mappings', {}))


@bp.route('/api/admin/mappings', methods=['POST'])
def add_mapping():
    """新增一条模型映射配置。"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '模型名不能为空'}), 400

    s = settings.get()
    mappings = s.setdefault('model_mappings', {})
    relay_profile = _validate_mapping_relay(s, data.get('relay_profile', ''))
    mappings[name] = {
        'upstream_model': data.get('upstream_model', name),
        'backend': data.get('backend', 'auto'),
        'relay_profile': relay_profile,
        'target_url': data.get('target_url', ''),
        'api_key': data.get('api_key', ''),
        'custom_instructions': data.get('custom_instructions', ''),
        'instructions_position': data.get('instructions_position', 'prepend'),
        'body_modifications': data.get('body_modifications') or {},
        'header_modifications': data.get('header_modifications') or {},
    }
    return _save_and_respond(s, f'添加映射: {name}')


@bp.route('/api/admin/mappings/<path:name>', methods=['PUT'])
def update_mapping(name):
    """更新指定模型映射配置，支持重命名。"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    s = settings.get()
    mappings = s.get('model_mappings', {})
    if name not in mappings:
        return jsonify({'error': '映射不存在'}), 404

    new_name = data.get('name', name).strip()
    relay_profile = _validate_mapping_relay(s, data.get('relay_profile', ''))
    entry = {
        'upstream_model': data.get('upstream_model', name),
        'backend': data.get('backend', 'auto'),
        'relay_profile': relay_profile,
        'target_url': data.get('target_url', ''),
        'api_key': data.get('api_key', ''),
        'custom_instructions': data.get('custom_instructions', ''),
        'instructions_position': data.get('instructions_position', 'prepend'),
        'body_modifications': data.get('body_modifications') or {},
        'header_modifications': data.get('header_modifications') or {},
    }
    if new_name != name:
        del mappings[name]
    mappings[new_name] = entry
    s['model_mappings'] = mappings
    return _save_and_respond(s, f'更新映射: {name} → {new_name}')


@bp.route('/api/admin/mappings/<path:name>', methods=['DELETE'])
def delete_mapping(name):
    """删除指定模型映射，若不存在则静默返回成功。"""
    err = _check_auth()
    if err:
        return err
    s = settings.get()
    mappings = s.get('model_mappings', {})
    if name in mappings:
        del mappings[name]
        s['model_mappings'] = mappings
        return _save_and_respond(s, f'删除映射: {name}')
    return jsonify({'ok': True})


# ─── 统计数据 ─────────────────────────────────────


@bp.route('/api/admin/stats', methods=['GET'])
def get_stats():
    """返回请求统计数据。"""
    err = _check_auth()
    if err:
        return err
    from utils.usage_tracker import usage_tracker
    return jsonify(usage_tracker.get_stats())


# ─── 鉴权辅助 ─────────────────────────────────────


def _check_auth():
    """校验 Admin API 访问令牌，通过返回 None，否则返回错误响应。"""
    if not Config.ACCESS_API_KEY:
        return None
    auth = request.headers.get('Authorization', '')
    token = auth[7:] if auth.startswith('Bearer ') else request.headers.get('x-api-key', '')
    if not hmac.compare_digest(token, Config.ACCESS_API_KEY):
        return jsonify({'error': '未授权'}), 401
    return None


# ─── 日志查看 ─────────────────────────────────────


@bp.route('/api/admin/logs', methods=['GET'])
def list_logs():
    """列出所有对话日志文件，按日期倒序排列。"""
    err = _check_auth()
    if err:
        return err
    import settings as _s
    log_dir = os.path.join(_s.DATA_DIR, 'conversations')
    result = []
    if not os.path.isdir(log_dir):
        return jsonify({'days': []})
    for date_name in sorted(os.listdir(log_dir), reverse=True):
        day_path = os.path.join(log_dir, date_name)
        if not os.path.isdir(day_path):
            continue
        files = []
        for fname in sorted(os.listdir(day_path), reverse=True):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(day_path, fname)
            try:
                stat = os.stat(fpath)
                with open(fpath, 'r', encoding='utf-8') as f:
                    doc = json.load(f)
                files.append({
                    'id': fname[:-5],
                    'date': date_name,
                    'conversation_id': doc.get('conversation_id', fname[:-5]),
                    'route': doc.get('route', ''),
                    'turn_count': doc.get('turn_count', len(doc.get('turns', []))),
                    'last_client_model': doc.get('last_client_model', ''),
                    'last_backend': doc.get('last_backend', ''),
                    'updated_at': doc.get('updated_at', ''),
                    'size': stat.st_size,
                })
            except Exception:
                files.append({'id': fname[:-5], 'date': date_name, 'conversation_id': fname[:-5]})
        if files:
            result.append({'date': date_name, 'files': files})
    return jsonify({'days': result})


@bp.route('/api/admin/logs/<date>/<conv_id>', methods=['GET'])
def get_log(date, conv_id):
    """读取指定对话日志详情。"""
    err = _check_auth()
    if err:
        return err
    import settings as _s
    safe_date = os.path.basename(date)
    safe_id = os.path.basename(conv_id)
    fpath = os.path.join(_s.DATA_DIR, 'conversations', safe_date, safe_id + '.json')
    if not os.path.isfile(fpath):
        return jsonify({'error': '日志不存在'}), 404
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/admin/live-logs', methods=['GET'])
def live_logs():
    """返回内存中最近的日志条目，支持增量拉取。"""
    err = _check_auth()
    if err:
        return err
    from utils.log_buffer import get_logs
    since = request.args.get('since', 0, type=int)
    logs, total = get_logs(since)
    return jsonify({'logs': logs, 'total': total})


@bp.route('/api/admin/logs/<date>/<conv_id>', methods=['DELETE'])
def delete_log(date, conv_id):
    """删除指定对话日志文件。"""
    err = _check_auth()
    if err:
        return err
    import settings as _s
    safe_date = os.path.basename(date)
    safe_id = os.path.basename(conv_id)
    fpath = os.path.join(_s.DATA_DIR, 'conversations', safe_date, safe_id + '.json')
    if os.path.isfile(fpath):
        os.remove(fpath)
    return jsonify({'ok': True})


def _validate_mapping_relay(current_settings, relay_name):
    relay_name = str(relay_name or '').strip()
    if not relay_name:
        return ''
    relay_profiles = current_settings.get('relay_profiles', {})
    if relay_name not in relay_profiles:
        raise ValueError('指定的中转站不存在')
    return relay_name


def _save_and_respond(data, log_msg):
    """保存配置并返回统一响应。

    保存成功返回 {'ok': True}，失败返回对应的 JSON 错误。
    """
    try:
        settings.save(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError as e:
        logger.error(f'保存失败: {e}')
        return jsonify({'error': {'message': f'保存失败: {e}', 'type': 'save_error'}}), 500
    logger.info(log_msg)
    return jsonify({'ok': True})
