import type { Metadata } from "next";
import { AdminReports } from "./AdminReports";

export const metadata: Metadata = { title: "Reports · Site Runner admin", robots: { index: false } };

export default function AdminPage() {
  return (
    <main className="narrow">
      <header className="topbar">
        <a className="logo" href="/">
          <span className="mark" aria-hidden="true" />
          Site Runner admin
        </a>
        <a href="/app">Open the app</a>
      </header>
      <AdminReports />
    </main>
  );
}
