#!/usr/bin/env bash
# api2cursor 后台运行管理脚本
# 用法:
#   ./run.sh start    # 后台启动
#   ./run.sh stop     # 停止
#   ./run.sh restart  # 重启
#   ./run.sh status   # 查看状态
#   ./run.sh logs     # 实时查看日志

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/api2cursor.pid"
LOG_FILE="$SCRIPT_DIR/api2cursor.log"

_start() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "已在运行 (PID $pid)，若要重启请用: $0 restart"
            exit 0
        fi
        rm -f "$PID_FILE"
    fi

    cd "$SCRIPT_DIR"
    nohup python start.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "已在后台启动 (PID $(cat "$PID_FILE"))，日志: $LOG_FILE"
}

_stop() {
    if [[ ! -f "$PID_FILE" ]]; then
        echo "未找到 PID 文件，服务可能未运行"
        return
    fi
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        rm -f "$PID_FILE"
        echo "已停止 (PID $pid)"
    else
        echo "进程 $pid 不存在，清理 PID 文件"
        rm -f "$PID_FILE"
    fi
}

_status() {
    if [[ ! -f "$PID_FILE" ]]; then
        echo "状态: 未运行"
        return
    fi
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        echo "状态: 运行中 (PID $pid)"
    else
        echo "状态: 进程已退出（残留 PID 文件），建议运行 $0 stop 清理"
    fi
}

case "${1:-}" in
    start)   _start ;;
    stop)    _stop ;;
    restart) _stop; sleep 1; _start ;;
    status)  _status ;;
    logs)    tail -f "$LOG_FILE" ;;
    *)
        echo "用法: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
