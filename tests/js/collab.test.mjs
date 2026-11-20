// Runs with `node --test tests/js/` (Node 22.18+ strips the types from lib/collab.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { lastKnownRemote, siteRole, staleLive, staleMessage } from "../../lib/collab.ts";

test("last known remote version is the highest pulled or published", () => {
  assert.equal(lastKnownRemote({}), null);
  assert.equal(lastKnownRemote({ 1: 3, 2: 5, 3: 4 }), 5);
});

test("not stale when live is ours or older", () => {
  assert.equal(staleLive({ 1: 3 }, 3), null);
  assert.equal(staleLive({ 1: 3, 2: 5 }, 4), null); // a rollback by someone: nothing newer to lose
  assert.equal(staleLive({ 1: 3 }, null), null);
});

test("stale when someone published a newer live version", () => {
  const versions = [{ n: 3, published_by_email: "me@x.com" }, { n: 4, published_by_email: "bob@x.com" }];
  assert.deepEqual(staleLive({ 1: 3 }, 4, versions), { live: 4, known: 3, by: "bob@x.com" });
  assert.deepEqual(staleLive({ 1: 3 }, 4, [{ n: 4, published_by_email: null }]), { live: 4, known: 3, by: null });
  assert.deepEqual(staleLive({}, 2), { live: 2, known: null, by: null });
});

test("message names the publisher, else Someone", () => {
  assert.equal(
    staleMessage({ live: 4, known: 3, by: "bob@x.com" }),
    "bob@x.com published v4 since you last pulled v3. Publishing replaces it as live — nothing is merged.",
  );
  assert.equal(
    staleMessage({ live: 2, known: null, by: null }),
    "Someone published v2. Publishing replaces it as live — nothing is merged.",
  );
});

test("role: account listing, then stored, then owner", () => {
  const account = [{ id: "a", role: "member" }, { id: "b", role: "owner" }];
  assert.equal(siteRole("a", undefined, account), "member");
  assert.equal(siteRole("b", "member", account), "owner");
  assert.equal(siteRole("c", "member", account), "member");
  assert.equal(siteRole("c", undefined, account), "owner");
});
