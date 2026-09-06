#!/usr/bin/env bash
# Runs both processes in one container: FastAPI internally on 8000,
# Streamlit on $PORT (the port a host like Render/HF Spaces exposes).
set -e

PORT="${PORT:-8501}"

uvicorn app.api.main:app --host 0.0.0.0 --port 8000 &

exec streamlit run app/ui/streamlit_app.py \
    --server.port "$PORT" \
    --server.address 0.0.0.0 \
    --server.headless true
