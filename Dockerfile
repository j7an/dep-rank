FROM python:3.11-alpine

WORKDIR /app

RUN apk update \
    && apk --no-cache --update add build-base libffi-dev openssl-dev curl

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock README.md ./

# Install dependencies
RUN uv sync --frozen --no-dev --no-cache --python-preference only-system

# Copy application code
COPY . .

ENTRYPOINT ["uv", "run", "python", "main.py"]