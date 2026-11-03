"""Turn Google Places results into the guachinche catalogue: what counts as a guachinche, where it is
(town -> zone -> region), and what it offers (features, dishes, wines).

Used offline by scripts/fetch_guachinches.py. Review texts are only scanned for keywords; they aren't stored.
"""

import math
import re
import unicodedata
from datetime import datetime, timezone

# The 31 municipalities of Tenerife, their zone (comarca) and region.
MUNICIPALITY_ZONE = {
    "La Orotava": "Valle de La Orotava",
    "Puerto de la Cruz": "Valle de La Orotava",
    "Los Realejos": "Valle de La Orotava",
    "Tacoronte": "Acentejo",
    "El Sauzal": "Acentejo",
    "La Matanza de Acentejo": "Acentejo",
    "La Victoria de Acentejo": "Acentejo",
    "Santa Úrsula": "Acentejo",
    "Icod de los Vinos": "Icod",
    "La Guancha": "Icod",
    "San Juan de la Rambla": "Icod",
    "Garachico": "Isla Baja",
    "El Tanque": "Isla Baja",
    "Los Silos": "Isla Baja",
    "Buenavista del Norte": "Isla Baja",
    "San Cristóbal de La Laguna": "Área Metropolitana",
    "Santa Cruz de Tenerife": "Área Metropolitana",
    "Tegueste": "Área Metropolitana",
    "El Rosario": "Área Metropolitana",
    "Güímar": "Valle de Güímar",
    "Arafo": "Valle de Güímar",
    "Candelaria": "Valle de Güímar",
    "Arona": "Sur",
    "Adeje": "Suroeste",
    "Guía de Isora": "Suroeste",
    "Santiago del Teide": "Suroeste",
    "San Miguel de Abona": "Abona",
    "Granadilla de Abona": "Abona",
    "Vilaflor de Chasna": "Abona",
    "Arico": "Abona",
    "Fasnia": "Abona",
}
ZONE_REGION = {
    "Valle de La Orotava": "Norte",
    "Acentejo": "Norte",
    "Icod": "Norte",
    "Isla Baja": "Norte",
    "Área Metropolitana": "Área Metropolitana",
    "Valle de Güímar": "Sur",
    "Sur": "Sur",
    "Suroeste": "Sur",
    "Abona": "Sur",
}
MUNICIPALITIES = list(MUNICIPALITY_ZONE)


def norm(text: str) -> str:
    """Lowercase and strip accents so 'güímar', 'Guimar' and 'GÜÍMAR' all match."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


# Short and alternative names Google uses for some municipalities.
_ALIASES = {
    norm(m): m for m in MUNICIPALITIES
} | {
    "la laguna": "San Cristóbal de La Laguna",
    "san cristobal de la laguna": "San Cristóbal de La Laguna",
    "santa cruz": "Santa Cruz de Tenerife",
    "vilaflor": "Vilaflor de Chasna",
    "la matanza": "La Matanza de Acentejo",
    "la victoria": "La Victoria de Acentejo",
    "buenavista": "Buenavista del Norte",
    "granadilla": "Granadilla de Abona",
    "san miguel": "San Miguel de Abona",
    "icod": "Icod de los Vinos",
    "guia de isora": "Guía de Isora",
}

# Towns (entidades de población) Google gives as "locality" instead of the municipality they belong to.
_TOWNS = {
    norm(town): municipality
    for municipality, towns in {
        "San Cristóbal de La Laguna": ["Tejina", "Valle de Guerra", "Bajamar", "Punta del Hidalgo", "Las Mercedes",
                                       "Guamasa", "La Cuesta", "Taco", "Los Baldíos", "Los Baldios", "Las Montañas"],
        "Santa Cruz de Tenerife": ["San Andrés", "Valle Seco", "Taganana", "Igueste de San Andrés", "María Jiménez"],
        "El Rosario": ["La Esperanza", "Tabaiba", "Radazul", "Llano del Moro", "Machado"],
        "Tegueste": ["El Portezuelo", "Pedro Álvarez", "El Socorro"],
        "La Orotava": ["La Perdoma", "La Florida", "Barroso", "Benijos", "Aguamansa", "Pinolere", "La Luz", "Camino de Chasna"],
        "Los Realejos": ["Icod el Alto", "Palo Blanco", "La Cruz Santa", "Tigaiga", "San Agustín"],
        "Tacoronte": ["Agua García", "El Cantillo", "Guayonje", "Juan Fernández"],
        "El Sauzal": ["Ravelo"],
        "La Matanza de Acentejo": ["San Antonio"],
        "Santa Úrsula": ["La Corujera", "La Quinta", "Cuesta de la Villa"],
        "Los Silos": ["Aregume", "Tierra del Trigo"],
        "Arona": ["Buzanada", "Valle de San Lorenzo", "La Escalona", "Los Cristianos", "Cabo Blanco", "Las Galletas"],
        "San Miguel de Abona": ["Las Chafiras", "Aldea Blanca", "Guargacho"],
        "Granadilla de Abona": ["Charco del Pino", "San Isidro", "El Médano", "Chimiche"],
        "Güímar": ["El Escobonal", "El Puertito"],
        "Candelaria": ["Araya", "Igueste de Candelaria", "Las Caletillas"],
        "Adeje": ["Costa Adeje", "Armeñime", "Tijoco"],
        "Guía de Isora": ["Chío", "Tejina de Isora", "Playa San Juan"],
        "Santiago del Teide": ["Tamaimo", "Puerto de Santiago", "Arguayo"],
    }.items()
    for town in towns
}
# Postal code prefixes by municipality (longest prefix wins). Coarse but reliable: Spanish postal codes
# follow municipal boundaries. Codes not listed fall through to the next signal.
_POSTAL = {
    "380": "Santa Cruz de Tenerife", "381": "Santa Cruz de Tenerife",
    "38109": "El Rosario", "38190": "El Rosario", "38290": "El Rosario",
    "382": "San Cristóbal de La Laguna", "38280": "Tegueste", "38292": "Tegueste",
    "3830": "La Orotava", "3831": "La Orotava", "3832": "San Cristóbal de La Laguna", "3833": "San Cristóbal de La Laguna",
    "3834": "Tacoronte", "3835": "Tacoronte", "3836": "El Sauzal", "3837": "La Matanza de Acentejo",
    "3838": "La Victoria de Acentejo", "3839": "Santa Úrsula",
    "3840": "Puerto de la Cruz", "3841": "Los Realejos", "3842": "San Juan de la Rambla", "3843": "Icod de los Vinos",
    "3844": "La Guancha", "3845": "Garachico", "3847": "Los Silos", "3848": "Buenavista del Norte",
    "3850": "Güímar", "3852": "Candelaria", "3853": "Candelaria", "3855": "Arafo", "3857": "Fasnia", "3858": "Arico",
    "3860": "Granadilla de Abona", "3861": "Granadilla de Abona", "38613": "Vilaflor de Chasna",
    "38620": "San Miguel de Abona", "38639": "San Miguel de Abona", "38626": "Arona", "38627": "Arona",
    "3864": "Arona", "3865": "Arona", "3867": "Adeje", "3868": "Guía de Isora", "3869": "Santiago del Teide",
}
# Google ends Tenerife addresses with the PROVINCE, "Santa Cruz de Tenerife", which must not be read as
# the municipality of the same name.
_PROVINCE_SUFFIX = re.compile(r",\s*santa cruz de tenerife\s*(,\s*(espana|spain))?\s*$")

# Feature id -> Places attribute check.
_ATTRIBUTE_FEATURES = {
    "terrace": lambda p: p.get("outdoorSeating") is True,
    "dog_friendly": lambda p: p.get("allowsDogs") is True,
    "kid_friendly": lambda p: p.get("goodForChildren") is True,
    "groups": lambda p: p.get("goodForGroups") is True,
    "live_music": lambda p: p.get("liveMusic") is True,
    "reservations": lambda p: p.get("reservable") is True,
    "vegetarian_options": lambda p: p.get("servesVegetarianFood") is True,
    "parking": lambda p: any(v is True for v in (p.get("parkingOptions") or {}).values()),
    "accepts_cards": lambda p: (p.get("paymentOptions") or {}).get("acceptsCreditCards") is True,
    "wheelchair_accessible": lambda p: (p.get("accessibilityOptions") or {}).get("wheelchairAccessibleEntrance") is True,
}
# Features only reviews can tell us.
_TEXT_FEATURES = {
    "views": [r"\bvistas?\b", r"\bmirador\b", r"\bviews?\b", r"vista al (mar|teide|valle)"],
    "sells_wine": [r"venden? (el )?vino", r"\bgarrafa", r"vino para llevar"],
}

# Canonical dish -> patterns (accent-insensitive, matched on normalised text).
DISHES = {
    "carne fiesta": [r"carne (de )?fiesta"],
    "costillas con papas y piña": [r"costillas?( con papas)?( y pina)?", r"costilla con pina"],
    "conejo en salmorejo": [r"conejo"],
    "garbanzas": [r"garbanzas?", r"garbanzo compuesto"],
    "potaje": [r"potaje"],
    "escaldón de gofio": [r"escaldon", r"gofio"],
    "queso asado con mojo": [r"queso (asado|a la plancha|frito|tierno|de cabra)", r"queso asado"],
    "papas arrugadas con mojo": [r"papas (arrugadas|sancochadas|negras|bonitas)", r"\bmojo"],
    "chuletas de cerdo": [r"chuletas?", r"chuleton"],
    "carne de cochino negro": [r"cochino negro"],
    "carne de cabra": [r"carne de cabra", r"\bcabra\b", r"cabrito"],
    # Not "vieja" (a local fish): it also matches "ropa vieja".
    "pescado": [r"\bcherne\b", r"\bpescado", r"\bsama\b"],
    "pulpo": [r"\bpulpo"],
    "ropa vieja": [r"ropa vieja"],
    "croquetas": [r"croquetas?"],
    "huevos con papas": [r"huevos (rotos|fritos|con papas)"],
    "bistec": [r"\bbistecs?\b", r"\bfilete"],
    "pollo": [r"\bpollo\b"],
}
WINES = {
    "tinto": [r"\btinto\b", r"vino negro", r"\bred wine\b"],
    "blanco": [r"\bblanco\b", r"\bwhite wine\b"],
    "rosado": [r"\brosado\b", r"\brose wine\b"],
}

DAYS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]  # Places: 0 = Sunday
PRICE_LEVEL_EUR = {
    "PRICE_LEVEL_INEXPENSIVE": 12,
    "PRICE_LEVEL_MODERATE": 22,
    "PRICE_LEVEL_EXPENSIVE": 35,
    "PRICE_LEVEL_VERY_EXPENSIVE": 55,
}


def _name(place: dict) -> str:
    return (place.get("displayName") or {}).get("text", "")


def _components(place: dict) -> list[dict]:
    return place.get("addressComponents") or []


def _postal_municipality(place: dict) -> str | None:
    code = next((c.get("longText", "") for c in _components(place) if "postal_code" in c.get("types", [])), "")
    for length in (5, 4, 3):
        if code[:length] in _POSTAL:
            return _POSTAL[code[:length]]
    return None


def municipality_of(place: dict) -> str | None:
    """Best guess at the municipality: the locality when it names one (or a known town), then the postal
    code, then the address without the province. None when it can't tell; build_catalogue then uses the
    nearest placed guachinche.

    A locality of "Santa Cruz de Tenerife" is often the province, not the city, so it only counts when the
    postal code doesn't say otherwise."""
    santa_cruz = False
    for component in _components(place):
        if "locality" in component.get("types", []):
            for value in (component.get("longText", ""), component.get("shortText", "")):
                key = norm(value)
                if key in _ALIASES:
                    if _ALIASES[key] == "Santa Cruz de Tenerife":
                        santa_cruz = True
                        break
                    return _ALIASES[key]
                if key in _TOWNS:
                    return _TOWNS[key]
    if postal := _postal_municipality(place):
        return postal
    if santa_cruz:
        return "Santa Cruz de Tenerife"
    address = _PROVINCE_SUFFIX.sub("", norm(place.get("formattedAddress", "")))
    # Longest names first so "san cristobal de la laguna" wins over "la laguna" fragments.
    for name, municipality in sorted((_ALIASES | _TOWNS).items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", address):
            return municipality
    return None


def _distance_km(a: dict, b: dict) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def nearest_municipality(place: dict, placed: list[tuple[dict, str]], max_km: float = 3.0) -> str | None:
    """Municipality of the closest place whose municipality we know, if it's near enough."""
    here = place.get("location")
    if not here:
        return None
    best = min(((_distance_km(here, loc), m) for loc, m in placed), default=None)
    return best[1] if best and best[0] <= max_km else None


def locality_of(place: dict, municipality: str) -> str:
    """The town within the municipality, if Google gives one (never the island, the province or another municipality)."""
    for kind in ("sublocality", "sublocality_level_1", "neighborhood", "locality"):
        for component in _components(place):
            value = component.get("longText", "")
            if kind in component.get("types", []) and norm(value) != "tenerife" and norm(value) not in _ALIASES:
                return value
    return municipality


# Names guachinches go by: most say "guachinche", others "bodegón", "bodega", "venta", "lagar", "parral"...
# The search for "guachinche en <municipio>" also returns plain restaurants and bars; those aren't worth a
# Place Details call. Lookalike names are fetched and then kept only if reviews call them a guachinche.
CANDIDATE_NAME = re.compile(r"guachinch|bodeg|lagar|\bventa\b|\bparral\b|\bparra\b|\bvina\b|\bvinedo|\bgarrafa")


def is_candidate(place: dict) -> bool:
    """Cheap pre-filter on Text Search results, before paying for Place Details."""
    # The search is already restricted to the island, so the municipality is worked out later.
    if place.get("businessStatus") == "CLOSED_PERMANENTLY":
        return False
    return bool(CANDIDATE_NAME.search(norm(_name(place))))


def _texts(place: dict) -> list[str]:
    texts = [
        (place.get("editorialSummary") or {}).get("text", ""),
        ((place.get("generativeSummary") or {}).get("overview") or {}).get("text", ""),
        ((place.get("reviewSummary") or {}).get("text") or {}).get("text", ""),
    ]
    for review in place.get("reviews") or []:
        texts.append((review.get("text") or {}).get("text", ""))
        texts.append((review.get("originalText") or {}).get("text", ""))
    return [norm(t) for t in texts if t]


def _matches(patterns: list[str], texts: list[str]) -> bool:
    return any(re.search(p, t) for p in patterns for t in texts)


def _summary(place: dict) -> str:
    for text in (
        (place.get("editorialSummary") or {}).get("text"),
        ((place.get("generativeSummary") or {}).get("overview") or {}).get("text"),
        ((place.get("reviewSummary") or {}).get("text") or {}).get("text"),
    ):
        if text:
            return text if len(text) <= 300 else text[:297].rsplit(" ", 1)[0] + "..."
    return ""


def _price(place: dict) -> int | None:
    price_range = place.get("priceRange") or {}
    start = (price_range.get("startPrice") or {}).get("units")
    end = (price_range.get("endPrice") or {}).get("units")
    if start and end:
        return round((int(start) + int(end)) / 2)
    if start:
        return int(start)
    return PRICE_LEVEL_EUR.get(place.get("priceLevel", ""))


def _open_days(place: dict) -> list[str]:
    periods = (place.get("regularOpeningHours") or {}).get("periods") or []
    days = sorted({p["open"]["day"] for p in periods if "open" in p and "day" in p["open"]})
    # Monday first, like people say it.
    return [DAYS[d] for d in sorted(days, key=lambda d: (d + 6) % 7)]


def classify(place: dict, municipality: str | None) -> tuple[dict | None, str]:
    """Return (catalogue record, "ok") or (None, rejection reason)."""
    name = _name(place)
    if place.get("businessStatus") == "CLOSED_PERMANENTLY":
        return None, "closed"
    if not municipality:
        return None, "unknown_municipality"
    texts = _texts(place)
    if "guachinch" in norm(name):
        evidence = "name"
    elif any("guachinch" in t for t in texts):
        evidence = "reviews"
    else:
        return None, "not_a_guachinche"

    zone = MUNICIPALITY_ZONE[municipality]
    features = [f for f, check in _ATTRIBUTE_FEATURES.items() if check(place)]
    features += [f for f, patterns in _TEXT_FEATURES.items() if _matches(patterns, texts)]
    location = place.get("location") or {}
    return {
        "id": place["id"],
        "name": name,
        "municipality": municipality,
        "locality": locality_of(place, municipality),
        "zone": zone,
        "region": ZONE_REGION[zone],
        "address": place.get("formattedAddress", ""),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "google_maps_uri": place.get("googleMapsUri", ""),
        "rating": place.get("rating"),
        "rating_count": place.get("userRatingCount", 0),
        "price_per_person": _price(place),
        "open_days": _open_days(place),
        "hours": (place.get("regularOpeningHours") or {}).get("weekdayDescriptions", []),
        "dishes": [d for d, patterns in DISHES.items() if _matches(patterns, texts)],
        "features": features,
        "wines": [w for w, patterns in WINES.items() if _matches(patterns, texts)],
        "description": _summary(place),
        "guachinche_evidence": evidence,
    }, "ok"


def build_catalogue(places) -> tuple[dict, dict]:
    places = list(places)
    municipalities = {p["id"]: municipality_of(p) for p in places}
    placed = [(p["location"], municipalities[p["id"]]) for p in places if municipalities[p["id"]] and p.get("location")]
    # An address can say one town while the pin sits in another. When a place is far from every other place
    # of its municipality but right next to places of another one, trust the map.
    for p in places:
        here, mine = p.get("location"), municipalities[p["id"]]
        if not here or not mine:
            continue
        others = [(_distance_km(here, loc), m) for loc, m in placed if loc is not here]
        same = [d for d, m in others if m == mine]
        near = min(((d, m) for d, m in others if m != mine), default=None)
        if same and min(same) > 10 and near and near[0] <= 3:
            municipalities[p["id"]] = near[1]
    records, rejected = [], {}
    for place in places:
        municipality = municipalities[place["id"]] or nearest_municipality(place, placed)
        record, reason = classify(place, municipality)
        if record:
            records.append(record)
        else:
            rejected[reason] = rejected.get(reason, 0) + 1
    records.sort(key=lambda r: (r["region"], r["zone"], r["municipality"], r["name"]))
    return {
        "source": "Google Places API (New)",
        "attribution": "Place data © Google",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "guachinches": records,
    }, rejected
