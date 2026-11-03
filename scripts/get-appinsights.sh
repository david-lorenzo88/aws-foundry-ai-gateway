#!/usr/bin/env bash
# Store the connection string of the Application Insights resource connected to the Foundry project.
# The AWS agent exports its traces there, which is where Foundry Control Plane reads them from.
#
#   scripts/get-appinsights.sh <resource-group> <app-insights-name>
source "$(dirname "$0")/common.sh"

RG="${1:?resource group}"
NAME="${2:?Application Insights name (Foundry: Manage > Project details > Connected resources)}"

CS="$(az monitor app-insights component show -g "$RG" -a "$NAME" --query connectionString -o tsv)"
save_env APPINSIGHTS_CONNECTION_STRING "$CS"
echo "Saved APPINSIGHTS_CONNECTION_STRING for $NAME to .env"
