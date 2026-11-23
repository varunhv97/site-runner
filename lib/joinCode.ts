// The code of a one-time invite link (/join#srj_...). It lives in the URL fragment so it never reaches a server log
// or a Referer; /join takes it out of the address bar at once and keeps it in sessionStorage (this tab only) so it
// survives the trip through Google sign-in.

export const JOIN_CODE_KEY = "sr-join-code";
const CODE_RE = /^srj_[A-Za-z0-9_-]{20,96}$/;

/** The invite code in a location hash ("#srj_..."), or null when it isn't one. */
export function codeFromHash(hash: string): string | null {
  const value = hash.replace(/^#/, "").trim();
  return CODE_RE.test(value) ? value : null;
}

type Loc = { hash: string; pathname: string; search: string };
type Hist = { replaceState(data: unknown, unused: string, url?: string): void };
type Store = { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void };

/** Takes the code from the address bar (removing the fragment, whatever it holds) into storage, and returns the code
 * to use: the one just opened, else one kept from before sign-in. */
export function takeCode(loc: Loc, history: Hist, storage: Store | null): string | null {
  const fresh = codeFromHash(loc.hash);
  if (loc.hash) history.replaceState(null, "", loc.pathname + loc.search);
  try {
    if (fresh) storage?.setItem(JOIN_CODE_KEY, fresh);
    return fresh ?? codeFromHash(storage?.getItem(JOIN_CODE_KEY) ?? "");
  } catch {
    return fresh; // storage blocked: works as long as no sign-in round trip is needed
  }
}

export function clearCode(storage: Store | null): void {
  try {
    storage?.removeItem(JOIN_CODE_KEY);
  } catch {
    // nothing kept
  }
}
