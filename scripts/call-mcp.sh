#!/usr/bin/env bash
# Call the guachinches MCP server directly with JSON-RPC (no agent, no model): handy for testing the
# gateway, the origin lock and tool blocking.
#
#   scripts/call-mcp.sh gateway tools/list
#   scripts/call-mcp.sh gateway search_guachinches '{"location": "Acentejo", "features": ["vistas"]}'
#   scripts/call-mcp.sh direct  add_favorite '{"guachinche_id": "tegueste-el-portezuelo"}'
#   USER_ID=ana scripts/call-mcp.sh gateway list_favorites
source "$(dirname "$0")/common.sh"
require MCP_API_KEY

ROUTE="${1:?gateway|direct}"
TOOL="${2:?tools/list or a tool name}"
ARGS="${3:-}"
[[ -n "$ARGS" ]] || ARGS='{}'

case "$ROUTE" in
  gateway) require MCP_GATEWAY_URL; URL="$MCP_GATEWAY_URL" ;;
  direct)  require MCP_ORIGIN_URL;  URL="$MCP_ORIGIN_URL" ;;
  *) echo "Route must be gateway or direct" >&2; exit 1 ;;
esac

BODY="$(python3 -c '
import json, sys
tool, args = sys.argv[1], json.loads(sys.argv[2])
if tool == "tools/list":
    print(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
else:
    print(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}))
' "$TOOL" "$ARGS")"

echo "POST $URL  ($TOOL)"
curl -sS -w '\nHTTP %{http_code}\n' \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-06-18" \
  -H "x-mcp-key: $MCP_API_KEY" -H "x-user-id: ${USER_ID:-demo}" \
  -d "$BODY" "$URL" | python3 -c '
import json, sys
raw = sys.stdin.read()
body, _, status = raw.rpartition("\nHTTP ")
try:
    msg = json.loads(body)
    result = msg.get("result", msg)
    if isinstance(result, dict) and "tools" in result:
        print("tools:", [t["name"] for t in result["tools"]])
    elif isinstance(result, dict) and "content" in result:
        if result.get("isError"):
            print("TOOL ERROR:", result["content"][0].get("text"))
        else:
            print(json.dumps(result.get("structuredContent") or json.loads(result["content"][0]["text"]), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(msg, ensure_ascii=False, indent=2))
except Exception:
    print(body)
print("HTTP", status)
'
