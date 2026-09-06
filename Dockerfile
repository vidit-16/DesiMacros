FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data && chmod +x start.sh

# 8000 = FastAPI, 8501 = Streamlit
EXPOSE 8000 8501

# Single-container default: both services, Streamlit on $PORT.
# docker-compose overrides this with one command per service.
CMD ["./start.sh"]
