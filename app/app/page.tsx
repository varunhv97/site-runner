"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fromDataTransfer, fromFileList, type FileMap } from "@/lib/ingest";
import { addVersion, deleteSite, listSites, saveSite, type LocalSite } from "@/lib/localStore";
import { useDialog } from "./AppDialog";
import {
  deleteRemoteVersion,
  fetchMe,
  fetchUsage,
  pinnedUrl,
  checkSlug,
  normalizeSlug,
  rotateToken,
  setRemoteLive,
  setSlug,
  slugify,
  shareVersion,
  signInUrl,
  signOut,
  unpublish,
  type Me,
  type Usage,
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

/** "38 MB", "4.2 MB", or KB/B below 1 MB: for the storage line. */
function formatUsage(bytes: number) {
  const mb = bytes / 1024 / 1024;
  if (mb < 1) return formatSize(bytes);
  return `${mb >= 10 ? Math.round(mb) : mb.toFixed(1)} MB`;
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
  const [usage, setUsage] = useState<Usage | null>(null);
  const { ask, dialog } = useDialog();

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
      if (m.user) fetchUsage().then(setUsage);
      if (new URLSearchParams(location.search).get("signin") === "failed") {
        setError("Google sign-in didn't finish. Try again, and pick an account with a verified email.");
      }
    });
  }, []);

  const mustSignIn = me.enabled && !me.user;
  const refreshUsage = async () => setUsage(await fetchUsage());

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

  const onNewSite = async (files: FileMap) => {
    const name = await ask({
      title: "Name this site",
      message: `${Object.keys(files).length} file${Object.keys(files).length === 1 ? "" : "s"} ready to save in this browser.`,
      input: { label: "Site name", defaultValue: "My site", required: true },
      confirmLabel: "Save site",
    });
    if (name === null) return;
    await run("Saving…", async () => {
      const s = await addVersion(files, null, name);
      setPreviewN(1);
      await refresh(s.id);
    });
  };

  const onNewVersion = async (files: FileMap) => {
    if (!site) return;
    const note = await ask({
      title: `Save version ${site.versions.length + 1} of ${site.name}`,
      message: "A short note shows next to the version in the list.",
      input: { label: "What changed (optional)", placeholder: "New header and footer" },
      confirmLabel: "Save version",
    });
    if (note === null) return;
    await run("Saving version…", async () => {
      const s = await addVersion(files, site, site.name, note);
      setPreviewN(s.versions.length);
      await refresh(s.id);
    });
  };

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
                      setUsage(null);
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
          {usage && (
            <div className="usage">
              <span className="muted">
                Shared sites use {formatUsage(usage.used_bytes)} of {formatUsage(usage.quota_bytes)}
              </span>
              <span className={`meter ${usage.used_bytes >= usage.quota_bytes * 0.9 ? "full" : ""}`} aria-hidden="true">
                <span style={{ width: `${Math.min(100, (usage.used_bytes / usage.quota_bytes) * 100)}%` }} />
              </span>
            </div>
          )}
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
                  onClick={async () =>
                    (await ask({
                      title: `Delete “${site.name}” from this browser?`,
                      message: site.remote
                        ? "All its local versions are removed. The shared link keeps working until you unpublish it."
                        : "All its versions are removed from this browser. This can't be undone.",
                      confirmLabel: "Delete",
                      danger: true,
                    })) !== null &&
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
                  <button
                    className="small"
                    disabled={!!busy}
                    onClick={async () => {
                      const remote = site.remote!;
                      const slug = await ask({
                        title: "Change link name",
                        message: "The current link keeps working and forwards to the new one.",
                        input: {
                          label: "Link name",
                          prefix: `${location.host}/s/`,
                          defaultValue: remote.slug ?? slugify(site.name),
                          normalize: normalizeSlug,
                          required: true,
                          validate: (value) => checkSlug(value, remote.slug),
                        },
                        confirmLabel: "Save name",
                      });
                      if (!slug || slug === remote.slug) return;
                      await run("Renaming…", async () => {
                        await saveSite(await setSlug(site, slug));
                        await refresh();
                      });
                    }}
                  >
                    Change link name
                  </button>
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
                    onClick={async () =>
                      (await ask({
                        title: "Unpublish this site?",
                        message: "Its live and pinned links stop working for everyone. Your local versions stay in this browser.",
                        confirmLabel: "Unpublish",
                        danger: true,
                      })) !== null &&
                      run("Unpublishing…", async () => {
                        await saveSite(await unpublish(site));
                        await refresh();
                        if (me.user) await refreshUsage();
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
                              onClick={async () => {
                                let slug: string | undefined;
                                if (!site.remote) {
                                  const picked = await ask({
                                    title: `Share “${site.name}”`,
                                    message: "Pick a link name, or leave it blank for a random link. Names are unique and can't be reused.",
                                    input: {
                                      label: "Link name",
                                      prefix: `${location.host}/s/`,
                                      defaultValue: slugify(site.name),
                                      normalize: normalizeSlug,
                                      validate: (value) => checkSlug(value),
                                    },
                                    confirmLabel: "Share",
                                  });
                                  if (picked === null) return;
                                  slug = picked || undefined;
                                }
                                await run("Uploading…", async () => {
                                  await saveSite(await shareVersion(site, v, slug));
                                  await refresh();
                                  if (me.user) await refreshUsage();
                                });
                              }}
                            >
                              {site.remote ? "Publish" : "Share"}
                            </button>
                          ) : isLive ? (
                            <span className="badge">Live</span>
                          ) : (
                            <>
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
                              <button
                                className="small danger"
                                disabled={!!busy}
                                onClick={async () =>
                                  (await ask({
                                    title: `Delete the shared copy of v${v.n}?`,
                                    message: `Its pinned link (${pinnedUrl(site, remoteN)}) stops working for everyone. The live link isn't affected, and v${v.n} stays in this browser.`,
                                    confirmLabel: "Delete shared copy",
                                    danger: true,
                                  })) !== null &&
                                  run("Deleting shared copy…", async () => {
                                    await saveSite(await deleteRemoteVersion(site, remoteN));
                                    await refresh();
                                    if (me.user) await refreshUsage();
                                  })
                                }
                              >
                                Delete shared copy
                              </button>
                            </>
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
      {dialog}
    </main>
  );
}
