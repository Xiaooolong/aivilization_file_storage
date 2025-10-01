export APP_LOCALE=cn
export PORT=8098
gunicorn app:app \
    -k uvicorn.workers.UvicornWorker \
    -w 4 \
    -b 0.0.0.0:"${PORT:-8000}"
