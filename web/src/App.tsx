import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type StartRunPayload } from "./api/client";
import { RunDetailView } from "./components/RunDetailView";
import { RunsList } from "./components/RunsList";
import styles from "./styles.module.css";

export default function App() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [workflowName, setWorkflowName] = useState("demo");
  const [request, setRequest] = useState("Which ticket categories drive the most open load?");
  const [crashAt, setCrashAt] = useState("");

  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => api.listRuns(),
    refetchInterval: 2000,
  });
  const workflows = useQuery({ queryKey: ["workflows"], queryFn: api.listWorkflows });

  const start = useMutation({
    mutationFn: (payload: StartRunPayload) => api.startRun(payload),
    onSuccess: (run) => {
      setSelected(run.id);
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  return (
    <div className={styles.app}>
      <aside className={styles.sidebar}>
        <h1 className={styles.title}>AgentMesh</h1>
        <div className={styles.subtle}>Multi-agent run inspector</div>

        <div className={styles.card} style={{ marginTop: 16 }}>
          <h3 style={{ marginTop: 0 }}>New run</h3>
          <div className={styles.list}>
            <select value={workflowName} onChange={(e) => setWorkflowName(e.target.value)}>
              {(workflows.data ?? [{ name: "demo", description: "", nodes: 0 }]).map((w) => (
                <option key={w.name} value={w.name}>
                  {w.name}
                </option>
              ))}
            </select>
            <textarea
              rows={3}
              value={request}
              onChange={(e) => setRequest(e.target.value)}
              aria-label="request"
            />
            <select value={crashAt} onChange={(e) => setCrashAt(e.target.value)}>
              <option value="">No crash injection</option>
              <option value="analyze">Crash at analyze</option>
              <option value="retrieve">Crash at retrieve</option>
            </select>
            <button
              onClick={() =>
                start.mutate({
                  workflow_name: workflowName,
                  input: { request },
                  crash_at_node: crashAt || null,
                })
              }
              disabled={start.isPending}
            >
              Start run
            </button>
            {start.error ? <div className={styles.subtle}>{String(start.error)}</div> : null}
          </div>
        </div>

        <h3>Runs</h3>
        <RunsList runs={runs.data ?? []} selected={selected} onSelect={setSelected} />
      </aside>

      <main className={styles.main}>
        {selected ? (
          <RunDetailView runId={selected} />
        ) : (
          <div className={styles.subtle}>Start a run or select one from the list.</div>
        )}
      </main>
    </div>
  );
}
