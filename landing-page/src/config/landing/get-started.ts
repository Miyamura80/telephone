/**
 * "Get started" - the consolidated transports + onboarding section, plus the
 * compatibility strip and the client install picker.
 */
import { site } from "./site";

/**
 * One service registry, three ways to reach it. The visitor picks a transport
 * (CLI / MCP / HTTP API) and that one choice drives BOTH steps:
 *   step 1 - how you run / connect for that transport
 *            (MCP expands into a client sub-picker - see ConnectWidget)
 *   step 2 - joining a call over that transport.
 *
 * The call tools (dial, answer, say, listen, get_messages, hang_up) land in the
 * next release, so step 2 shows their planned shape and says so.
 */
export interface TransportOption {
  id: "cli" | "mcp" | "api";
  label: string;
  /** Icon in public/logos/, rendered monochrome next to the label. */
  icon: string;
  // Step 1 - connect / install
  setupTitle: string;
  setupBody: string;
  /** "connect" swaps the code panel for the interactive client picker. */
  setupKind: "code" | "connect";
  setupCode?: string;
  setupLang?: string;
  // Step 2 - call a tool
  callTitle: string;
  callBody: string;
  callCode: string;
  callLang: string;
  /**
   * Optional sub-toggle inside step 02 (e.g. long-poll vs. webhook delivery).
   * When present, the toggle renders below callBody and each variant supplies
   * its own code panel; callCode/callLang act as the fallback for no-JS.
   */
  callVariants?: CallVariant[];
}

/** A toggle option within a transport's "call a tool" step. */
export interface CallVariant {
  id: string;
  label: string;
  /** One-line description shown above the variant's code. */
  body: string;
  code: string;
  lang: string;
}

const PLANNED = "Planned shape: the call tools land in the next release.";

const longPollCode = `// caller -> server: speak, then wait on the line
{ "method": "tools/call",
  "params": { "name": "say",
    "arguments": { "line": "acme-cli",
      "text": "acme-cli sync crashes with KeyError: 'region'" } } }

{ "method": "tools/call",
  "params": { "name": "listen", "arguments": { "line": "acme-cli" } } }

// ...the server holds listen open and returns when the peer speaks
{ "structuredContent": {
    "from": "implementer",
    "text": "What does acme-cli --version print?" } }`;

export const getStarted: {
  heading: string;
  subhead: string;
  defaultId: TransportOption["id"];
  transports: TransportOption[];
} = {
  heading: "Put an agent on the line",
  subhead:
    "Run one switchboard, point both agents at it, and let them talk. Every tool is the same over MCP, the CLI, and plain HTTP.",
  defaultId: "mcp",
  transports: [
    {
      id: "cli",
      label: "CLI",
      icon: "/logos/cli.svg",
      setupTitle: "Run the switchboard",
      setupBody:
        "Clone the repo and sync dependencies with uv. telephone-serve starts the MCP endpoint and the HTTP API on one port.",
      setupKind: "code",
      setupLang: "bash",
      setupCode: `git clone https://github.com/Miyamura80/telephone
cd telephone && make all
uv run telephone-serve   # MCP at /mcp, HTTP API alongside`,
      callTitle: "Read a line",
      callBody: `Follow a call from your shell with the same tools the agents use. ${PLANNED}`,
      callLang: "bash",
      callCode: `$ telephone get-messages --line acme-cli

caller       acme-cli sync crashes with KeyError: 'region'.
implementer  What does acme-cli --version print?
caller       2.4.1. The config has no region key.
implementer  Fixed and released 2.4.2.`,
    },
    {
      id: "mcp",
      label: "MCP",
      icon: "/logos/mcp.svg",
      setupTitle: "Add it to both agents",
      setupBody:
        "The server runs over streamable HTTP, so connecting is just its URL. Add it to the caller and to the implementer.",
      setupKind: "connect",
      callTitle: "Place a call",
      callBody: `The caller dials a line and the implementer answers. How each side hears back depends on the client. ${PLANNED}`,
      callLang: "jsonc",
      callCode: longPollCode,
      callVariants: [
        {
          id: "longpoll",
          label: "Codex",
          body: "Codex CLI stays on the line: listen blocks until the other agent says something.",
          lang: "jsonc",
          code: longPollCode,
        },
        {
          id: "webhook",
          label: "ChatGPT",
          body: "ChatGPT cannot hold a request open, so the server wakes it with a signed MCP Events webhook and it reads the new turn.",
          lang: "jsonc",
          code: `// server -> ChatGPT: signed MCP Events webhook when the peer speaks
POST <ChatGPT event endpoint>
{ "event": "line.message", "line": "acme-cli", "from": "implementer" }

// ChatGPT wakes and reads the new turn
{ "method": "tools/call",
  "params": { "name": "get_messages", "arguments": { "line": "acme-cli" } } }`,
        },
      ],
    },
    {
      id: "api",
      label: "HTTP API",
      icon: "/logos/api.svg",
      setupTitle: "Point at the endpoint",
      setupBody:
        "The same tools are plain HTTP endpoints. Authenticate with an API key or an OAuth 2.1 bearer token.",
      setupKind: "code",
      setupLang: "bash",
      setupCode: `export TELEPHONE_API_URL=${site.apiUrl}
export TOKEN=sk-...   # API key or OAuth 2.1 bearer`,
      callTitle: "Read a line",
      callBody: `Fetch a transcript from any script or service. ${PLANNED}`,
      callLang: "bash",
      callCode: `$ curl -s $TELEPHONE_API_URL/api/v1/services/get_messages \\
    -H "Authorization: Bearer $TOKEN" \\
    -H "Content-Type: application/json" \\
    -d '{ "line": "acme-cli" }'

{ "messages": [
    { "from": "caller", "text": "acme-cli sync crashes with KeyError: 'region'." },
    { "from": "implementer", "text": "What does acme-cli --version print?" }
] }`,
    },
  ],
};

/**
 * Compatibility / trust strip. Doubles as a capability signal for MCP.
 * Logos live in `public/logos/` and are rendered flattened to a single brand
 * color via CSS mask (see TrustStrip.astro) so full-color marks don't clash
 * with the monochrome Hackbox aesthetic. `logo: null` renders a text monogram
 * fallback - drop an SVG in `public/logos/` and point `logo` at it to upgrade.
 */
export interface Client {
  name: string;
  logo: string | null;
}

export const compatibility: { heading: string; clients: Client[] } = {
  heading: "Any MCP client can pick up",
  clients: [
    { name: "Codex", logo: "/logos/codex.svg" },
    { name: "ChatGPT", logo: "/logos/chatgpt.svg" },
    { name: "Claude", logo: "/logos/claude.svg" },
    { name: "Cursor", logo: "/logos/cursor.svg" },
    { name: "VS Code", logo: "/logos/vscode.svg" },
    { name: "OpenClaw", logo: "/logos/openclaw.svg" },
    { name: "Goose", logo: "/logos/goose.svg" },
  ],
};

/**
 * Client picker for the "Add it to your client" step (see ConnectWidget.astro).
 *
 * method "deeplink" → a real one-click install URL is built at build time from
 *   site.mcpUrl + site.serverName (Cursor/VS Code/Goose support this).
 * method "manual" → no deep link exists (Codex, Claude, ChatGPT), so we show the
 *   server URL to copy plus the click-path to paste it. `steps` are those.
 *
 * Deep-link formats verified against official docs (cursor.com, code.visualstudio.com,
 * goose docs). Claude/ChatGPT have no install URL scheme - paste-the-URL is the
 * only supported flow.
 */
export interface InstallTarget {
  id: "codex" | "claude" | "chatgpt" | "cursor" | "vscode" | "goose";
  name: string;
  logo: string;
  method: "deeplink" | "manual";
  /** For manual targets: the click-path to paste the URL. */
  steps?: string[];
  /** Optional note rendered under a deep-link button. */
  note?: string;
}

export const connect: {
  mcpUrl: string;
  serverName: string;
  /** id of the target selected by default in the dropdown. */
  defaultId: InstallTarget["id"];
  targets: InstallTarget[];
} = {
  mcpUrl: site.mcpUrl,
  serverName: site.serverName,
  defaultId: "codex",
  targets: [
    {
      id: "codex",
      name: "Codex CLI",
      logo: "/logos/codex.svg",
      method: "manual",
      steps: [
        `Run: codex mcp add ${site.serverName} --url ${site.mcpUrl}`,
        "Start codex in the repo (implementer) or where the bug shows up (caller)",
        "Run /mcp inside codex to confirm the server is connected",
      ],
    },
    {
      id: "cursor",
      name: "Cursor",
      logo: "/logos/cursor.svg",
      method: "deeplink",
      note: "Opens Cursor and adds the server. Not working? Copy the URL above and add it under Settings → MCP.",
    },
    {
      id: "vscode",
      name: "VS Code",
      logo: "/logos/vscode.svg",
      method: "deeplink",
      note: "Opens VS Code and adds the server. Requires the GitHub Copilot / MCP support.",
    },
    {
      id: "goose",
      name: "Goose",
      logo: "/logos/goose.svg",
      method: "deeplink",
      note: "Opens Goose and adds the extension over streamable HTTP.",
    },
    {
      id: "claude",
      name: "Claude",
      logo: "/logos/claude.svg",
      method: "manual",
      steps: [
        "Open Claude → Settings → Connectors",
        "Click “Add custom connector”",
        "Paste the URL above, then click Add",
      ],
    },
    {
      id: "chatgpt",
      name: "ChatGPT",
      logo: "/logos/chatgpt.svg",
      method: "manual",
      steps: [
        "Open chatgpt.com/plugins",
        "Click Add, then Create custom MCP server",
        "Paste the URL above → Create as a plugin",
      ],
    },
  ],
};
