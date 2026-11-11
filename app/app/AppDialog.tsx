"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// In-app replacement for window.prompt / window.confirm, styled like the rest of the tool.
// <dialog>.showModal() gives a real modal: focus stays inside, Esc cancels, the page behind is inert.

export type DialogOptions = {
  title: string;
  message?: string;
  confirmLabel: string;
  danger?: boolean;
  /** Ask for text: the dialog resolves with what was typed. */
  input?: { label: string; defaultValue?: string; placeholder?: string; required?: boolean };
};

type Open = DialogOptions & { resolve: (value: string | null) => void };

/** ask() resolves with the typed text (or "" for a plain confirm) when confirmed, null when cancelled. */
export function useDialog() {
  const [open, setOpen] = useState<Open | null>(null);
  const ask = useCallback(
    (options: DialogOptions) => new Promise<string | null>((resolve) => setOpen({ ...options, resolve })),
    [],
  );
  const finish = (value: string | null) => {
    open?.resolve(value);
    setOpen(null);
  };
  const dialog = open ? <AppDialog key={open.title} options={open} onDone={finish} /> : null;
  return { ask, dialog };
}

function AppDialog({ options, onDone }: { options: DialogOptions; onDone: (value: string | null) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState(options.input?.defaultValue ?? "");

  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) el.showModal();
  }, []);

  const blocked = !!options.input?.required && !value.trim();

  return (
    <dialog
      ref={ref}
      className="app-dialog"
      aria-labelledby="app-dialog-title"
      onCancel={(e) => {
        e.preventDefault(); // Esc: close through onDone so the promise settles
        onDone(null);
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onDone(null); // a click on the backdrop cancels
      }}
    >
      <form
        method="dialog"
        onSubmit={(e) => {
          e.preventDefault();
          if (!blocked) onDone(options.input ? value.trim() : "");
        }}
      >
        <h2 id="app-dialog-title">{options.title}</h2>
        {options.message && <p className="app-dialog__message">{options.message}</p>}
        {options.input && (
          <label className="app-dialog__field">
            <span>{options.input.label}</span>
            <input
              autoFocus
              value={value}
              placeholder={options.input.placeholder}
              maxLength={100}
              onChange={(e) => setValue(e.target.value)}
              onFocus={(e) => e.target.select()} // typing replaces the suggested name
            />
          </label>
        )}
        <div className="app-dialog__actions">
          <button type="button" onClick={() => onDone(null)}>
            Cancel
          </button>
          <button type="submit" className={options.danger ? "danger-solid" : "primary"} disabled={blocked} autoFocus={!options.input}>
            {options.confirmLabel}
          </button>
        </div>
      </form>
    </dialog>
  );
}
