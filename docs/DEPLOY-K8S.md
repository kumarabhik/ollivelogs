# Self-hosted Kubernetes deployment

This guide deploys OlliveLogs on a self-hosted Kubernetes cluster using the
Helm chart at [`infra/helm/ollivelogs/`](../infra/helm/ollivelogs/).

It has been verified end-to-end on minikube (Kubernetes v1.33.1) with the
`docker` driver, but the chart targets any conformant cluster (k3s, kind, EKS,
GKE, AKS, bare-metal kubeadm).

## Prerequisites

- `kubectl` v1.30+
- `helm` v3.14+ (v4 also works)
- A Kubernetes cluster with at least **4 vCPU / 6 GB RAM** free
- A storage class that supports `ReadWriteOnce` PVCs (defaults: `standard` on
  minikube, `local-path` on k3s)
- Your provider API keys exported in `.env` at the repo root

## 1. Build images into the cluster's container runtime

For local clusters (no registry), build the four app images directly into
the cluster's docker daemon:

```bash
# minikube
eval $(minikube -p ollivelogs docker-env)

docker build -t local/ollivelogs-web:0.1.0          -f apps/web/Dockerfile .
docker build -t local/ollivelogs-chat-api:0.1.0     -f apps/chat-api/Dockerfile .
docker build -t local/ollivelogs-ingest-api:0.1.0   -f apps/ingest-api/Dockerfile .
docker build -t local/ollivelogs-log-consumer:0.1.0 -f workers/log-consumer/Dockerfile .
```

For a real cluster, push to a registry instead and override
`global.imageRegistry` + `global.imageTag`.

## 2. Install the chart

```bash
helm install ollivelogs infra/helm/ollivelogs \
  -f infra/helm/ollivelogs/values-minikube.yaml \
  --namespace ollivelogs \
  --create-namespace
```

The `values-minikube.yaml` overlay scales replicas down to 1, disables Loki
and the OTel collector, and turns off Ingress so you can reach the UI via
`kubectl port-forward`. For prod, drop the flag and tune
[`values.yaml`](../infra/helm/ollivelogs/values.yaml).

## 3. Create the secrets (out-of-band)

The chart references two Kubernetes Secrets it does **not** manage so keys
never live in `values.yaml` or git:

```bash
set -a; source .env; set +a

kubectl -n ollivelogs create secret generic ollive-session \
  --from-literal=SESSION_SECRET="${SESSION_SECRET}" \
  --from-literal=POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=RAW_ENCRYPTION_KEY="${RAW_ENCRYPTION_KEY}"

kubectl -n ollivelogs create secret generic ollive-providers \
  --from-literal=HF_TOKEN="${HF_TOKEN}" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY}" \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  --from-literal=GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
  --from-literal=DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY}" \
  --from-literal=XAI_API_KEY="${XAI_API_KEY}"

# Pick up the new secrets
kubectl -n ollivelogs rollout restart deployment
```

## 4. Apply database migrations

The chat-api expects the schema to exist before it serves requests. Run
Alembic inside any chat-api pod:

```bash
POD=$(kubectl -n ollivelogs get pod -l app.kubernetes.io/component=chat-api \
        -o jsonpath='{.items[0].metadata.name}')
kubectl -n ollivelogs exec "$POD" -- sh -c 'cd /app && alembic upgrade head'
```

## 5. Wait for pods, then port-forward

```bash
kubectl -n ollivelogs get pods -w   # Ctrl-C once everything is 1/1

kubectl -n ollivelogs port-forward svc/ollivelogs-web      13000:3000 &
kubectl -n ollivelogs port-forward svc/ollivelogs-chat-api 18001:8001 &
kubectl -n ollivelogs port-forward svc/ollivelogs-grafana  13001:3000 &
```

Then visit:

- Web UI: <http://localhost:13000>
- chat-api docs: <http://localhost:18001/docs>
- Grafana dashboards: <http://localhost:13001>

## Notes / gotchas

- **log-consumer cold start (~3-5 min).** The Presidio analyzer downloads the
  ~400 MB `en_core_web_lg` spaCy model on first boot. The chart sets a
  `startupProbe` with a 10-minute window so kubelet doesn't kill the pod
  during this download. Subsequent restarts use the cached wheel.
- **HPAs need metrics-server.** The chart enables HPAs by default for
  `chat-api` and `ingest-api`. On a bare minikube install you'll see
  "metrics API not available"; either install
  [`metrics-server`](https://github.com/kubernetes-sigs/metrics-server) or
  disable autoscaling in your values overlay.
- **Ingress is off in the minikube overlay.** For a real deployment, set
  `ingress.enabled=true`, `ingress.className` to your controller (Traefik,
  nginx, ALB, etc.) and `ingress.host` to your DNS name. The TLS issuer
  expects cert-manager to be running with a `ClusterIssuer` matching
  `ingress.tls.issuer`.
- **Migrations are not (yet) a Helm hook.** They run manually in step 4. A
  pre-upgrade `Job` is the next obvious improvement so installs are
  one-command from start to finish.

## Verified state (this install)

```
NAME                                       READY   STATUS    RESTARTS   AGE
ollivelogs-chat-api-858bb57879-m7j87       1/1     Running   5          34m
ollivelogs-clickhouse-0                    1/1     Running   0          35m
ollivelogs-grafana-0                       1/1     Running   0          35m
ollivelogs-ingest-api-769f4cdd76-cvc9q     1/1     Running   5          34m
ollivelogs-log-consumer-7777777599-zhhtp   1/1     Running   0          15m
ollivelogs-postgres-0                      1/1     Running   0          34m
ollivelogs-prometheus-0                    1/1     Running   0          35m
ollivelogs-redis-0                         1/1     Running   0          35m
ollivelogs-web-84c75db756-qn55c            1/1     Running   0          34m
```

End-to-end streaming verified through the deployed chat-api:

```
POST /v1/conversations/<id>/messages  →  event: start
                                          event: token "hello"
                                          event: token " from"
                                          event: token " k"
                                          event: token "8s"
                                          event: done
```
