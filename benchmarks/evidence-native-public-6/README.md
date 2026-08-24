# Evidence-native public-6 extension

This is a low-bandwidth extension to the existing Evidence-native GitHub PR
matrix. It adds six real public PRs without cloning repositories or uploading
raw source packs to JYL.

## Cases

| Case | Project | Focus | Status boundary |
|---|---|---|---|
| `kubernetes-140407` | Kubernetes | EventBroadcaster goroutine/memory growth and backpressure | Closed, unmerged candidate |
| `kubernetes-139850` | Kubernetes | Kubelet context cancellation retention | Merged |
| `prometheus-17895` | Prometheus | `sync.Pool` reference retention during WAL replay | Merged |
| `opentelemetry-python-5027` | OpenTelemetry Python | `TracerMetrics` / `ProxyMeterProvider` retention | Closed, unmerged candidate |
| `envoy-44448` | Envoy | StatName map/comparison CPU hot paths | Merged |
| `redis-15677` | Redis | Trailing-backslash parser bounds safety | Closed, unmerged candidate |

The manifest contains private oracle fields and synthetic runtime fixtures. The
runner writes those to the local `oracle/` directory and never includes them in
public packs or model messages.

## Local preparation

Run from the repository root. This fetches bounded GitHub REST responses and
writes all raw responses, compact packs, projections, and oracle files locally:

```bash
python scripts/run_github_pr_attribution_eval.py \
  --case-spec-file benchmarks/evidence-native-public-6/manifest.json \
  --cases kubernetes-140407,kubernetes-139850,prometheus-17895,opentelemetry-python-5027,envoy-44448,redis-15677 \
  --rounds 1 --low-bandwidth \
  --output-dir reports/eval/github-pr-public-6-v1
```

This command does not clone a repository, start Docker, contact JYL, or upload
anything. Use `--offline` after the first successful fetch to validate the
local cache without network access.

## Optional JYL run

Only after local preflight passes, import the compact projections once using a
case map and the protected JYL endpoint. Do not use raw packs. For a server with
limited upload budget, run one round first and keep `AGENT_UPLOAD_ARTIFACTS=0`
and `MINI_DROP_ANALYZER_UPLOAD=0` unless the test explicitly requires a real
Worker artifact.

The six cases are intentionally not a replacement for the existing 9x3. They
add parser safety, context lifecycle, pool retention, goroutine backpressure,
and C++ hot-path diversity. The three closed-but-unmerged cases are calibration
cases: the model should explain the mechanism while refusing to claim a shipped
production fix.

## Scoring

Use the same four 10-point dimensions as the 9x3 contract:

- mechanism attribution: 0-4;
- Evidence citation: 0-3;
- counterevidence and uncertainty: 0-2;
- impact boundary: 0-1.

Synthetic runtime data is directional wiring evidence only. A passing structural
run is not a production RCA or security-validation result.
