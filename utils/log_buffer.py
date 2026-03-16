"""内存日志缓冲区

捕获所有 Python logging 记录并保存到内存环形缓冲区，
供前端实时日志页面轮询查看。
"""

from __future__ import annotations

import collections
import logging
import threading
from datetime import datetime, timezone

_MAX_ENTRIES = 1000
_buffer: collections.deque[dict] = collections.deque(maxlen=_MAX_ENTRIES)
_lock = threading.Lock()
_total = 0  # 单调递增计数，用作 offset


class MemoryLogHandler(logging.Handler):
    """将日志记录写入内存缓冲区的 Handler。"""

    def emit(self, record: logging.LogRecord) -> None:
        global _total
        try:
            msg = self.format(record)
            entry = {
                'idx': 0,       # 占位，写入时填充
                'ts': datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3],
                'level': record.levelname,
                'logger': record.name,
                'msg': msg,
            }
            with _lock:
                entry['idx'] = _total
                _total += 1
                _buffer.append(entry)
        except Exception:
            pass


def get_logs(since_idx: int = 0) -> tuple[list[dict], int]:
    """返回 idx >= since_idx 的日志条目和当前总计数。"""
    with _lock:
        items = [e for e in _buffer if e['idx'] >= since_idx]
        total = _total
    return items, total
