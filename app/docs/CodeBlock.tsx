"use client";

import { useState } from "react";
import styles from "./docs.module.css";

// A command or config snippet with a copy button.
export default function CodeBlock({ code, label }: { code: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className={styles.code}>
      {label && <span className={styles.codeLabel}>{label}</span>}
      <pre>
        <code>{code}</code>
      </pre>
      <button
        className="small"
        onClick={async () => {
          await navigator.clipboard.writeText(code);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
