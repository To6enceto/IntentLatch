import { useId, useLayoutEffect, useRef, type ReactNode } from "react";
import { Icon } from "../Icon";
import { Button } from "./Button";

type DialogProps = {
  title: string;
  description?: ReactNode;
  onClose: () => void;
  children?: ReactNode;
  wide?: boolean;
};

/** A modal dialog. Render it only while open; unmounting returns focus to where it was. */
export function Dialog({ title, description, onClose, children, wide }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();

  useLayoutEffect(() => {
    const dialog = ref.current;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog?.showModal();
    // React does not render autoFocus as an attribute, so mark the target with data-autofocus.
    dialog?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    return () => {
      dialog?.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className={wide ? "dialog is-wide" : "dialog"}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
    >
      <header className="dialog-header">
        <h2 id={titleId}>{title}</h2>
        <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}><Icon name="close" /></Button>
      </header>
      {description && <div id={descriptionId} className="dialog-description">{description}</div>}
      {children}
    </dialog>
  );
}

export function DialogFooter({ children }: { children: ReactNode }) {
  return <footer className="dialog-footer">{children}</footer>;
}
