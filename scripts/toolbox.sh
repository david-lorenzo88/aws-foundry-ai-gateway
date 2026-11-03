#!/usr/bin/env bash
# Publish the guachinches MCP tools as a Foundry Toolbox, and choose which tools agents may use.
#
#   scripts/toolbox.sh --create                   # first version: all tools (saves TOOLBOX_URL in .env)
#   scripts/toolbox.sh add_favorite list_locations # new version without these tools, promoted to default
#   scripts/toolbox.sh --none                     # new version with every tool, promoted to default
#   scripts/toolbox.sh --status                   # default version and the tools it allows
#
# A Toolbox version is an immutable list of tools; agents read the default version. Leaving a tool out of
# allowed_tools hides it from tools/list and makes the Toolbox reject calls to it (PermissionDenied).
# The Toolbox reaches the MCP server through the AI Gateway (the project connection Foundry created when
# the MCP tool was registered), so the gateway's own controls (scripts/block-tool.sh) still apply.
source "$(dirname "$0")/common.sh"
require FOUNDRY_ACCOUNT FOUNDRY_PROJECT MCP_GATEWAY_URL

TOOLBOX="${TOOLBOX_NAME:-guachinches}"
CONNECTION="${TOOLBOX_CONNECTION:-guachinches-mcp}"
TOOLS=(search_guachinches get_guachinche list_locations add_favorite remove_favorite list_favorites)
ENDPOINT="https://$FOUNDRY_ACCOUNT.services.ai.azure.com/api/projects/$FOUNDRY_PROJECT"
(( $# > 0 )) || { echo "Usage: $0 --create | <tool> [<tool> ...] | --none | --status" >&2; exit 1; }

TOKEN="$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)"
api() {  # api METHOD PATH [JSON]
  curl -sS --fail-with-body -X "$1" "$ENDPOINT$2?api-version=v1" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" ${3:+-d "$3"}
}

if [[ "$1" == --status ]]; then
  DEFAULT="$(api GET "/toolboxes/$TOOLBOX" | python3 -c 'import json,sys; print(json.load(sys.stdin)["default_version"])')"
  echo "default version: $DEFAULT"
  api GET "/toolboxes/$TOOLBOX/versions/$DEFAULT" | python3 -c '
import json, sys
tool = json.load(sys.stdin)["tools"][0]
print("allowed tools:  ", ", ".join(tool.get("allowed_tools") or ["(all)"]))'
  exit 0
fi

# Plain strings rather than arrays: macOS bash 3.2 treats empty arrays as unset under set -u.
BLOCKED=""
[[ "$1" == --create || "$1" == --none ]] || BLOCKED="$*"
for tool in $BLOCKED; do
  [[ " ${TOOLS[*]} " == *" $tool "* ]] || { echo "Unknown tool: $tool (known: ${TOOLS[*]})" >&2; exit 1; }
done
ALLOWED=""
for tool in "${TOOLS[@]}"; do [[ " $BLOCKED " == *" $tool "* ]] || ALLOWED="$ALLOWED $tool"; done
[[ -n "$ALLOWED" ]] || { echo "Blocking every tool isn't supported; block the agent instead." >&2; exit 1; }

BODY="$(python3 -c '
import json, sys
url, connection, blocked, allowed = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split()
print(json.dumps({
    "description": "Guachinches de Tenerife (MCP on AWS, via the Foundry AI Gateway). Blocked: " + (blocked or "none"),
    "tools": [{"type": "mcp", "server_label": "guachinches", "server_url": url, "require_approval": "never",
               "project_connection_id": connection, "allowed_tools": allowed}],
}))' "$MCP_GATEWAY_URL" "$CONNECTION" "$BLOCKED" "$ALLOWED")"
VERSION="$(api POST "/toolboxes/$TOOLBOX/versions" "$BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
api PATCH "/toolboxes/$TOOLBOX" "{\"default_version\": \"$VERSION\"}" > /dev/null

save_env TOOLBOX_URL "$ENDPOINT/toolboxes/$TOOLBOX/mcp?api-version=v1"
if [[ -n "$BLOCKED" ]]; then echo "Toolbox $TOOLBOX v$VERSION is now default; left out: $BLOCKED"
else echo "Toolbox $TOOLBOX v$VERSION is now default, with every tool."; fi
