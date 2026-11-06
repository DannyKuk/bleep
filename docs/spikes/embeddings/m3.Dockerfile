# The backend image with bge-m3 baked in where bge-small was (#146), for the spike only: the
# model RUN is the one change against backend/Dockerfile.
#   docker build -f docs/spikes/embeddings/m3.Dockerfile \
#       --build-context spike=docs/spikes/embeddings -t bleep-spike-146:m3 backend
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

# No uv cache in the image: it would keep a second copy of every wheel.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
# The curated Docker docs knowledge search ingests on first start (docs/design/knowledge.md).
COPY knowledge ./knowledge

# The local embedding model for long-term memory, fetched once at build time, so recall works
# offline and the first incident does not wait for a download (docs/decisions/embeddings.md).
ENV FASTEMBED_CACHE_PATH=/app/models
COPY --from=spike models.py /opt/spike/models.py
RUN cd /opt/spike && MODEL=m3 /app/.venv/bin/python -c "from models import embedder; embedder().embed(['ok'])" \
    && chmod -R a+rX /app/models

# A fixed uid/gid, so a mounted key can be chowned to it on a Linux host (docs/setup.md).
RUN groupadd --system --gid 10001 bleep \
    && useradd --system --create-home --uid 10001 --gid 10001 bleep \
    && mkdir /data && chown bleep /data
USER bleep

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "bleep.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
