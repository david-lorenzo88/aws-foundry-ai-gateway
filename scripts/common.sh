# Shared settings for the scripts. Values live in .env at the repo root (gitignored).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

AGENT_NAME="${AGENT_NAME:-guachinche-agent}"
STACK_NAME="${STACK_NAME:-$AGENT_NAME}"

# Upsert KEY=VALUE in .env so later scripts (and re-runs) reuse it.
save_env() {
  local key="$1" value="$2"
  touch "$ENV_FILE"
  grep -v "^${key}=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
  printf '%s=%q\n' "$key" "$value" >> "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
  export "$key=$value"
}

require() {
  for var in "$@"; do
    [[ -n "${!var:-}" ]] || { echo "Missing $var (set it in .env or the environment)." >&2; exit 1; }
  done
}
