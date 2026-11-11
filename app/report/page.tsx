import type { Metadata } from "next";
import { Suspense } from "react";
import { PrefilledReportForm, ReportForm } from "./ReportForm";

export const metadata: Metadata = {
  title: "Report abuse · Site Runner",
  description: "Tell us about a shared site that is phishing, malware, a scam or otherwise abusive.",
};

export default function ReportPage() {
  return (
    <main className="narrow">
      <header className="topbar">
        <a className="logo" href="/">
          <span className="mark" aria-hidden="true" />
          Site Runner
        </a>
        <a href="/app">Open the app</a>
      </header>
      <h1>Report a shared site</h1>
      <p className="muted lead">
        Shared sites are made by people using Site Runner, not by us. If one is phishing, spreading malware, a scam or
        otherwise abusive, tell us here. We review every report and take sites down.
      </p>
      {/* the ?url= prefill is read on the client; the plain form shows until then */}
      <Suspense fallback={<ReportForm initialUrl="" />}>
        <PrefilledReportForm />
      </Suspense>
      <p className="muted small-print">
        Found a security problem in Site Runner itself? See{" "}
        <a href="https://github.com/varunhv97/site-runner/blob/main/SECURITY.md">SECURITY.md</a> instead.
      </p>
    </main>
  );
}
