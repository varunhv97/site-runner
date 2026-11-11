// Pre-share secret scan: finds likely API keys and private keys in a version's text files, so /app can warn
// before they go public. Pure functions with plain TypeScript only (no enums etc.), so `node --test` can import
// this file directly (tests/js/secrets.test.mjs).

export type SecretFinding = { file: string; line: number; kind: string; preview: string };

const TEXT_FILE = /\.(html?|xhtml|m?js|cjs|jsx|tsx?|json|css|txt|md|ya?ml|xml|svg|toml|ini|cfg|conf|env|py|php|rb|sh)$/i;
const MAX_FILE_BYTES = 1024 * 1024;
const MAX_FINDINGS = 20;

type Rule = { kind: string; re: RegExp; group?: number; check?: (value: string) => boolean };

const hasDigit = (v: string) => /\d/.test(v);

const RULES: Rule[] = [
  { kind: "AWS access key", re: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g },
  { kind: "Google API key", re: /\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])/g },
  { kind: "Stripe live key", re: /\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b/g },
  { kind: "GitHub token", re: /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b/g },
  { kind: "Slack token", re: /\bxox[baprs]-[A-Za-z0-9-]{10,}/g },
  { kind: "Anthropic API key", re: /\bsk-ant-[A-Za-z0-9_-]{20,}/g },
  {
    kind: "OpenAI API key",
    // project/service keys, or the older sk- followed by 40+ letters and digits (CSS like "sk-fading-circle" has neither)
    re: /\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}|\bsk-[A-Za-z0-9]{40,}\b/g,
    check: hasDigit,
  },
  { kind: "Private key", re: /-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?-----/g },
  {
    kind: "Secret in code",
    // apiKey = "…", "client_secret": "…", AUTH_TOKEN: '…' with a long, random-looking value
    re: /\b[A-Za-z0-9_]*(?:api[_-]?key|apikey|secret|token|access[_-]?key|private[_-]?key)["']?\s*[:=]\s*["'`]([^"'`\s]{20,200})["'`]/gi,
    group: 1,
    check: (v) => looksRandom(v),
  },
];

const PLACEHOLDER = /your|example|sample|dummy|placeholder|xxxx|change[_-]?me|insert|replace|todo|<|>|\$\{|\{\{|process\.env/i;

/** Bits per character (Shannon entropy). Random keys score ~4+, words and repeated characters much less. */
export function entropy(value: string): number {
  const counts = new Map<string, number>();
  for (const c of value) counts.set(c, (counts.get(c) ?? 0) + 1);
  let bits = 0;
  for (const n of counts.values()) bits -= (n / value.length) * Math.log2(n / value.length);
  return bits;
}

export function looksRandom(value: string): boolean {
  return /[A-Za-z]/.test(value) && hasDigit(value) && !PLACEHOLDER.test(value) && entropy(value) >= 3.5;
}

/** "AKIA…MPLE": enough to recognise a key, not enough to use it. */
export function mask(value: string): string {
  if (value.length <= 12) return `${value.slice(0, 2)}…`;
  return `${value.slice(0, 6)}…${value.slice(-4)}`;
}

const lineAt = (text: string, index: number) => text.slice(0, index).split("\n").length;

/** Likely secrets in one file's text. */
export function scanText(file: string, text: string): SecretFinding[] {
  const out: SecretFinding[] = [];
  const seen = new Set<string>();
  for (const rule of RULES) {
    for (const m of text.matchAll(rule.re)) {
      const value = rule.group ? m[rule.group] : m[0];
      if (!value || (rule.check && !rule.check(value))) continue;
      const start = (m.index ?? 0) + (rule.group ? m[0].indexOf(value) : 0);
      // a more specific rule already reported this spot (e.g. sk-ant- before the generic sk- rule)
      if ([...seen].some((s) => s.includes(value) || value.includes(s))) continue;
      seen.add(value);
      const preview = rule.kind === "Private key" ? "-----BEGIN … PRIVATE KEY-----" : mask(value);
      out.push({ file, line: lineAt(text, start), kind: rule.kind, preview });
    }
  }
  return out.sort((a, b) => a.line - b.line);
}

/** Likely secrets across a version's files (text files only, the first 1 MB of each). */
export function scanFiles(files: Record<string, Uint8Array>): SecretFinding[] {
  const decoder = new TextDecoder("utf-8", { fatal: false });
  const out: SecretFinding[] = [];
  for (const path of Object.keys(files).sort()) {
    if (!TEXT_FILE.test(path)) continue;
    out.push(...scanText(path, decoder.decode(files[path].subarray(0, MAX_FILE_BYTES))));
    if (out.length >= MAX_FINDINGS) break;
  }
  return out.slice(0, MAX_FINDINGS);
}

/** One line per finding, for the warning dialog: "js/config.js line 3: AWS access key (AKIAIO…MPLE)". */
export const describe = (f: SecretFinding) => `${f.file} line ${f.line}: ${f.kind} (${f.preview})`;
