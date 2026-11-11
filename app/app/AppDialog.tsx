"use client";

import { useCallback, useEffect, useRef, useState } from "react";

// In-app replacement for window.prompt / window.confirm, styled like the rest of the tool.
// <dialog>.showModal() gives a real modal: focus stays inside, Esc cancels, the page behind is inert.

export type DialogOptions = {
  title: string;
  message?: string;
  /** A list under the message, e.g. the files a warning is about. */
  items?: string[];
  confirmLabel: string;
  danger?: boolean;
  /** Ask for text: the dialog resolves with what was typed. */
  input?: {
    label: string;
    defaultValue?: string;
    placeholder?: string;
    required?: boolean;
    /** Shown in front of the field, e.g. the start of a link. */
    prefix?: string;
    /** Turns what is typed into the stored value (e.g. lower-case a link name). */
    normalize?: (value: string) => string;
    /** Checked as you type (debounced) and before confirming: an error message, or null when fine. */
    validate?: (value: string) => Promise<string | null>;
    /** Pick one of these instead of typing (a dropdown). */
    choices?: string[];
  };
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
  const input = options.input;
  const [value, setValue] = useState(input?.defaultValue ?? "");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const check = useRef(0);

  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) el.showModal();
  }, []);

  // live check while typing, a moment after the last keystroke; stale answers are ignored
  useEffect(() => {
    if (!input?.validate) return;
    const id = ++check.current;
    const timer = setTimeout(async () => {
      const problem = await input.validate!(value.trim());
      if (id === check.current) setError(problem);
    }, 300);
    return () => clearTimeout(timer);
  }, [value, input]);

  const blocked = (!!input?.required && !value.trim()) || !!error || checking;

  async function confirm() {
    if (input?.validate) {
      setChecking(true);
      const problem = await input.validate(value.trim());
      setChecking(false);
      if (problem) return setError(problem);
    }
    onDone(input ? value.trim() : "");
  }

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
          if (!blocked) confirm();
        }}
      >
        <h2 id="app-dialog-title">{options.title}</h2>
        {options.message && <p className="app-dialog__message">{options.message}</p>}
        {options.items && options.items.length > 0 && (
          <ul className="app-dialog__items">
            {options.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        )}
        {input && (
          <label className="app-dialog__field">
            <span>{input.label}</span>
            <span className={`app-dialog__control${error ? " has-error" : ""}`}>
              {input.prefix && <span className="app-dialog__prefix">{input.prefix}</span>}
              {input.choices ? (
                <select autoFocus value={value} onChange={(e) => setValue(e.target.value)}>
                  {input.choices.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              ) : (
              <input
                autoFocus
                value={value}
                placeholder={input.placeholder}
                maxLength={100}
                aria-invalid={!!error}
                aria-describedby="app-dialog-error"
                onChange={(e) => setValue(input.normalize ? input.normalize(e.target.value) : e.target.value)}
                onFocus={(e) => e.target.select()} // typing replaces the suggestion
              />
              )}
            </span>
            <span id="app-dialog-error" className="app-dialog__error" role="status">
              {error ?? ""}
            </span>
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
