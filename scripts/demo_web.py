#!/usr/bin/env python3
"""Local demo web for the guachinche agent: chat, tool-call traces and the AI Gateway controls.

    python3 scripts/demo_web.py          # then open http://127.0.0.1:8080

Runs on the presenter's laptop and only listens on 127.0.0.1: the agent and MCP keys stay in .env and never
reach the browser, and blocking tools runs scripts/block-tool.sh with your az login. Standard library only
(certifi is used if installed, for macOS python.org builds without root certificates).

API used by web/index.html:
    GET  /api/config                 names and hosts shown in the header
    GET  /api/status                 is the agent reachable through the gateway, or blocked in Foundry?
    POST /api/chat  {message, user_id, session_id, route}   the agent, via gateway or direct
    POST /api/mcp   {tool, args, user_id, route}            one MCP tool, no agent or model involved
                                     (route: gateway, direct, or toolbox with your az login)
    GET  /api/blocked                tools blocked at the gateway
    POST /api/blocked {tools: [...]} block exactly these tools ([] unblocks all)
    GET  /api/toolbox                tools left out of the Toolbox's default version
    POST /api/toolbox {tools: [...]} publish a Toolbox version without these tools ([] = all tools)
"""

import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "web" / "index.html"
PORT = int(os.environ.get("DEMO_WEB_PORT", "8080"))

try:
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            # .env is written by save_env with printf %q; the values we need have no shell escapes.
            env[key.strip()] = value.strip().strip("'\"").replace("\\ ", " ")
    return env


ENV = load_env()
TOOLS = ["search_guachinches", "get_guachinche", "list_locations", "add_favorite", "remove_favorite", "list_favorites"]
TOOLBOX_URL = ENV.get("TOOLBOX_URL", "")
_az_token = {"value": "", "expires": 0.0}


def az_token() -> str:
    """Your az login's token for Foundry, to call the Toolbox the way the agent does (it uses its own identity)."""
    if time.time() > _az_token["expires"]:
        out = subprocess.run(
            ["az", "account", "get-access-token", "--resource", "https://ai.azure.com", "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=60, check=True,
        )
        _az_token.update(value=out.stdout.strip(), expires=time.time() + 30 * 60)
    return _az_token["value"]


def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: int = 130) -> tuple[int, object, int]:
    """Call url; return (status, parsed JSON or text, elapsed ms). Never raises for HTTP errors."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **headers})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
            status, raw = response.status, response.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read().decode(errors="replace")
    except Exception as error:  # network errors, timeouts
        return 0, {"error": f"{type(error).__name__}: {error}"}, round((time.monotonic() - started) * 1000)
    elapsed = round((time.monotonic() - started) * 1000)
    try:
        return status, json.loads(raw), elapsed
    except json.JSONDecodeError:
        return status, raw.strip(), elapsed


def agent_url(route: str) -> str:
    return (ENV["AGENT_GATEWAY_URL"] if route == "gateway" else ENV["AGENT_ORIGIN_URL"]).rstrip("/")


def mcp_url(route: str) -> str:
    return ENV["MCP_GATEWAY_URL"] if route == "gateway" else ENV["MCP_ORIGIN_URL"]


def chat(payload: dict) -> dict:
    route = payload.get("route", "gateway")
    body = {"message": payload["message"], "user_id": payload.get("user_id") or "demo"}
    if payload.get("session_id"):
        body["session_id"] = payload["session_id"]
    status, result, elapsed = http("POST", agent_url(route) + "/invoke", {"x-agent-key": ENV["AGENT_API_KEY"]}, body)
    return {"route": route, "url": agent_url(route) + "/invoke", "status": status, "elapsed_ms": elapsed, "body": result}


def sse_json(result):
    """The Toolbox may answer in SSE framing; pull the JSON-RPC message out of its data lines."""
    if isinstance(result, str) and "data:" in result:
        data = "\n".join(line[5:].strip() for line in result.splitlines() if line.startswith("data:"))
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            pass
    return result


def toolbox_call(tool: str, args: dict, user_id: str) -> tuple[int, object, int]:
    """initialize + tools/call on the Toolbox endpoint. Tools are namespaced, and the user rides in W3C baggage."""
    headers = {"Accept": "application/json, text/event-stream", "Authorization": f"Bearer {az_token()}", "baggage": f"enduser.id={user_id}"}
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "demo-web", "version": "1"}}}
    request = urllib.request.Request(TOOLBOX_URL, data=json.dumps(init).encode(), method="POST", headers={"Content-Type": "application/json", **headers})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=40, context=SSL_CONTEXT) as response:
        session = response.headers.get("Mcp-Session-Id")
        response.read()
    if session:
        headers["Mcp-Session-Id"] = session
    http("POST", TOOLBOX_URL, headers, {"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=40)
    rpc = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
           "params": {"name": f"guachinches___{tool}", "arguments": args}}
    status, result, _ = http("POST", TOOLBOX_URL, headers, rpc, timeout=40)
    return status, sse_json(result), round((time.monotonic() - started) * 1000)


def mcp_call(payload: dict) -> dict:
    route, tool = payload.get("route", "gateway"), payload["tool"]
    args, user_id = payload.get("args") or {}, payload.get("user_id") or "demo"
    if route == "toolbox":
        status, result, elapsed = toolbox_call(tool, args, user_id)
        url = TOOLBOX_URL
    else:
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
            "x-mcp-key": ENV["MCP_API_KEY"],
            "x-user-id": user_id,
        }
        status, result, elapsed = http("POST", mcp_url(route), headers, rpc, timeout=40)
        url = mcp_url(route)
    call = {"route": route, "url": url, "status": status, "elapsed_ms": elapsed, "tool": tool, "args": args, "blocked_by": None}
    if isinstance(result, dict) and isinstance(result.get("result"), dict):
        res = result["result"]
        text = " ".join(c.get("text", "") for c in res.get("content", []) if isinstance(c, dict))
        blocked = "blocked by an administrator" in text
        call |= {"is_error": bool(res.get("isError")), "blocked": blocked, "blocked_by": "gateway" if blocked else None, "output": text}
    elif isinstance(result, dict) and isinstance(result.get("error"), dict):
        # The Toolbox rejects a tool left out of allowed_tools with a JSON-RPC error (PermissionDenied).
        message = result["error"].get("message", "")
        blocked = "not permitted by allowed_tools" in message
        call |= {"is_error": True, "blocked": blocked, "blocked_by": "toolbox" if blocked else None, "output": message}
    else:
        call |= {"is_error": True, "blocked": False, "output": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)}
    return call


def status() -> dict:
    code, result, elapsed = http("GET", agent_url("gateway") + "/health", {}, timeout=15)
    blocked = code == 403 and isinstance(result, dict) and result.get("error") == "AgentBlocked"
    state = "active" if code == 200 else "blocked" if blocked else "error"
    return {"agent": state, "status": code, "elapsed_ms": elapsed, "detail": result}


def blocked_tools() -> list[str]:
    out = subprocess.run(
        ["az", "apim", "nv", "show", "-g", ENV["APIM_RG"], "--service-name", ENV["APIM_NAME"],
         "--named-value-id", "blocked-mcp-tools", "--query", "value", "-o", "tsv"],
        capture_output=True, text=True, timeout=60,
    )
    value = out.stdout.strip()
    return [] if out.returncode or value in ("", "-") else [t.strip() for t in value.split(",") if t.strip()]


def toolbox_blocked() -> list[str]:
    out = subprocess.run([str(ROOT / "scripts" / "toolbox.sh"), "--status"], capture_output=True, text=True, timeout=60, cwd=ROOT)
    for line in out.stdout.splitlines():
        if line.startswith("allowed tools:"):
            allowed = [t.strip() for t in line.split(":", 1)[1].split(",")]
            return [] if "(all)" in allowed else [t for t in TOOLS if t not in allowed]
    return []


def set_toolbox(tools: list[str]) -> dict:
    unknown = [t for t in tools if t not in TOOLS]
    if unknown:
        return {"error": f"Unknown tools: {unknown}"}
    out = subprocess.run([str(ROOT / "scripts" / "toolbox.sh"), *(tools or ["--none"])], capture_output=True, text=True, timeout=180, cwd=ROOT)
    if out.returncode:
        return {"error": (out.stderr or out.stdout).strip()[-800:]}
    version = re.search(r" v(\d+) is now default", out.stdout)
    return {"blocked": tools, "version": version.group(1) if version else None}


def set_blocked(tools: list[str]) -> dict:
    unknown = [t for t in tools if t not in TOOLS]
    if unknown:
        return {"error": f"Unknown tools: {unknown}"}
    args = tools or ["--none"]
    out = subprocess.run([str(ROOT / "scripts" / "block-tool.sh"), *args], capture_output=True, text=True, timeout=180, cwd=ROOT)
    if out.returncode:
        return {"error": (out.stderr or out.stdout).strip()[-800:]}
    return {"blocked": tools}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # The page polls /api/status every 5 s; logging it would bury the interesting requests.
        if self.path not in ("/api/status", "/favicon.ico"):
            sys.stderr.write("  %s %s\n" % (self.command, self.path))

    def _send(self, code: int, payload, content_type: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/config":
            self._send(200, {
                "agent_gateway": ENV.get("AGENT_GATEWAY_URL"),
                "agent_origin": ENV.get("AGENT_ORIGIN_URL"),
                "mcp_gateway": ENV.get("MCP_GATEWAY_URL"),
                "mcp_origin": ENV.get("MCP_ORIGIN_URL"),
                "apim": ENV.get("APIM_NAME"),
                "toolbox": TOOLBOX_URL or None,
                "tools": TOOLS,
            })
        elif self.path == "/api/status":
            self._send(200, status())
        elif self.path == "/api/blocked":
            self._send(200, {"blocked": blocked_tools()})
        elif self.path == "/api/toolbox":
            self._send(200, {"blocked": toolbox_blocked() if TOOLBOX_URL else [], "enabled": bool(TOOLBOX_URL)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            payload = self._json()
            if self.path == "/api/chat":
                self._send(200, chat(payload))
            elif self.path == "/api/mcp":
                self._send(200, mcp_call(payload))
            elif self.path == "/api/blocked":
                result = set_blocked(payload.get("tools") or [])
                self._send(400 if "error" in result else 200, result)
            elif self.path == "/api/toolbox":
                result = set_toolbox(payload.get("tools") or [])
                self._send(400 if "error" in result else 200, result)
            else:
                self._send(404, {"error": "not found"})
        except Exception as error:
            self._send(500, {"error": f"{type(error).__name__}: {error}"})


if __name__ == "__main__":
    missing = [k for k in ("AGENT_GATEWAY_URL", "AGENT_ORIGIN_URL", "AGENT_API_KEY", "MCP_GATEWAY_URL", "MCP_ORIGIN_URL", "MCP_API_KEY", "APIM_RG", "APIM_NAME") if not ENV.get(k)]
    if missing:
        raise SystemExit(f"Missing in .env: {', '.join(missing)}")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as error:
        raise SystemExit(f"Port {PORT} is busy ({error.strerror}): another copy is probably running. Stop it, or use DEMO_WEB_PORT=8081.") from None
    print(f"Demo web on http://127.0.0.1:{PORT}  (Ctrl-C to stop)")
    server.serve_forever()
