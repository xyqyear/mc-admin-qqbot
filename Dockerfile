FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN python -m pip install --no-cache-dir uv==0.12.0

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN groupadd --gid 1000 bot \
    && useradd --uid 1000 --gid 1000 --no-create-home \
        --home-dir /app --shell /usr/sbin/nologin bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app

USER bot

ENV PATH="/app/.venv/bin:$PATH"

CMD ["mc-admin-qqbot"]
