import type { RunDetail } from "../api/types";
import styles from "../styles.module.css";

interface Props {
  run: RunDetail;
  nodeId: string | null;
}

export function StepDrawer({ run, nodeId }: Props) {
  if (!nodeId) {
    return (
      <div className={styles.drawer}>
        <div className={styles.subtle}>Select a node to inspect its latest step.</div>
      </div>
    );
  }
  const steps = run.steps.filter((s) => s.node_id === nodeId);
  const step = steps[steps.length - 1];
  if (!step) {
    return (
      <div className={styles.drawer}>
        <div className={styles.subtle}>No step recorded for {nodeId}.</div>
      </div>
    );
  }
  return (
    <div className={styles.drawer}>
      <div className={styles.row} style={{ justifyContent: "space-between" }}>
        <strong>{nodeId}</strong>
        <span className={styles.subtle}>
          attempt {step.attempt} · {step.model_used ?? "—"}
        </span>
      </div>
      <p className={styles.subtle}>
        agent {step.agent_name ?? "—"} · {step.prompt_tokens + step.completion_tokens} tokens ·{" "}
        {step.latency_ms} ms
      </p>
      {step.error ? <div className={styles.banner}>{step.error}</div> : null}

      <h4>Output</h4>
      <pre>{JSON.stringify(step.output, null, 2)}</pre>

      <h4>Tool calls ({step.tool_calls.length})</h4>
      {step.tool_calls.map((call) => (
        <div key={call.id} className={styles.toolCall}>
          <div className={styles.row} style={{ justifyContent: "space-between" }}>
            <strong>{call.tool_name}</strong>
            <span className={styles.subtle}>
              {call.ok ? "ok" : "failed"}
              {call.timed_out ? " · timeout" : ""} · {call.latency_ms} ms
            </span>
          </div>
          <pre>{JSON.stringify(call.args, null, 2)}</pre>
          {call.error ? <pre>{call.error}</pre> : null}
        </div>
      ))}

      <h4>Retrieved chunks ({step.retrievals.length})</h4>
      {step.retrievals.map((chunk) => (
        <div key={chunk.id} className={styles.chunk}>
          <div className={styles.subtle}>score {chunk.score.toFixed(3)}</div>
          <div>{chunk.chunk_text?.slice(0, 240)}</div>
        </div>
      ))}
    </div>
  );
}
