import { Dialog, DialogFooter } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { formatDateTime } from "../../lib/format";
import { OutcomeBadge } from "../tests/RunView";
import type { ReportItem } from "./reportsApi";

// The decision log's placeholders: [RGX-EMAIL], [masked: RGX-CLOUD-KEY, ...] and [encoded value].
const CODE = "[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*";
const MASK = new RegExp(`(\\[(?:masked: )?${CODE}(?:, ${CODE})*\\]|\\[encoded value\\])`);
const AGENT_LABELS: Record<ReportItem["control_agent_status"], string> = {
  skipped: "Not needed", pass: "Passed", blocked: "Found a violation", modified: "Rewrote the text", error: "Failed",
};

/** Masked text, with each mask set apart so a reader sees what was removed and by which policy. */
export function MaskedText({ text }: { text: string }) {
  return (
    <>
      {text.split(MASK).map((part, index) => (index % 2 ? <mark key={index} className="mask">{part.slice(1, -1)}</mark> : part))}
    </>
  );
}

export function RecordDialog({ item, onClose }: { item: ReportItem; onClose: () => void }) {
  return (
    <Dialog
      title={`${item.outcome === "blocked" ? "Blocked" : "Edited"} ${item.direction}`}
      description={<><time dateTime={item.ts}>{formatDateTime(item.ts)}</time> · {item.employee}, team {item.team}</>}
      wide
      onClose={onClose}
    >
      <div className="dialog-body">
        <dl className="policy-facts record-facts">
          <div><dt>Outcome</dt><dd><OutcomeBadge outcome={item.outcome} /></dd></div>
          <div><dt>Model</dt><dd className="mono">{item.model}</dd></div>
          <div><dt>Control agent</dt><dd>{AGENT_LABELS[item.control_agent_status]}</dd></div>
          <div><dt>Policy version</dt><dd>{item.policy_version}</dd></div>
          <div className="span-all"><dt>Request ID</dt><dd className="mono">{item.request_id}</dd></div>
        </dl>

        <section aria-labelledby="record-reasoning">
          <h3 id="record-reasoning" className="section-title">Why</h3>
          {item.policies.length ? (
            <ul className="reasoning-list">
              {item.policies.map((policy) => (
                <li key={policy.code}>
                  <code className="code-tag">{policy.code}</code>
                  <span className={`badge ${policy.action === "block" ? "is-danger" : "is-warning"}`}>{policy.action}</span>
                  <span>{policy.reasoning ?? "Violated."}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="field-hint">
              No policy fired: the control agent failed, and the gateway blocks rather than letting unchecked text through.
            </p>
          )}
        </section>

        <section aria-labelledby="record-content">
          <h3 id="record-content" className="section-title">{item.direction === "prompt" ? "Prompt" : "Model response"}</h3>
          <pre className="record-content"><MaskedText text={item.source_masked} /></pre>
          <p className="field-hint">
            Values any regex policy looks for, and long encoded values such as keys, were masked before this record
            was written. A piece that hides a value in an encoded form is withheld whole. The original text is not stored.
          </p>
        </section>

        {item.rewritten_masked !== null && (
          <section aria-labelledby="record-rewritten">
            <h3 id="record-rewritten" className="section-title">Sent instead, after the control agent's rewrite</h3>
            <pre className="record-content"><MaskedText text={item.rewritten_masked} /></pre>
          </section>
        )}
      </div>
      <DialogFooter><Button onClick={onClose}>Close</Button></DialogFooter>
    </Dialog>
  );
}
