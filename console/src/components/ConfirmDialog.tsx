import { useState, type ReactNode } from "react";
import { errorMessage } from "../lib/api";
import { Icon } from "./Icon";
import { Button } from "./ui/Button";
import { Dialog, DialogFooter } from "./ui/Dialog";

type ConfirmDialogProps = {
  title: string;
  description: ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  /** Runs the action; the caller closes the dialog when it succeeds. */
  onConfirm: () => Promise<void>;
  onClose: () => void;
};

export function ConfirmDialog({ title, description, confirmLabel, destructive, onConfirm, onClose }: ConfirmDialogProps) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function confirm() {
    setPending(true);
    setError(null);
    try {
      await onConfirm();
    } catch (reason) {
      setError(errorMessage(reason));
      setPending(false);
    }
  }

  return (
    <Dialog title={title} description={description} onClose={() => { if (!pending) onClose(); }}>
      {error && (
        <div className="dialog-body">
          <p className="notice is-error" role="alert"><Icon name="alert" />{error}</p>
        </div>
      )}
      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={pending} data-autofocus>Cancel</Button>
        <Button variant={destructive ? "destructive" : "default"} onClick={() => void confirm()} disabled={pending}>
          {pending ? "Working…" : confirmLabel}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
