# Runbook: guachinche agent on AWS, governed from Foundry

All scripts read and write settings in `.env` at the repo root.

## A. Setup (do this days before the session)

### 0. Azure: Foundry resource, project, App Insights

```bash
az login
```

```bash
az group create -n rg-aws-foundry-gateway -l swedencentral
```

```bash
az deployment group create -g rg-aws-foundry-gateway --template-file azure/main.bicep
```

This creates the Foundry resource, the project and an App Insights resource connected to the project
([azure/main.bicep](../azure/main.bicep)).

### 1. Foundry: add the AI Gateway

In [Foundry (new)](https://ai.azure.com): **Manage > AI Gateway > Add AI Gateway** on the Foundry resource
(create a new API Management instance), then **Add project to gateway**. Save the APIM details:

```bash
echo "APIM_RG=rg-aws-foundry-gateway" >> .env
```

```bash
echo "APIM_NAME=<apim-name>" >> .env
```

> App Insights must be connected **before** you register agents. Foundry doesn't pick up an App Insights
> connection added later; you'd have to unregister and register again.

### 2. App Insights connection string

```bash
scripts/get-appinsights.sh rg-aws-foundry-gateway <appinsights-name>
```

### 2b. Guachinche data (Google Places)

The MCP server reads `aws/mcp/data/guachinches.json`, a snapshot of real guachinches. **It isn't in the repo**
(Google's terms don't allow redistributing Places content). Without it the server falls back to
`aws/mcp/data/guachinches.sample.json`, 40 **invented** guachinches (`scripts/make_sample_data.py`), which is enough
to try everything. For real places, build the snapshot before deploying. You need a Google
Cloud project with billing and **Places API (New)** enabled, plus an API key restricted to it, saved as
`GOOGLE_MAPS_API_KEY` in `.env`:

```bash
set -a && source .env && set +a && python3 scripts/fetch_guachinches.py
```

Roughly 90 Text Search calls (the cheaper tier) plus one Place Details call (Enterprise + Atmosphere tier)
per guachinche-like name, about 170. Raw responses are cached in `data/raw/`; re-run the classification for
free with `--classify-only`. On macOS with python.org Python, install `certifi` (or run "Install
Certificates") if you get SSL certificate errors.

### 3. AWS: deploy the agent and the MCP server

```bash
aws login
```

```bash
AWS_REGION=eu-north-1 scripts/deploy-aws.sh
```

Save `AWS_REGION` in `.env`. Optional: `BEDROCK_MODEL_ID` (default `global.anthropic.claude-sonnet-4-6`)
and `BEDROCK_REGION` (where your Bedrock quota is, e.g. `us-east-1`; the Lambdas stay where they are).
The script generates `AGENT_API_KEY` and `MCP_API_KEY` and prints two origin URLs: `AGENT_ORIGIN_URL` and
`MCP_ORIGIN_URL`.

Check the MCP server, which doesn't need Bedrock:

```bash
scripts/call-mcp.sh direct tools/list
```

```bash
scripts/call-mcp.sh direct search_guachinches '{"location": "Acentejo", "features": ["vistas"]}'
```

Check the agent:

```bash
scripts/call-agent.sh direct "Busco un guachinche en Tacoronte"
```

**New AWS accounts** hit three Bedrock gates. Clear them early:

1. *"Model use case details have not been submitted"*: submit the Anthropic use-case form once (Bedrock
   console > Model catalog > any Claude model).
2. *"Your account is currently being verified"*: wait (usually under 2 hours).
3. *"Too many tokens per day"* on every model: the account's daily token quota is 0. Open an AWS Support
   case (Service limit increase > Bedrock) for your model and region.

Bedrock quotas are per region. If the Lambdas' region has none, set `BEDROCK_REGION` in `.env` and
redeploy.

### 4. Foundry: register the agent

**Operate > Overview > Register asset**:

| Field | Value |
|---|---|
| Agent URL | `AGENT_ORIGIN_URL` |
| Protocol | HTTP |
| OpenTelemetry agent ID | `guachinche-agent` |
| Admin portal URL | the Lambda console URL of `guachinche-agent` (optional) |
| Project | your project |
| Agent name | `guachinche-agent` |
| Description | Finds guachinches in Tenerife. Runs on AWS Lambda + Bedrock (Strands); tools via MCP |

**Operate > Assets**, Source = Custom, select the agent's radio button, copy its **Agent URL**:

```bash
echo "AGENT_GATEWAY_URL=https://<apim>.azure-api.net/guachinche-agent/" >> .env
```

### 5. Foundry: register the MCP server as a governed tool

**Build > Tools > Custom > Model Context Protocol**:

| Field | Value |
|---|---|
| Name | `guachinches-tenerife` |
| Remote MCP server endpoint | `MCP_ORIGIN_URL` (ends in `/mcp`) |
| Authentication | Key-based: header `x-mcp-key`, value `MCP_API_KEY` from `.env` (stored in the project connection `guachinches-mcp`; the agent never gets it) |

Check that the tool's endpoint now shows the **AI Gateway URL** (`https://<apim>.azure-api.net/...`), not
the Lambda URL. Save it, plus the APIM API id that Foundry created:

```bash
echo "MCP_GATEWAY_URL=<gateway MCP endpoint, exactly as Foundry shows it>" >> .env
```

Use the URL **as shown** (`https://<apim>.azure-api.net/tool-<project>-...`), without adding `/mcp`.
The API's backend already ends in `/mcp`, so an extra `/mcp` becomes `/mcp/mcp` and returns 404. The APIM
API is of type `mcp` and doesn't appear in `az apim api list`; list it with the REST API
(`api-version=2025-03-01-preview`) or find it in the Azure portal under APIs > MCP servers.

```bash
az rest --method get --url "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$APIM_RG/providers/Microsoft.ApiManagement/service/$APIM_NAME/apis?api-version=2025-03-01-preview" --query "value[].{id:name,type:properties.type,backend:properties.serviceUrl}" -o table
```

```bash
echo "MCP_API_ID=<id of the type-mcp API whose backend is the MCP Lambda>" >> .env
```

```bash
scripts/call-mcp.sh gateway tools/list
```

The agent doesn't call this endpoint itself: it gets its tools from the Foundry Toolbox (step 7), which calls
it through the gateway.

### 6. Lock both origins

```bash
scripts/lock-origin.sh <agent-api-id> <mcp-api-id>
```

It adds the `x-foundry-gateway` header policy to both APIs (keeping Foundry's policy) and redeploys with
`ORIGIN_LOCKED=true`. Then:

```bash
scripts/call-mcp.sh direct tools/list      # 403: direct access is not allowed
```

```bash
scripts/call-mcp.sh gateway tools/list     # 200
```

```bash
scripts/call-agent.sh direct "hola"        # 403
```

Test **Block/Unblock** once in Foundry. Block adds a `return-response` 403 in front of the existing policy,
and Unblock removes it. We've checked that both keep the lock header.

### 7. Foundry Toolbox: the agent's only source of tools

A [Toolbox](https://learn.microsoft.com/azure/foundry/agents/concepts/toolbox-overview) (preview) publishes
the MCP tools as a versioned catalogue on one Foundry MCP endpoint. The agent gets its tools **only** from it,
so the Toolbox's default version decides what the agent can use: a tool left out disappears from `tools/list`,
and a direct call to it gets `PermissionDenied`. The Toolbox calls the MCP server through the AI Gateway with the
`guachinches-mcp` project connection from step 5, so the origin lock still applies.

```bash
scripts/toolbox.sh --create        # version 1 with every tool; saves TOOLBOX_URL in .env
```

```bash
scripts/toolbox-identity.sh        # app registration + secret for the Lambda, "Foundry User" on the project
```

```bash
scripts/rotate-mcp-key.sh          # new MCP key, only in the MCP Lambda and the Foundry connection; redeploys
```

Needs `FOUNDRY_ACCOUNT` and `FOUNDRY_PROJECT` in `.env`. `toolbox-identity.sh` and `rotate-mcp-key.sh` create
credentials in your tenant, so run them yourself; re-running them rotates the secrets. After this the agent has
an Entra identity for the Toolbox and **no MCP key**: it can't go around Foundry.

Block and unblock tools by publishing Toolbox versions:

```bash
scripts/toolbox.sh add_favorite remove_favorite   # new version without them, made default
```

```bash
scripts/toolbox.sh --status
```

```bash
scripts/toolbox.sh --none                         # every tool again
```

Or in the **Foundry portal**: **Build > Tools > Toolboxes > guachinches**, select the `guachinches-mcp` entry,
and in **Allowed guachinches-mcp tools** list the tools to keep (comma-separated; empty = all). **Save** creates a
new version. Also possible from VS Code (Foundry Toolkit), `azd ai toolbox` or the Foundry SDKs.

Details of the Toolbox worth knowing:

- It names tools `guachinches___<tool>` (the agent and the web show the plain name).
- It doesn't forward the client's headers and replaces the tool call's `_meta`, but it propagates the W3C
  `baggage` header. The agent sends the user there as `enduser.id`, and the MCP server reads it when
  `x-user-id` is missing.
- It continues the W3C trace from the request's `traceparent` **header** (not `_meta`), so the agent injects
  it when connecting. The Toolbox reports its own spans to the project's App Insights as `agentsv2`, and one
  trace covers AI Gateway → agent → Toolbox → MCP.
- It adds the project's **Application Insights connection string** (ingestion key included) to the `baggage`
  it sends to the MCP server. Fine for our own server; think twice before putting third-party MCP servers in a
  Toolbox of a project with App Insights connected.

### 7b. Optional: tool guard on the AI Gateway

A second, independent layer in API Management: the call reaches the gateway and gets "blocked by an
administrator" back. It's configured in the Azure portal (APIM), not in Foundry.

```bash
scripts/block-tool.sh --none            # installs the policy with nothing blocked
```

```bash
scripts/block-tool.sh add_favorite
```

```bash
scripts/call-mcp.sh gateway add_favorite '{"guachinche_id": "tegueste-el-portezuelo"}'   # TOOL ERROR: blocked
```

```bash
scripts/block-tool.sh --none
```

Unblocking takes a few seconds to apply in API Management; wait before asking again.

### 8. Conversations

`POST /invoke` takes `{"message", "user_id", "session_id"}`. Send the same `session_id` to continue a
conversation, or leave it out to start a new one (the response returns the new id). The history is stored
per user and session in the stack's S3 bucket (Strands `S3SessionManager`, expiring after 7 days), and a
sliding window keeps long chats inside the model's context. If a turn fails (quota, a blocked gateway),
the next turn in that session closes it with a short note, so the history keeps alternating user/assistant.

### 9. Demo web (what the audience sees)

```bash
uv run --no-project --with certifi python scripts/demo_web.py
```

Open http://127.0.0.1:8080. It runs only on your laptop: the keys stay in `.env`, and the tool switches use
your `az login`. On screen:

- **Chat** with the agent, which keeps the conversation (session) until "Nueva conversación".
- **Traza de la última llamada**: the route the request took (Cliente → AI Gateway → Agente → Toolbox → AI Gateway → MCP, and Bedrock),
  with the step where it stopped in red, plus HTTP status, time, tokens and every tool call (OK / ERROR /
  BLOQUEADA EN EL GATEWAY), expandable to see input and output.
- **Control de las tools en Foundry**: two switches per MCP tool. **Toolbox · Foundry** publishes a Toolbox
  version without the tool (`scripts/toolbox.sh`): the main control. **AI Gateway · Azure** blocks the call
  (`scripts/block-tool.sh`), the optional second layer. Both take a second or two, with no deploy. The trace
  shows Toolbox → AI Gateway → MCP, how many tools the agent could see, and which ones the Toolbox hid.
- **Probar sin agente**: calls one MCP tool through the Toolbox (with your `az login`), without the agent or the
  model, or straight to AWS with "Directo a AWS". Handy if Bedrock fails.
- **Vía AI Gateway / Directo a AWS**: send the next request straight to the Lambda to show the 403.
- The **Agente** chip at the top checks the agent through the gateway every 5 s and turns red when it's
  blocked in Foundry.

### 10. Warm-up

10–15 minutes before the session, make 5–10 agent calls through the gateway (cold starts, and data for
Traces and run counts).

## B. Live demo (about 8 minutes)

This is **demo 2** of the session (AI Gateway, David). Demo 1 (Hosted Agent, Jorge) already cuts a tool from
a Toolbox, so here the tool cut is quick ("the same Toolbox, but the agent is in AWS") and the time goes to
what only the AI Gateway gives: an agent outside Azure in the inventory, one trace across clouds, Block, and
the origin lock. Deck order: Preguntar → Trazar → Cortar una tool → Bloquear → ¿Y directo? → Desbloquear.

1. **"Lunch in Tenerife? A guachinche."** This agent isn't ours, or rather it isn't in Azure: Lambda +
   Strands + Bedrock, and its tools are an MCP server also on Lambda. No Microsoft code besides an OTel exporter.
2. **Inventory.** **Operate > Assets**, Source = Custom: `guachinche-agent` next to the session's .NET agent.
   **Tools**: the `guachinches-tenerife` MCP server, with a gateway URL.
3. **Ask through the gateway.** One conversation (`SESSION_ID`), so follow-ups like "el primero" work. The
   agent keeps each session's history in S3 for a week.
   ```bash
   export SESSION_ID=comida USER_ID=demo
   ```
   ```bash
   scripts/call-agent.sh gateway "Busco un guachinche en el norte con terraza y que admita perros, para comer conejo el domingo"
   ```
   ```bash
   scripts/call-agent.sh gateway "Guárdame el primero en favoritos con la nota: ir con la familia"
   ```
4. **Traces.** Open the latest call in the agent's **Traces**: gateway request, `invoke_agent
   guachinche-agent`, `chat` (Bedrock, tokens), `execute_tool search_guachinches` / `add_favorite`.
   Same view as the .NET agent's.
5. **Cut a tool live, from Foundry.** "The favourites tool writes to a database. Let's leave the agent
   read-only." In the Foundry portal (**Build > Tools > Toolboxes > guachinches**), remove `add_favorite` and
   `remove_favorite` from the allowed tools and **Save**; or switch them off in the web's **Toolbox** column, or:
   ```bash
   scripts/toolbox.sh add_favorite remove_favorite
   ```
   ```bash
   scripts/call-agent.sh gateway "Añade también el segundo a mis favoritos"
   ```
   The agent still searches, but it no longer has the favourites tools: the trace shows which ones the Toolbox
   hid. Nothing changed in AWS, and the agent can't reach the MCP server any other way (it has no key).
   *(Optional second layer: block the call at the AI Gateway instead, `scripts/block-tool.sh add_favorite`. The
   agent sees the tool, calls it and gets "blocked by an administrator".)*
6. **Fix it without touching code.**
   ```bash
   scripts/toolbox.sh --none
   ```
   Ask again → saved.
7. **"It's misbehaving, stop it."** Foundry: **Update status > Block**:
   ```bash
   scripts/call-agent.sh gateway "¿Dónde como hoy?"
   ```
   → `403 AgentBlocked`. The Lambda is still running in AWS: Foundry closed the only door. It didn't switch
   anything off in AWS, and Bedrock isn't touched.
8. **"Can't I just call AWS directly?"**
   ```bash
   scripts/call-agent.sh direct "¿Dónde como hoy?"
   ```
   → 403, without the gateway's header the agent refuses to answer. Same for the MCP server.
9. **Unblock**, ask again → 200.
10. **Limits, honestly.** Foundry governs what crosses the gateway: inbound to the agent, and agent → tools.
    The agent's calls to Bedrock stay inside AWS. Foundry can't start/stop the compute, and it only sees what
    the agent reports through OTel.

## C. Troubleshooting

| Symptom | Check |
|---|---|
| Agent 503 "Too many tokens per day" | Bedrock quota (section A.3). Try the MCP part on its own with `scripts/call-mcp.sh`. |
| Agent 503 mentioning MCP / connection | Is the MCP API in APIM blocked or missing the lock header? Try `scripts/call-mcp.sh gateway tools/list`, then "Probar sin agente" (via the Toolbox) in the web. |
| No traces in Foundry | App Insights connected before registration; same connection string in `.env`; wait 2–5 minutes. |
| 401 from the agent or MCP | `x-agent-key` / `x-mcp-key` missing or wrong; the gateway passes the origin's auth through. |
| 403 "Direct access is not allowed" through the gateway | The lock policy is missing on that API: look for `x-foundry-gateway` in its inbound policy, and check the named value matches `GATEWAY_SHARED_SECRET`. |
| Blocked tool still works | `MCP_API_ID` is the API the agent actually calls? Is the `mcp-tool-guard` policy in its inbound? `scripts/block-tool.sh --status`. |
| First call slow | Lambda cold starts (agent + MCP). Warm up before the demo. |
| Agent answers but has no tools | It was deployed without `TOOLBOX_URL` / identity: run `scripts/toolbox.sh --create` and `scripts/toolbox-identity.sh`, then `scripts/deploy-aws.sh`. |
| Toolbox: tools/list empty or tool calls 401 | The `guachinches-mcp` connection's key doesn't match the MCP Lambda's: `scripts/rotate-mcp-key.sh`. |
| Agent 503 with 401/403 from the Toolbox | The service principal's role can take minutes to apply; check it has "Foundry User" on the project, and that the secret in `.env` is current. |
| Toolbox call: "No tool config matches tool name" | Toolbox tool names are `guachinches___<tool>`. |

Clean up after the session (and unregister the agent and the tool in Foundry):

```bash
sam delete --stack-name guachinche-agent --region "$AWS_REGION"
```

```bash
az group delete -n rg-aws-foundry-gateway
```
