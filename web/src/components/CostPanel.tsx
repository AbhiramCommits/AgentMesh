import type { RunDetail } from "../api/types";
import { costSummary } from "../lib/cost";
import styles from "../styles.module.css";

export function CostPanel({ run }: { run: RunDetail }) {
  const cost = costSummary(run.cost);
  const cells: Array<[string, string]> = [
    ["Total tokens", cost.total],
    ["Prompt tokens", cost.prompt],
    ["Completion tokens", cost.completion],
    ["Model latency", cost.latency],
    ["Wall clock", cost.wallClock],
    ["Tool calls", String(cost.toolCalls)],
  ];
  return (
    <div className={styles.card}>
      <h3 style={{ marginTop: 0 }}>Cost</h3>
      <div className={styles.costGrid}>
        {cells.map(([label, value]) => (
          <div key={label} className={styles.costCell}>
            <div className={styles.subtle}>{label}</div>
            <div className={styles.costValue}>{value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
