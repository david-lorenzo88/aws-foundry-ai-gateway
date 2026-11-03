#!/usr/bin/env python3
"""Generate aws/mcp/data/guachinches.sample.json: invented guachinches with the same schema as the real snapshot.

    python3 scripts/make_sample_data.py

Every name, address, rating, price and dish is made up (deterministic seed, so re-running gives the same file).
Towns, zones and regions are real, and coordinates are near each town centre, so searches by place, zone,
region, dish, feature, wine, day and price behave like with the real data. The MCP server uses this file when
there's no aws/mcp/data/guachinches.json (the Google Places snapshot, which isn't in the repo).
"""

import json
import random
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "aws" / "mcp"))
from classify import DISHES, MUNICIPALITY_ZONE, ZONE_REGION  # noqa: E402

OUT = ROOT / "aws" / "mcp" / "data" / "guachinches.sample.json"

# Approximate town centres; places are scattered a few hundred metres around them.
TOWNS = {
    "La Orotava": (28.390, -16.523, ["La Perdoma", "La Florida", "Aguamansa"]),
    "Los Realejos": (28.384, -16.583, ["Palo Blanco", "Icod el Alto"]),
    "Puerto de la Cruz": (28.414, -16.548, ["Las Arenas"]),
    "Santa Úrsula": (28.426, -16.488, ["La Corujera", "Cuesta de la Villa"]),
    "La Victoria de Acentejo": (28.432, -16.461, ["El Lomo"]),
    "La Matanza de Acentejo": (28.452, -16.447, ["San Antonio"]),
    "El Sauzal": (28.478, -16.436, ["Ravelo"]),
    "Tacoronte": (28.477, -16.410, ["Agua García", "El Cantillo"]),
    "Tegueste": (28.518, -16.338, ["El Portezuelo"]),
    "San Cristóbal de La Laguna": (28.487, -16.316, ["Los Baldíos", "Valle de Guerra", "Tejina"]),
    "El Rosario": (28.454, -16.368, ["La Esperanza"]),
    "Santa Cruz de Tenerife": (28.468, -16.254, ["San Andrés"]),
    "Icod de los Vinos": (28.367, -16.711, ["Santa Bárbara"]),
    "La Guancha": (28.373, -16.652, []),
    "Los Silos": (28.366, -16.815, ["Aregume"]),
    "Güímar": (28.315, -16.411, ["El Escobonal"]),
    "Arafo": (28.340, -16.420, []),
    "Candelaria": (28.354, -16.372, ["Igueste"]),
    "Arona": (28.099, -16.681, ["Valle de San Lorenzo", "Buzanada"]),
    "Granadilla de Abona": (28.119, -16.576, ["Charco del Pino"]),
    "San Miguel de Abona": (28.098, -16.618, ["Las Chafiras"]),
    "Vilaflor de Chasna": (28.157, -16.636, []),
}
# More places up north, where most guachinches are.
WEIGHTS = {"Norte": 5, "Área Metropolitana": 3, "Sur": 1}

PREFIXES = ["Guachinche", "Guachinche", "Guachinche", "Bodega", "Lagar", "Casa", "Bodegón", "La Parra de"]
NAMES = [
    "Tía Candelaria", "El Lomo Alto", "Don Fermín", "Los Almendros", "La Higuera Vieja", "Chano el del Monte",
    "El Barranquillo", "Las Tres Pipas", "La Garrafa Llena", "Mi Abuela Rosario", "El Tostadero", "Los Castaños",
    "La Viña de Nicolás", "El Pajar", "Doña Eulogia", "El Rincón de Pancho", "Las Brumas", "La Cumbrita",
    "El Lagar de Tomás", "Los Hermanos Hernández", "La Era", "El Mojo Verde", "La Tea", "El Alpendre",
    "Tío Juanito", "Las Hoyas", "El Bernegal", "La Bodeguita de Chencho", "El Sabor de Antes", "Los Pinos",
    "La Fuente Santa", "El Brezal", "Mamá Carmela", "La Atalaya", "El Drago Chico", "Las Medianías",
    "El Lagarito", "La Cochinilla", "Juana la del Valle", "El Granero",
]
STREETS = ["Camino", "Calle", "Carretera", "Camino Real", "Calle La"]
STREET_NAMES = ["El Lomo", "Las Toscas", "La Cruz", "El Medio", "La Vera", "San Antonio", "El Monte",
                "La Fuente", "Los Majuelos", "La Montañeta", "El Calvario", "Las Lajas"]
DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
FEATURE_ODDS = {
    "parking": 0.85, "kid_friendly": 0.8, "groups": 0.75, "reservations": 0.7, "accepts_cards": 0.65,
    "wheelchair_accessible": 0.5, "terrace": 0.55, "dog_friendly": 0.25, "views": 0.25,
    "vegetarian_options": 0.12, "live_music": 0.08, "sells_wine": 0.3,
}
POSTAL = {"La Orotava": "38300", "Los Realejos": "38410", "Puerto de la Cruz": "38400", "Santa Úrsula": "38390",
          "La Victoria de Acentejo": "38380", "La Matanza de Acentejo": "38370", "El Sauzal": "38360",
          "Tacoronte": "38350", "Tegueste": "38280", "San Cristóbal de La Laguna": "38205", "El Rosario": "38290",
          "Santa Cruz de Tenerife": "38001", "Icod de los Vinos": "38430", "La Guancha": "38440",
          "Los Silos": "38470", "Güímar": "38500", "Arafo": "38550", "Candelaria": "38530", "Arona": "38640",
          "Granadilla de Abona": "38600", "San Miguel de Abona": "38620", "Vilaflor de Chasna": "38613"}


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def main() -> None:
    rng = random.Random(2026)
    towns = list(TOWNS)
    for town in towns:
        assert town in MUNICIPALITY_ZONE, town
    weights = [WEIGHTS[ZONE_REGION[MUNICIPALITY_ZONE[t]]] for t in towns]
    dishes = list(DISHES)
    places = []
    for name in NAMES:
        town = rng.choices(towns, weights)[0]
        lat0, lon0, localities = TOWNS[town]
        locality = rng.choice(localities) if localities and rng.random() < 0.6 else town
        lat, lon = round(lat0 + rng.uniform(-0.012, 0.012), 6), round(lon0 + rng.uniform(-0.012, 0.012), 6)
        full = f"{rng.choice(PREFIXES)} {name}"
        closed = set(rng.sample(["lunes", "martes", "miércoles"], rng.choice([1, 2, 2, 3])))
        open_days = [d for d in DAYS if d not in closed]
        opening = rng.choice(["12:00", "12:30", "13:00"])
        closing = rng.choice(["17:00", "18:00", "22:00", "23:00"])
        features = [f for f, odds in FEATURE_ODDS.items() if rng.random() < odds]
        wines = sorted(rng.sample(["tinto", "blanco", "rosado"], rng.choice([1, 2, 2, 3])))
        places.append({
            "id": "demo-" + slug(full),
            "name": full,
            "municipality": town,
            "locality": locality,
            "zone": MUNICIPALITY_ZONE[town],
            "region": ZONE_REGION[MUNICIPALITY_ZONE[town]],
            "address": f"{rng.choice(STREETS)} {rng.choice(STREET_NAMES)}, {rng.randint(2, 180)}, "
                       f"{POSTAL[town]} {locality}, Santa Cruz de Tenerife",
            "latitude": lat,
            "longitude": lon,
            "google_maps_uri": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
            "rating": round(rng.uniform(3.9, 4.9), 1),
            "rating_count": rng.randint(35, 1400),
            "price_per_person": rng.choice([10, 12, 15, 15, 15, 18, 20, 25]),
            "open_days": open_days,
            "hours": [f"{d}: {'Cerrado' if d in closed else f'{opening}–{closing}'}" for d in DAYS],
            "dishes": sorted(rng.sample(dishes, rng.randint(3, 6)), key=dishes.index),
            "features": features,
            "wines": wines,
            "description": "Guachinche inventado para la demo: el nombre, la dirección y los datos no son reales.",
            "guachinche_evidence": "invented",
        })
    OUT.write_text(json.dumps({
        "source": "Invented sample data (scripts/make_sample_data.py)",
        "attribution": "Fictional places; towns, zones and regions are real",
        "fetched_at": "2026-11-03T00:00:00+00:00",
        "sample": True,
        "guachinches": places,
    }, ensure_ascii=False, indent=1) + "\n")
    by_region = {}
    for p in places:
        by_region[p["region"]] = by_region.get(p["region"], 0) + 1
    print(f"Wrote {len(places)} invented guachinches to {OUT.relative_to(ROOT)}: {by_region}")


if __name__ == "__main__":
    main()
