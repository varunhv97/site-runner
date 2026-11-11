// Abuse reports (anyone) and admin review (ADMIN_EMAILS) against the Python API.

export const REASONS = [
  { value: "phishing", label: "Phishing (asks for passwords or card details)" },
  { value: "malware", label: "Malware or a harmful download" },
  { value: "scam", label: "Scam or fraud" },
  { value: "spam", label: "Spam" },
  { value: "illegal", label: "Illegal content" },
  { value: "other", label: "Something else" },
] as const;

export type Report = {
  id: number;
  site_id: string;
  slug: string | null;
  site_url: string;
  owner_email: string | null;
  owner_banned: boolean;
  site_unpublished: boolean;
  url: string;
  reason: string;
  details: string;
  contact: string;
  created_at: number;
  status: "open" | "actioned" | "dismissed";
  site_reports: number;
};

/** Thrown with the server's message and status, so pages can tell "not an admin" (403) from other failures. */
export class AbuseApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function call<T>(url: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new AbuseApiError("Couldn't reach the server. Check your connection and try again.", 0);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new AbuseApiError(body.error ?? `Request failed (${res.status})`, res.status);
  return body as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export type ReportInput = { url: string; reason: string; details: string; contact: string };

export const sendReport = (input: ReportInput) => call<{ ok: true; id: number }>("/api/reports", json("POST", input));

export const fetchReports = async (status = "open") =>
  (await call<{ reports: Report[] }>(`/api/admin/reports?status=${encodeURIComponent(status)}`)).reports;

export const setReportStatus = (id: number, status: "actioned" | "dismissed") =>
  call(`/api/admin/reports/${id}`, json("POST", { status }));

/** Admin takedown: unpublishes the site and marks its open reports actioned. */
export const unpublishSite = (siteId: string) => call(`/api/sites/${encodeURIComponent(siteId)}`, { method: "DELETE" });

export const banAccount = (email: string, reason: string) =>
  call<{ sites_unpublished: string[] }>("/api/admin/bans", json("POST", { email, reason }));
