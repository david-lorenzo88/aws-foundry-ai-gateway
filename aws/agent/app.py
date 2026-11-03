"""HTTP front door for the guachinche agent.

Foundry Control Plane registers this endpoint as a custom agent (protocol: HTTP) and publishes a
proxy URL on the AI Gateway (API Management). Clients call the proxy URL; APIM forwards to here.

Two checks protect the origin:
- x-agent-key: the agent's own API key. Foundry passes it through untouched, so clients send the
  same header to the proxy URL as they would to the origin.
- x-foundry-gateway (optional): a secret that only the AI Gateway adds (see scripts/lock-origin.sh).
  With it set, calling the Lambda URL directly fails, so blocking the agent in Foundry really
  cuts it off.
"""

import hmac
import os
import time
import uuid

import telemetry  # noqa: F401  (must run before Strands is imported)
from fastapi import FastAPI, Header, HTTPException, Request
from mangum import Mangum
from opentelemetry import propagate, trace
from pydantic import BaseModel, Field

from guachinche_agent import MODEL_ID, ToolTrace, agent_session, debug_info
from telemetry import AGENT_NAME, flush, tracer

AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
GATEWAY_SHARED_SECRET = os.environ.get("GATEWAY_SHARED_SECRET", "")

app = FastAPI(title=AGENT_NAME)


# Both ids end up in S3 keys and HTTP headers, so keep them to safe characters.
SAFE_ID = r"^[A-Za-z0-9_-]{1,64}$"


class InvokeRequest(BaseModel):
    message: str
    user_id: str = Field(default="demo", pattern=SAFE_ID)  # whose favourites and sessions
    session_id: str | None = Field(default=None, pattern=SAFE_ID)  # omit to start a new conversation


class InvokeResponse(BaseModel):
    agent: str
    model: str
    session_id: str
    output: str
    debug: dict  # tool calls, timings, tokens and routes, for the demo web


def _check(expected: str, provided: str | None, status: int, detail: str) -> None:
    if expected and not hmac.compare_digest(expected, provided or ""):
        raise HTTPException(status_code=status, detail=detail)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agent": AGENT_NAME, "cloud": "aws"}


@app.post("/invoke", response_model=InvokeResponse)
def invoke(
    body: InvokeRequest,
    request: Request,
    x_agent_key: str | None = Header(default=None),
    x_foundry_gateway: str | None = Header(default=None),
) -> InvokeResponse:
    _check(GATEWAY_SHARED_SECRET, x_foundry_gateway, 403, "Direct access is not allowed; call the agent through the Foundry AI Gateway.")
    _check(AGENT_API_KEY, x_agent_key, 401, "Missing or invalid x-agent-key.")

    # Continue the trace started by API Management (W3C traceparent), so the gateway request and the
    # agent's spans line up as one end-to-end transaction in Application Insights.
    parent = propagate.extract(dict(request.headers))
    try:
        with tracer.start_as_current_span("POST /invoke", context=parent, kind=trace.SpanKind.SERVER) as span:
            session_id = body.session_id or uuid.uuid4().hex
            span.set_attributes({"gen_ai.agent.id": AGENT_NAME, "gen_ai.conversation.id": session_id})
            tool_trace, agent, started = ToolTrace(), None, time.monotonic()
            try:
                with agent_session(body.user_id, session_id, tool_trace) as agent:
                    result = agent(body.message)
            except Exception as error:
                # Surface model or MCP errors (throttling, access, gateway) as a readable 503 instead of a bare
                # 500, with whatever the request did before failing.
                span.record_exception(error)
                span.set_status(trace.StatusCode.ERROR)
                debug = debug_info(agent, tool_trace) | {"duration_ms": round((time.monotonic() - started) * 1000)}
                raise HTTPException(
                    status_code=503,
                    detail={"error": f"Agent call failed: {type(error).__name__}: {error}", "session_id": session_id, "debug": debug},
                ) from error
            debug = debug_info(agent, tool_trace, result) | {"duration_ms": round((time.monotonic() - started) * 1000)}
            return InvokeResponse(agent=AGENT_NAME, model=MODEL_ID, session_id=session_id, output=str(result).strip(), debug=debug)
    finally:
        flush()


handler = Mangum(app, lifespan="off")
