#!/usr/bin/env bash
# smoke-test-image.sh - smoke-test a built dep-rank MCP server image.
#
# Used by the ci.yml docker-build job and the release.yml publish-ghcr job.
#
# Usage: smoke-test-image.sh <image-ref> <expected-version>
#
# Checks:
#   1. `dep-rank --version` reports the expected version.
#   2. The default entrypoint (`dep-rank mcp`) answers an MCP initialize request,
#      which fails if the image lacks the `mcp` extra.
#   3. A fresh named volume mounted at /cache is writable by the image's user.
#
# Exit:
#   0 - all checks passed
#   1 - a check failed (a ::error:: line names it)
#   2 - usage error

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <image-ref> <expected-version>" >&2
  exit 2
fi

image="$1"
version="$2"

version_output=$(docker run --rm --entrypoint dep-rank "$image" --version)
case "$version_output" in
  *"$version"*) ;;
  *)
    echo "::error::$image reports '$version_output', expected version '$version'"
    exit 1
    ;;
esac

initialize='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}'
# The server may exit non-zero or be stopped by timeout after stdin closes; only the
# initialize response matters.
handshake_output=$(printf '%s\n' "$initialize" | timeout 60 docker run -i --rm "$image" 2>&1 || true)
case "$handshake_output" in
  *'"serverInfo"'*) ;;
  *)
    printf '%s\n' "$handshake_output"
    echo "::error::$image did not answer the MCP initialize request"
    exit 1
    ;;
esac

volume="dep-rank-smoke-$$-$RANDOM"
trap 'docker volume rm -f "$volume" >/dev/null 2>&1 || true' EXIT
docker volume create "$volume" >/dev/null
if ! docker run --rm -v "$volume":/cache --entrypoint sh "$image" -c 'touch /cache/probe'; then
  echo "::error::$image cannot write to a fresh volume mounted at /cache"
  exit 1
fi

echo "smoke test passed: $image ($version)"
