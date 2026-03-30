#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]]; then
  echo "CLOUDFLARE_TUNNEL_TOKEN is required" >&2
  exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed or not on PATH" >&2
  exit 1
fi

exec cloudflared tunnel run --token "$CLOUDFLARE_TUNNEL_TOKEN"
