FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests

RUN uv sync --no-install-project

RUN uv sync

CMD ["uv", "run", "python", "-m", "bulario_service"]