"use client";

import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { REASONS, sendReport } from "@/lib/abuse";
import styles from "./report.module.css";

export function PrefilledReportForm() {
  const params = useSearchParams();
  return <ReportForm initialUrl={params.get("url") ?? ""} />;
}

export function ReportForm({ initialUrl }: { initialUrl: string }) {
  const [url, setUrl] = useState(initialUrl);
  const [reason, setReason] = useState("");
  const [details, setDetails] = useState("");
  const [contact, setContact] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return setError("Paste the link to the site you're reporting.");
    if (!reason) return setError("Pick what's wrong with the site.");
    if (reason === "other" && !details.trim()) return setError("Tell us what's wrong with the site in the details.");
    setError(null);
    setSending(true);
    try {
      await sendReport({ url, reason, details, contact });
      setSent(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSending(false);
    }
  };

  if (sent) {
    return (
      <div className="panel" role="status">
        <h2>Thanks, we got your report.</h2>
        <p className="muted">
          We&apos;ll look at the site and take it down if it breaks the rules.
          {contact ? ` If we need more details, we'll email ${contact}.` : ""}
        </p>
        <button
          onClick={() => {
            setSent(false);
            setUrl("");
            setReason("");
            setDetails("");
          }}
        >
          Report another site
        </button>
      </div>
    );
  }

  return (
    <form className="panel form" onSubmit={submit} noValidate>
      <label className="field">
        <span>Link to the site</span>
        <input
          type="url"
          required
          maxLength={500}
          placeholder="https://spinsite.app/s/site-name/ or https://site-name.sites.spinsite.app/"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </label>
      <fieldset className={styles.reasons}>
        <legend>What&apos;s wrong with it?</legend>
        {REASONS.map((r) => (
          <label key={r.value} className={styles.reason}>
            <input
              type="radio"
              name="reason"
              value={r.value}
              checked={reason === r.value}
              onChange={() => setReason(r.value)}
            />
            <strong>{r.label}</strong>
            <span>{r.hint}</span>
          </label>
        ))}
      </fieldset>
      <label className="field">
        <span>Details {reason === "other" ? "(required)" : "(optional)"}</span>
        <textarea
          rows={4}
          maxLength={2000}
          placeholder="What does the site do? Which page? What does it ask for?"
          value={details}
          onChange={(e) => setDetails(e.target.value)}
        />
      </label>
      <label className="field">
        <span>Your email (optional, only if we may contact you about this report)</span>
        <input
          type="email"
          maxLength={320}
          placeholder="you@example.com"
          value={contact}
          onChange={(e) => setContact(e.target.value)}
        />
      </label>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div>
        <button className="primary" type="submit" disabled={sending}>
          {sending ? "Sending…" : "Send report"}
        </button>
      </div>
    </form>
  );
}
