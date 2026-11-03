"""Guachinche agent: helps find a guachinche in Tenerife for lunch, built with Strands Agents on Amazon Bedrock.

Its tools aren't in this code: they come from a Foundry Toolbox (TOOLBOX_URL), which publishes the
guachinches MCP server and calls it through the Foundry AI Gateway. Foundry decides which tools the agent
gets: a tool left out of the Toolbox's default version disappears for the agent. The agent has no key to
the MCP server, so it can't go around the Toolbox.
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager, nullcontext
from urllib.parse import urlparse

from botocore.config import Config as BotocoreConfig
from opentelemetry import propagate
from strands import Agent, ModelRetryStrategy
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry
from strands.models import BedrockModel
from strands.session import FileSessionManager, S3SessionManager
from strands.tools.mcp import MCPClient

from telemetry import AGENT_NAME, tracer

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-5-5")
# Bedrock quotas are per region; this lets the agent call a region other than the one it runs in.
BEDROCK_REGION = os.environ.get("BEDROCK_REGION") or None
# Conversation history per session lives in S3 (expires after a few days); local runs use files.
SESSIONS_BUCKET = os.environ.get("SESSIONS_BUCKET")
# Foundry Toolbox endpoint, and the Entra service principal the agent calls it with. Empty only while
# bootstrapping (before the Toolbox exists): the agent then runs without tools.
TOOLBOX_URL = os.environ.get("TOOLBOX_URL", "")
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")

SYSTEM_PROMPT = (
    "You help people find a guachinche in Tenerife for lunch. Guachinches are traditional wine houses, "
    "usually in the north, serving their own wine and home cooking for a few euros. "
    "Always use the tools: search by town, zone or region, dishes, features (terrace, views, kids, dogs...), "
    "wine, day and price; use list_locations to resolve vague places. Suggest two or three options with why "
    "they fit, and offer to save one to the user's favourites. Manage favourites only with the favourites tools. "
    "If a tool returns an error or is blocked, say so plainly and continue with what still works. "
    "Include each suggestion's Google Maps link. Dishes come from what reviews mention, so present them as "
    "what reviewers talk about rather than promising a dish. Answer in the user's language, briefly."
)

# Foundry's troubleshooting guide looks for a create_agent span carrying the agent id, so record
# one when the agent is defined (once per Lambda cold start).
with tracer.start_as_current_span(f"create_agent {AGENT_NAME}") as span:
    span.set_attributes(
        {
            "gen_ai.operation.name": "create_agent",
            "gen_ai.provider.name": "aws.bedrock",
            "gen_ai.agent.id": AGENT_NAME,
            "gen_ai.agent.name": AGENT_NAME,
            "gen_ai.request.model": MODEL_ID,
        }
    )
    # boto retries throttling 4 times by default, on top of Strands' own retry: cap it so errors show fast.
    _model = BedrockModel(
        model_id=MODEL_ID,
        region_name=BEDROCK_REGION,
        boto_client_config=BotocoreConfig(retries={"max_attempts": 2, "mode": "standard"}),
    )


# How each layer answers a blocked call: a Toolbox rejects tools left out of allowed_tools (it also hides
# them from tools/list), and the AI Gateway's optional tool guard returns an MCP error result.
BLOCKED_MARKERS = {"gateway": "blocked by an administrator", "toolbox": "not permitted by allowed_tools"}


def short_name(tool: str) -> str:
    """A Toolbox namespaces MCP tools as <server_label>___<tool>; show the MCP server's own name."""
    return tool.rsplit("___", 1)[-1]


class ToolTrace(HookProvider):
    """Records every tool call of a request (input, result, time) for the debug view of the demo web."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(AfterToolCallEvent, self._after)

    def _after(self, event: AfterToolCallEvent) -> None:
        result = event.result or {}
        text = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
        blocked_by = next((layer for layer, marker in BLOCKED_MARKERS.items() if marker in text), None)
        self.calls.append(
            {
                "name": short_name(event.tool_use.get("name", "")),
                "input": event.tool_use.get("input"),
                "status": result.get("status", "error"),
                "blocked": blocked_by is not None,
                "blocked_by": blocked_by,
                "duration_ms": round((getattr(event, "duration", 0) or 0) * 1000),
                "output": text if len(text) <= 1500 else text[:1500] + "…",
            }
        )


def debug_info(agent: Agent | None, trace: ToolTrace, result=None) -> dict:
    """What the demo web shows next to each answer: tools, timings, tokens and where calls went."""
    info = {
        "model": MODEL_ID,
        "bedrock_region": BEDROCK_REGION or os.environ.get("AWS_REGION"),
        "mcp_route": "toolbox" if TOOLBOX_URL else "none",
        "mcp_host": urlparse(TOOLBOX_URL).hostname if TOOLBOX_URL else None,
        "tools_available": sorted(short_name(t) for t in agent.tool_names) if agent else [],
        "history_messages": len(agent.messages) if agent else 0,
        "tool_calls": trace.calls,
    }
    if result is not None:
        usage = result.metrics.accumulated_usage
        info["usage"] = {"input_tokens": usage.get("inputTokens", 0), "output_tokens": usage.get("outputTokens", 0)}
        info["cycles"] = result.metrics.cycle_count
        info["stop_reason"] = str(result.stop_reason)
    return json.loads(json.dumps(info, default=str))


_token: dict = {"value": "", "expires": 0.0}
_token_lock = threading.Lock()


def _entra_token() -> str:
    """Client-credentials token for Foundry (scope ai.azure.com), cached until shortly before it expires."""
    with _token_lock:
        if time.time() < _token["expires"] - 300:
            return _token["value"]
        form = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": AZURE_CLIENT_ID,
                "client_secret": AZURE_CLIENT_SECRET,
                "scope": "https://ai.azure.com/.default",
            }
        ).encode()
        url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
        with urllib.request.urlopen(urllib.request.Request(url, data=form), timeout=15) as response:
            body = json.load(response)
        _token.update(value=body["access_token"], expires=time.time() + int(body["expires_in"]))
        return _token["value"]


def _mcp_client(user_id: str):
    if not TOOLBOX_URL:
        return nullcontext(None)
    # The Toolbox continues the W3C trace from our headers (it replaces the _meta where Strands puts it), so
    # inject the current trace context: gateway, agent, Toolbox and MCP then share one trace. It drops our
    # other headers but propagates baggage: the user goes there, as OpenTelemetry's enduser.id.
    headers: dict[str, str] = {}
    propagate.inject(headers)
    headers |= {"Authorization": f"Bearer {_entra_token()}", "baggage": f"enduser.id={user_id}"}
    return MCPClient(url=TOOLBOX_URL, headers=headers)


def _session_manager(user_id: str, session_id: str):
    # Prefix by user so one user can't resume another user's conversation by guessing its id.
    if SESSIONS_BUCKET:
        return S3SessionManager(session_id=session_id, bucket=SESSIONS_BUCKET, prefix=f"{user_id}/")
    return FileSessionManager(session_id=session_id, storage_dir=os.path.join("/tmp/guachinche-sessions", user_id))


INTERRUPTED_TURN = "(The previous request failed before I could answer it.)"


@contextmanager
def agent_session(user_id: str, session_id: str, trace: ToolTrace):
    """One Toolbox connection and one Agent per request, resuming the session's earlier messages."""
    with _mcp_client(user_id) as mcp:
        session_manager = _session_manager(user_id, session_id)
        agent = Agent(
            hooks=[trace],
            name=AGENT_NAME,
            agent_id=AGENT_NAME,
            model=_model,
            system_prompt=SYSTEM_PROMPT,
            tools=mcp.list_tools_sync() if mcp else [],
            trace_attributes={
                "gen_ai.agent.id": AGENT_NAME,
                "gen_ai.conversation.id": session_id,
                "cloud.provider": "aws",
                "enduser.id": user_id,
            },
            session_manager=session_manager,
            # Keep long chats inside the context window; drops the oldest turns as a unit.
            conversation_manager=SlidingWindowConversationManager(window_size=30),
            callback_handler=None,
            # The default backoff (up to ~4 minutes) outlives the Lambda and gateway timeouts; fail fast instead.
            retry_strategy=ModelRetryStrategy(max_attempts=2, initial_delay=1, max_delay=4),
        )
        # A turn that failed (throttling, a blocked gateway...) leaves the user's message unanswered, and
        # Bedrock rejects two user messages in a row. Close the gap, in memory and in the stored session.
        if agent.messages and agent.messages[-1]["role"] == "user":
            message = {"role": "assistant", "content": [{"text": INTERRUPTED_TURN}]}
            agent.messages.append(message)
            session_manager.append_message(message, agent)
        yield agent
