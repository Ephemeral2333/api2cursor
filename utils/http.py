"""HTTP helpers for upstream forwarding and SSE parsing."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Iterator

try:
    from curl_cffi import requests

    _IMPERSONATE = 'chrome120'
except ImportError:
    import requests  # type: ignore

    _IMPERSONATE = None

from flask import Response, jsonify

from config import Config

logger = logging.getLogger(__name__)


def gen_id(prefix: str = '') -> str:
    """Generate a short random id."""
    return f'{prefix}{uuid.uuid4().hex[:24]}'


def build_openai_headers(api_key: str) -> dict[str, str]:
    """Build OpenAI-compatible headers."""
    return {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }


def build_anthropic_headers(api_key: str) -> dict[str, str]:
    """Build Anthropic headers."""
    headers = {
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
    }
    if api_key.startswith('sk-'):
        headers['x-api-key'] = api_key
    else:
        headers['Authorization'] = f'Bearer {api_key}'
    return headers


def build_gemini_headers(api_key: str) -> dict[str, str]:
    """Build Gemini headers."""
    headers = {'Content-Type': 'application/json'}
    if api_key.startswith('AIza'):
        headers['x-goog-api-key'] = api_key
    else:
        headers['Authorization'] = f'Bearer {api_key}'
    return headers


def sse_response(generator):
    """Wrap a generator as an SSE response."""
    return Response(
        generator,
        content_type='text/event-stream; charset=utf-8',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


def error_json(message, error_type='proxy_error', status=502):
    """Build a standard JSON error response."""
    return jsonify({'error': {'message': str(message), 'type': error_type}}), status


def read_error_response(resp) -> tuple[bytes, str]:
    """Best-effort read of an upstream error response body."""
    body_bytes = _coerce_body_bytes(_safe_get_content(resp))

    if not body_bytes:
        body_bytes = _read_body_from_stream(resp)

    body_text = ''
    if body_bytes:
        body_text = body_bytes.decode('utf-8', errors='replace')

    if not body_text:
        text = _safe_get_text(resp)
        if text:
            body_text = text
            body_bytes = text.encode('utf-8', errors='replace')

    if not body_text:
        body_text = _format_empty_error(resp)
        body_bytes = body_text.encode('utf-8', errors='replace')

    return body_bytes, body_text


def forward_request(url, headers, payload, stream=False):
    """Forward a request to the upstream API.

    Returns:
        success: (response, None)
        failure (streaming): (None, error_body_str)
        failure (non-streaming): (None, Flask Response)
    """
    try:
        kwargs: dict[str, Any] = dict(
            headers=headers,
            json=payload,
            timeout=Config.API_TIMEOUT,
            stream=stream,
        )
        if _IMPERSONATE:
            kwargs['impersonate'] = _IMPERSONATE

        import settings as _settings

        proxy = _settings.get_upstream_proxy()
        if proxy:
            kwargs['proxies'] = {'https': proxy, 'http': proxy}

        resp = requests.post(url, **kwargs)
        if resp.status_code != 200:
            body_bytes, body = read_error_response(resp)
            logger.warning('上游返回 %s: %s', resp.status_code, body[:300])
            if stream:
                return None, f'上游错误 {resp.status_code}: {body}'
            return None, Response(
                body_bytes,
                status=resp.status_code,
                content_type=resp.headers.get('Content-Type', 'application/json'),
            )
        return resp, None
    except Exception as e:
        logger.error('请求上游失败: %s', e)
        if stream:
            return None, str(e)
        return None, error_json(str(e))


def iter_openai_sse(response) -> Iterator[dict[str, Any] | None]:
    """Parse OpenAI SSE and yield dict chunks or None for [DONE]."""
    for line in response.iter_lines():
        if not line:
            continue
        decoded = line.decode('utf-8', errors='replace')
        if not decoded.startswith('data:'):
            continue
        data_str = decoded[5:].strip()
        if data_str == '[DONE]':
            yield None
            return
        try:
            yield json.loads(data_str)
        except json.JSONDecodeError:
            continue


def iter_anthropic_sse(response) -> Iterator[tuple[str, dict[str, Any]]]:
    """Parse Anthropic SSE and yield (event_type, data_dict)."""
    yield from _iter_event_sse(response)


def iter_responses_sse(response) -> Iterator[tuple[str, dict[str, Any]]]:
    """Parse OpenAI Responses SSE and yield (event_type, data_dict)."""
    yield from _iter_event_sse(response)


def iter_gemini_sse(response) -> Iterator[dict[str, Any]]:
    """Parse Gemini SSE and yield complete response objects."""
    for line in response.iter_lines():
        if not line:
            continue
        decoded = line.decode('utf-8', errors='replace')
        if not decoded.startswith('data:'):
            continue
        data_str = decoded[5:].strip()
        if not data_str:
            continue
        try:
            yield json.loads(data_str)
        except json.JSONDecodeError:
            continue


def _iter_event_sse(response) -> Iterator[tuple[str, dict[str, Any]]]:
    """Parse generic SSE streams with event/data lines."""
    event_type = ''
    for line in response.iter_lines():
        if not line:
            event_type = ''
            continue
        decoded = line.decode('utf-8', errors='replace')
        if decoded.startswith('event:'):
            event_type = decoded[6:].strip()
        elif decoded.startswith('data:'):
            data_str = decoded[5:].strip()
            if not data_str:
                continue
            try:
                yield event_type, json.loads(data_str)
            except json.JSONDecodeError:
                continue


def _safe_get_content(resp) -> Any:
    try:
        return resp.content
    except Exception:
        return b''


def _safe_get_text(resp) -> str:
    try:
        text = resp.text
    except Exception:
        return ''
    return text if isinstance(text, str) else ''


def _coerce_body_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return value.encode('utf-8', errors='replace')
    return b''


def _read_body_from_stream(resp) -> bytes:
    iter_content = getattr(resp, 'iter_content', None)
    if callable(iter_content):
        try:
            chunks: list[bytes] = []
            for chunk in iter_content(chunk_size=65536):
                chunk_bytes = _coerce_body_bytes(chunk)
                if chunk_bytes:
                    chunks.append(chunk_bytes)
            if chunks:
                return b''.join(chunks)
        except Exception:
            pass

    raw = getattr(resp, 'raw', None)
    if raw is not None and hasattr(raw, 'read'):
        try:
            return _coerce_body_bytes(raw.read())
        except Exception:
            return b''

    return b''


def _format_empty_error(resp) -> str:
    status = getattr(resp, 'status_code', 'unknown')
    reason = str(getattr(resp, 'reason', '') or '').strip()
    headers = getattr(resp, 'headers', {}) or {}
    content_type = str(headers.get('Content-Type', '') or '').strip()
    content_length = str(headers.get('Content-Length', '') or '').strip()
    transfer_encoding = str(headers.get('Transfer-Encoding', '') or '').strip()

    parts = [f'<empty upstream body status={status}>']
    if reason:
        parts.append(f'reason={reason}')
    if content_type:
        parts.append(f'content-type={content_type}')
    if content_length:
        parts.append(f'content-length={content_length}')
    if transfer_encoding:
        parts.append(f'transfer-encoding={transfer_encoding}')
    return ' '.join(parts)
