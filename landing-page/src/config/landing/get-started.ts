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
 * Step 2 uses the six shipped call tools (dial, answer, say, listen,
 * get_messages, hang_up). Keep the payloads in step with models/telephone.py.
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

const longPollCode = `// caller -> server: dial, then wait on the line (up to wait_s)
{ "method": "tools/call",
  "params": { "name": "dial",
    "arguments": { "line": "acme-cli",
      "message": "acme-cli sync crashes with KeyError: 'region'" } } }

// ...the server holds dial open and returns when the peer speaks
{ "structuredContent": {
    "outcome": "message",
    "seat": "<seat token>",
    "messages": [ { "seq": 2, "sender": "implementer",
      "text": "What does acme-cli --version print?" } ],
    "next_step": "Answer their questions with say (it waits for the reply). ..." } }

// reply on the same seat; say waits for the next turn too
{ "method": "tools/call",
  "params": { "name": "say",
    "arguments": { "line": "acme-cli", "seat": "<seat token>",
      "text": "2.4.1. The config has no region key." } } }`;

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
      callTitle: "Watch a call",
      callBody:
        "Run the bundled eval: two agents fix a broken acme-cli over a real line. Every call is logged as JSONL under .telephone/transcripts/.",
      callLang: "bash",
      callCode: `$ make eval_codex_call_fake   # scripted agents, no LLM
$ make eval_codex_call        # two real Codex CLIs

$ tail -n 2 .telephone/transcripts/acme-cli__*.jsonl
{"event": "message", "seq": 5, "sender": "caller", "text": "Re-ran it: done, 3 items synced.", ...}
{"event": "ended", "reason": "caller hung up: fixed", "resolved": true, ...}`,
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
      callBody:
        "The caller dials a line and the implementer answers. Both get a seat token to pass on every later call. How each side hears back depends on the client.",
      callLang: "jsonc",
      callCode: longPollCode,
      callVariants: [
        {
          id: "longpoll",
          label: "Codex",
          body: "Codex CLI stays on the line: dial, answer, say and listen block up to wait_s (50s default) until the other agent speaks. Set tool_timeout_sec = 600 for long calls.",
          lang: "jsonc",
          code: longPollCode,
        },
        {
          id: "webhook",
          label: "MCP Events",
          body: "Event-driven clients subscribe to line.message and get a signed webhook carrying the message when the peer speaks. ChatGPT is the intended client; this path has not yet been tried against live ChatGPT.",
          lang: "jsonc",
          code: `// client -> server: subscribe for its seat (draft MCP Events)
{ "method": "events/subscribe",
  "params": { "name": "line.message",
    "arguments": { "line": "acme-cli", "seat": "<seat token>" },
    "delivery": { "mode": "webhook", "url": "https://client.example.com/hook",
                  "secret": "whsec_<client-generated base64 secret>" } } }

// server -> client when the peer speaks (Standard Webhooks)
POST https://client.example.com/hook
webhook-id: <event id>
webhook-timestamp: <unix seconds>
webhook-signature: v1,<base64 HMAC-SHA256 of "id.timestamp.body">
X-MCP-Subscription-Id: <subscription id>
{ "name": "line.message", "data": { "message": { "text": "What does --version print?", ... } } }

// client wakes, reads data.message, replies without waiting
{ "method": "tools/call",
  "params": { "name": "say",
    "arguments": { "line": "acme-cli", "seat": "<seat token>",
                   "text": "acme-cli 2.4.1", "wait_s": 0 } } }`,
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
      callBody:
        "Fetch a transcript from any script or service with your seat token. dial, answer, say, listen and hang_up change state, so they also need an Idempotency-Key header.",
      callLang: "bash",
      callCode: `$ curl -s $TELEPHONE_API_URL/api/v1/services/get_messages \\
    -H "Authorization: Bearer $TOKEN" \\
    -H "Content-Type: application/json" \\
    -d '{ "line": "acme-cli", "seat": "<seat token>" }'

{ "line": "acme-cli", "status": "connected", "turns": 2, "max_turns": 60,
  "messages": [
    { "seq": 1, "sender": "caller", "text": "acme-cli sync crashes with KeyError: 'region'.", ... },
    { "seq": 2, "sender": "implementer", "text": "What does acme-cli --version print?", ... }
  ], ... }   # abbreviated`,
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
