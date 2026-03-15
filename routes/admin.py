"""??: ????

?? Web ????? API?
  - /admin         ? ??????
  - /v1/models     ? ?????? Cursor ???
  - /api/admin/*   ? ????????? CRUD????? CRUD??????
"""

import json
import os
import logging

from flask import Blueprint, request, jsonify, send_from_directory

import settings
from config import Config

logger = logging.getLogger(__name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')

bp = Blueprint('admin', __name__)


# ??? ???? ?????????????????????????????????????


@bp.route('/admin')
@bp.route('/admin/')
def admin_page():
    """???????? HTML ??????????????"""
    return send_from_directory(_STATIC_DIR, 'admin.html')


@bp.route('/static/<path:filename>')
def static_files(filename):
    """????????????????"""
    return send_from_directory(_STATIC_DIR, filename)


# ??? ???? ?????????????????????????????????????


@bp.route('/v1/models', methods=['GET'])
def list_models():
    """????????????? Cursor ???????"""
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


# ??? ???? ?????????????????????????????????????


@bp.route('/api/admin/login', methods=['POST'])
def admin_login():
    """???????????????????????"""
    data = request.get_json(force=True)
    if not Config.ACCESS_API_KEY:
        return jsonify({'ok': True, 'message': '?????'})
    if data.get('key', '') == Config.ACCESS_API_KEY:
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'message': '????'}), 401


# ??? ???? ?????????????????????????????????????


@bp.route('/api/admin/settings', methods=['GET'])
def get_settings():
    """??????????????"""
    err = _check_auth()
    if err:
        return err
    s = settings.get()
    active_relay = settings.get_active_relay()
    effective_url = settings.get_url()
    return jsonify({
        'proxy_target_url': s.get('proxy_target_url', ''),
        'proxy_api_key': s.get('proxy_api_key', ''),
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
    """??????????????????????"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    s = settings.get()
    for key in ('proxy_target_url', 'proxy_api_key', 'debug_mode'):
        if key in data:
            s[key] = data[key]

    if 'active_relay' in data:
        active_relay = str(data.get('active_relay', '') or '').strip()
        relay_profiles = s.get('relay_profiles', {})
        if active_relay and active_relay not in relay_profiles:
            return jsonify({'error': '?????????'}), 400
        s['active_relay'] = active_relay
    return _save_and_respond(s, '???????')


# ??? ????? ???????????????????????????????????


@bp.route('/api/admin/relays', methods=['GET'])
def list_relays():
    """??????????"""
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
    """??????????"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    name = str(data.get('name', '') or '').strip()
    if not name:
        return jsonify({'error': '?????????'}), 400

    s = settings.get()
    relays = s.setdefault('relay_profiles', {})
    if name in relays:
        return jsonify({'error': '??????'}), 400

    relays[name] = {
        'name': name,
        'base_url': str(data.get('base_url', '') or '').strip(),
        'api_key': str(data.get('api_key', '') or ''),
    }
    return _save_and_respond(s, f'??????: {name}')


@bp.route('/api/admin/relays/<path:name>', methods=['PUT'])
def update_relay(name):
    """????????????????"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    s = settings.get()
    relays = s.get('relay_profiles', {})
    if name not in relays:
        return jsonify({'error': '??????'}), 404

    new_name = str(data.get('name', name) or '').strip()
    if not new_name:
        return jsonify({'error': '?????????'}), 400
    if new_name != name and new_name in relays:
        return jsonify({'error': '??????????'}), 400

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
    return _save_and_respond(s, f'??????: {name} ? {new_name}')


@bp.route('/api/admin/relays/<path:name>', methods=['DELETE'])
def delete_relay(name):
    """??????????"""
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
        return jsonify({'error': f'????????????: {", ".join(refs[:5])}'}), 400

    del relays[name]
    s['relay_profiles'] = relays
    if s.get('active_relay') == name:
        s['active_relay'] = ''
    return _save_and_respond(s, f'??????: {name}')


# ??? ???? CRUD ????????????????????????????????


@bp.route('/api/admin/mappings', methods=['GET'])
def list_mappings():
    """??????????????????????"""
    err = _check_auth()
    if err:
        return err
    return jsonify(settings.get().get('model_mappings', {}))


@bp.route('/api/admin/mappings', methods=['POST'])
def add_mapping():
    """??????????????????"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '??????'}), 400

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
    return _save_and_respond(s, f'?????: {name}')


@bp.route('/api/admin/mappings/<path:name>', methods=['PUT'])
def update_mapping(name):
    """?????????????????????"""
    err = _check_auth()
    if err:
        return err
    data = request.get_json(force=True)
    s = settings.get()
    mappings = s.get('model_mappings', {})
    if name not in mappings:
        return jsonify({'error': '?????'}), 404

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
    return _save_and_respond(s, f'?????: {name} ? {new_name}')


@bp.route('/api/admin/mappings/<path:name>', methods=['DELETE'])
def delete_mapping(name):
    """????????????????????????"""
    err = _check_auth()
    if err:
        return err
    s = settings.get()
    mappings = s.get('model_mappings', {})
    if name in mappings:
        del mappings[name]
        s['model_mappings'] = mappings
        return _save_and_respond(s, f'?????: {name}')
    return jsonify({'ok': True})


# ??? ???? ?????????????????????????????????????


@bp.route('/api/admin/stats', methods=['GET'])
def get_stats():
    """????????????"""
    err = _check_auth()
    if err:
        return err
    from utils.usage_tracker import usage_tracker
    return jsonify(usage_tracker.get_stats())


# ??? ???? ?????????????????????????????????????


def _check_auth():
    """Admin API ????? None ????"""
    if not Config.ACCESS_API_KEY:
        return None
    auth = request.headers.get('Authorization', '')
    token = auth[7:] if auth.startswith('Bearer ') else request.headers.get('x-api-key', '')
    if token != Config.ACCESS_API_KEY:
        return jsonify({'error': '???'}), 401
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
        raise ValueError('???????????')
    return relay_name


def _save_and_respond(data, log_msg):
    """??????????????

    ????????????????????? JSON ?????
    """
    try:
        settings.save(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError as e:
        logger.error(f'????: {e}')
        return jsonify({'error': {'message': f'????: {e}', 'type': 'save_error'}}), 500
    logger.info(log_msg)
    return jsonify({'ok': True})
