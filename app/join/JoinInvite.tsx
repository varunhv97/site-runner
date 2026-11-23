"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { clearCode, takeCode } from "@/lib/joinCode";
import { fetchMe, isInvalidLink, lookupInviteLink, redeemInviteLink, type InviteLinkInfo } from "@/lib/share";

const INVALID = "This invite link is invalid, expired or already used. Ask the site's owner for a new one.";
const signInHere = "/api/auth/login?next=/join";

type State =
  | { step: "loading" }
  | { step: "no-code" }
  | { step: "sign-in" }
  | { step: "ask"; info: InviteLinkInfo }
  | { step: "joining"; info: InviteLinkInfo }
  | { step: "invalid" }
  | { step: "error"; message: string };

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

// Reads the code from the fragment (and drops it from the address bar), asks the account to sign in if needed, says
// whose site it is, and joins only when asked to. Names and emails render as text.
export default function JoinInvite() {
  const [state, setState] = useState<State>({ step: "loading" });
  const [code, setCode] = useState<string | null>(null);
  const router = useRouter();

  useEffect(() => {
    const found = takeCode(window.location, window.history, storage());
    let current = true;
    const show = (s: State) => current && setState(s);
    (async () => {
      if (!found) return show({ step: "no-code" });
      setCode(found);
      const me = await fetchMe();
      if (!me.enabled) return show({ step: "error", message: "Sign-in isn't set up on this server." });
      if (!me.user) return show({ step: "sign-in" });
      try {
        show({ step: "ask", info: await lookupInviteLink(found) });
      } catch (e) {
        if (isInvalidLink(e)) clearCode(storage());
        show(isInvalidLink(e) ? { step: "invalid" } : { step: "error", message: message(e) });
      }
    })();
    return () => {
      current = false;
    };
  }, []);

  const join = async (info: InviteLinkInfo) => {
    if (!code) return;
    setState({ step: "joining", info });
    try {
      await redeemInviteLink(code);
      clearCode(storage());
      router.replace("/app");
    } catch (e) {
      if (isInvalidLink(e)) {
        clearCode(storage());
        setState({ step: "invalid" });
      } else setState({ step: "error", message: message(e) });
    }
  };

  const noThanks = () => {
    clearCode(storage());
    router.replace("/app");
  };

  switch (state.step) {
    case "loading":
      return <p className="muted lead">Checking the invite link…</p>;
    case "no-code":
      return (
        <div className="panel join">
          <p>There&apos;s no invite code here. Open the whole link you were sent, including the part after #.</p>
          <a className="btn-link" href="/app">Open the app</a>
        </div>
      );
    case "sign-in":
      return (
        <div className="panel join">
          <h2>Sign in to see this invitation</h2>
          <p className="muted">
            Sign in with Google to join — this creates your Site Runner account. You come back here afterwards and
            decide whether to join.
          </p>
          <a className="btn-link" href={signInHere}>Sign in with Google</a>
        </div>
      );
    case "invalid":
      return (
        <div className="panel join">
          <p role="alert">{INVALID}</p>
          <a className="btn-link" href="/app">Open the app</a>
        </div>
      );
    case "error":
      return (
        <div className="panel join">
          <p className="form-error" role="alert">{state.message}</p>
          <button onClick={noThanks}>Back to the app</button>
        </div>
      );
    case "ask":
    case "joining":
      return (
        <div className="panel join">
          <h2>
            <span className="join-who">{state.info.owner_email ?? "Someone"}</span> invited you to edit{" "}
            <span className="join-site">{state.info.site_name}</span>
          </h2>
          <p className="muted">
            As a member you can publish new versions, make one live, change the start page and pull its versions. The
            owner can remove you at any time. This link works once and expires{" "}
            {new Date(state.info.expires_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.
          </p>
          <div className="join-actions">
            <button className="primary" disabled={state.step === "joining"} onClick={() => join(state.info)}>
              {state.step === "joining" ? "Joining…" : "Join"}
            </button>
            <button disabled={state.step === "joining"} onClick={noThanks}>
              No thanks
            </button>
          </div>
        </div>
      );
  }
}
