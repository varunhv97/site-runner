"use client";

import { useEffect, useRef, useState } from "react";
import type { LocalVersion } from "@/lib/localStore";
import { bootUrl, handOff, openPreviewTab, previewOrigin, type ContentConfig } from "@/lib/preview";

/** A local version, running on its own origin (p-<site id>.<content domain>) in a sandboxed frame. */
export default function Preview({ config, siteId, version }: { config: ContentConfig; siteId: string; version: LocalVersion }) {
  const frame = useRef<HTMLIFrameElement>(null);
  // The parent remounts this for another site, version or start page; a new copy of the same version
  // (after any refresh of the site list) must not restart the hand-off.
  const [files] = useState(version);
  const [failed, setFailed] = useState<string | null>(null);
  const [tabError, setTabError] = useState<string | null>(null);
  const origin = previewOrigin(config, siteId);

  useEffect(() => {
    const { done, cancel } = handOff(() => frame.current?.contentWindow, origin, files);
    done.catch((e: Error) => setFailed(e.message));
    return cancel;
  }, [origin, files]);

  const openTab = () => {
    setTabError(null);
    openPreviewTab(origin, files).catch((e: Error) => setTabError(e.message));
  };

  return (
    <div className="preview">
      <div className="preview-bar">
        <span className="muted">Local preview · v{version.n}</span>
        <button className="small" onClick={openTab}>
          Open in new tab ↗
        </button>
      </div>
      {tabError && <div className="error">{tabError}</div>}
      {failed ? (
        <div className="preview-blocked">
          <p>
            This browser didn&apos;t start the preview inside the page. Some browsers, like Safari, block it in a frame.
            A new tab works everywhere.
          </p>
          <p className="muted">{failed}</p>
          <button className="primary" onClick={openTab}>
            Open preview in a new tab
          </button>
        </div>
      ) : (
        <iframe
          ref={frame}
          src={bootUrl(origin)}
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
          title="preview"
        />
      )}
    </div>
  );
}
