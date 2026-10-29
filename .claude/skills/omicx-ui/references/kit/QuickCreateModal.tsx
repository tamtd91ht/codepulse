import type { FormEvent, InputHTMLAttributes, ReactNode } from "react";
import { useEffect, useId, useRef } from "react";
import { Button } from "./Button";
import { cx } from "./cx";

/**
 * Quick-create in a native <dialog>: the browser traps focus, closes on Escape and draws the
 * backdrop. Show only required and most-used fields; `more` renders the "show full form"
 * disclosure on the left of the footer.
 */
export function QuickCreateModal({
  open,
  title,
  onClose,
  onSubmit,
  submitting = false,
  submitLabel = "Thêm mới",
  closeLabel = "Đóng",
  more,
  children,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  onSubmit: (e: FormEvent<HTMLFormElement>) => void;
  submitting?: boolean;
  submitLabel?: string;
  closeLabel?: string;
  more?: ReactNode;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose(); // click on the backdrop
      }}
      className={cx(
        "m-auto max-h-[85vh] w-[468px] max-w-[calc(100vw-32px)] overflow-hidden rounded-dialog border border-tertiary bg-overlay p-0 text-primary shadow-popup",
        "backdrop:bg-black/50",
      )}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(e);
        }}
        className="flex max-h-[85vh] flex-col"
      >
        <header className="flex items-center justify-between px-6 pt-6 pb-4">
          <h2 id={titleId} className="font-semibold">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={closeLabel}
            className="flex size-7 items-center justify-center rounded-full bg-quaternary text-secondary hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            ×
          </button>
        </header>
        <div className="flex-1 overflow-y-auto px-6 pb-2">{children}</div>
        <footer className="flex items-center justify-between gap-2 px-6 pt-4 pb-6">
          <div>{more}</div>
          <div className="flex items-center gap-2">
            <Button variant="tertiary" size="md" onClick={onClose}>
              {closeLabel}
            </Button>
            <Button variant="create" size="md" type="submit" loading={submitting}>
              {submitLabel}
            </Button>
          </div>
        </footer>
      </form>
    </dialog>
  );
}

/** Label above, required marker in danger, optional prefix / suffix, error under the field. */
export function Field({
  label,
  required = false,
  error,
  prefix,
  suffix,
  className,
  ...input
}: InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  error?: string;
  prefix?: ReactNode;
  suffix?: ReactNode;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  return (
    <div className={cx("flex flex-col gap-1.5 pb-4", className)}>
      <label htmlFor={id} className="text-sm text-primary">
        {label}
        {required && <span className="text-danger">*</span>}
      </label>
      <div
        className={cx(
          "flex h-9 items-center gap-2 rounded-large border bg-surface px-4 focus-within:ring-2 focus-within:ring-accent",
          error ? "border-danger" : "border-tertiary",
        )}
      >
        {prefix && <span className="text-xs text-secondary">{prefix}</span>}
        <input
          id={id}
          required={required}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          placeholder="Nhập giá trị"
          className="h-full min-w-0 flex-1 bg-transparent text-sm text-primary placeholder:text-secondary focus:outline-none"
          {...input}
        />
        {suffix && <span className="text-secondary">{suffix}</span>}
      </div>
      {error && (
        <p id={errorId} className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
