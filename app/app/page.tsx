"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fromDataTransfer, fromFileList, type FileMap } from "@/lib/ingest";
import { addVersion, deleteSite, listSites, saveSite, type LocalSite } from "@/lib/localStore";
import {
  fetchMe,
  pinnedUrl,
  rotateToken,
  setRemoteLive,
  shareVersion,
  signInUrl,
  signOut,
  unpublish,
  type Me,
} from "@/lib/share";

async function registerPreviewWorker() {
  if (!("serviceWorker" in navigator)) throw new Error("This browser doesn't support Service Workers.");
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/preview/" });
  if (reg.active) return;
  const sw = reg.installing ?? reg.waiting;
  await new Promise<void>((resolve) => {
    sw?.addEventListener("statechange", () => sw.state === "activated" && resolve());
  });
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function DropZone({ label, onFiles }: { label: string; onFiles: (files: FileMap) => void }) {
  const [over, setOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  useEffect(() => folderInput.current?.setAttribute("webkitdirectory", ""), []);

  const handleInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) onFiles(await fromFileList(e.target.files));
    e.target.value = "";
  };

  return (
    <div
      className={`dropzone ${over ? "over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={async (e) => {
        e.preventDefault();
        setOver(false);
        onFiles(await fromDataTransfer(e.dataTransfer));
      }}
    >
      <p>{label}</p>
      <p className="muted">HTML files, a folder or a .zip</p>
      <div className="row">
        <button onClick={() => fileInput.current?.click()}>Choose files or a zip</button>
        <button onClick={() => folderInput.current?.click()}>Choose a folder</button>
      </div>
      <input ref={fileInput} type="file" multiple hidden onChange={handleInput} />
      <input ref={folderInput} type="file" hidden onChange={handleInput} />
    </div>
  );
}

function CopyLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="link">
      <a href={url} target="_blank" rel="noreferrer">
        {url}
      </a>
      <button
        className="small"
        onClick={async () => {
          await navigator.clipboard.writeText(url);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

export default function Home() {
  const [ready, setReady] = useState(false);
  const [sites, setSites] = useState<LocalSite[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewN, setPreviewN] = useState<number | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<Me>({ enabled: false, user: null });

  const site = sites.find((s) => s.id === selectedId) ?? null;

  const refresh = useCallback(async (select?: string) => {
    setSites(await listSites());
    if (select) setSelectedId(select);
  }, []);

  useEffect(() => {
    registerPreviewWorker()
      .then(() => setReady(true))
      .catch((e) => setError(String(e.message ?? e)));
    listSites().then(setSites);
    fetchMe().then((m) => {
      setMe(m);
      if (new URLSearchParams(location.search).get("signin") === "failed") {
        setError("Google sign-in didn't finish. Try again, and pick an account with a verified email.");
      }
    });
  }, []);

  const mustSignIn = me.enabled && !me.user;

  async function run(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const onNewSite = (files: FileMap) =>
    run("Saving…", async () => {
      const name = prompt("Name this site", "My site") || "My site";
      const s = await addVersion(files, null, name);
      setPreviewN(1);
      await refresh(s.id);
    });

  const onNewVersion = (files: FileMap) =>
    run("Saving version…", async () => {
      if (!site) return;
      const note = prompt("What changed? (optional)") ?? "";
      const s = await addVersion(files, site, site.name, note);
      setPreviewN(s.versions.length);
      await refresh(s.id);
    });

  const latest = site?.versions[site.versions.length - 1];
  const shownN = site ? (previewN && previewN <= site.versions.length ? previewN : latest!.n) : null;

  return (
    <main>
      <header className="topbar">
        <a className="logo" href="/">
          <span className="mark" aria-hidden="true" />
          Site Runner
        </a>
        <div className="account">
          <p className="muted">Files stay in this browser until you share a version.</p>
          {me.enabled &&
            (me.user ? (
              <>
                <span className="who">{me.user.email}</span>
                <button
                  className="small"
                  onClick={() =>
                    run("Signing out…", async () => {
                      await signOut();
                      setMe(await fetchMe());
                    })
                  }
                >
                  Sign out
                </button>
              </>
            ) : (
              <a className="btn-link" href={signInUrl}>
                Sign in with Google
              </a>
            ))}
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      {busy && <div className="busy">{busy}</div>}

      <div className="layout">
        <aside>
          <DropZone label="Drop a new site" onFiles={onNewSite} />
          <ul className="sites">
            {sites.map((s) => (
              <li key={s.id}>
                <button
                  className={`site ${s.id === selectedId ? "active" : ""}`}
                  onClick={() => {
                    setSelectedId(s.id);
                    setPreviewN(null);
                  }}
                >
                  <strong>{s.name}</strong>
                  <span className="muted">
                    {s.versions.length} version{s.versions.length === 1 ? "" : "s"}
                    {s.remote ? " · shared" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        <section>
          {!site ? (
            <div className="empty muted">Drop a site on the left or pick one from the list.</div>
          ) : (
            <>
              <div className="site-head">
                <input
                  className="name"
                  defaultValue={site.name}
                  key={site.id}
                  onBlur={async (e) => {
                    await saveSite({ ...site, name: e.target.value || site.name });
                    refresh();
                  }}
                />
                <button
                  className="danger small"
                  onClick={() =>
                    confirm(`Delete "${site.name}" from this browser? Shared links keep working.`) &&
                    run("Deleting…", async () => {
                      await deleteSite(site.id);
                      setSelectedId(null);
                      await refresh();
                    })
                  }
                >
                  Delete
                </button>
              </div>

              {site.remote && (
                <div className="share">
                  <span className="share-label">Live link · version {site.remote.live}</span> <CopyLink url={site.remote.url} />
                  <details>
                    <summary className="muted">Edit token</summary>
                    <code>{site.remote.token}</code>
                    <p className="muted">Anyone with this token can publish versions of this site, through the API or MCP.</p>
                    <button
                      className="small"
                      disabled={!!busy}
                      onClick={() =>
                        run("Replacing token…", async () => {
                          await saveSite(await rotateToken(site));
                          await refresh();
                        })
                      }
                    >
                      Replace token
                    </button>
                  </details>
                  <button
                    className="small danger unpublish"
                    disabled={!!busy}
                    onClick={() =>
                      confirm("Unpublish this site? Its links stop working for everyone. Your local versions stay.") &&
                      run("Unpublishing…", async () => {
                        await saveSite(await unpublish(site));
                        await refresh();
                      })
                    }
                  >
                    Unpublish
                  </button>
                </div>
              )}

              <DropZone label="Drop a new version" onFiles={onNewVersion} />

              <table className="versions">
                <tbody>
                  {[...site.versions].reverse().map((v) => {
                    const remoteN = site.remote?.pushed[v.n];
                    const isLive = remoteN !== undefined && site.remote?.live === remoteN;
                    return (
                      <tr key={v.n} className={v.n === shownN ? "shown" : ""}>
                        <td>
                          <strong>v{v.n}</strong>
                        </td>
                        <td className="muted">
                          {new Date(v.createdAt).toLocaleString()} · {Object.keys(v.manifest).length} file{Object.keys(v.manifest).length === 1 ? "" : "s"} ·{" "}
                          {formatSize(v.size)}
                          {v.note && <div>{v.note}</div>}
                        </td>
                        <td className="actions">
                          <button className="small" onClick={() => setPreviewN(v.n)}>
                            Preview
                          </button>
                          {remoteN === undefined && mustSignIn ? (
                            <a className="btn-link small" href={signInUrl}>
                              Sign in to share
                            </a>
                          ) : remoteN === undefined ? (
                            <button
                              className="small primary"
                              disabled={!!busy}
                              onClick={() =>
                                run("Uploading…", async () => {
                                  await saveSite(await shareVersion(site, v));
                                  await refresh();
                                })
                              }
                            >
                              {site.remote ? "Publish" : "Share"}
                            </button>
                          ) : isLive ? (
                            <span className="badge">Live</span>
                          ) : (
                            <button
                              className="small"
                              disabled={!!busy}
                              onClick={() =>
                                run("Updating…", async () => {
                                  await saveSite(await setRemoteLive(site, remoteN));
                                  await refresh();
                                })
                              }
                            >
                              Make live
                            </button>
                          )}
                          {remoteN !== undefined && (
                            <a className="small-link" href={pinnedUrl(site, remoteN)} target="_blank" rel="noreferrer">
                              @{remoteN}
                            </a>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {ready && shownN && (
                <div className="preview">
                  <div className="preview-bar">
                    <span className="muted">Local preview · v{shownN}</span>
                    <a href={`/preview/${site.id}/${shownN}/`} target="_blank" rel="noreferrer">
                      Open in new tab ↗
                    </a>
                  </div>
                  <iframe key={`${site.id}-${shownN}`} src={`/preview/${site.id}/${shownN}/`} title="preview" />
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </main>
  );
}
