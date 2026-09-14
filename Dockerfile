FROM python:3.11-slim

# Hugging Face Spaces runs containers as UID 1000, so anything the app writes
# (the SQLite files, Streamlit's own state) has to be owned by that user.
RUN useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data && chmod +x start.sh && chown -R appuser:appuser /app

ENV HOME=/home/appuser
# Containers default to UTC, which files a late-night meal under the previous
# day for anyone in India. Override TZ if you are elsewhere.
ENV TZ=Asia/Kolkata
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

USER appuser

# 8000 = FastAPI (internal only), 8501 = Streamlit (the public port)
EXPOSE 8000 8501

# Streamlit's built-in health endpoint on the public port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \n  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:%s/_stcore/health' % os.getenv('PORT','8501'), timeout=4)" || exit 1

# Single-container default: both services, Streamlit on $PORT.
# docker-compose overrides this with one command per service.
CMD ["./start.sh"]
