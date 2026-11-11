import type { Metadata } from "next";
import { Suspense } from "react";
import { PrefilledReportForm, ReportForm } from "./ReportForm";
import styles from "./report.module.css";

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
        otherwise abusive, tell us here. It goes straight to the people who can take it down.
      </p>
      {/* the ?url= prefill is read on the client; the plain form shows until then */}
      <Suspense fallback={<ReportForm initialUrl="" />}>
        <PrefilledReportForm />
      </Suspense>

      <section className={styles.next} aria-labelledby="next-h">
        <h2 id="next-h">What happens next</h2>
        <ol>
          <li>
            <strong>Your report reaches us right away</strong>, by email and in our review queue.
          </li>
          <li>
            <strong>We check the site.</strong> If it breaks the rules, we take it down, and its link stops working
            for everyone.
          </li>
          <li>
            <strong>Repeat or serious abuse gets the account banned</strong>, so it can&apos;t share sites again.
          </li>
        </ol>
        <p className={styles.alt}>
          Rather write an email? Send the link and what&apos;s wrong to{" "}
          <a href="mailto:abuse@spinsite.app">abuse@spinsite.app</a>.
        </p>
        <p className={styles.alt}>
          Found a security problem in Site Runner itself? Email{" "}
          <a href="mailto:security@spinsite.app">security@spinsite.app</a> or see{" "}
          <a href="https://github.com/varunhv97/site-runner/blob/main/SECURITY.md">SECURITY.md</a>.
        </p>
      </section>
    </main>
  );
}
