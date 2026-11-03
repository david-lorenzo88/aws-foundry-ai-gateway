#!/usr/bin/env python3
"""Find real guachinches in Tenerife with Google Places API (New) and build the MCP server's catalogue.

    GOOGLE_MAPS_API_KEY=... python3 scripts/fetch_guachinches.py            # discover + details + classify
    python3 scripts/fetch_guachinches.py --classify-only                     # re-classify the cached raw data
    python3 scripts/fetch_guachinches.py --max-details 50                    # cap the expensive calls

1. Discovery: Text Search "guachinche en <municipio>, Tenerife" for each of the 31 municipalities, restricted
   to the island, up to 60 results each (Pro fields only: cheap).
2. Details: one Place Details call per candidate with the Enterprise + Atmosphere fields (attributes,
   opening hours, price, summaries, reviews). This is the expensive part; --max-details caps it.
3. Classification (aws/mcp/classify.py): keeps only guachinches, maps town -> zone -> region, features from
   Places attributes, dishes and wines from summaries and reviews.

The raw Google responses are cached in data/raw/ (gitignored) so classification can be re-run for free.
Output: aws/mcp/data/guachinches.json, read by the MCP server.

Note: Google Maps Platform terms restrict storing Places content other than place IDs. This snapshot is for
a private demo; refresh it rather than keeping it around.
"""

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    # python.org builds on macOS ship without root certificates; certifi (if installed) provides them.
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "aws" / "mcp"))
import classify  # noqa: E402

RAW_DIR = ROOT / "data" / "raw"
SEARCH_CACHE = RAW_DIR / "search.json"
DETAILS_CACHE = RAW_DIR / "details.json"
OUTPUT = ROOT / "aws" / "mcp" / "data" / "guachinches.json"

# Tenerife bounding box (south-west, north-east).
TENERIFE = {"low": {"latitude": 27.99, "longitude": -16.93}, "high": {"latitude": 28.61, "longitude": -16.11}}

SEARCH_FIELDS = ",".join(
    [
        "places.id", "places.displayName", "places.formattedAddress", "places.addressComponents",
        "places.location", "places.types", "places.businessStatus", "nextPageToken",
    ]
)
DETAIL_FIELDS = ",".join(
    [
        "id", "displayName", "formattedAddress", "addressComponents", "location", "types", "primaryType",
        "businessStatus", "googleMapsUri", "rating", "userRatingCount", "priceLevel", "priceRange",
        "regularOpeningHours", "editorialSummary", "generativeSummary", "reviewSummary", "reviews",
        "allowsDogs", "goodForChildren", "goodForGroups", "liveMusic", "outdoorSeating", "parkingOptions",
        "paymentOptions", "reservable", "servesLunch", "servesVegetarianFood", "servesWine", "accessibilityOptions",
    ]
)


def _call(method: str, url: str, key: str, fields: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": fields},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            if error.code in (429, 500, 503) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"Google Places error {error.code} on {url}: {detail}") from None
    raise SystemExit("unreachable")


def discover(key: str) -> dict:
    found: dict[str, dict] = {}
    for municipality in classify.MUNICIPALITIES:
        body = {
            "textQuery": f"guachinche en {municipality}, Tenerife",
            "languageCode": "es",
            "regionCode": "ES",
            "pageSize": 20,
            "locationRestriction": {"rectangle": TENERIFE},
        }
        pages = 0
        while True:
            data = _call("POST", "https://places.googleapis.com/v1/places:searchText", key, SEARCH_FIELDS, body)
            for place in data.get("places", []):
                found.setdefault(place["id"], place)
            pages += 1
            token = data.get("nextPageToken")
            if not token or pages == 3:
                break
            body = {**body, "pageToken": token}
            time.sleep(1)  # the next page token can take a moment to become valid
        print(f"  {municipality}: {len(found)} unique places so far", file=sys.stderr)
    return found


def fetch_details(key: str, place_ids: list[str], cache: dict) -> dict:
    for i, place_id in enumerate(place_ids, 1):
        if place_id in cache:
            continue
        cache[place_id] = _call(
            "GET", f"https://places.googleapis.com/v1/places/{place_id}?languageCode=es&regionCode=ES", key, DETAIL_FIELDS
        )
        if i % 10 == 0:
            print(f"  details {i}/{len(place_ids)}", file=sys.stderr)
            DETAILS_CACHE.write_text(json.dumps(cache, ensure_ascii=False))
    return cache


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--classify-only", action="store_true", help="only re-run classification on cached data")
    parser.add_argument("--max-details", type=int, default=400, help="cap on Place Details calls (the costly ones)")
    parser.add_argument("--refresh-search", action="store_true", help="re-run discovery even if cached")
    args = parser.parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    if not args.classify_only:
        key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not key:
            raise SystemExit("Set GOOGLE_MAPS_API_KEY (in .env or the environment).")

        if SEARCH_CACHE.exists() and not args.refresh_search:
            found = json.loads(SEARCH_CACHE.read_text())
            print(f"Discovery: {len(found)} places from cache (use --refresh-search to redo)", file=sys.stderr)
        else:
            print("Discovery (Text Search)...", file=sys.stderr)
            found = discover(key)
            SEARCH_CACHE.write_text(json.dumps(found, ensure_ascii=False))

        candidates = [pid for pid, place in found.items() if classify.is_candidate(place)]
        # Places named "guachinche..." first, so a --max-details cap never drops the obvious ones.
        candidates.sort(key=lambda pid: "guachinch" not in classify.norm(found[pid].get("displayName", {}).get("text", "")))
        print(f"Candidates on Tenerife that look like guachinches: {len(candidates)}", file=sys.stderr)
        if len(candidates) > args.max_details:
            print(f"Capping details at {args.max_details} (--max-details)", file=sys.stderr)
            candidates = candidates[: args.max_details]

        cache = json.loads(DETAILS_CACHE.read_text()) if DETAILS_CACHE.exists() else {}
        new = sum(1 for c in candidates if c not in cache)
        print(f"Place Details: {new} new calls ({len(candidates) - new} cached)", file=sys.stderr)
        cache = fetch_details(key, candidates, cache)
        DETAILS_CACHE.write_text(json.dumps(cache, ensure_ascii=False))

    details = json.loads(DETAILS_CACHE.read_text())
    catalogue, rejected = classify.build_catalogue(details.values())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(catalogue, ensure_ascii=False, indent=1))

    by_region: dict[str, int] = {}
    for g in catalogue["guachinches"]:
        by_region[g["region"]] = by_region.get(g["region"], 0) + 1
    print(f"\nCatalogue: {len(catalogue['guachinches'])} guachinches -> {OUTPUT.relative_to(ROOT)}", file=sys.stderr)
    print(f"By region: {by_region}", file=sys.stderr)
    print(f"Rejected: {dict(sorted(rejected.items()))}", file=sys.stderr)


if __name__ == "__main__":
    main()
