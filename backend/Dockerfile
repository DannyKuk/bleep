FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

# A fixed uid/gid, so a mounted key can be chowned to it on a Linux host (docs/setup.md).
RUN groupadd --system --gid 10001 bleep \
    && useradd --system --create-home --uid 10001 --gid 10001 bleep \
    && mkdir /data && chown bleep /data
USER bleep

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "bleep.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
