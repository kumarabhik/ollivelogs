# Deploying OlliveLogs on k3s

This chart deploys the full OlliveLogs stack (web, chat-api, ingest-api, log-consumer, Postgres, Redis, ClickHouse, Prometheus, Loki, Grafana, OTel Collector) onto a single-VM k3s cluster. Same chart works on any vanilla Kubernetes — just override `ingress.className` and `global.storageClass`.

> Companion to [DESIGN.md](../../DESIGN.md) §11 and [docs/architecture.md](../../docs/architecture.md#5-deployment-topology--k3s-single-vm).

---

## 0. Prerequisites

| Need | Why |
| --- | --- |
| A Linux VM, ≥2 vCPU / 4 GB RAM / 60 GB disk | postgres + clickhouse + grafana + loki PVCs |
| Domain name pointed at the VM (e.g. `ollivelogs.example.com`) | Ingress + TLS |
| `kubectl` and `helm ≥ 3.13` locally, or installed on the VM | apply manifests |
| Provider keys you actually want wired (HF / OpenAI / Anthropic) | filled into a Kubernetes Secret below |

---

## 1. Install k3s + cert-manager (one-time, on the VM)

```bash
# k3s with Traefik (default)
curl -sfL https://get.k3s.io | sh -
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $(id -u):$(id -g) ~/.kube/config

# cert-manager for automatic Let's Encrypt TLS
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml

# ClusterIssuer (replace email)
cat <<'EOF' | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    email: you@example.com
    server: https://acme-v01.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: letsencrypt-prod-account
    solvers:
      - http01:
          ingress:
            class: traefik
EOF
```

---

## 2. Build and push images

The chart expects `ghcr.io/kumarabhik/ollivelogs-{web,chat-api,ingest-api,log-consumer}:0.1.0`. From the repo root:

```bash
export REGISTRY=ghcr.io/kumarabhik
export TAG=0.1.0

docker build -t $REGISTRY/ollivelogs-chat-api:$TAG     -f apps/chat-api/Dockerfile     .
docker build -t $REGISTRY/ollivelogs-ingest-api:$TAG   -f apps/ingest-api/Dockerfile   .
docker build -t $REGISTRY/ollivelogs-log-consumer:$TAG -f workers/log-consumer/Dockerfile .
docker build -t $REGISTRY/ollivelogs-web:$TAG          -f apps/web/Dockerfile          .

docker push $REGISTRY/ollivelogs-chat-api:$TAG
docker push $REGISTRY/ollivelogs-ingest-api:$TAG
docker push $REGISTRY/ollivelogs-log-consumer:$TAG
docker push $REGISTRY/ollivelogs-web:$TAG
```

---

## 3. Create the secrets out-of-band

We **never** commit secrets to the chart. Create them once before install:

```bash
kubectl create namespace ollivelogs

# Provider keys
kubectl -n ollivelogs create secret generic ollive-providers \
  --from-literal=HF_TOKEN="$HF_TOKEN" \
  --from-literal=OPENAI_API_KEY="$OPENAI_API_KEY" \
  --from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  --from-literal=GOOGLE_API_KEY="$GOOGLE_API_KEY" \
  --from-literal=DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
  --from-literal=XAI_API_KEY="$XAI_API_KEY"

# Session secret + db password
kubectl -n ollivelogs create secret generic ollive-session \
  --from-literal=SESSION_SECRET="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  --from-literal=RAW_ENCRYPTION_KEY="$(openssl rand -hex 16)"
```

---

## 4. Install the chart

From the repo root:

```bash
helm install ollive infra/helm/ollivelogs \
  -n ollivelogs --create-namespace \
  --set ingress.host=ollivelogs.example.com \
  --set ingress.tls.issuer=letsencrypt-prod
```

Watch the rollout:

```bash
kubectl -n ollivelogs get pods -w
kubectl -n ollivelogs get ingress
```

Expected steady-state pods (with default values):

```text
NAME                                       READY   STATUS    RESTARTS
ollive-chat-api-xxx                        1/1     Running   0
ollive-chat-api-xxx                        1/1     Running   0
ollive-ingest-api-xxx                      1/1     Running   0
ollive-ingest-api-xxx                      1/1     Running   0
ollive-log-consumer-xxx                    1/1     Running   0
ollive-log-consumer-xxx                    1/1     Running   0
ollive-web-xxx                             1/1     Running   0
ollive-web-xxx                             1/1     Running   0
ollive-postgres-0                          1/1     Running   0
ollive-redis-0                             1/1     Running   0
ollive-clickhouse-0                        1/1     Running   0
ollive-prometheus-0                        1/1     Running   0
ollive-loki-0                              1/1     Running   0
ollive-grafana-0                           1/1     Running   0
ollive-otel-collector-xxx                  1/1     Running   0
```

---

## 5. Verify

```bash
# Web console (browser): https://ollivelogs.example.com/
# chat-api OpenAPI:       https://ollivelogs.example.com/api/chat/docs
# ingest-api OpenAPI:     https://ollivelogs.example.com/api/ingest/docs
# Grafana:                https://ollivelogs.example.com/grafana

# Roundtrip a synthetic log
curl -X POST https://ollivelogs.example.com/api/ingest/v1/logs \
  -H 'Content-Type: application/json' \
  -d '[{"event_id":"'"$(uuidgen)"'","ts":"'"$(date -Iseconds)"'","conversation_id":"'"$(uuidgen)"'","client":"manual","sdk_version":"0.1.0","provider":"huggingface","model":"Qwen/Qwen2.5-72B-Instruct","status":"ok","timing":{"latency_ms":420,"ttft_ms":120},"usage":{"prompt_tokens":12,"completion_tokens":24,"total_tokens":36},"cost_usd":0.0001}]'
```

Within ~1s, the row should appear in ClickHouse:

```bash
kubectl -n ollivelogs exec ollive-clickhouse-0 -- \
  clickhouse-client -q "SELECT count() FROM ollivelogs.inference_logs"
```

---

## 6. Upgrades

```bash
# Re-tag, re-push, then:
helm upgrade ollive infra/helm/ollivelogs -n ollivelogs \
  --set global.imageTag=0.2.0
```

Rollouts are zero-downtime (RollingUpdate, `maxUnavailable=0` on the log-consumer so the stream is never stranded).

Rollback:

```bash
helm rollback ollive 1 -n ollivelogs
```

---

## 7. Operational notes

- **Cancel signal**: chat-api sets a Redis key with a 5-minute TTL. If a pod is terminated mid-stream, the next pod (or a retried request) sees the same flag and short-circuits — no orphan provider streams.
- **Log consumer drain**: `terminationGracePeriodSeconds: 45` + a `preStop` sleep + worker stop hook ensure in-flight batches `XACK` before exit. New pods resume from the consumer group offset.
- **PVCs are not auto-grown**. If ClickHouse fills its 50Gi, edit the PVC manually (k3s `local-path` supports expansion) or bump `clickhouse.storage` and apply.
- **Backups**: take logical Postgres dumps via `kubectl exec ollive-postgres-0 -- pg_dump -U ollive ollivelogs` on a CronJob (not shipped by default — out of scope for the demo).
- **TLS** is automatic via cert-manager once DNS is correct. First request may 502 for 30–60s while ACME completes.

---

## 8. Uninstall

```bash
helm uninstall ollive -n ollivelogs
kubectl delete ns ollivelogs   # also drops PVCs — destructive
```

To keep data, delete the namespace **after** running `helm uninstall` and migrating PVCs out, or set `storageClass: "manual"` and pre-create PVs.
