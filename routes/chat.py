"""路由: /v1/chat/completions

处理 Cursor 发来的 OpenAI Chat Completions 格式请求。
根据模型映射的后端类型，转发到 OpenAI 兼容接口、Anthropic Messages 接口，
或原生 OpenAI Responses 接口。
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

import settings
from flask import Blueprint, jsonify, request

from adapters.cc_anthropic_adapter import (
    AnthropicStreamConverter,
    cc_to_messages_request,
    messages_to_cc_response,
)
from adapters.cc_gemini_adapter import (
    GeminiStreamConverter,
    cc_to_gemini_request,
    gemini_to_cc_response,
)
from adapters.openai_compat_fixer import fix_response, fix_stream_chunk, normalize_request
from adapters.responses_cc_adapter import (
    ResponsesToCCStreamConverter,
    cc_to_responses_request,
    responses_to_cc,
    responses_to_cc_response,
)
from config import Config
from routes.common import (
    RouteContext,
    apply_body_modifications,
    apply_header_modifications,
    build_anthropic_target,
    build_gemini_target,
    build_openai_target,
    build_responses_target,
    build_route_context,
    chat_error_chunk,
    inject_instructions_anthropic,
    inject_instructions_cc,
    inject_instructions_responses,
    log_route_context,
    log_usage,
    sse_data_message,
)
from utils.http import (
    forward_request,
    gen_id,
    iter_anthropic_sse,
    iter_gemini_sse,
    iter_openai_sse,
    iter_responses_sse,
    sse_response,
)
from utils.request_logger import (
    append_client_event,
    append_upstream_event,
    attach_client_response,
    attach_error,
    attach_upstream_request,
    attach_upstream_response,
    finalize_turn,
    set_stream_summary,
    start_turn,
)
from utils.think_tag import ThinkTagExtractor
from utils.thinking_cache import thinking_cache
from utils.usage_tracker import usage_tracker

logger = logging.getLogger(__name__)

bp = Blueprint('chat', __name__)


def _dbg(message: str) -> None:
    """Output detailed log only when debug mode is enabled."""
    if settings.get_debug_mode() in ('simple', 'verbose'):
        logger.info('[chat:dbg] %s', message)


def _is_verbose() -> bool:
    return settings.get_debug_mode() == 'verbose'


def _extract_responses_usage(event_data: dict[str, Any]) -> dict[str, Any] | None:
    """从原生 Responses 事件中提取 usage。

    `/v1/chat/completions -> /v1/responses` 的桥接流式路径也需要读取 usage，
    因此在本文件保留一个本地辅助函数，避免依赖其他路由模块的私有实现。
    """
    if not isinstance(event_data, dict):
        return None
    usage = event_data.get('usage')
    if isinstance(usage, dict):
        return usage
    response_obj = event_data.get('response')
    if isinstance(response_obj, dict):
        nested_usage = response_obj.get('usage')
        if isinstance(nested_usage, dict):
            return nested_usage
    return None


@bp.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """处理聊天补全请求并按模型映射分发到不同后端。"""
    original_payload = request.get_json(force=True)
    payload, message_count = _normalize_chat_payload(copy.deepcopy(original_payload))

    client_model = payload.get('model', 'unknown')
    is_stream = payload.get('stream', False)
    ctx = build_route_context(client_model, is_stream)
    turn = start_turn(
        route='chat',
        client_model=client_model,
        backend=ctx.backend,
        stream=is_stream,
        client_request=original_payload,
        request_headers=dict(request.headers),
        target_url=ctx.target_url,
        upstream_model=ctx.upstream_model,
        relay_label=ctx.relay_label,
        relay_source=ctx.relay_source,
        metadata={
            'message_count': message_count,
            'relay_label': ctx.relay_label,
            'relay_source': ctx.relay_source,
        },
    )

    log_route_context('chat', ctx, extra=f'消息数={message_count}')

    if ctx.backend != 'responses':
        payload['messages'] = thinking_cache.inject(payload.get('messages', []))

    if ctx.backend == 'openai':
        return _handle_openai_backend(ctx, payload, turn)
    if ctx.backend == 'responses':
        return _handle_responses_backend(ctx, payload, turn)
    if ctx.backend == 'gemini':
        return _handle_gemini_backend(ctx, payload, turn)
    return _handle_anthropic_backend(ctx, payload, turn)


def _normalize_chat_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """整理聊天补全入口的请求体。

    这里保留了一层兼容逻辑：当 Cursor 或调用方把 Responses 格式误发到
    `/v1/chat/completions` 时，先降级转换成 Chat Completions，再进入统一主流程。
    """
    message_count = len(payload.get('messages', []))

    if message_count == 0 and 'input' in payload:
        logger.info('Responses format detected on /v1/chat/completions, auto-converted to Chat Completions')
        payload = responses_to_cc(payload)
        message_count = len(payload.get('messages', []))
    elif message_count == 0:
        logger.warning('empty messages list  fields=%s', list(payload.keys()))

    return payload, message_count


def _handle_openai_backend(ctx: RouteContext, payload: dict[str, Any], turn: dict[str, Any]):
    """处理走 OpenAI 兼容后端的聊天补全请求。"""
    payload = normalize_request(payload, ctx.upstream_model)
    payload = inject_instructions_cc(payload, ctx.custom_instructions, ctx.instructions_position)
    _dbg(f'→ openai  model={payload.get("model")}  tools={len(payload.get("tools", []))}')

    url, headers = build_openai_target(ctx)
    payload = apply_body_modifications(payload, ctx.body_modifications)
    headers = apply_header_modifications(headers, ctx.header_modifications)

    if ctx.is_stream:
        return _handle_openai_stream(ctx, payload, url, headers, turn)
    return _handle_openai_non_stream(ctx, payload, url, headers, turn)


def _handle_openai_non_stream(
    ctx: RouteContext,
    payload: dict[str, Any],
    url: str,
    headers: dict[str, str],
    turn: dict[str, Any],
):
    """处理 OpenAI 兼容后端的非流式返回。"""
    payload['stream'] = False
    attach_upstream_request(turn, payload, headers)
    resp, err = forward_request(url, headers, payload)
    if err:
        attach_error(turn, {'stage': 'forward_request', 'message': 'upstream request failed'})
        finalize_turn(turn)
        return err

    raw = resp.json()
    attach_upstream_response(turn, raw)
    data = fix_response(raw)
    return _finalize_chat_response(ctx, data, turn=turn)


def _handle_openai_stream(
    ctx: RouteContext,
    payload: dict[str, Any],
    url: str,
    headers: dict[str, str],
    turn: dict[str, Any],
):
    """处理 OpenAI 兼容后端的流式返回。"""
    payload['stream'] = True

    def generate():
        """消费上游 OpenAI SSE，并逐段产出给 Cursor 的聊天补全流。"""
        attach_upstream_request(turn, payload, headers)
        resp, err = forward_request(url, headers, payload, stream=True)
        if err:
            attach_error(turn, {'stage': 'forward_request', 'message': str(err)})
            set_stream_summary(turn, {'status': 'error'})
            finalize_turn(turn)
            yield chat_error_chunk(str(err))
            return

        think_extractor = ThinkTagExtractor()
        chunk_count = 0
        last_usage = None
        client_chunks: list[dict[str, Any]] = []

        for chunk in iter_openai_sse(resp):
            if chunk is None:
                close_chunk = think_extractor.finalize()
                if close_chunk:
                    client_chunks.append(close_chunk)
                    append_client_event(turn, {'type': 'chat_chunk', 'data': close_chunk})
                    yield sse_data_message(close_chunk)
                append_client_event(turn, {'type': 'done'})
                yield sse_data_message('[DONE]')
                usage_tracker.record(ctx.client_model, last_usage)
                in_tok = (last_usage or {}).get('prompt_tokens', 0)
                out_tok = (last_usage or {}).get('completion_tokens', 0)
                _dbg(f'stream done  chunks={chunk_count}  in={in_tok}  out={out_tok}')
                set_stream_summary(turn, {
                    'chunk_count': chunk_count,
                    'client_chunk_count': len(client_chunks),
                    'usage': last_usage,
                })
                attach_client_response(turn, {
                    'type': 'chat.completion.stream.summary',
                    'model': ctx.client_model,
                    'chunk_count': len(client_chunks),
                    'usage': last_usage,
                })
                finalize_turn(turn, usage=last_usage)
                return

            if _is_verbose():
                append_upstream_event(turn, {'type': 'openai_chunk', 'data': chunk})
            if chunk.get('usage'):
                last_usage = chunk['usage']

            chunk = fix_stream_chunk(chunk)
            chunk['model'] = ctx.client_model

            for out in think_extractor.process_chunk(chunk):
                client_chunks.append(out)
                if _is_verbose():
                    append_client_event(turn, {'type': 'chat_chunk', 'data': out})
                yield sse_data_message(out)

            chunk_count += 1

        usage_tracker.record(ctx.client_model, last_usage)
        set_stream_summary(turn, {
            'chunk_count': chunk_count,
            'client_chunk_count': len(client_chunks),
            'usage': last_usage,
            'ended_without_done': True,
        })
        attach_client_response(turn, {
            'type': 'chat.completion.stream.summary',
            'model': ctx.client_model,
            'chunk_count': len(client_chunks),
            'usage': last_usage,
        })
        finalize_turn(turn, usage=last_usage)

    return sse_response(generate())


def _handle_responses_backend(ctx: RouteContext, payload: dict[str, Any], turn: dict[str, Any] | None):
    """处理走原生 Responses 后端的聊天补全请求。

    当上游只支持 `/v1/responses` 时，需要先把聊天补全请求转换为 Responses 请求，
    返回时再转换回聊天补全协议。
    """
    responses_payload = cc_to_responses_request(payload)
    responses_payload['model'] = ctx.upstream_model
    responses_payload = inject_instructions_responses(responses_payload, ctx.custom_instructions, ctx.instructions_position)
    _dbg(f'→ responses  inputs={len(responses_payload.get("input", []))}')

    url, headers = build_responses_target(ctx)
    responses_payload = apply_body_modifications(responses_payload, ctx.body_modifications)
    headers = apply_header_modifications(headers, ctx.header_modifications)

    if ctx.is_stream:
        return _handle_responses_stream(ctx, responses_payload, url, headers, turn)
    return _handle_responses_non_stream(ctx, responses_payload, url, headers, turn)


def _handle_responses_non_stream(
    ctx: RouteContext,
    payload: dict[str, Any],
    url: str,
    headers: dict[str, str],
    turn: dict[str, Any] | None,
):
    """处理原生 Responses 后端的非流式返回。"""
    payload['stream'] = False
    attach_upstream_request(turn, payload, headers)
    resp, err = forward_request(url, headers, payload)
    if err:
        attach_error(turn, {'stage': 'forward_request', 'message': 'upstream request failed'})
        finalize_turn(turn)
        return err

    raw = resp.json()
    attach_upstream_response(turn, raw)
    data = responses_to_cc_response(raw, ctx.client_model)
    return _finalize_chat_response(ctx, data, turn=turn)


def _handle_responses_stream(
    ctx: RouteContext,
    payload: dict[str, Any],
    url: str,
    headers: dict[str, str],
    turn: dict[str, Any] | None,
):
    """处理原生 Responses 后端的流式返回。"""
    payload['stream'] = True
    converter = ResponsesToCCStreamConverter(model=ctx.client_model)

    def generate():
        """消费上游 Responses 事件，并实时转换成聊天补全 chunk。"""
        attach_upstream_request(turn, payload, headers)
        resp, err = forward_request(url, headers, payload, stream=True)
        if err:
            attach_error(turn, {'stage': 'forward_request', 'message': str(err)})
            set_stream_summary(turn, {'status': 'error'})
            finalize_turn(turn)
            yield chat_error_chunk(str(err))
            return

        event_count = 0
        client_chunks: list[Any] = []
        last_usage: dict[str, Any] | None = None
        for event_type, event_data in iter_responses_sse(resp):
            if _is_verbose():
                append_upstream_event(turn, {'type': event_type, 'data': event_data})
            extracted_usage = _extract_responses_usage(event_data)
            if extracted_usage:
                last_usage = {
                    'prompt_tokens': extracted_usage.get('input_tokens', 0),
                    'completion_tokens': extracted_usage.get('output_tokens', 0),
                    'total_tokens': extracted_usage.get('total_tokens', 0),
                }

            for chunk in converter.process_event(event_type, event_data):
                client_chunks.append(chunk)
                if _is_verbose():
                    append_client_event(turn, {'type': 'chat_chunk', 'data': chunk})
                if isinstance(chunk, dict) and isinstance(chunk.get('usage'), dict):
                    last_usage = chunk['usage']
                yield sse_data_message(chunk)

            event_count += 1

        in_tok = (last_usage or {}).get('prompt_tokens', 0)
        out_tok = (last_usage or {}).get('completion_tokens', 0)
        _dbg(f'stream done  events={event_count}  in={in_tok}  out={out_tok}')
        append_client_event(turn, {'type': 'done'})
        yield sse_data_message('[DONE]')
        usage_tracker.record(ctx.client_model, last_usage)
        set_stream_summary(turn, {
            'event_count': event_count,
            'client_chunk_count': len(client_chunks),
            'usage': last_usage,
        })
        attach_client_response(turn, {
            'type': 'chat.completion.stream.summary',
            'model': ctx.client_model,
            'chunk_count': len(client_chunks),
            'usage': last_usage,
        })
        finalize_turn(turn, usage=last_usage)

    return sse_response(generate())


def _handle_gemini_backend(ctx: RouteContext, payload: dict[str, Any], turn: dict[str, Any] | None):
    """处理走 Gemini Contents 后端的聊天补全请求。"""
    payload = inject_instructions_cc(payload, ctx.custom_instructions, ctx.instructions_position)
    gemini_payload = cc_to_gemini_request(payload)
    _dbg(f'→ gemini  contents={len(gemini_payload.get("contents", []))}')

    url, headers = build_gemini_target(ctx, stream=ctx.is_stream)
    gemini_payload = apply_body_modifications(gemini_payload, ctx.body_modifications)
    headers = apply_header_modifications(headers, ctx.header_modifications)

    if ctx.is_stream:
        return _handle_gemini_stream(ctx, gemini_payload, url, headers, turn)
    return _handle_gemini_non_stream(ctx, gemini_payload, url, headers, turn)


def _handle_gemini_non_stream(
    ctx: RouteContext,
    payload: dict[str, Any],
    url: str,
    headers: dict[str, str],
    turn: dict[str, Any] | None,
):
    """处理 Gemini 后端的非流式返回。"""
    attach_upstream_request(turn, payload, headers)
    resp, err = forward_request(url, headers, payload)
    if err:
        attach_error(turn, {'stage': 'forward_request', 'message': 'upstream request failed'})
        finalize_turn(turn)
        return err

    raw = resp.json()
    attach_upstream_response(turn, raw)
    data = gemini_to_cc_response(raw)
    return _finalize_chat_response(ctx, data, turn=turn)


def _handle_gemini_stream(
    ctx: RouteContext,
    payload: dict[str, Any],
    url: str,
    headers: dict[str, str],
    turn: dict[str, Any] | None,
):
    """处理 Gemini 后端的流式返回。"""
    converter = GeminiStreamConverter()

    def generate():
        attach_upstream_request(turn, payload, headers)
        resp, err = forward_request(url, headers, payload, stream=True)
        if err:
            attach_error(turn, {'stage': 'forward_request', 'message': str(err)})
            set_stream_summary(turn, {'status': 'error'})
            finalize_turn(turn)
            yield chat_error_chunk(str(err))
            return

        chunk_count = 0
        client_chunks: list[Any] = []
        last_usage: dict[str, Any] | None = None
        for gemini_chunk in iter_gemini_sse(resp):
            if _is_verbose():
                append_upstream_event(turn, {'type': 'gemini_chunk', 'data': gemini_chunk})
            usage_meta = gemini_chunk.get('usageMetadata') if isinstance(gemini_chunk, dict) else None
            if isinstance(usage_meta, dict):
                last_usage = {
                    'prompt_tokens': usage_meta.get('promptTokenCount', 0),
                    'completion_tokens': usage_meta.get('candidatesTokenCount', 0),
                    'total_tokens': usage_meta.get('totalTokenCount', 0),
                }

            for cc_chunk in converter.process_chunk(gemini_chunk):
                cc_chunk['model'] = ctx.client_model
                client_chunks.append(cc_chunk)
                if _is_verbose():
                    append_client_event(turn, {'type': 'chat_chunk', 'data': cc_chunk})
                if isinstance(cc_chunk, dict) and isinstance(cc_chunk.get('usage'), dict):
                    last_usage = cc_chunk['usage']
                yield sse_data_message(cc_chunk)

            chunk_count += 1

        in_tok = (last_usage or {}).get('prompt_tokens', 0)
        out_tok = (last_usage or {}).get('completion_tokens', 0)
        _dbg(f'stream done  chunks={chunk_count}  in={in_tok}  out={out_tok}')
        append_client_event(turn, {'type': 'done'})
        yield sse_data_message('[DONE]')
        usage_tracker.record(ctx.client_model, last_usage)
        set_stream_summary(turn, {
            'chunk_count': chunk_count,
            'client_chunk_count': len(client_chunks),
            'usage': last_usage,
        })
        attach_client_response(turn, {
            'type': 'chat.completion.stream.summary',
            'model': ctx.client_model,
            'chunk_count': len(client_chunks),
            'usage': last_usage,
        })
        finalize_turn(turn, usage=last_usage)

    return sse_response(generate())


def _handle_anthropic_backend(ctx: RouteContext, payload: dict[str, Any], turn: dict[str, Any] | None):
    """处理走 Anthropic Messages 后端的聊天补全请求。"""
    payload['model'] = ctx.upstream_model
    anthropic_payload = cc_to_messages_request(payload)
    anthropic_payload = inject_instructions_anthropic(anthropic_payload, ctx.custom_instructions, ctx.instructions_position)
    _dbg(f'→ anthropic/messages  msgs={len(anthropic_payload.get("messages", []))}  tools={len(anthropic_payload.get("tools", []))}')

    url, headers = build_anthropic_target(ctx)
    anthropic_payload = apply_body_modifications(anthropic_payload, ctx.body_modifications)
    headers = apply_header_modifications(headers, ctx.header_modifications)

    if ctx.is_stream:
        return _handle_anthropic_stream(ctx, anthropic_payload, url, headers, turn)
    return _handle_anthropic_non_stream(ctx, anthropic_payload, url, headers, turn)


def _handle_anthropic_non_stream(
    ctx: RouteContext,
    payload: dict[str, Any],
    url: str,
    headers: dict[str, str],
    turn: dict[str, Any] | None,
):
    """处理 Anthropic 后端的非流式返回。"""
    payload['stream'] = False
    attach_upstream_request(turn, payload, headers)
    resp, err = forward_request(url, headers, payload)
    if err:
        attach_error(turn, {'stage': 'forward_request', 'message': 'upstream request failed'})
        finalize_turn(turn)
        return err

    raw = resp.json()
    attach_upstream_response(turn, raw)
    data = messages_to_cc_response(raw)
    return _finalize_chat_response(ctx, data, turn=turn)


def _handle_anthropic_stream(
    ctx: RouteContext,
    payload: dict[str, Any],
    url: str,
    headers: dict[str, str],
    turn: dict[str, Any] | None,
):
    """处理 Anthropic 后端的流式返回。

    这里仍然保留独立的事件级转换器，而不是先落成完整响应再回放，
    是为了尽量保持 Cursor 端的流式体验和工具调用时序。
    """
    payload['stream'] = True
    converter = AnthropicStreamConverter()

    def generate():
        """消费上游 Anthropic 事件流，并逐步映射为聊天补全 SSE。"""
        attach_upstream_request(turn, payload, headers)
        resp, err = forward_request(url, headers, payload, stream=True)
        if err:
            attach_error(turn, {'stage': 'forward_request', 'message': str(err)})
            set_stream_summary(turn, {'status': 'error'})
            finalize_turn(turn)
            yield chat_error_chunk(str(err))
            return

        event_count = 0
        client_chunks: list[Any] = []
        last_usage: dict[str, Any] | None = None
        for event_type, event_data in iter_anthropic_sse(resp):
            if _is_verbose():
                append_upstream_event(turn, {'type': event_type, 'data': event_data})
            if event_type == 'message_start':
                message_usage = event_data.get('message', {}).get('usage', {})
                if isinstance(message_usage, dict):
                    last_usage = {
                        'prompt_tokens': message_usage.get('input_tokens', 0),
                        'completion_tokens': 0,
                        'total_tokens': message_usage.get('input_tokens', 0),
                    }
            elif event_type == 'message_delta':
                delta_usage = event_data.get('usage', {})
                if isinstance(delta_usage, dict):
                    prompt_tokens = 0
                    if isinstance(last_usage, dict):
                        prompt_tokens = last_usage.get('prompt_tokens', 0)
                    completion_tokens = delta_usage.get('output_tokens', 0)
                    last_usage = {
                        'prompt_tokens': prompt_tokens,
                        'completion_tokens': completion_tokens,
                        'total_tokens': prompt_tokens + completion_tokens,
                    }

            for chunk_str in converter.process_event(event_type, event_data):
                try:
                    chunk_obj = json.loads(chunk_str)
                    chunk_obj['model'] = ctx.client_model
                    if isinstance(chunk_obj.get('usage'), dict):
                        last_usage = chunk_obj['usage']
                    chunk_str = json.dumps(chunk_obj, ensure_ascii=False)
                except (json.JSONDecodeError, TypeError):
                    pass

                client_chunks.append(chunk_str)
                if _is_verbose():
                    append_client_event(turn, {'type': 'chat_chunk', 'data': chunk_str})
                yield sse_data_message(chunk_str)

            event_count += 1

        in_tok = (last_usage or {}).get('prompt_tokens', 0)
        out_tok = (last_usage or {}).get('completion_tokens', 0)
        _dbg(f'stream done  events={event_count}  in={in_tok}  out={out_tok}')
        append_client_event(turn, {'type': 'done'})
        yield sse_data_message('[DONE]')
        usage_tracker.record(ctx.client_model, last_usage)
        set_stream_summary(turn, {
            'event_count': event_count,
            'client_chunk_count': len(client_chunks),
            'usage': last_usage,
        })
        attach_client_response(turn, {
            'type': 'chat.completion.stream.summary',
            'model': ctx.client_model,
            'chunk_count': len(client_chunks),
            'usage': last_usage,
        })
        finalize_turn(turn, usage=last_usage)

    return sse_response(generate())


def _finalize_chat_response(
    ctx: RouteContext,
    data: dict[str, Any],
    *,
    turn: dict[str, Any] | None,
):
    """统一收尾非流式聊天补全响应。

    三条后端链路最终都会回到 Chat Completions 格式，因此这里集中做：
    - 回填给 Cursor 展示的模型名
    - 输出统一调试日志
    - 输出统一令牌统计日志
    """
    data['model'] = ctx.client_model
    log_usage('chat', data.get('usage', {}), input_key='prompt_tokens', output_key='completion_tokens')

    usage_tracker.record(ctx.client_model, data.get('usage'))
    attach_client_response(turn, data)
    finalize_turn(turn, usage=data.get('usage'))

    for choice in data.get('choices', []):
        msg = choice.get('message', {})
        if msg.get('reasoning_content'):
            thinking_cache.store_from_response(
                request.get_json(silent=True, force=True).get('messages', []),
                msg['reasoning_content'],
            )
            break

    return jsonify(data)


