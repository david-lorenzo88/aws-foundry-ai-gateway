#!/usr/bin/env bash
# Ask the agent something, either through the Foundry AI Gateway or straight at the Lambda URL.
# Pass SESSION_ID to keep a conversation going; without it each call starts a new one.
#
#   SESSION_ID=comida scripts/call-agent.sh gateway "Busco un guachinche en el norte con terraza y conejo en salmorejo para el domingo"
#   SESSION_ID=comida scripts/call-agent.sh gateway "Guárdame el primero en favoritos"
#   USER_ID=ana scripts/call-agent.sh direct "Find me a guachinche near La Laguna"
source "$(dirname "$0")/common.sh"
require AGENT_API_KEY

ROUTE="${1:?gateway|direct}"
MESSAGE="${2:-Busco un guachinche en el norte de Tenerife con vistas para comer este domingo. ¿Qué me recomiendas?}"

case "$ROUTE" in
  gateway) require AGENT_GATEWAY_URL; BASE="$AGENT_GATEWAY_URL" ;;
  direct)  require AGENT_ORIGIN_URL;  BASE="$AGENT_ORIGIN_URL" ;;
  *) echo "Route must be gateway or direct" >&2; exit 1 ;;
esac

# The gateway proxies the origin's own auth, so the same x-agent-key goes to both URLs.
HEADERS=(-H "Content-Type: application/json" -H "x-agent-key: $AGENT_API_KEY")
[[ "$ROUTE" == gateway && -n "${APIM_SUBSCRIPTION_KEY:-}" ]] && HEADERS+=(-H "Ocp-Apim-Subscription-Key: $APIM_SUBSCRIPTION_KEY")

BODY="$(python3 -c '
import json, sys
body = {"message": sys.argv[1], "user_id": sys.argv[2]}
if sys.argv[3]:
    body["session_id"] = sys.argv[3]
print(json.dumps(body))
' "$MESSAGE" "${USER_ID:-demo}" "${SESSION_ID:-}")"

echo "POST ${BASE%/}/invoke  (user ${USER_ID:-demo}, session ${SESSION_ID:-new})"
curl -sS -w '\nHTTP %{http_code}\n' "${HEADERS[@]}" -d "$BODY" "${BASE%/}/invoke" | python3 -c '
import json, sys
raw = sys.stdin.read()
body, _, status = raw.rpartition("\nHTTP ")
try:
    msg = json.loads(body)
    if "output" in msg:
        print(msg["output"])
        sid = msg["session_id"]
        print(f"\n[session {sid}; continue with SESSION_ID={sid}]")
    else:
        print(json.dumps(msg, ensure_ascii=False))
except Exception:
    print(body)
print("HTTP", status)
'
