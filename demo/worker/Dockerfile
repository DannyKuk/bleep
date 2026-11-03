FROM python:3.14-slim

RUN useradd --system --uid 10003 worker
WORKDIR /app
COPY worker.py .
ENV PYTHONUNBUFFERED=1
USER worker
CMD ["python", "worker.py"]
