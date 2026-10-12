# AgentMesh Architecture

This document is the engineering reference: the workflow YAML schema, the
component map, the durable state machine, and how to extend AgentMesh with a new
agent or tool.

## Execution model

```
workflow YAML ──► WorkflowGraph ──► Executor (state machine)
                                      │
                     ┌────────────────┼─────────────────┐
                     ▼                ▼                 ▼
                 Supervisor       Agent.run()      Policies
             (capability+health)  (planner/… )  (retry/fallback/
                     │                             compensate/escalate)
                     ▼
              AgentRegistry ──► ToolRegistry ──► MCP JSON-RPC
                                      │
                     ┌────────────────┴─────────────────┐
                     ▼                                  ▼
              PostgreSQL + pgvector                 Redis
        runs/steps/tool_calls/checkpoints        heartbeat state
        retrievals/escalations/documents/chunks  escalation queue
```

The executor owns the loop. After every node completes it serialises the whole
run state and writes one `checkpoints` row (one transaction) before advancing.
`resume_run` reads the newest checkpoint and continues from `current_node`, so
already-committed nodes are never re-executed. A crash mid-node leaves the step
row (status `running`) but no checkpoint, so the node is retried on resume.

## State machine

Run statuses (`runs.status`):

| Status | Meaning | Transitions to |
|---|---|---|
| `pending` | created, not started | `running` |
| `running` | executor is advancing nodes | `completed`, `failed`, `awaiting_human` |
| `awaiting_human` | an escalation is open | `running` (after resolve), `completed` |
| `completed` | `current_node` is null | terminal |
| `failed` | unrecoverable node failure (or injected crash) | `running` (resume) |

Step statuses (`steps.status`):

| Status | Meaning |
|---|---|
| `pending` | row created, not started |
| `running` | agent executing (a crash leaves it here) |
| `completed` | agent returned a result |
| `failed` | attempt raised or verifier returned `fail` |
| `skipped` | reserved for conditional skips |

Checkpoints: exactly one `checkpoints` row per completed node execution, holding
`state_json` = `{completed_nodes, outputs, iterations, verdicts, visited,
current_node, run_input}`. `iterations` bounds revise loops.

## Workflow YAML schema

```yaml
name: demo                 # required; file is workflows/<name>.yaml
description: "..."         # optional
start: plan                # required; must name a node
max_iterations: 50         # optional; required to permit cycles unless a node
                           # in the cycle sets loop_limit > 0
nodes:
  - id: plan               # required, unique
    capability: plan       # required; supervisor routes by capability
    agent: planner         # optional; prefer this agent for the node
    description: "..."     # optional
    models: [fake-model, fake-model-alt]   # optional; fallback model routing
    next: [retrieve]       # unconditional successor(s)
    conditional:           # verdict-keyed successors (verifier branches)
      pass: end            # "end"/"__end__"/"$end"/"" finish the run
      revise: analyze
      fail: end
    retry:
      max_attempts: 3      # bounded attempts (1..10)
      backoff_base_s: 0.01 # exponential base
      backoff_max_s: 0.1   # cap
      jitter: 0.1          # uniform jitter fraction (0..1)
    on_failure: escalate   # or "fail"
    compensate_on_failure: true   # run compensating actions on failure
    loop_limit: 0          # per-node execution bound for cycles
    params: {}             # node-specific config (e.g. retrieve k, revise_once)
```

Validation (at load): unique ids, non-empty capability, `start` exists, all
`next`/`conditional` targets exist, and every cycle is bounded.

## Failure-recovery policies

Handled in `agentmesh/engine/policies.py`, applied per node:

1. **Bounded retries** with exponential backoff and jitter (`compute_backoff`).
2. **Fallback model routing**: attempt *i* uses `models[min(i-1, len-1)]`, so a
   provider error or verifier `fail` advances the model list; the model used is
   recorded in `steps.model_used`.
3. **Compensating actions**: after a node exhausts retries, every successful
   non-idempotent tool call for that node is compensated in reverse order
   (`compensate_step`). The file sandbox write declares such a compensation.
4. **Escalation**: when a node with `on_failure: escalate` exhausts its budget,
   an `escalations` row is written, a Redis queue entry is pushed, the run moves
   to `awaiting_human`, and execution stops. `resolve_escalation` records the
   resolution, writes a checkpoint past the failed node, and resumes.

## How to add an agent

1. Implement the protocol in `agentmesh/agents/`:

   ```python
   class MyAgent:
       name = "my-agent"
       capabilities: tuple[str, ...] = ("my-capability",)

       async def run(self, ctx: RunContext) -> StepResult:
           response = await ctx.provider.complete(messages, model=ctx.model)
           return StepResult(output={"...": ...}, model_used=response.model,
                             prompt_tokens=response.prompt_tokens,
                             completion_tokens=response.completion_tokens)
   ```

2. Register it in `build_default_agent_registry()` in
   `agentmesh/engine/registry.py`.
3. Reference the capability from a node in a workflow YAML.

`RunContext` exposes `run_input`, upstream `outputs`, `iterations`, the model
provider, the tool registry, `step_id`, and a per-run `sandbox_dir`.

## How to add a tool

1. Define a Pydantic argument model and an async handler in
   `agentmesh/tools/`, then build a `ToolSpec`:

   ```python
   TOOL_SPEC = ToolSpec(
       name="my_tool",
       description="...",
       args_model=MyArgs,
       handler=my_handler,
       timeout_s=10.0,
       idempotent=False,
       compensate=my_compensate,  # optional
   )
   ```

2. Register it in `build_default_registry()` in `agentmesh/tools/__init__.py`.

Tools are automatically exposed over the MCP JSON-RPC endpoint (`/mcp`,
methods `tools/list` and `tools/call`) and through `GET /tools`. Every
invocation — success, error, or timeout — writes a `tool_calls` row.

## Component map

| Component | File |
|---|---|
| Settings | `agentmesh/config.py` |
| Engine/session | `agentmesh/db.py` |
| ORM models | `agentmesh/models.py` |
| API schemas | `agentmesh/schemas.py` |
| Workflow graph | `agentmesh/engine/graph.py` |
| Agent registry + health | `agentmesh/engine/registry.py` |
| Durable executor | `agentmesh/engine/executor.py` |
| Recovery policies | `agentmesh/engine/policies.py` |
| Shared state helpers | `agentmesh/engine/store.py` |
| Runtime bundle | `agentmesh/engine/runtime.py` |
| Tool registry | `agentmesh/tools/base.py` |
| SQL / HTTP / sandbox tools | `agentmesh/tools/{sql_query,http_fetch,file_sandbox}.py` |
| MCP JSON-RPC | `agentmesh/tools/mcp_server.py` |
| Ingest / search / labels | `agentmesh/retrieval/{ingest,search,labels}.py` |
| Providers | `agentmesh/llm/{provider,embeddings}.py` |
| Agents | `agentmesh/agents/*.py` |
| API + SSE | `agentmesh/api/main.py` |
| Run inspector | `web/src/**` |
| Benchmark | `scripts/benchmark.py` |
