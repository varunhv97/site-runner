// Runs with `node --test tests/js/` (Node 22.18+ strips the types from lib/joinCode.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { JOIN_CODE_KEY, clearCode, codeFromHash, takeCode } from "../../lib/joinCode.ts";

const CODE = "srj_" + "aB3-_".repeat(9); // 45 chars after the prefix, like token_urlsafe(32)

function fakes(hash, kept) {
  const map = new Map(kept ? [[JOIN_CODE_KEY, kept]] : []);
  const replaced = [];
  return {
    loc: { hash, pathname: "/join", search: "" },
    history: { replaceState: (_d, _u, url) => replaced.push(url) },
    storage: { getItem: (k) => map.get(k) ?? null, setItem: (k, v) => map.set(k, v), removeItem: (k) => map.delete(k) },
    map,
    replaced,
  };
}

test("reads only well-formed codes from the hash", () => {
  assert.equal(codeFromHash(`#${CODE}`), CODE);
  assert.equal(codeFromHash(CODE), CODE);
  assert.equal(codeFromHash(""), null);
  assert.equal(codeFromHash("#srk_" + "a".repeat(43)), null); // an API token isn't an invite code
  assert.equal(codeFromHash("#srj_short"), null);
  assert.equal(codeFromHash("#srj_" + "a".repeat(43) + "<script>"), null);
  assert.equal(codeFromHash("#srj_" + "a".repeat(200)), null);
});

test("takes the code out of the address bar and keeps it for after sign-in", () => {
  const f = fakes(`#${CODE}`);
  assert.equal(takeCode(f.loc, f.history, f.storage), CODE);
  assert.deepEqual(f.replaced, ["/join"]);
  assert.equal(f.map.get(JOIN_CODE_KEY), CODE);

  const back = fakes("", CODE); // returning from Google: no hash, code in sessionStorage
  assert.equal(takeCode(back.loc, back.history, back.storage), CODE);
  assert.deepEqual(back.replaced, []);
  clearCode(back.storage);
  assert.equal(back.map.has(JOIN_CODE_KEY), false);
});

test("a new link replaces a kept code; junk in the hash is removed too", () => {
  const other = "srj_" + "z".repeat(43);
  const f = fakes(`#${CODE}`, other);
  assert.equal(takeCode(f.loc, f.history, f.storage), CODE);
  const junk = fakes("#nonsense");
  assert.equal(takeCode(junk.loc, junk.history, junk.storage), null);
  assert.deepEqual(junk.replaced, ["/join"]);
});

test("blocked storage still uses a fresh code", () => {
  const f = fakes(`#${CODE}`);
  const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } };
  assert.equal(takeCode(f.loc, f.history, throwing), CODE);
  assert.equal(takeCode(fakes("").loc, f.history, throwing), null);
  clearCode(throwing);
  assert.equal(takeCode(fakes("").loc, f.history, null), null);
});
