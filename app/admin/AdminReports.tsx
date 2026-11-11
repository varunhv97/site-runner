"use client";

import { useCallback, useEffect, useState } from "react";
import { useDialog } from "../app/AppDialog";
import { AbuseApiError, banAccount, fetchReports, setReportStatus, unpublishSite, type Report } from "@/lib/abuse";

const STATUSES = ["open", "actioned", "dismissed", "all"];

/** Review abuse reports (ADMIN_EMAILS only; the API answers 403 to everyone else). */
export function AdminReports() {
  const [status, setStatus] = useState("open");
  const [reports, setReports] = useState<Report[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const { ask, dialog } = useDialog();

  const failed = useCallback((e: unknown) => {
    if (e instanceof AbuseApiError && e.status === 403) setForbidden(true);
    else setError(e instanceof Error ? e.message : String(e));
  }, []);

  useEffect(() => {
    fetchReports(status).then(setReports, failed);
  }, [status, failed]);

  const act = async (work: () => Promise<unknown>) => {
    try {
      await work();
      setReports(await fetchReports(status));
      setError(null);
    } catch (e) {
      failed(e);
    }
  };

  if (forbidden) {
    return (
      <div className="panel">
        <h2>Admins only</h2>
        <p className="muted">
          Sign in with an admin account to review reports. <a href="/api/auth/login?next=/admin">Sign in</a>
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="site-head">
        <h1 style={{ flex: 1, margin: 0 }}>Reports</h1>
        <label className="field">
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error && <div className="error">{error}</div>}
      {reports === null ? (
        <p className="muted">Loading…</p>
      ) : reports.length === 0 ? (
        <p className="muted">No {status === "all" ? "" : status} reports.</p>
      ) : (
        <ul className="reports">
          {reports.map((r) => (
            <li key={r.id} className="panel">
              <div className="meta">
                <strong>{r.reason}</strong>
                <span className="muted">{new Date(r.created_at * 1000).toLocaleString()}</span>
                <span className="muted">{r.status}</span>
                <span className="muted">
                  {r.site_reports} report{r.site_reports === 1 ? "" : "s"} on this site
                </span>
              </div>
              <div className="meta">
                <a className="small-link" href={r.site_url} target="_blank" rel="noreferrer">
                  {r.slug ?? r.site_id}
                </a>
                <span className="muted">reported link: {r.url}</span>
              </div>
              <div className="meta">
                <span>Owner: {r.owner_email ?? "none (token-only site)"}</span>
                {r.owner_banned && <span className="badge">banned</span>}
                {r.site_unpublished && <span className="badge">unpublished</span>}
                {r.contact && <span className="muted">Reporter: {r.contact}</span>}
              </div>
              {r.details && <p className="details">{r.details}</p>}
              <div className="row">
                {!r.site_unpublished && (
                  <button
                    className="small danger"
                    onClick={async () =>
                      (await ask({
                        title: `Unpublish ${r.slug ?? r.site_id}?`,
                        message: "Its links stop working for everyone, and its open reports are marked actioned.",
                        confirmLabel: "Unpublish",
                        danger: true,
                      })) !== null && act(() => unpublishSite(r.site_id))
                    }
                  >
                    Unpublish
                  </button>
                )}
                {r.status === "open" && (
                  <button className="small" onClick={() => act(() => setReportStatus(r.id, "dismissed"))}>
                    Dismiss
                  </button>
                )}
                {r.status === "open" && (
                  <button className="small" onClick={() => act(() => setReportStatus(r.id, "actioned"))}>
                    Mark actioned
                  </button>
                )}
                {r.owner_email && !r.owner_banned && (
                  <button
                    className="small danger"
                    onClick={async () => {
                      const reason = await ask({
                        title: `Ban ${r.owner_email}?`,
                        message: "They can't share any more, and all their shared sites are unpublished now.",
                        confirmLabel: "Ban owner",
                        danger: true,
                        input: { label: "Reason (kept with the ban)", defaultValue: r.reason },
                      });
                      if (reason !== null) await act(() => banAccount(r.owner_email!, reason));
                    }}
                  >
                    Ban owner
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
      {dialog}
    </>
  );
}
