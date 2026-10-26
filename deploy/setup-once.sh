#!/usr/bin/env bash
# One-time Google Cloud setup for AgentForge, after the cluster and the database exist.
# Safe to run again: every step skips what's already there.
#
#   ./deploy/setup-once.sh
#
# Reads your API keys from .env and puts them in Secret Manager. It never prints them.
set -euo pipefail

PROJECT=agentforge-shre-2026
REGION=europe-west2
INSTANCE=agentforge-db
GSA=agentforge-app
GSA_EMAIL="$GSA@$PROJECT.iam.gserviceaccount.com"
NAMESPACE=agentforge
KSA=agentforge

cd "$(dirname "$0")/.."   # the repo root, where .env is
# The value of KEY: from .env (allowing "export KEY=", spaces and quotes), else from your
# terminal's environment. The Gemini key may be called GOOGLE_API_KEY or GEMINI_API_KEY.
env_value() {
  local v
  v=$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=" .env 2>/dev/null | tail -n1 \
      | sed -E "s/^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=[[:space:]]*//; s/[[:space:]]+$//; s/^[\"']//; s/[\"']$//")
  [ -n "$v" ] || v=$(printenv "$1" || true)
  if [ -z "$v" ] && [ "$1" = GOOGLE_API_KEY ]; then v=$(env_value GEMINI_API_KEY); fi
  printf '%s' "$v"
}
for key in GOOGLE_API_KEY TAVILY_API_KEY GOOGLE_CLIENT_ID LLM_MODEL; do
  [ -n "$(env_value "$key")" ] || { echo "$key is missing in .env (names found: $(grep -oE '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_]+' .env | sed -E 's/^[[:space:]]*(export[[:space:]]+)?//' | tr '\n' ' '))"; exit 1; }
done

echo "1/6 Waiting for the database and the cluster to be ready (checks every 20 s)..."
until [ "$(gcloud sql instances describe $INSTANCE --format='value(state)')" = RUNNABLE ]; do sleep 20; done
until [ "$(gcloud container clusters describe agentforge --location=$REGION --format='value(status)')" = RUNNING ]; do sleep 20; done

echo "2/6 The database, its user, and DATABASE_URL in Secret Manager"
gcloud sql databases describe agentforge --instance=$INSTANCE >/dev/null 2>&1 \
  || gcloud sql databases create agentforge --instance=$INSTANCE
if ! gcloud secrets describe database-url >/dev/null 2>&1; then
  DB_PASS=$(openssl rand -hex 24)   # 48 random letters/digits: strong, and safe inside a URL
  gcloud sql users create agentforge --instance=$INSTANCE --password="$DB_PASS" 2>/dev/null \
    || gcloud sql users set-password agentforge --instance=$INSTANCE --password="$DB_PASS"
  # 127.0.0.1:5432 is the Cloud SQL Auth Proxy sidecar inside each pod.
  printf 'postgresql://agentforge:%s@127.0.0.1:5432/agentforge' "$DB_PASS" \
    | gcloud secrets create database-url --data-file=- --replication-policy=user-managed --locations=$REGION
  unset DB_PASS
fi

echo "3/6 API keys into Secret Manager (kept in London only)"
for pair in gemini-api-key:GOOGLE_API_KEY tavily-api-key:TAVILY_API_KEY; do
  name=${pair%%:*}; key=${pair##*:}
  gcloud secrets describe "$name" >/dev/null 2>&1 \
    || env_value "$key" | tr -d '\n' \
       | gcloud secrets create "$name" --data-file=- --replication-policy=user-managed --locations=$REGION
done

echo "4/6 The Google service account the pods act as, with only the access they need"
if ! gcloud iam service-accounts describe "$GSA_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create $GSA --display-name="AgentForge pods"
  sleep 15   # a new service account takes a moment to appear everywhere
fi
gcloud projects add-iam-policy-binding $PROJECT --condition=None --quiet \
  --member="serviceAccount:$GSA_EMAIL" --role=roles/cloudsql.client >/dev/null
gcloud pubsub topics add-iam-policy-binding runs \
  --member="serviceAccount:$GSA_EMAIL" --role=roles/pubsub.publisher >/dev/null
gcloud pubsub subscriptions add-iam-policy-binding runs-workers \
  --member="serviceAccount:$GSA_EMAIL" --role=roles/pubsub.subscriber >/dev/null
# Workload Identity: the Kubernetes service account agentforge/agentforge may act as it.
gcloud iam service-accounts add-iam-policy-binding "$GSA_EMAIL" \
  --role=roles/iam.workloadIdentityUser \
  --member="serviceAccount:$PROJECT.svc.id.goog[$NAMESPACE/$KSA]" >/dev/null

echo "5/6 A fixed public IP address for the website"
gcloud compute addresses describe agentforge-ip --global >/dev/null 2>&1 \
  || gcloud compute addresses create agentforge-ip --global
IP=$(gcloud compute addresses describe agentforge-ip --global --format='value(address)')

echo "6/6 Connecting kubectl to the cluster"
gcloud container clusters get-credentials agentforge --location=$REGION

DOMAIN="$(echo "$IP" | tr . -).sslip.io"
cat <<EOF

Setup done. Your site will be:  https://$DOMAIN

Before deploying, in the Google Cloud console, project "agentforge" (where your sign-in
Client ID lives): Google Auth Platform > Clients > your client > Authorised JavaScript
origins > add  https://$DOMAIN  > Save.
Then:  ./deploy/deploy.sh
EOF
