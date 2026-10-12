import styles from "../styles.module.css";
import type { RunStatus, StepStatus } from "../api/types";

const RUN_CLASS: Record<RunStatus, string> = {
  pending: styles.badgePending,
  running: styles.badgeRunning,
  completed: styles.badgeCompleted,
  failed: styles.badgeFailed,
  awaiting_human: styles.badgeAwaiting,
};

export function StatusBadge({ status }: { status: RunStatus | StepStatus }) {
  const cls =
    RUN_CLASS[status as RunStatus] ?? styles.badgePending;
  return <span className={`${styles.badge} ${cls}`}>{status.replace("_", " ")}</span>;
}
