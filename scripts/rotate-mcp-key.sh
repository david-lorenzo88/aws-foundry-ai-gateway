#!/usr/bin/env bash
# Rotate the MCP server's key, so that only Foundry holds it.
#
#   scripts/rotate-mcp-key.sh
#
# Generates a new MCP_API_KEY, deploys it to the MCP Lambda, and writes it into the Foundry project connection
# the Toolbox uses (TOOLBOX_CONNECTION, default guachinches-mcp). The agent has no copy: its tools come only
# from the Toolbox. The key also stays in .env for scripts/call-mcp.sh and the demo web's direct tests.
# Tool calls fail for the few seconds between the deploy and the connection update.
source "$(dirname "$0")/common.sh"
require APIM_RG FOUNDRY_ACCOUNT FOUNDRY_PROJECT

CONNECTION="${TOOLBOX_CONNECTION:-guachinches-mcp}"
URL="https://management.azure.com$(az cognitiveservices account show -g "$APIM_RG" -n "$FOUNDRY_ACCOUNT" --query id -o tsv)/projects/$FOUNDRY_PROJECT/connections/$CONNECTION?api-version=2025-06-01"

save_env MCP_API_KEY "$(openssl rand -hex 24)"
"$ROOT/scripts/deploy-aws.sh" > /dev/null

# Keep the connection as Foundry created it; only the credential changes.
az rest --method get --url "$URL" --query properties -o json | python3 -c '
import json, os, sys
props = json.load(sys.stdin)
keep = ("authType", "category", "target", "metadata", "isSharedToAll", "sharedUserList", "useWorkspaceManagedIdentity", "peRequirement")
body = {"properties": {k: props[k] for k in keep if props.get(k) is not None}}
body["properties"]["credentials"] = {"keys": {"x-mcp-key": os.environ["MCP_API_KEY"]}}
json.dump(body, open(sys.argv[1], "w"))
' "$ROOT/.env.tmp"
az rest --method put --url "$URL" --body @"$ROOT/.env.tmp" -o none
rm -f "$ROOT/.env.tmp"
echo "New MCP key deployed to the MCP Lambda and saved in the Foundry connection $CONNECTION."
