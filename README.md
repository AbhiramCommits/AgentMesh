# AgentMesh

AgentMesh is a **multi-agent workflow orchestrator with durable state and
failure recovery**. It runs a declarative workflow graph — planner → specialist
agents → verifier — through a supervisor that routes each node to an agent by
**capability and live health**. Agents call typed tools through an MCP-style
registry (SQL, HTTP, file sandbox) with per-tool argument validation and
timeouts. Every step transition is committed to PostgreSQL, so a crashed run
**resumes from its last checkpoint** instead of restarting. Failure-recovery
policies cover bounded retries with backoff, fallback model routing,
compensating actions, and escalation to a human review queue. A retrieval step
grounds answers in a pgvector document store with chunk-level provenance, and a
React + TypeScript inspector renders the live DAG, tool calls, cost, and replay.

The whole repository runs **offline with no API key** thanks to a deterministic
`FakeModelProvider` and hash-based fake embedder.

## Architecture

```
                     ┌──────────────────────────┐
                     │  workflows/*.yaml        │
                     └────────────┬─────────────┘
                                  ▼
                     ┌──────────────────────────┐
                     │ WorkflowGraph (validated)│
                     └────────────┬─────────────┘
                                  ▼
        ┌────────────────────────────────────────────────────┐
        │ Executor state machine  (checkpoint per node)      │
        │  node → supervisor → agent → persist → checkpoint  │
        └───────┬───────────────────────┬────────────────────┘
                ▼                       ▼
      ┌──────────────────┐    ┌──────────────────────┐
      │ Supervisor       │    │ Recovery policies    │
      │ capability+health│    │ retry / fallback /   │
      └────────┬─────────┘    │ compensate / escalate│
               ▼              └──────────┬───────────┘
      ┌──────────────────┐               │
      │ Agent registry   │               │
      │ planner/retriever│               │
      │ analyst/verifier │               │
      │ summarizer       │               │
      └────────┬─────────┘               │
               ▼                         │
      ┌──────────────────────────┐       │
      │ Tool registry (MCP)      │◄──────┘
      │ sql / http / sandbox     │
      └────────┬─────────────────┘
               ▼
   ┌───────────────────────┐   ┌──────────────────────────┐
   │ PostgreSQL + pgvector │   │ Redis                    │
   │ runs steps tool_calls │   │ agent heartbeats         │
   │ checkpoints retrievals│   │ human-escalation queue    │
   │ escalations docs chks │   └──────────────────────────┘
   └───────────┬───────────┘
               ▼
   ┌───────────────────────┐   ┌──────────────────────────┐
   │ FastAPI  /runs /events│──►│ React + TS run inspector │
   │ SSE, MCP /mcp         │   │ DAG, cost, replay, resume│
   └───────────────────────┘   └──────────────────────────┘
```

A full component/file table and the graph schema live in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### State machine

```text
run:   pending → running → completed
                       ↘ failed        → (resume) → running → completed
                       ↘ awaiting_human → (resolve) → running → completed
step:  pending → running → completed
                        ↘ failed        (retried with fallback model)
```

A `checkpoints` row is committed after every completed node; `resume_run` loads
the newest checkpoint and continues from the next node, never re-executing
committed work. A crash mid-node leaves a `running` step but no checkpoint, so
only that node is retried.

## Requirements

- Python 3.11
- Node 20+ (frontend)
- Docker + Docker Compose (Postgres+pgvector and Redis)

No API key is required: the default provider is the deterministic fake.

## Run it

```bash
# 1. Postgres (pgvector) + Redis on host ports 5433 / 6380
docker compose up -d postgres redis

# 2. Python env
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 3. Schema + seed corpus
.venv/bin/python scripts/init_db.py
.venv/bin/python scripts/seed_docs.py

# 4. Run a workflow end to end
.venv/bin/python scripts/demo.py                  # 4-node demo
.venv/bin/python scripts/demo.py field_ops_revise # 6-node revise loop

# 5. Tests and benchmarks
.venv/bin/pytest -q
.venv/bin/python scripts/benchmark.py

# 6. Full stack (API on :8000, UI on :5173)
docker compose up --build
```

`make` targets wrap the same commands: `make dev-up`, `make init-db`,
`make seed`, `make demo`, `make bench`, `make test`, `make web-test`.

### Frontend

```bash
cd web
npm install
npm run dev      # Vite dev server on :5173
npm test         # Vitest
npm run build    # tsc + vite build
```

The UI reads `VITE_API_BASE` (default `http://localhost:8000`).

### Switching to a real OpenAI-compatible provider

Copy `.env.example` to `.env` and set:

```dotenv
AGENTMESH_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1   # or any compatible endpoint
AGENTMESH_MODEL=gpt-4o-mini
AGENTMESH_FALLBACK_MODEL=gpt-4o
```

Embeddings switch to the OpenAI embedding API with the same flag; the fake
embedder (256-dim) and model provider are used otherwise.

## Results

All numbers below are measured by `scripts/benchmark.py` on the machine
described. See [`results/RESULTS.md`](results/RESULTS.md) and
[`results/benchmark.json`](results/benchmark.json) for the raw data.

**Environment.** macOS 27.0.1 (Apple Silicon, arm64, 8 cores), Python 3.11.14,
PostgreSQL 16.15 with pgvector 16 (`pgvector/pgvector:pg16` in Docker), Redis 7,
deterministic `FakeModelProvider` (offline). No integration suites were skipped —
Postgres and Redis were available via Docker and every test ran.

**1. Throughput** — 200 runs of the 4-node demo graph at each concurrency.

| Concurrency | Runs/sec | p50 (ms) | p95 (ms) | p99 (ms) | DB commits |
|---|---|---|---|---|---|
| 1 | 30.59 | 30.01 | 40.24 | 44.59 | 4000 |
| 8 | 73.05 | 99.75 | 123.75 | 138.60 | 4000 |
| 32 | 65.41 | 423.58 | 659.21 | 680.13 | 4000 |

**2. Crash recovery** — crashes injected at every node across 100 runs.

| Metric | Value |
|---|---|
| Resume success rate | 1.0 (100/100) |
| Mean nodes re-executed after resume | 1.0 (proves no full restart) |
| Max nodes re-executed | 1 |
| Mean wasted work saved vs. full restart | 37.5% |

**3. Failure policies** — tool failures and provider errors injected at rates.

| Injected rate | Completion | Escalation | Retries | Fallbacks | Compensations |
|---|---|---|---|---|---|
| 0.0 | 1.000 | 0.000 | 0 | 0 | 0 |
| 0.1 | 0.800 | 0.200 | 35 | 9 | 30 |
| 0.3 | 0.667 | 0.333 | 53 | 14 | 36 |

**4. Retrieval** — 32 labeled queries over the seed corpus (pgvector, HNSW).

| Metric | Value |
|---|---|
| Recall@1 | 0.969 |
| Recall@5 | 1.000 |
| MRR | 0.984 |
| Mean search latency | 1.248 ms |

**5. Health-based routing** — primary planner marked unhealthy mid-suite.

| Metric | Value |
|---|---|
| Fraction of plan steps routed away from the unhealthy agent | 1.0 |

**6. Test suite.**

| Metric | Value |
|---|---|
| Tests passed | 50 |
| Coverage (`agentmesh`, excluding the FastAPI layer) | 90.3% |

## Design notes

- **Checkpoint per node.** Committing one checkpoint transaction after each
  completed node makes recovery cheap and simple: the last checkpoint is the
  source of truth, so resume never replays committed work. The measured cost is
  exactly one extra node execution after a crash.
- **Capability + health routing.** The supervisor filters agents by advertised
  capability, skips stale-heartbeat agents always, and skips low-success-rate
  agents when a healthier candidate exists — but still uses a degraded agent as a
  last resort so retries can make progress. Heartbeats and outcomes are mirrored
  to Redis for cross-process visibility.
- **Compensation vs. escalation.** Compensation fires after a node exhausts its
  retries: every successful non-idempotent tool call for that node is undone in
  reverse order, so half-finished side effects are cleaned up. Escalation fires
  instead when a node is marked `on_failure: escalate`; it writes an escalation
  row, queues it in Redis, and parks the run at `awaiting_human`. Resolving the
  escalation records a human decision as a checkpoint and resumes.
- **Typed tools over MCP.** Tools validate arguments against a Pydantic model
  before execution, enforce a `wait_for` timeout, and record every attempt
  (including timeouts and validation failures) as a `tool_calls` row. The same
  registry is exposed over MCP JSON-RPC so external clients see identical
  behaviour.
- **Offline determinism.** The fake provider and feature-hashing embedder make
  every test and the entire benchmark reproducible without network access; the
  embedder deliberately preserves lexical similarity so retrieval quality is
  measurable (recall@1 ≈ 0.97).

## Known limitations

- The executor is single-process. Redis mirrors heartbeats and the escalation
  queue, but the executor does not yet lease runs across workers (no
  distributed lock), so two processes could run the same run concurrently.
- Model fallback advances one model per attempt; there is no cost- or
  latency-aware model selection.
- The human-resolution path skips the failed node and resumes downstream; it does
  not re-run the node with the human's guidance.
- Retrieval uses a deterministic hash embedder offline; with the real provider it
  uses OpenAI embeddings. There is no reranking step.
- SSE is a polling-backed stream (0.4 s) rather than a push bus; it is simple and
  robust but not optimized for very high event rates.

## Next steps

- Run leasing with `SELECT ... FOR UPDATE SKIP LOCKED` plus a Redis lock so runs
  can be executed by a pool of workers.
- A proper event bus (Redis Streams / Postgres `LISTEN/NOTIFY`) for push SSE.
- Cost-aware model routing and a second-chance verifier using a stronger model.
- Reranking and hybrid (lexical + vector) retrieval for higher recall.
- A typed escalation workflow that feeds human guidance back into re-execution.

## License

MIT — see [`LICENSE`](LICENSE).
