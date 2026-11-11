// Runs with `node --test tests/js/` (Node 22.18+ strips the types from lib/secrets.ts).
import assert from "node:assert/strict";
import { test } from "node:test";
import { describe as line, entropy, looksRandom, mask, scanFiles, scanText } from "../../lib/secrets.ts";

const enc = (s) => new TextEncoder().encode(s);
const kinds = (text) => scanText("a.js", text).map((f) => f.kind);

// Built from parts so this file doesn't itself look like it holds secrets.
const AWS = "AKIA" + "Z3XQ7RB2LMN4PQ8T";
const GOOGLE = "AIza" + "SyD4k9Lm2Qx7Vb8Np3Rt6Wy1Zc5Hj0Ke2Fg";
const STRIPE = "sk_" + "live_" + "51HxQz8Lk2Mn4Pq6Rs8Tu0Vw";
const GITHUB = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8";
const SLACK = "xoxb-" + "123456789012-abcDEF456ghi";
const OPENAI = "sk-proj-" + "Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z";
const ANTHROPIC = "sk-ant-" + "api03-Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2";

test("finds the common key formats", () => {
  assert.deepEqual(kinds(`const k = "${AWS}";`), ["AWS access key"]);
  assert.deepEqual(kinds(`firebase({apiKey: "${GOOGLE}"})`), ["Google API key"]);
  assert.deepEqual(kinds(`Stripe("${STRIPE}")`), ["Stripe live key"]);
  assert.deepEqual(kinds(`gh = '${GITHUB}'`), ["GitHub token"]);
  assert.deepEqual(kinds(`slack("${SLACK}")`), ["Slack token"]);
  assert.deepEqual(kinds(`new OpenAI({ key: "${OPENAI}" })`), ["OpenAI API key"]);
  assert.deepEqual(kinds(`headers["x-api-key"] = "${ANTHROPIC}"`), ["Anthropic API key"]);
  assert.deepEqual(kinds("-----BEGIN RSA PRIVATE KEY-----\nMIIE..."), ["Private key"]);
  assert.deepEqual(kinds(`const API_SECRET = "f3Kq9Zt2Lm8Xw4Rv7Np1Bc6Hd";`), ["Secret in code"]);
});

test("reports file, line and a masked preview", () => {
  const [f] = scanText("js/config.js", `// config\n\nconst aws = "${AWS}";`);
  assert.equal(f.line, 3);
  assert.equal(f.preview, "AKIAZ3…PQ8T");
  assert.ok(!f.preview.includes(AWS));
  assert.equal(line(f), "js/config.js line 3: AWS access key (AKIAZ3…PQ8T)");
  assert.equal(mask("short"), "sh…");
});

test("ordinary code isn't flagged", () => {
  const clean = [
    'const token = "";',
    'const apiKey = "YOUR_API_KEY_HERE_1234567";',
    'const apiKey = process.env.API_KEY;',
    'const secret = "aaaaaaaaaaaaaaaaaaaaaaaa1";',
    'localStorage.setItem("token", token);',
    '<div class="sk-fading-circle sk-circle-bounce-animation-delay"></div>',
    'const csrfToken = "{{ csrf_token }}";',
    'const sessionToken = "this-is-a-readable-sentence";',
    "fetch('/api/login', { body: JSON.stringify({ password }) })",
    '<input type="password" name="password">',
    "const AKIA = 1; // not a key",
  ];
  for (const text of clean) assert.deepEqual(kinds(text), [], text);
});

test("one finding per key, and only text files are read", () => {
  const files = {
    "index.html": enc(`<script>const apiKey = "${GOOGLE}";</script>`),
    "logo.png": enc(`${AWS}`),
    "app.js": enc(`a("${ANTHROPIC}")`),
  };
  const found = scanFiles(files);
  assert.deepEqual(
    found.map((f) => [f.file, f.kind]),
    [
      ["app.js", "Anthropic API key"],
      ["index.html", "Google API key"],
    ],
  );
});

test("entropy and randomness checks", () => {
  assert.ok(entropy("aaaa") === 0);
  assert.ok(entropy("f3Kq9Zt2Lm8Xw4Rv7Np1Bc6Hd") > 4);
  assert.ok(looksRandom("f3Kq9Zt2Lm8Xw4Rv7Np1Bc6Hd"));
  assert.ok(!looksRandom("abcdefghijklmnopqrstuvwxyz"));
});
