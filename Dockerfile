# dep-rank MCP server image. Build context: dist/<wheel> plus dist/requirements.txt from
# `uv export --frozen --no-dev --extra mcp --no-emit-project -o dist/requirements.txt`.
FROM python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962

LABEL org.opencontainers.image.source=https://github.com/j7an/dep-rank

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY dist/requirements.txt dist/*.whl /tmp/dist/
RUN pip install --require-hashes -r /tmp/dist/requirements.txt \
    && pip install --no-deps /tmp/dist/*.whl \
    && rm -rf /tmp/dist \
    && useradd --create-home --uid 10001 app \
    && mkdir /cache \
    && chown app /cache

# A fresh named volume mounted here inherits the directory's ownership.
ENV XDG_CACHE_HOME=/cache
USER app
ENTRYPOINT ["dep-rank", "mcp"]
