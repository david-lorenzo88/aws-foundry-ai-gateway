"""MCP server: find guachinches in Tenerife and keep a list of favourites.

Streamable HTTP, stateless, JSON responses, served from AWS Lambda behind a Function URL. In the demo
it's published through the Foundry AI Gateway, so the agent reaches it via API Management, where
Foundry/APIM can block the whole server or individual tools.

Headers:
- x-mcp-key: the server's own API key (checked here; passed through by the gateway).
- x-foundry-gateway: secret added by the gateway when the origin is locked (scripts/lock-origin.sh).
- x-user-id: whose favourites list to use. Defaults to "demo". Through a Foundry Toolbox, which doesn't
  forward headers, the user arrives as enduser.id in the W3C baggage header instead.
"""

import hmac
import json
import os
import urllib.parse

from mangum import Mangum
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from opentelemetry import trace

import favorites
import guachinches
import telemetry

MCP_API_KEY = os.environ.get("MCP_API_KEY", "")
GATEWAY_SHARED_SECRET = os.environ.get("GATEWAY_SHARED_SECRET", "")

mcp = MCPServer(
    name="guachinches-tenerife",
    instructions=(
        "Find guachinches (traditional Tenerife wine houses serving home cooking) by town, zone or region, "
        "dishes, features, wine, day and price, and manage the user's favourites list."
    ),
)


def _user_id(ctx: Context) -> str:
    request = getattr(ctx.request_context, "request", None)
    headers = getattr(request, "headers", None) or {}
    return headers.get("x-user-id") or _baggage(headers.get("baggage", "")).get("enduser.id") or "demo"


def _baggage(header: str) -> dict:
    """W3C baggage. A Foundry Toolbox drops the client's headers and _meta but propagates its baggage, so
    through a Toolbox the agent sends the user as enduser.id there."""
    items = (item.split(";", 1)[0].split("=", 1) for item in header.split(",") if "=" in item)
    return {key.strip(): urllib.parse.unquote(value.strip()) for key, value in items}


def _traced(tool: str, **attributes):
    span = telemetry.tracer.start_as_current_span(f"execute_tool {tool}", kind=trace.SpanKind.SERVER)
    return span, {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool, **attributes}


@mcp.tool()
def search_guachinches(
    query: str | None = None,
    location: str | None = None,
    dishes: list[str] | None = None,
    features: list[str] | None = None,
    wine: str | None = None,
    open_on: str | None = None,
    max_price: int | None = None,
    min_rating: float | None = None,
    sort_by: str = "rating",
    limit: int = 5,
) -> dict:
    """Search real guachinches in Tenerife (Google Places snapshot). All filters are optional and combine with AND.

    Features come from Google's attributes; dishes and wines are what reviews mention, so a missing dish
    doesn't mean they don't serve it. Prefer few filters, then widen if there are no results.

    Args:
        query: Free text matched against name, dishes, features, place and description (e.g. "teide views carne fiesta").
        location: Town, locality, zone or region, e.g. "La Orotava", "Tejina", "Acentejo", "Valle de Güímar", "Isla Baja", "Norte", "Sur", "Área Metropolitana". Accents optional.
        dishes: Dishes reviews mention, in Spanish, e.g. ["carne fiesta", "conejo", "garbanzas", "costillas"]. Partial names match.
        features: Required features. Ids: terrace, views, parking, vegetarian_options, kid_friendly, dog_friendly, accepts_cards, reservations, live_music, wheelchair_accessible, groups, sells_wine. Spanish words like "terraza", "vistas", "perros" also work.
        wine: "tinto", "blanco" or "rosado" (or red/white/rose).
        open_on: Day of the week the place must be open, in Spanish or English (e.g. "domingo", "sunday").
        max_price: Maximum average price per person in euros.
        min_rating: Minimum rating, 0-5.
        sort_by: "rating" (default; ties broken by number of ratings), "popularity" (number of ratings), "price" or "name".
        limit: Number of results to return, 1-20.
    """
    span, attrs = _traced("search_guachinches", **{"guachinche.location": location or "", "guachinche.query": query or ""})
    with span as s:
        s.set_attributes(attrs)
        result = guachinches.search(query, location, dishes, features, wine, open_on, max_price, min_rating, sort_by, limit)
        s.set_attribute("guachinche.matches", result["total_matches"])
        return result


@mcp.tool()
def get_guachinche(guachinche_id: str) -> dict:
    """Full details of one guachinche (address, opening hours, Google Maps link, summary...) by its id.

    Args:
        guachinche_id: The id returned by search_guachinches (a Google place id).
    """
    span, attrs = _traced("get_guachinche", **{"guachinche.id": guachinche_id})
    with span as s:
        s.set_attributes(attrs)
        g = guachinches.BY_ID.get(guachinche_id)
        if not g:
            return {"error": f"No guachinche with id {guachinche_id}. Use search_guachinches to find ids."}
        return {**guachinches.detail(g), "data_note": guachinches.DATA_NOTE}


@mcp.tool()
def list_locations() -> dict:
    """List the regions, zones, towns and localities with guachinches, plus the known features, dishes and wines.

    Use it to resolve vague places ("the north", "near La Laguna") or to see what filters exist.
    """
    span, attrs = _traced("list_locations")
    with span as s:
        s.set_attributes(attrs)
        return guachinches.locations()


@mcp.tool()
def add_favorite(guachinche_id: str, ctx: Context, note: str | None = None) -> dict:
    """Add a guachinche to the user's favourites list.

    Args:
        guachinche_id: The id returned by search_guachinches.
        note: Optional note, e.g. "go on a Sunday, order the conejo".
    """
    user = _user_id(ctx)
    span, attrs = _traced("add_favorite", **{"guachinche.id": guachinche_id, "enduser.id": user})
    with span as s:
        s.set_attributes(attrs)
        if guachinche_id not in guachinches.BY_ID:
            return {"error": f"No guachinche with id {guachinche_id}. Use search_guachinches to find ids."}
        favorites.store.put(user, favorites.new_item(guachinche_id, note))
        return {"added": guachinche_id, "name": guachinches.BY_ID[guachinche_id]["name"], "favorites_count": len(favorites.store.list(user))}


@mcp.tool()
def remove_favorite(guachinche_id: str, ctx: Context) -> dict:
    """Remove a guachinche from the user's favourites list.

    Args:
        guachinche_id: The id of the favourite to remove.
    """
    user = _user_id(ctx)
    span, attrs = _traced("remove_favorite", **{"guachinche.id": guachinche_id, "enduser.id": user})
    with span as s:
        s.set_attributes(attrs)
        removed = favorites.store.delete(user, guachinche_id)
        return {"removed": guachinche_id if removed else None, "was_in_favorites": removed}


@mcp.tool()
def list_favorites(ctx: Context) -> dict:
    """List the user's favourite guachinches, with their notes."""
    user = _user_id(ctx)
    span, attrs = _traced("list_favorites", **{"enduser.id": user})
    with span as s:
        s.set_attributes(attrs)
        items = sorted(favorites.store.list(user), key=lambda i: i["added_at"])
        enriched = []
        for item in items:
            g = guachinches.BY_ID.get(item["guachinche_id"])
            enriched.append({**item, **(guachinches.summary(g) if g else {"name": "(no longer in catalogue)"})})
        return {"user": user, "favorites": enriched, "data_note": guachinches.DATA_NOTE}


def _http_app():
    # Lambda URLs arrive with their own Host header, and the gateway adds the security checks below.
    return mcp.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


class Guard:
    """Reject calls that skip the gateway (when locked) or lack the MCP key, before MCP sees them."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            for expected, header, status, message in (
                (GATEWAY_SHARED_SECRET, "x-foundry-gateway", 403, "Direct access is not allowed; use the Foundry AI Gateway."),
                (MCP_API_KEY, "x-mcp-key", 401, "Missing or invalid x-mcp-key."),
            ):
                if expected and not hmac.compare_digest(expected, headers.get(header, "")):
                    body = json.dumps({"error": message}).encode()
                    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return
        await self.app(scope, receive, send)


def handler(event, context):
    # The MCP session manager can only start once per app instance, and Mangum runs the app's
    # lifespan on every invocation, so build a fresh HTTP app each time (tools are registered once).
    try:
        return Mangum(Guard(_http_app()), lifespan="on")(event, context)
    finally:
        telemetry.flush()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(Guard(_http_app()), host="127.0.0.1", port=int(os.environ.get("PORT", "8765")))
