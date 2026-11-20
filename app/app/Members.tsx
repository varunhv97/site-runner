"use client";

import { useEffect, useState } from "react";
import { fetchMembers, inviteMember, removeMember, type Members as MemberList } from "@/lib/share";
import type { DialogOptions } from "./AppDialog";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

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

  useEffect(() => {
    let current = true;
    fetchMembers(siteId)
      .then((l) => current && setList(l))
      .catch((e) => current && setLoadError(message(e)));
    return () => {
      current = false;
    };
  }, [siteId]);

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
      message: "They can't publish to this site or pull its versions any more. Versions they published stay.",
      confirmLabel: "Remove",
      danger: true,
    });
    if (ok === null) return;
    setWorking(true);
    try {
      await removeMember(siteId, who);
      setList(await fetchMembers(siteId));
    } catch (e) {
      setInviteError(message(e));
    } finally {
      setWorking(false);
    }
  };

  const full = !!list && list.members.length >= list.max_members;

  return (
    <details className="members">
      <summary className="muted">Members{list ? ` · ${list.members.length} of ${list.max_members}` : ""}</summary>
      {loadError && <p className="form-error">{loadError}</p>}
      {list && (
        <>
          <p className="muted">Members can publish versions, make one live, change the start page and pull versions.</p>
          <ul>
            <li>
              <span className="member-email">{list.owner.email ?? "You"}</span>
              <span className="member-status">Owner</span>
            </li>
            {list.members.map((m) => (
              <li key={m.email}>
                <span className="member-email">{m.email}</span>
                <span className={`member-status ${m.status}`}>
                  {m.status === "pending" ? "Invited · not signed in yet" : "Member"}
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
              (full ? `A site can have ${list.max_members} members. Remove one to invite someone else.` : "")}
          </p>
        </>
      )}
    </details>
  );
}
