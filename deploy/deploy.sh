#!/usr/bin/env bash
# Deploy the current commit to GKE: build and push the image (if new), then apply the
# Kubernetes files with this commit's image, your settings and your secrets.
#
#   ./deploy/deploy.sh
#
# Run deploy/setup-once.sh first (once).
set -euo pipefail

PROJECT=agentforge-shre-2026
REGION=europe-west2
NAMESPACE=agentforge
REPO="$REGION-docker.pkg.dev/$PROJECT/agentforge/agentforge"

cd "$(dirname "$0")/.."
env_value() {
  grep -E "^$1=" .env | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}

# The image is tagged with the commit, so the tag must describe exactly what's deployed.
if [ -n "$(git status --porcelain)" ]; then
  echo "You have uncommitted changes. Commit them first, so the image matches a commit."
  exit 1
fi
TAG=$(git rev-parse --short HEAD)
IMAGE="$REPO:$TAG"

echo "1/5 Image $IMAGE"
if gcloud artifacts docker images describe "$IMAGE" >/dev/null 2>&1; then
  echo "    already pushed"
else
  docker buildx build --platform linux/amd64 -t "$IMAGE" --push .
fi

echo "2/5 Writing the overlay k8s/live/ (settings, secrets, image tag, certificate)"
IP=$(gcloud compute addresses describe agentforge-ip --global --format='value(address)')
DOMAIN="$(echo "$IP" | tr . -).sslip.io"
mkdir -p k8s/live
trap 'rm -f k8s/live/secrets.env' EXIT   # the secrets file never outlives this script
(
  umask 077   # secrets.env readable by you only
  cat > k8s/live/secrets.env <<EOF
DATABASE_URL=$(gcloud secrets versions access latest --secret=database-url)
GOOGLE_API_KEY=$(gcloud secrets versions access latest --secret=gemini-api-key)
TAVILY_API_KEY=$(gcloud secrets versions access latest --secret=tavily-api-key)
EOF
)
cat > k8s/live/config.env <<EOF
FAKE_LLM=false
LLM_MODEL=$(env_value LLM_MODEL)
LLM_REQUESTS_PER_MINUTE=30
GOOGLE_CLIENT_ID=$(env_value GOOGLE_CLIENT_ID)
GCP_PROJECT=$PROJECT
INSTANCE_CONNECTION_NAME=$(gcloud sql instances describe agentforge-db --format='value(connectionName)')
EOF
cat > k8s/live/certificate.yaml <<EOF
apiVersion: networking.gke.io/v1
kind: ManagedCertificate
metadata:
  name: agentforge-cert
spec:
  domains: [$DOMAIN]
EOF
# The generators add a hash of the contents to the ConfigMap and Secret names. Change a
# setting or a key, and the name changes, so Kubernetes rolls the pods automatically.
cat > k8s/live/kustomization.yaml <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: $NAMESPACE
resources:
  - ../base
  - certificate.yaml
images:
  - name: agentforge
    newName: $REPO
    newTag: $TAG
configMapGenerator:
  - name: agentforge-config
    envs: [config.env]
secretGenerator:
  - name: agentforge-secrets
    envs: [secrets.env]
EOF

# A failed lookup inside $(...) above wouldn't stop the script, so check nothing is empty.
if grep -qE '^[A-Z_]+=$' k8s/live/secrets.env k8s/live/config.env; then
  echo "Empty setting(s):"; grep -hE '^[A-Z_]+=$' k8s/live/secrets.env k8s/live/config.env | cut -d= -f1
  exit 1
fi

echo "3/5 Applying to the cluster"
kubectl -n $NAMESPACE delete job migrate --ignore-not-found >/dev/null 2>&1 || true
kubectl apply -k k8s/live
rm -f k8s/live/secrets.env

echo "4/5 Waiting for the database tables (migrate job), then the new pods"
kubectl -n $NAMESPACE wait --for=condition=complete job/migrate --timeout=10m \
  || { echo "Migrate failed. See: kubectl -n $NAMESPACE logs job/migrate -c migrate"; exit 1; }
kubectl -n $NAMESPACE rollout status deployment/api --timeout=10m
kubectl -n $NAMESPACE rollout status deployment/worker --timeout=10m

echo "5/5 HTTPS certificate: $(kubectl -n $NAMESPACE get managedcertificate agentforge-cert \
  -o jsonpath='{.status.certificateStatus}' 2>/dev/null || echo unknown)"
cat <<EOF

Deployed $TAG.  https://$DOMAIN
The first time, the load balancer takes ~10 minutes and the certificate 15-60 minutes
("Provisioning" -> "Active"). Check:  kubectl -n $NAMESPACE get managedcertificate
EOF
