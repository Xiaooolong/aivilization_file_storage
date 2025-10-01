APP_LOCALE=cn
PORT=4010
WORKERS=4
BIND="0.0.0.0:${PORT:-8000}"
APP="app:app"
SESSION="aivil_storage"
PIDFILE="/tmp/gunicorn_${SESSION}.pid"

start() {
    echo "启动应用..."
    screen -dmS "$SESSION" bash -c "exec gunicorn $APP \
        -k uvicorn.workers.UvicornWorker \
        -w $WORKERS \
        -b $BIND \
        --pid $PIDFILE"
    echo "已在 screen 会话 '$SESSION' 中启动"
}

stop() {
    echo "停止应用..."
    if [[ -f $PIDFILE ]]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill -TERM "$PID"
            # 等待最多10秒优雅退出
            for i in {1..10}; do
                if kill -0 "$PID" 2>/dev/null; then
                    sleep 1
                else
                    break
                fi
            done
            # 还没死就强杀
            if kill -0 "$PID" 2>/dev/null; then
                echo "优雅退出超时，执行强制结束..."
                kill -KILL "$PID"
            fi
        fi
        rm -f "$PIDFILE"
    else
        echo "未找到 pid 文件，尝试通过端口与进程名查杀..."
        # 任选其一：按端口或按命令行匹配
        lsof -ti :"${PORT}" | xargs -r kill -TERM
        pkill -f "gunicorn .*${APP}" || true
    fi

    # 关掉 screen（如果还在）
    screen -S "$SESSION" -X quit 2>/dev/null || true
    echo "应用已停止"
}

restart() {
    stop
    sleep 1
    start
}

status() {
    if [[ -f $PIDFILE ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "应用正在运行 (PID $(cat "$PIDFILE"))"
    else
        # 兜底：看 screen 是否在
        if screen -ls | grep -q "$SESSION"; then
            echo "Screen 会话在，但 pid 文件缺失；应用状态未知"
        else
            echo "应用未运行"
        fi
    fi
}

attach() {
    echo "进入 screen 会话..."
    screen -r "$SESSION"
}

case "$1" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    status) status ;;
    attach) attach ;;
    *) echo "用法: $0 {start|stop|restart|status|attach}"; exit 1 ;;
esac
