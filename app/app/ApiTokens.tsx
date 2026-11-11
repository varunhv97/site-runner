"use client";

import { useEffect, useState } from "react";
import { createApiToken, fetchApiTokens, revokeApiToken, type ApiToken } from "@/lib/share";
import type { DialogOptions } from "./AppDialog";

const day = (t: number) => new Date(t * 1000).toLocaleDateString();

// Account API tokens for a CLI or MCP client. The secret is shown once, right after it's created.
export default function ApiTokens({
  ask,
  onError,
}: {
  ask: (options: DialogOptions) => Promise<string | null>;
  onError: (message: string) => void;
}) {
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [secret, setSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetchApiTokens().then(setTokens);
  }, []);

  const create = async () => {
    const name = await ask({
      title: "New API token",
      message: "A CLI or MCP client with this token can share, publish and delete your sites. It can't do admin work.",
      confirmLabel: "Create token",
      input: { label: "Name", placeholder: "e.g. laptop, CI", required: true },
    });
    if (name === null) return;
    try {
      const created = await createApiToken(name);
      setSecret(created.secret);
      setCopied(false);
      setTokens(await fetchApiTokens());
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const revoke = async (t: ApiToken) => {
    const ok = await ask({
      title: `Revoke "${t.name}"?`,
      message: "Clients using it stop working right away.",
      confirmLabel: "Revoke",
      danger: true,
    });
    if (ok === null) return;
    try {
      await revokeApiToken(t.id);
      setTokens(await fetchApiTokens());
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <details className="api-tokens">
      <summary className="muted">API tokens</summary>
      {secret && (
        <div className="api-token-secret">
          <p className="muted">Copy it now. It won&apos;t be shown again.</p>
          <code>{secret}</code>
          <button
            className="small"
            onClick={async () => {
              await navigator.clipboard.writeText(secret);
              setCopied(true);
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      )}
      {tokens.length > 0 && (
        <ul>
          {tokens.map((t) => (
            <li key={t.id}>
              <span>
                <strong>{t.name}</strong>
                <span className="muted">
                  …{t.hint} · {t.last_used_at ? `used ${day(t.last_used_at)}` : "never used"}
                </span>
              </span>
              <button className="small danger" onClick={() => revoke(t)}>
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
      <button className="small" onClick={create}>
        New token
      </button>
    </details>
  );
}
