import type { Metadata } from "next";
import JoinInvite from "./JoinInvite";

// The invite code is in the URL fragment; besides, this page sends no Referer at all (also set in next.config.ts
// and vercel.json).
export const metadata: Metadata = {
  title: "Join a site · Site Runner",
  description: "Accept a one-time invite link to publish to someone's shared site.",
  referrer: "no-referrer",
  robots: { index: false, follow: false },
};

export default function JoinPage() {
  return (
    <main className="narrow">
      <header className="topbar">
        <a className="logo" href="/">
          <span className="mark" aria-hidden="true" />
          Site Runner
        </a>
        <a href="/app">Open the app</a>
      </header>
      <h1>Join a shared site</h1>
      <JoinInvite />
    </main>
  );
}
