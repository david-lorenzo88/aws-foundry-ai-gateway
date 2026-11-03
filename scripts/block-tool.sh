#!/usr/bin/env bash
# Block or unblock individual MCP tools at the Foundry AI Gateway, without touching the MCP server or the agent.
#
#   scripts/block-tool.sh add_favorite remove_favorite   # block these tools (replaces the blocked list)
#   scripts/block-tool.sh --none                         # unblock everything
#   scripts/block-tool.sh --status                       # show what's blocked
#
# How: a policy on the MCP API (reinstalled on every run) answers tools/call for a blocked tool with
# an MCP error result (HTTP 200, isError: true, resultType "complete" as MCP 2026-07-28 requires)
# instead of forwarding it. That's the same behaviour
# Agent 365's tool-level blocking uses, so the agent tells the user rather than retrying. The list of
# blocked tools lives in the named value "blocked-mcp-tools", so blocking is one value change.
#
# Needs APIM_RG, APIM_NAME and MCP_API_ID in .env.
source "$(dirname "$0")/common.sh"
require APIM_RG APIM_NAME MCP_API_ID

NV="blocked-mcp-tools"
(( $# > 0 )) || { echo "Usage: $0 <tool> [<tool> ...] | --none | --status" >&2; exit 1; }

if [[ "$1" == --status ]]; then
  az apim nv show -g "$APIM_RG" --service-name "$APIM_NAME" --named-value-id "$NV" --query value -o tsv 2>/dev/null || echo "(not installed)"
  exit 0
fi

# Named values can't be empty, so "-" means nothing is blocked.
if [[ "$1" == --none ]]; then VALUE="-"; else VALUE="$(IFS=,; echo "$*")"; fi

az apim nv create -g "$APIM_RG" --service-name "$APIM_NAME" --named-value-id "$NV" \
  --display-name "$NV" --value "$VALUE" -o none 2>/dev/null \
  || az apim nv update -g "$APIM_RG" --service-name "$APIM_NAME" --named-value-id "$NV" --value "$VALUE" -o none

read -r -d '' GUARD <<'XML' || true
<choose id="mcp-tool-guard">
  <when condition="@{
      var body = context.Request.Body?.As<JObject>(preserveContent: true);
      if (body == null || (string)body["method"] != "tools/call") { return false; }
      var blocked = "{{blocked-mcp-tools}}".Split(',').Select(t => t.Trim());
      return blocked.Contains((string)body["params"]?["name"]);
    }">
    <return-response>
      <set-status code="200" reason="OK" />
      <set-header name="Content-Type" exists-action="override"><value>application/json</value></set-header>
      <set-body>@{
          var body = context.Request.Body.As<JObject>(preserveContent: true);
          var tool = (string)body["params"]?["name"];
          var text = "The tool '" + tool + "' has been blocked by an administrator in the Foundry AI Gateway.";
          return new JObject(
            new JProperty("jsonrpc", "2.0"),
            new JProperty("id", body["id"]),
            new JProperty("result", new JObject(
              new JProperty("resultType", "complete"),
              new JProperty("isError", true),
              new JProperty("content", new JArray(new JObject(new JProperty("type", "text"), new JProperty("text", text))))
            ))
          ).ToString();
        }</set-body>
    </return-response>
  </when>
</choose>
XML

# Replace rather than keep: an older copy of the guard may already be installed.
"$ROOT/scripts/apim-policy.sh" "$MCP_API_ID" remove mcp-tool-guard
"$ROOT/scripts/apim-policy.sh" "$MCP_API_ID" add mcp-tool-guard "$GUARD"
if [[ "$VALUE" == "-" ]]; then echo "No MCP tools blocked."; else echo "Blocked at the gateway: $VALUE"; fi
