"""Catalogue of guachinches in Tenerife and the search over it.

The catalogue is a snapshot built from Google Places by scripts/fetch_guachinches.py (data/guachinches.json).
Features come from Places attributes; dishes, wines and views are inferred from summaries and reviews,
so they can be incomplete. Without that file (it isn't in the repo), the server uses invented sample data
from scripts/make_sample_data.py (data/guachinches.sample.json).
"""

import json
from pathlib import Path

from classify import MUNICIPALITY_ZONE, ZONE_REGION, norm

_DIR = Path(__file__).parent / "data"
_FILE = _DIR / "guachinches.json"
_DATA = json.loads((_FILE if _FILE.exists() else _DIR / "guachinches.sample.json").read_text())
GUACHINCHES: list[dict] = _DATA["guachinches"]
BY_ID = {g["id"]: g for g in GUACHINCHES}

if _DATA.get("sample"):
    DATA_NOTE = (
        "Invented sample data: these guachinches don't exist (names, addresses, ratings and dishes are made up; "
        "towns and zones are real). Say so if the user asks for real recommendations."
    )
else:
    DATA_NOTE = (
        f"Real places from Google Places (snapshot {_DATA['fetched_at'][:10]}). Features come from Google's "
        "attributes; dishes and wines are inferred from reviews and may be incomplete. Place data © Google."
    )

# Feature ids and the words people use for them (Spanish and English).
FEATURES = {
    "terrace": ["terraza", "terrace", "al aire libre", "outdoor"],
    "views": ["vistas", "views", "mirador", "vista al mar", "sea view", "vista al teide"],
    "parking": ["aparcamiento", "parking", "aparcar"],
    "vegetarian_options": ["vegetariano", "vegetarian", "vegano", "vegan"],
    "kid_friendly": ["niños", "ninos", "kids", "familia", "family", "infantil"],
    "dog_friendly": ["perros", "perro", "mascotas", "dogs", "pet friendly", "pets"],
    "accepts_cards": ["tarjeta", "cards", "card", "datáfono", "datafono"],
    "reservations": ["reserva", "reservas", "reservar", "booking", "reservations"],
    "live_music": ["música", "musica", "parranda", "live music", "timple"],
    "wheelchair_accessible": ["accesible", "silla de ruedas", "wheelchair", "accessible"],
    "groups": ["grupos", "groups", "comidas grandes", "celebraciones"],
    "sells_wine": ["vende vino", "garrafa", "venta de vino", "buy wine"],
}

DAY_ALIASES = {
    "monday": "lunes", "tuesday": "martes", "wednesday": "miércoles", "thursday": "jueves",
    "friday": "viernes", "saturday": "sábado", "sunday": "domingo",
}


def _feature_ids(terms: list[str]) -> set[str]:
    wanted = set()
    for term in terms:
        t = norm(term).replace("-", " ").replace("_", " ")
        for fid, aliases in FEATURES.items():
            if t == norm(fid).replace("_", " ") or any(t == norm(a) or norm(a) in t for a in aliases):
                wanted.add(fid)
                break
        else:
            wanted.add(f"unknown:{term}")
    return wanted


def _location_text(g: dict) -> str:
    return norm(" ".join([g["municipality"], g["locality"], g["zone"], g["region"], g["address"]]))


def _full_text(g: dict) -> str:
    feature_words = " ".join(" ".join([f, *FEATURES.get(f, [])]) for f in g["features"])
    return " ".join(
        [norm(" ".join([g["name"], g["description"], " ".join(g["dishes"]), " ".join(g["wines"]), feature_words])), _location_text(g)]
    )


def summary(g: dict) -> dict:
    return {
        "id": g["id"],
        "name": g["name"],
        "location": f"{g['locality']}, {g['municipality']} ({g['zone']})" if g["locality"] != g["municipality"] else f"{g['municipality']} ({g['zone']})",
        "rating": g["rating"],
        "rating_count": g["rating_count"],
        "price_per_person_eur": g["price_per_person"],
        "open_days": g["open_days"] or "unknown",
        "features": g["features"],
        "dishes_mentioned": g["dishes"],
        "wines_mentioned": g["wines"],
        "google_maps": g["google_maps_uri"],
    }


def detail(g: dict) -> dict:
    return {k: v for k, v in g.items() if k not in ("latitude", "longitude")} | {
        "coordinates": [g["latitude"], g["longitude"]],
    }


def search(
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
    results = list(GUACHINCHES)

    if location:
        loc = norm(location)
        results = [g for g in results if loc in _location_text(g)]
    if dishes:
        for dish in dishes:
            d = norm(dish)
            results = [g for g in results if any(d in norm(x) for x in g["dishes"])]
    unknown_features: list[str] = []
    if features:
        wanted = _feature_ids(features)
        unknown_features = [w.split(":", 1)[1] for w in wanted if w.startswith("unknown:")]
        known = {w for w in wanted if not w.startswith("unknown:")}
        results = [g for g in results if known <= set(g["features"])]
    if wine:
        w = norm(wine)
        w = {"red": "tinto", "white": "blanco", "rose": "rosado"}.get(w, w)
        results = [g for g in results if w in g["wines"]]
    if open_on:
        day = norm(DAY_ALIASES.get(norm(open_on), open_on))
        results = [g for g in results if day in [norm(x) for x in g["open_days"]]]
    if max_price is not None:
        results = [g for g in results if g["price_per_person"] is not None and g["price_per_person"] <= max_price]
    if min_rating is not None:
        results = [g for g in results if (g["rating"] or 0) >= min_rating]

    # Free text: every word must appear somewhere (name, dishes, features, place, description).
    if query:
        words = [w for w in norm(query).split() if len(w) > 2]
        results = [g for g in results if all(w in _full_text(g) for w in words)]

    keys = {
        # Rating first, then how many people rated it, so a 4.8 from 900 reviews beats a 5.0 from 3.
        "rating": lambda g: (-(g["rating"] or 0), -g["rating_count"]),
        "popularity": lambda g: -g["rating_count"],
        "price": lambda g: (g["price_per_person"] is None, g["price_per_person"] or 0),
        "name": lambda g: norm(g["name"]),
    }
    results.sort(key=keys.get(sort_by, keys["rating"]))

    response = {
        "total_matches": len(results),
        "results": [summary(g) for g in results[: max(1, min(limit, 20))]],
        "data_note": DATA_NOTE,
    }
    if unknown_features:
        response["ignored_features"] = unknown_features
        response["known_features"] = list(FEATURES)
    return response


def locations() -> dict:
    """Regions > zones > municipalities > localities present in the catalogue, with counts."""
    tree: dict = {}
    for g in GUACHINCHES:
        munis = tree.setdefault(g["region"], {}).setdefault(g["zone"], {})
        entry = munis.setdefault(g["municipality"], {"count": 0, "localities": []})
        entry["count"] += 1
        if g["locality"] != g["municipality"] and g["locality"] not in entry["localities"]:
            entry["localities"].append(g["locality"])
    dishes = sorted({d for g in GUACHINCHES for d in g["dishes"]})
    return {
        "total": len(GUACHINCHES),
        "regions": tree,
        "features": list(FEATURES),
        "dishes": dishes,
        "wines": ["tinto", "blanco", "rosado"],
        "data_note": DATA_NOTE,
    }


__all__ = ["BY_ID", "DATA_NOTE", "GUACHINCHES", "MUNICIPALITY_ZONE", "ZONE_REGION", "detail", "locations", "search", "summary"]
