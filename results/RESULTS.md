# AgentMesh Benchmark Results

_Generated 2026-10-12T17:33:33.046548+00:00._

## Environment

- Platform: macOS-27.0.1-arm64-arm-64bit
- CPU: arm (8 cores)
- Python: 3.11.14
- Postgres: PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2) on aarch64-unknown-linux-gnu, compiled by gcc (Debian 12.2.0-14+deb12u1) 12.2.0, 64-bit
- Provider: fake (deterministic, offline)
- Config: runs=200, recovery=100, policies=60, quick=False

## 1. Throughput

| Concurrency | Runs | Wall (s) | Runs/sec | p50 (ms) | p95 (ms) | p99 (ms) | DB commits |
|---|---|---|---|---|---|---|---|
| 1 | 200 | 6.538 | 30.59 | 30.01 | 40.24 | 44.59 | 4000 |
| 8 | 200 | 2.738 | 73.05 | 99.75 | 123.75 | 138.6 | 4000 |
| 32 | 200 | 3.058 | 65.41 | 423.58 | 659.21 | 680.13 | 4000 |

## 2. Crash Recovery

| Metric | Value |
|---|---|
| Runs (crash at each node) | 100 |
| Resume success rate | 1.0 |
| Mean nodes re-executed after resume | 1.0 |
| Max nodes re-executed | 1 |
| Mean wasted work saved vs full restart | 37.5% |

## 3. Failure Policies

| Injected rate | Runs | Completion | Escalation | Retries | Fallbacks | Compensations |
|---|---|---|---|---|---|---|
| 0.0 | 60 | 1.0 | 0.0 | 0 | 0 | 0 |
| 0.1 | 60 | 0.8 | 0.2 | 35 | 9 | 30 |
| 0.3 | 60 | 0.6667 | 0.3333 | 53 | 14 | 36 |

## 4. Retrieval

| Metric | Value |
|---|---|
| Labeled queries | 32 |
| Recall@1 | 0.96875 |
| Recall@5 | 1.0 |
| MRR | 0.984375 |
| Mean search latency (ms) | 1.248 |

## 5. Health-Based Routing

| Metric | Value |
|---|---|
| Runs before marking unhealthy | 20 |
| Runs after marking unhealthy | 20 |
| Fraction of plan steps routed away | 1.0 |

## 6. Test Suite

| Metric | Value |
|---|---|
| Tests passed | 50 |
| Tests skipped | 0 |
| Coverage (agentmesh, excl. api) | 90.3% |
