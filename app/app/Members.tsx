"use client";

import { useEffect, useState } from "react";
import {
  createInviteLink,
  fetchInviteLinks,
  fetchMembers,
  inviteMember,
  removeMember,
  revokeInviteLink,
  type InviteLinks,
  type Members as MemberList,
} from "@/lib/share";
import type { DialogOptions } from "./AppDialog";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const minutesLeft = (t: number) => Math.max(1, Math.round((t * 1000 - Date.now()) / 60000));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

// The owner's view of who can publish to a shared site: invite Google accounts by email, remove them.
export default function Members({
  siteId,
  ask,
}: {
  siteId: string;
  ask: (options: DialogOptions) => Promise<string | null>;
}) {
  const [list, setList] = useState<MemberList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [links, setLinks] = useState<InviteLinks | null>(null);
  const [newLink, setNewLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let current = true;
    fetchMembers(siteId)
      .then((l) => current && setList(l))
      .catch((e) => current && setLoadError(message(e)));
    fetchInviteLinks(siteId)
      .then((l) => current && setLinks(l))
      .catch(() => {});
    return () => {
      current = false;
    };
  }, [siteId]);

  const reload = async () => {
    const [l, k] = await Promise.all([fetchMembers(siteId), fetchInviteLinks(siteId)]);
    setList(l);
    setLinks(k);
  };

  // The link (with its one-time code) comes back only now: shown once, never stored.
  const makeLink = async () => {
    setWorking(true);
    setInviteError(null);
    try {
      setNewLink((await createInviteLink(siteId)).url);
      setCopied(false);
      await reload();
    } catch (e) {
      setInviteError(message(e));
    } finally {
      setWorking(false);
    }
  };

  const revokeLink = async (id: string) => {
    setWorking(true);
    try {
      await revokeInviteLink(siteId, id);
      setNewLink(null);
      await reload();
    } catch (e) {
      setInviteError(message(e));
    } finally {
      setWorking(false);
    }
  };

  const invite = async () => {
    const value = email.trim();
    if (!value) return;
    setWorking(true);
    setInviteError(null);
    try {
      setList(await inviteMember(siteId, value));
      setEmail("");
    } catch (e) {
      setInviteError(message(e));
    } finally {
      setWorking(false);
    }
  };

  const remove = async (who: string) => {
    const ok = await ask({
      title: `Remove ${who}?`,
      message: "They can't publish to this site or pull its versions any more (or accept the invitation). Versions they published stay.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (ok === null) return;
    setWorking(true);
    try {
      await removeMember(siteId, who);
      await reload();
    } catch (e) {
      setInviteError(message(e));
    } finally {
      setWorking(false);
    }
  };

  const outstanding = list?.invite_links ?? 0;
  const full = !!list && list.members.length + outstanding >= list.max_members;

  return (
    <details className="members">
      <summary className="muted">
        Members{list ? ` · ${list.members.length} of ${list.max_members}` : ""}
        {outstanding > 0 ? ` (+${plural(outstanding, "invite link")})` : ""}
      </summary>
      {loadError && <p className="form-error">{loadError}</p>}
      {list && (
        <>
          <p className="muted">
            Members can publish versions, make one live, change the start page and pull versions. Someone you invite
            gets access only after they accept.
          </p>
          <ul>
            <li>
              <span className="member-email">{list.owner.email ?? "You"}</span>
              <span className="member-status">Owner</span>
            </li>
            {list.members.map((m) => (
              <li key={m.email}>
                <span className="member-email">{m.email}</span>
                <span className={`member-status ${m.status}`}>
                  {m.status === "pending" ? "Invited, hasn't accepted yet" : "Member"}
                </span>
                <button className="small danger" disabled={working} onClick={() => remove(m.email)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <form
            className="member-invite"
            onSubmit={(e) => {
              e.preventDefault();
              invite();
            }}
          >
            <input
              type="email"
              value={email}
              placeholder="Google account email"
              aria-label="Invite by Google account email"
              aria-invalid={!!inviteError}
              aria-describedby="member-invite-error"
              disabled={full}
              onChange={(e) => {
                setEmail(e.target.value);
                setInviteError(null);
              }}
            />
            <button type="submit" className="small" disabled={working || full || !email.trim()}>
              Invite
            </button>
          </form>
          <p id="member-invite-error" className="form-error" role="status">
            {inviteError ??
              (full
                ? `A site can have ${list.max_members} members, counting pending invites and unused invite links. Remove one (or revoke a link) to invite someone else.`
                : "")}
          </p>
          <div className="invite-links">
            <p className="muted">
              Or make a one-time invite link: whoever opens it first, signed in with Google, can join. It expires in an
              hour and takes a member seat until then.
            </p>
            {newLink && (
              <div className="api-token-secret">
                <p className="muted">Expires in 1 hour · works once. Copy it now: it won&apos;t be shown again.</p>
                <code>{newLink}</code>
                <button
                  className="small"
                  onClick={async () => {
                    await navigator.clipboard.writeText(newLink);
                    setCopied(true);
                  }}
                >
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
            )}
            {links && (links.links.length > 0 || links.used.length > 0) && (
              <ul>
                {links.links.map((l) => (
                  <li key={l.id}>
                    <span className="member-email">Invite link …{l.id.slice(-4)}</span>
                    <span className="member-status">expires in {minutesLeft(l.expires_at)} min</span>
                    <button className="small danger" disabled={working} onClick={() => revokeLink(l.id)}>
                      Revoke
                    </button>
                  </li>
                ))}
                {links.used.map((l) => (
                  <li key={l.id}>
                    <span className="member-email">Invite link …{l.id.slice(-4)}</span>
                    <span className="member-status accepted">used by {l.used_by_email ?? "an account"}</span>
                  </li>
                ))}
              </ul>
            )}
            <button className="small" disabled={working || full} onClick={makeLink}>
              Create invite link
            </button>
          </div>
        </>
      )}
    </details>
  );
}
