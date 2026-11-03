FROM python:3.14-slim

RUN pip install --no-cache-dir fastapi==0.162.0 uvicorn==0.54.0 "psycopg[binary]==3.3.7" \
    && useradd --system --uid 10002 shop

WORKDIR /app
COPY app.py .
ENV PYTHONUNBUFFERED=1
USER shop
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
