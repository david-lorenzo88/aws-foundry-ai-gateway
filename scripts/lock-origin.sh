#!/usr/bin/env bash
# Make the Foundry AI Gateway the only way into the agent and the MCP server.
#
# 1. Stores a secret as a secret named value in the APIM instance behind the AI Gateway.
# 2. Adds a set-header policy to each API that Foundry created (the agent's and the MCP tool's),
#    keeping whatever policy Foundry already put there.
# 3. Sets ORIGIN_LOCKED=true and redeploys, so both Lambdas reject requests without that header.
#
#   scripts/lock-origin.sh <api-id> [<api-id> ...]      (az apim api list -g $APIM_RG -n $APIM_NAME -o table)
#
# Needs APIM_RG and APIM_NAME in .env. Run it after registering the agent and the MCP tool in Foundry,
# and after the agent calls MCP through the gateway (MCP_GATEWAY_URL). Test Block/Unblock afterwards.
source "$(dirname "$0")/common.sh"
require APIM_RG APIM_NAME
(( $# > 0 )) || { echo "Usage: $0 <api-id> [<api-id> ...]" >&2; az apim api list -g "$APIM_RG" -n "$APIM_NAME" --query "[].{id:name,path:path,backend:serviceUrl}" -o table 2>/dev/null; exit 1; }

NV_NAME="${STACK_NAME}-gateway-secret"
[[ -n "${GATEWAY_SHARED_SECRET:-}" ]] || save_env GATEWAY_SHARED_SECRET "$(openssl rand -hex 24)"

az apim nv create -g "$APIM_RG" --service-name "$APIM_NAME" --named-value-id "$NV_NAME" \
  --display-name "$NV_NAME" --value "$GATEWAY_SHARED_SECRET" --secret true -o none 2>/dev/null \
  || az apim nv update -g "$APIM_RG" --service-name "$APIM_NAME" --named-value-id "$NV_NAME" --value "$GATEWAY_SHARED_SECRET" -o none

HEADER="<set-header name=\"x-foundry-gateway\" exists-action=\"override\"><value>{{$NV_NAME}}</value></set-header>"
for API_ID in "$@"; do
  "$ROOT/scripts/apim-policy.sh" "$API_ID" add x-foundry-gateway "$HEADER"
  echo "Gateway now adds x-foundry-gateway on API $API_ID."
done

save_env ORIGIN_LOCKED true
"$ROOT/scripts/deploy-aws.sh"
echo "Origin locked. Direct calls to the Lambda URLs now return 403; calls through the gateway work."
