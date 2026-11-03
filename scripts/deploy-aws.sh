#!/usr/bin/env bash
# Build and deploy the agent and the guachinches MCP server to AWS Lambda with SAM.
# Prints the two origin URLs to register in Foundry.
#
#   AWS_REGION=eu-north-1 scripts/deploy-aws.sh
#
# Settings read from .env: BEDROCK_MODEL_ID, BEDROCK_REGION, ORIGIN_LOCKED=true (enforce the gateway secret,
# set by scripts/lock-origin.sh), and TOOLBOX_URL + TOOLBOX_* (the agent's tools come from the Foundry Toolbox;
# without them it deploys with no tools, for the first bootstrap).
source "$(dirname "$0")/common.sh"
require AWS_REGION APPINSIGHTS_CONNECTION_STRING

[[ -n "${AGENT_API_KEY:-}" ]] || save_env AGENT_API_KEY "$(openssl rand -hex 24)"
[[ -n "${MCP_API_KEY:-}" ]] || save_env MCP_API_KEY "$(openssl rand -hex 24)"

PARAMS=(
  "AgentName=$AGENT_NAME"
  "BedrockModelId=${BEDROCK_MODEL_ID:-global.anthropic.claude-sonnet-4-6}"
  "AgentApiKey=$AGENT_API_KEY"
  "McpApiKey=$MCP_API_KEY"
  "AppInsightsConnectionString=$APPINSIGHTS_CONNECTION_STRING"
)
# SAM rejects empty values; the template defaults these to "" (origin open, Bedrock in the Lambda's
# region, no Toolbox yet).
[[ "${ORIGIN_LOCKED:-}" == true && -n "${GATEWAY_SHARED_SECRET:-}" ]] && PARAMS+=("GatewaySharedSecret=$GATEWAY_SHARED_SECRET")
[[ -n "${BEDROCK_REGION:-}" ]] && PARAMS+=("BedrockRegion=$BEDROCK_REGION")
# Foundry Toolbox: its endpoint and the service principal the agent calls it with (scripts/toolbox*.sh).
if [[ -n "${TOOLBOX_URL:-}" && -n "${TOOLBOX_CLIENT_SECRET:-}" ]]; then
  PARAMS+=("ToolboxUrl=$TOOLBOX_URL" "AzureTenantId=$TOOLBOX_TENANT_ID" "AzureClientId=$TOOLBOX_CLIENT_ID" "AzureClientSecret=$TOOLBOX_CLIENT_SECRET")
fi

cd "$ROOT/aws"
# Add --use-container if a dependency has no Linux arm64 wheel.
sam build
sam deploy \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides "${PARAMS[@]}"

output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
save_env AGENT_ORIGIN_URL "$(output AgentUrl)"
save_env MCP_ORIGIN_URL "$(output McpUrl)"
echo
echo "Agent origin URL (Foundry > Register asset > Agent URL): $AGENT_ORIGIN_URL"
echo "MCP origin URL   (Foundry > Build > Tools > Custom > MCP): $MCP_ORIGIN_URL"
echo "Agent tools: ${TOOLBOX_URL:-none yet (create the Toolbox, then redeploy)}"
