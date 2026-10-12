import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { RunDetail } from "../api/types";
import styles from "../styles.module.css";

export function EscalationBanner({ run }: { run: RunDetail }) {
  const queryClient = useQueryClient();
  const [resolution, setResolution] = useState("Approved by on-call manager");
  const open = run.escalations.filter((e) => e.status === "open");

  const resolve = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) =>
      api.resolveEscalation(id, text),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run", run.id] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  if (run.status !== "awaiting_human") return null;

  return (
    <div className={styles.banner}>
      <strong>Awaiting human review</strong>
      {open.length === 0 ? <p className={styles.subtle}>No open escalations.</p> : null}
      {open.map((escalation) => (
        <div key={escalation.id}>
          <p className={styles.subtle}>{escalation.reason}</p>
          <div className={styles.row}>
            <input
              value={resolution}
              onChange={(event) => setResolution(event.target.value)}
              aria-label="resolution"
            />
            <button
              onClick={() =>
                resolve.mutate({ id: escalation.id, text: resolution })
              }
              disabled={resolve.isPending}
            >
              Resolve &amp; resume
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
