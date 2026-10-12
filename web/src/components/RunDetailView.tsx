import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { RunDetail } from "../api/types";
import { CostPanel } from "./CostPanel";
import { Dag } from "./Dag";
import { EscalationBanner } from "./EscalationBanner";
import { StatusBadge } from "./StatusBadge";
import { StepDrawer } from "./StepDrawer";
import styles from "../styles.module.css";

const TERMINAL = new Set(["completed", "failed", "awaiting_human"]);

export function RunDetailView({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [replayNode, setReplayNode] = useState<string>("");

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.getRun(runId),
    refetchInterval: (query) =>
      query.state.data && TERMINAL.has(query.state.data.status) ? false : 1000,
  });

  // Live updates over SSE; falls back to the polling above if SSE is unavailable.
  useEffect(() => {
    const source = new EventSource(api.eventsUrl(runId));
    const invalidate = () => queryClient.invalidateQueries({ queryKey: ["run", runId] });
    source.addEventListener("step", invalidate);
    source.addEventListener("run", invalidate);
    source.addEventListener("end", invalidate);
    return () => source.close();
  }, [runId, queryClient]);

  const resume = useMutation({
    mutationFn: () => api.resumeRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  const replay = useMutation({
    mutationFn: (fromNode: string) => api.replayRun(runId, fromNode),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["runs"] }),
  });

  if (run.isLoading) return <div className={styles.subtle}>Loading run…</div>;
  if (run.error) return <div className={styles.banner}>{String(run.error)}</div>;
  if (!run.data) return null;

  const detail: RunDetail = run.data;
  const nodes = detail.graph?.nodes.map((n) => n.id) ?? [];
  const canResume = detail.status === "failed" || detail.status === "running";
  const canReplay = detail.status === "completed" || detail.status === "failed";

  return (
    <div>
      <div className={styles.header}>
        <div>
          <h2 className={styles.title}>
            {detail.workflow_name} <StatusBadge status={detail.status} />
          </h2>
          <div className={styles.subtle}>
            run {detail.id.slice(0, 8)} · {detail.steps.length} steps
          </div>
        </div>
        <div className={styles.row}>
          <button
            className={styles.secondary}
            onClick={() => resume.mutate()}
            disabled={!canResume || resume.isPending}
          >
            Resume from checkpoint
          </button>
          <select value={replayNode} onChange={(e) => setReplayNode(e.target.value)}>
            <option value="">Replay from…</option>
            {nodes.map((node) => (
              <option key={node} value={node}>
                {node}
              </option>
            ))}
          </select>
          <button
            onClick={() => replayNode && replay.mutate(replayNode)}
            disabled={!canReplay || !replayNode || replay.isPending}
          >
            Replay
          </button>
        </div>
      </div>

      <EscalationBanner run={detail} />

      <div className={styles.grid}>
        <div className={styles.card}>
          <h3 style={{ marginTop: 0 }}>Workflow DAG</h3>
          <Dag run={detail} onSelect={setSelectedNode} />
        </div>
        <div>
          <CostPanel run={detail} />
          <StepDrawer run={detail} nodeId={selectedNode} />
        </div>
      </div>
    </div>
  );
}
