#!/usr/bin/env bash
# Create the Entra identity the agent (in AWS) uses to call the Foundry Toolbox, and let it use the project.
#
#   scripts/toolbox-identity.sh
#
# A Toolbox endpoint only accepts Entra tokens (scope https://ai.azure.com/.default). Lambda has no Azure
# identity, so this creates an app registration + service principal with a client secret, gives it the
# "Foundry User" role on the Foundry project, and saves TOOLBOX_TENANT_ID / TOOLBOX_CLIENT_ID /
# TOOLBOX_CLIENT_SECRET in .env for scripts/deploy-aws.sh. Re-running rotates the secret.
source "$(dirname "$0")/common.sh"
require APIM_RG FOUNDRY_ACCOUNT FOUNDRY_PROJECT

APP_NAME="${AGENT_NAME}-toolbox-client"
ROLE="${TOOLBOX_ROLE:-Foundry User}"
SCOPE="$(az cognitiveservices account show -g "$APIM_RG" -n "$FOUNDRY_ACCOUNT" --query id -o tsv)/projects/$FOUNDRY_PROJECT"

APP_ID="$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)"
if [[ -z "$APP_ID" ]]; then
  APP_ID="$(az ad app create --display-name "$APP_NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv)"
  echo "Created app registration $APP_NAME"
fi
az ad sp show --id "$APP_ID" -o none 2>/dev/null || az ad sp create --id "$APP_ID" -o none

SECRET="$(az ad app credential reset --id "$APP_ID" --display-name lambda --years 1 --query password -o tsv 2>/dev/null)"
save_env TOOLBOX_TENANT_ID "$(az account show --query tenantId -o tsv)"
save_env TOOLBOX_CLIENT_ID "$APP_ID"
save_env TOOLBOX_CLIENT_SECRET "$SECRET"

SP_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv)"
az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
  --role "$ROLE" --scope "$SCOPE" -o none 2>/dev/null || true
echo "Service principal $APP_NAME has \"$ROLE\" on the project. Credentials saved in .env (TOOLBOX_*)."
echo "Role assignments can take a few minutes to apply."
