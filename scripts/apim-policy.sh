#!/usr/bin/env bash
# Add or remove a policy fragment in an APIM API's inbound section, right after <base />, leaving the
# rest of the policy (including whatever Foundry put there, like its Block rule or CORS) untouched.
#
#   scripts/apim-policy.sh <api-id> add <marker> '<xml fragment>'   (no-op if <marker> is already there)
#   scripts/apim-policy.sh <api-id> remove <marker>                  (removes the element containing <marker>)
source "$(dirname "$0")/common.sh"
require APIM_RG APIM_NAME

API_ID="${1:?api id}"
ACTION="${2:?add|remove}"
MARKER="${3:?marker text that identifies the fragment}"
FRAGMENT="${4:-}"

SUB="$(az account show --query id -o tsv)"
URL="https://management.azure.com/subscriptions/$SUB/resourceGroups/$APIM_RG/providers/Microsoft.ApiManagement/service/$APIM_NAME/apis/$API_ID/policies/policy?api-version=2025-03-01-preview"
CURRENT="$(az rest --method get --url "$URL&format=rawxml" --query properties.value -o tsv 2>/dev/null || true)"

NEW="$(CURRENT="$CURRENT" ACTION="$ACTION" MARKER="$MARKER" FRAGMENT="$FRAGMENT" python3 - <<'PY'
import os, re
current = os.environ["CURRENT"].lstrip("﻿").strip() or (
    "<policies><inbound><base /></inbound><backend><base /></backend>"
    "<outbound><base /></outbound><on-error><base /></on-error></policies>"
)
marker, fragment = os.environ["MARKER"], os.environ["FRAGMENT"]
if os.environ["ACTION"] == "add":
    if marker not in current:
        if re.search(r"<inbound>\s*<base\s*/>", current):
            current = re.sub(r"(<inbound>\s*<base\s*/>)", lambda m: m.group(1) + fragment, current, count=1)
        else:
            current = current.replace("<inbound>", "<inbound>" + fragment, 1)
else:
    # Our fragments are single top-level elements tagged with an id="..." or a header/name marker.
    pattern = re.compile(r"<(choose|set-header)\b[^>]*%s[^>]*>.*?</\1>" % re.escape(marker), re.S)
    current = pattern.sub("", current, count=1)
print(current)
PY
)"

BODY="$(NEW="$NEW" python3 -c 'import json,os; print(json.dumps({"properties": {"format": "rawxml", "value": os.environ["NEW"]}}))')"
# The PUT answers with the raw XML, which az rest prints even with -o none.
az rest --method put --url "$URL" --body "$BODY" -o none > /dev/null 2>&1
