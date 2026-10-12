import type { Run } from "../api/types";
import styles from "../styles.module.css";
import { StatusBadge } from "./StatusBadge";

interface Props {
  runs: Run[];
  selected: string | null;
  onSelect: (id: string) => void;
}

export function RunsList({ runs, selected, onSelect }: Props) {
  if (runs.length === 0) {
    return <div className={styles.subtle}>No runs yet. Start one.</div>;
  }
  return (
    <div className={styles.list}>
      {runs.map((run) => (
        <button
          key={run.id}
          className={`${styles.runItem} ${selected === run.id ? styles.runItemActive : ""}`}
          onClick={() => onSelect(run.id)}
        >
          <div className={styles.row} style={{ justifyContent: "space-between" }}>
            <strong>{run.workflow_name}</strong>
            <StatusBadge status={run.status} />
          </div>
          <div className={styles.subtle}>{run.id.slice(0, 8)}</div>
          <div className={styles.subtle}>{String(run.input?.request ?? "")}</div>
        </button>
      ))}
    </div>
  );
}
