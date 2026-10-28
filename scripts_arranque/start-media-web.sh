#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "$script_dir/.." && pwd)"
api_dir="$repo_dir/services/media-api"
web_dir="$repo_dir/apps/media-web"

if [[ "${1:-}" == "--install" ]]; then
  (cd "$api_dir" && uv sync --extra dev)
  (cd "$web_dir" && npm ci)
fi

if [[ ! -x "$api_dir/.venv/bin/python" ]]; then
  printf '%s\n' 'Run ./scripts/start-media-web.sh --install first.' >&2
  exit 1
fi

printf '%s\n' 'Media Core must be running at http://127.0.0.1:8001/mcp.'
printf '%s\n' 'MedIA: http://127.0.0.1:3000   API: http://127.0.0.1:8010/docs'

(cd "$api_dir" && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010) &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT INT TERM

cd "$web_dir"
npm run dev
