import type { Metadata } from "next";
import CodeBlock from "./CodeBlock";
import styles from "./docs.module.css";

export const metadata: Metadata = {
  title: "CLI and MCP · Site Runner",
  description: "Publish and manage your shared sites from the terminal with the spinsite CLI, or let Claude do it over MCP.",
};

const REPO = "https://github.com/varunhv97/site-runner";
const FROM_GIT = "git+https://github.com/varunhv97/site-runner#subdirectory=cli";

const COMMANDS: [string, string][] = [
  ["spinsite deploy ./dist", "Share a folder (or .zip) as a new site. Add --slug my-site for my-site.sites.spinsite.app."],
  ["spinsite deploy ./dist --site my-site", "Publish a new version of one of your sites. It goes live right away."],
  ["spinsite sites", "Your shared sites and the ones you're a member of, their live version and link."],
  ["spinsite pull my-site", "Download the live version into ./my-site (or --version 3) to edit and deploy back."],
  ["spinsite versions my-site", "A site's versions. The live one is marked with *."],
  ["spinsite live my-site 3", "Make version 3 live: roll back or forward."],
  ["spinsite rename my-site new-name", "Change the link name. Old links forward to the new one."],
  ["spinsite rm my-site 2", "Delete version 2 (not the live one). Its pinned link stops working."],
  ["spinsite rm my-site", "Unpublish the whole site. All its links stop working."],
  ["spinsite members · invite · remove-member", "See, add or remove who can publish to your site."],
  ["spinsite usage", "Storage your shared sites use, against your 250MB."],
  ["spinsite whoami · logout", "Which account you're logged in as; forget the saved token."],
];

const TOOLS: [string, string][] = [
  ["deploy", "Publish a folder from your computer as a new site or a new version"],
  ["list_sites · list_versions", "See your sites and their versions"],
  ["pull", "Download a version of a site into a folder to edit"],
  ["list_members · invite_member · remove_member", "Manage who can publish to your site"],
  ["set_live", "Roll back or forward"],
  ["rename_site", "Change a link name"],
  ["delete_version · unpublish_site", "Remove a version or a whole site (Claude asks you first)"],
  ["usage · whoami", "Storage used; which account it acts as"],
];

const DESKTOP_CONFIG = `{
  "mcpServers": {
    "spinsite": {
      "command": "uvx",
      "args": ["--from", "${FROM_GIT}", "spinsite", "mcp"],
      "env": { "SPINSITE_TOKEN": "srk_..." }
    }
  }
}`;

export default function DocsPage() {
  return (
    <main className="narrow">
      <header className="topbar">
        <a className="logo" href="/">
          <span className="mark" aria-hidden="true" />
          Site Runner
        </a>
        <a href="/app">Open the app</a>
      </header>
      <h1>Publish from the terminal, or let Claude do it</h1>
      <p className="muted lead">
        The <code>spinsite</code> command publishes a folder as a shared site and manages its versions, without
        opening the app. The same commands work as an MCP server, so Claude can deploy and roll back your sites for
        you.
      </p>
      <ul className={styles.toc}>
        <li>
          <a href="#token">1. Get a token</a>
        </li>
        <li>
          <a href="#cli">2. Install the CLI</a>
        </li>
        <li>
          <a href="#claude">3. Use it with Claude</a>
        </li>
        <li>
          <a href="#together">Working together</a>
        </li>
        <li>
          <a href="#safety">Keeping it safe</a>
        </li>
      </ul>

      <section className={styles.section} id="token" aria-labelledby="token-h">
        <h2 id="token-h">1. Get an API token</h2>
        <ol>
          <li>
            Open <a href="/app">the app</a> and sign in with Google.
          </li>
          <li>
            At the bottom of the left column, open <strong>API tokens</strong> and click <strong>New token</strong>.
          </li>
          <li>
            Name it after where it will live, like <code>macbook</code> or <code>claude-code</code>. The name is only for
            you, so you know which one to revoke later.
          </li>
          <li>
            <strong>Copy the token now.</strong> It starts with <code>srk_</code> and is shown only once.
          </li>
        </ol>
        <p>Make one token per computer or tool. If one leaks, revoke just that one.</p>
      </section>

      <section className={styles.section} id="cli" aria-labelledby="cli-h">
        <h2 id="cli-h">2. Install the CLI and log in</h2>
        <p>
          It needs Python 3.12 or newer. With <a href="https://docs.astral.sh/uv/">uv</a> or{" "}
          <a href="https://pipx.pypa.io/">pipx</a>:
        </p>
        <CodeBlock label="uv" code={`uv tool install "${FROM_GIT}"`} />
        <CodeBlock label="or pipx" code={`pipx install "${FROM_GIT}"`} />
        <p>Then log in with your token. It checks the token and saves it on your computer, readable only by you.</p>
        <CodeBlock code="spinsite login" />
        <p>Share a folder with your built site, then publish new versions to it:</p>
        <CodeBlock
          code={`spinsite deploy ./dist --slug my-site
spinsite deploy ./dist --site my-site --note "fix the nav"`}
        />
        <h3>Commands</h3>
        <table className={styles.table}>
          <tbody>
            {COMMANDS.map(([cmd, what]) => (
              <tr key={cmd}>
                <td>
                  <code>{cmd}</code>
                </td>
                <td>{what}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          Every command takes <code>--json</code> for scripts. <code>SPINSITE_TOKEN</code> overrides the saved login,
          which is handy in CI: store the token as a secret, never in the repository.
        </p>
      </section>

      <section className={styles.section} id="claude" aria-labelledby="claude-h">
        <h2 id="claude-h">3. Use it with Claude (MCP)</h2>
        <p>
          <code>spinsite mcp</code> runs the same commands as an MCP server on your computer. Claude reads the site
          straight from your disk, so your files never pass through the chat.
        </p>
        <h3>Claude Code</h3>
        <p>
          After <code>spinsite login</code>, add it once for all your projects:
        </p>
        <CodeBlock code="claude mcp add --scope user spinsite -- spinsite mcp" />
        <p>Without installing anything, run it with uvx and pass the token:</p>
        <CodeBlock
          code={`claude mcp add --scope user --env SPINSITE_TOKEN=srk_... spinsite -- \\
  uvx --from "${FROM_GIT}" spinsite mcp`}
        />
        <h3>Claude Desktop, Cursor and other MCP apps</h3>
        <p>
          Add this to the app&apos;s MCP config (for Claude Desktop: Settings → Developer → Edit Config), with your
          token in place of <code>srk_...</code>:
        </p>
        <CodeBlock code={DESKTOP_CONFIG} />
        <h3>What Claude can do</h3>
        <table className={styles.table}>
          <tbody>
            {TOOLS.map(([tool, what]) => (
              <tr key={tool}>
                <td>
                  <code>{tool}</code>
                </td>
                <td>{what}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p>
          Try: <em>&ldquo;Build the site and deploy ./dist to my-site&rdquo;</em> or{" "}
          <em>&ldquo;The last deploy of my-site broke the nav, roll it back.&rdquo;</em>
        </p>
      </section>

      <section className={styles.section} id="together" aria-labelledby="together-h">
        <h2 id="together-h">Working together</h2>
        <p>
          Invite up to 10 people by their Google email. Once they sign in, they can pull your site, publish new
          versions and make one live. Renaming, deleting versions, unpublishing and managing members stay yours.
        </p>
        <CodeBlock
          code={`spinsite invite my-site sam@example.com   # you, the owner
spinsite pull my-site                      # sam: downloads the live version into ./my-site
spinsite deploy my-site                    # sam: publishes it back as the new live version`}
        />
        <p>
          A pulled folder remembers its site and version in a hidden <code>.spinsite.json</code> (never uploaded), so{" "}
          <code>deploy</code> needs no <code>--site</code>.
        </p>
        <ul>
          <li>
            <strong>There is no merging: the last publish wins.</strong> If someone published since your pull,{" "}
            <code>deploy</code> stops and tells you who. Run <code>spinsite pull</code> to get their version (into a new
            folder if you have changes to carry over), or <code>--force</code> to replace it anyway.
          </li>
          <li>
            Every version records who published it (<code>spinsite versions my-site</code>), and any of them can be
            made live again.
          </li>
          <li>
            <code>spinsite remove-member my-site sam@example.com</code> removes someone; with your own email it leaves a
            site you were invited to.
          </li>
        </ul>
      </section>

      <section className={styles.section} id="safety" aria-labelledby="safety-h">
        <h2 id="safety-h">Keeping it safe</h2>
        <ul>
          <li>
            <strong>A token acts as your account</strong> for your sites: it can share, publish, roll back and delete
            them. It can&apos;t create other tokens or change your account.
          </li>
          <li>
            <strong>Revoke a token</strong> in the app under API tokens. Anything using it stops working right away.
          </li>
          <li>
            <strong>Hidden files and symlinks are never uploaded</strong>, so <code>.env</code>, <code>.git</code> and
            links to files outside the folder stay on your computer. <code>pull</code> never writes hidden files or
            anything outside the folder.
          </li>
          <li>
            <strong>Deployed sites are public</strong> to anyone with the link, and go through the same safety checks
            as sites shared from the app.
          </li>
          <li>
            <strong>Limits:</strong> 4MB zipped, 50MB unzipped and 2000 files per version; 100 uploads a day.
          </li>
        </ul>
        <p>
          Building your own tool? See the <a href={`${REPO}/blob/main/docs/api.md`}>API reference</a>.
        </p>
      </section>
    </main>
  );
}
