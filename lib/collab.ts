// Collaborators: pure decisions for /app (no imports, so node --test can load it directly).

/** A shared version as /api/me/sites lists it (published_by_email: null for token or old publishes). */
export type RemoteVersionInfo = { n: number; published_by_email?: string | null };

/** The newest shared version this browser has pulled or published (local n -> remote n), or null. */
export function lastKnownRemote(pushed: Record<number, number>): number | null {
  const ns = Object.values(pushed);
  return ns.length ? Math.max(...ns) : null;
}

/** Live is newer than anything this browser pulled or published: publishing would replace someone's work. */
export type Stale = { live: number; known: number | null; by: string | null };

export function staleLive(
  pushed: Record<number, number>,
  live: number | null,
  versions: RemoteVersionInfo[] = [],
): Stale | null {
  if (live === null) return null;
  const known = lastKnownRemote(pushed);
  if (known !== null && live <= known) return null; // ours, or someone rolled back to an older one
  const by = versions.find((v) => v.n === live)?.published_by_email ?? null;
  return { live, known, by };
}

/** The base_version to publish with: the last version this browser knows, unless publishing anyway (none). */
export function publishBase(pushed: Record<number, number>, anyway: boolean): number | undefined {
  return anyway ? undefined : (lastKnownRemote(pushed) ?? undefined);
}

export function staleMessage(s: Stale): string {
  const since = s.known === null ? "" : ` since you last pulled v${s.known}`;
  return `${s.by ?? "Someone"} published v${s.live}${since}. Publishing replaces it as live — nothing is merged.`;
}

/** "owner" | "member" for a local site linked to a shared one. The account's listing wins; then what was stored
 * when it was opened here; else owner (shared from this browser, or with its edit token). */
export function siteRole(
  siteId: string,
  stored: "owner" | "member" | undefined,
  account: { id: string; role: "owner" | "member" }[],
): "owner" | "member" {
  return account.find((a) => a.id === siteId)?.role ?? stored ?? "owner";
}
