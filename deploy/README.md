# Deploying AgentForge to Google Cloud (GKE Autopilot)

```
Browser ──HTTPS──► Load balancer (static IP, Google-managed certificate)
                        │
                        ▼
                  api pods (2-3) ──publish run ID──► Pub/Sub topic "runs"
                        │                                   │
                        ▼                                   ▼
                  Cloud SQL Postgres ◄──────────── worker pods (2) ──► Gemini, Tavily
                        ▲
                  sweeper (CronJob, every minute)
```

Every pod reaches Postgres through a **Cloud SQL Auth Proxy sidecar** (IAM-checked and
encrypted; the database accepts no direct connections). Pods get Google access through
**Workload Identity**: there are no key files anywhere.

## Commands, in order

| # | Command | Does |
|---|---|---|
| 1 | `gcloud container clusters create-auto agentforge --location=europe-west2 --async` | The cluster (done) |
| 2 | `gcloud sql instances create agentforge-db ... --async` | The database (done) |
| 3 | `gcloud pubsub topics create runs` + subscription `runs-workers` (ack 600 s) | The queue (done) |
| 4 | `./deploy/setup-once.sh` | Database user, secrets, service account and IAM, static IP, kubectl |
| 5 | Add `https://<your-domain>` to the sign-in Client ID's **Authorised JavaScript origins** | Google sign-in works on the new site |
| 6 | `./deploy/deploy.sh` | Build, push and deploy the current commit |

To release new code: commit, then `./deploy/deploy.sh`. To roll back: check out the old commit, then `./deploy/deploy.sh` (its image is already pushed, so it only re-applies).

## Checking it

```
kubectl -n agentforge get pods                      # api x2, worker x2: Running, READY 2/2
kubectl -n agentforge get managedcertificate        # Provisioning -> Active (15-60 min)
kubectl -n agentforge logs deploy/worker -c worker -f
kubectl -n agentforge get cronjob,jobs              # the sweeper, every minute
```

Graceful shutdown on the cluster: start a run, then
`kubectl -n agentforge rollout restart deployment/worker`. The old worker logs
"handed back", and a new one finishes the run.

## Pausing it to save money

```
kubectl -n agentforge scale deployment api worker --replicas=0
kubectl -n agentforge patch cronjob sweeper -p '{"spec":{"suspend":true}}'
gcloud sql instances patch agentforge-db --activation-policy=NEVER
```

Resume: `--activation-policy=ALWAYS`, `"suspend":false`, and `--replicas=2`. The load
balancer (~$18 a month) keeps billing until you delete the Ingress.

## Why it's built like this (interview notes)

- **One image, three jobs.** The api, the worker and the sweeper run the same tested image with different start commands. There's nothing to drift apart.
- **Native sidecar for the proxy.** Kubernetes starts it before the app and stops it after the app. So a worker that's shutting down can still write "handed back" to the database.
- **Graceful shutdown, end to end.**
  - api: a `preStop` sleep of 10 s lets the load balancer stop sending traffic, then uvicorn finishes in-flight requests.
  - worker: on SIGTERM it finishes its step and hands the run back, within `terminationGracePeriodSeconds: 120`.
- **Probes.** `/readyz` (needs the database) controls traffic. `/healthz` (needs nothing) controls restarts, so a database blip never restarts every pod.
- **Scaling.**
  - The api has an HPA on CPU, capped at 3 because db-f1-micro allows 25 connections.
  - Workers wait on the AI, not on CPU, so they should scale on Pub/Sub backlog. Next step: KEDA.
- **Secrets.** Secret Manager (kept in London) is the source of truth. The deploy script copies the secrets into a Kubernetes Secret, and its content hash forces a rollout when a key changes. Next step: the Secret Manager CSI add-on, so secrets never sit in etcd.
- **Least privilege.** The pods' service account can only publish to `runs`, consume `runs-workers`, and connect to Cloud SQL.
- **For real production:**
  - a dedicated-core, `REGIONAL` (high-availability) Cloud SQL instance
  - separate service accounts for the api and the worker
  - a real domain
  - CI that runs the tests and evals before deploying
