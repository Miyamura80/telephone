/**
 * Competitive comparison - powers the on-page comparison section
 * (Comparison.astro), the /compare hub, the per-competitor /vs/<slug> pages,
 * and the machine-readable "How it compares" block in llms-full.txt / agents.md.
 *
 * Editing this one object updates every comparison surface at once.
 *
 * Honesty rules (keep us credible + AEO-citable):
 * - `pickThem` must give a genuine, fair reason to choose the competitor.
 * - Matrix claims should be defensible as of `asOf`; competitor capabilities
 *   shift fast, so date-stamp and keep cells verifiable, not slanted.
 */
export type MatrixState = "yes" | "no" | "partial";

export interface MatrixCell {
  state: MatrixState;
  /** Optional short qualifier shown under the mark. */
  note?: string;
}

export interface MatrixRow {
  /** Capability being compared. */
  capability: string;
  /** One-line explanation of why this capability matters. */
  detail?: string;
  /** Our value. */
  us: MatrixCell;
  /** Per-competitor value, keyed by competitor `id`. */
  cells: Record<string, MatrixCell>;
}

export interface Competitor {
  /** URL slug → /vs/<id>. */
  id: string;
  name: string;
  /** Bucket label, e.g. "Open-source wrapper". */
  category: string;
  /** One line: what they are. */
  blurb: string;
  /** Canonical link to the alternative. Omit for DIY approaches with no home page. */
  url?: string;
  /** Headline contrast for the /vs page hero. */
  headline: string;
  /** Paragraph summary of the difference. */
  summary: string;
  /** Honest "when to pick them" - keeps the page credible + citable. */
  pickThem: string;
  /** "when to pick us". */
  pickUs: string;
}

export interface ComparisonPillar {
  title: string;
  body: string;
}

export const comparison: {
  /** Section + page headings. */
  heading: string;
  subhead: string;
  /** Defensibility stamp surfaced on the comparison pages. */
  asOf: string;
  disclaimer: string;
  /** The three headline differentiators. */
  pillars: ComparisonPillar[];
  competitors: Competitor[];
  matrix: MatrixRow[];
} = {
  heading: "How Telephone compares",
  subhead:
    "There are other ways to get two agents talking. Most assume one side can host a server, or that a person will carry messages between chats. Telephone is built for agents that are only MCP clients.",
  asOf: "October 2026",
  disclaimer:
    "Comparison reflects publicly documented capabilities as of October 2026. Telephone's MCP Events path follows a draft spec and has not yet been tried against live ChatGPT. If something here is out of date, open an issue and we'll fix it.",
  pillars: [
    {
      title: "Built for MCP clients",
      body: "Codex CLI and ChatGPT are MCP clients: they call servers, they cannot host one. Telephone puts the endpoint in the middle, so both agents join a call with ordinary tool calls and nothing extra to run on their side.",
    },
    {
      title: "A real back-and-forth",
      body: "Either side can ask, answer, and follow up over as many turns as it takes, on a named line with one shared transcript. The implementer can ask for a version string or a config file before it touches the code, and the caller confirms the fix at the end.",
    },
    {
      title: "Delivery that fits each client",
      body: "Clients that can wait hold a long-poll listen call open and get the next message as the tool result. Clients that cannot, like ChatGPT, are woken by a signed MCP Events webhook. Same line, same transcript either way.",
    },
  ],
  competitors: [
    {
      id: "a2a",
      name: "A2A protocol",
      category: "Open protocol",
      blurb: "Google's Agent2Agent protocol: agents publish an Agent Card and expose a server endpoint other agents call.",
      url: "https://a2a-protocol.org/",
      headline: "A2A connects agent servers. Telephone connects agent clients.",
      summary:
        "A2A is a well-specified protocol for agents that run as services: each one publishes an Agent Card and an endpoint, and peers send it tasks. That is a good fit for hosted agents. A Codex CLI session on a laptop, or a ChatGPT conversation, has no endpoint to publish. Telephone lets those clients reach each other by both dialing into one MCP server.",
      pickThem:
        "your agents already run as services you control, and you want a standard, vendor-neutral task protocol between them.",
      pickUs:
        "your agents are MCP clients like Codex CLI or ChatGPT, and you want them talking without wrapping each one in a server.",
    },
    {
      id: "codex-mcp-server",
      name: "codex mcp-server",
      category: "Agent as a tool",
      blurb: "Run Codex itself as an MCP server, so another agent can start a Codex session as a tool call.",
      url: "https://github.com/openai/codex",
      headline: "Calling an agent vs. talking with one.",
      summary:
        "Wrapping Codex as an MCP server turns it into a tool: the caller sends a prompt, a fresh Codex session runs, and a result comes back, with a follow-up call to continue. The caller drives every turn, and the spawned session runs wherever the caller hosts it. With Telephone, the implementer is an agent that is already running in its own repo, and either side can speak next.",
      pickThem:
        "you want one agent to delegate a self-contained task to a new Codex session and collect the result.",
      pickUs:
        "the other agent already exists in its own environment, and you need it to ask questions back before it acts.",
    },
    {
      id: "shared-file",
      name: "Shared scratch file",
      category: "DIY",
      blurb: "Both agents read and append to a file or folder they can both see, by convention.",
      headline: "A switchboard instead of a polling convention.",
      summary:
        "A shared NOTES.md or scratch folder works when both agents run on the same machine and you tell each to check it. Nothing wakes the other side, turn-taking is whatever the prompts say, and a hosted client like ChatGPT cannot see the file at all. Telephone gives you named lines, ordered turns, and delivery that waits or wakes the peer.",
      pickThem:
        "both agents share a filesystem and you are fine nudging them to re-read the file.",
      pickUs:
        "the agents run in different places, one of them is a hosted client, or you do not want to babysit the turn-taking.",
    },
    {
      id: "human-relay",
      name: "Copy-paste relay",
      category: "Manual",
      blurb: "A person copies each message from one chat window and pastes it into the other.",
      headline: "Take yourself out of the loop.",
      summary:
        "Copy-pasting between two chats works with every client and needs no setup, which is why everyone does it. It also means you are the message bus: every diagnosis question waits for you to notice it, and the transcript is scattered across two windows. Telephone lets the agents carry the conversation and keeps one transcript you can read whenever you want.",
      pickThem:
        "it is a one-off exchange, or you want to review every message before the other agent sees it.",
      pickUs:
        "the exchange takes several rounds, or you would rather come back to a fixed bug than to a queue of questions.",
    },
  ],
  matrix: [
    {
      capability: "Works with MCP-only clients",
      detail: "Codex CLI and ChatGPT can take part without hosting anything.",
      us: { state: "yes" },
      cells: {
        a2a: { state: "no", note: "each agent hosts a server" },
        "codex-mcp-server": { state: "partial", note: "callee must be Codex" },
        "shared-file": { state: "partial", note: "needs a shared disk" },
        "human-relay": { state: "yes" },
      },
    },
    {
      capability: "Either side can speak next",
      detail: "The callee can ask questions back, not just return a result.",
      us: { state: "yes" },
      cells: {
        a2a: { state: "yes" },
        "codex-mcp-server": { state: "partial", note: "caller drives turns" },
        "shared-file": { state: "partial", note: "by convention" },
        "human-relay": { state: "yes" },
      },
    },
    {
      capability: "No human relaying messages",
      detail: "Agents hear each other without someone passing notes.",
      us: { state: "yes" },
      cells: {
        a2a: { state: "yes" },
        "codex-mcp-server": { state: "yes" },
        "shared-file": { state: "partial", note: "someone nudges" },
        "human-relay": { state: "no" },
      },
    },
    {
      capability: "Callee keeps its own repo and context",
      detail: "The implementer is an agent already working in its codebase.",
      us: { state: "yes" },
      cells: {
        a2a: { state: "yes" },
        "codex-mcp-server": { state: "partial", note: "new session" },
        "shared-file": { state: "yes" },
        "human-relay": { state: "yes" },
      },
    },
    {
      capability: "One shared transcript",
      detail: "Both sides, and you, read the same ordered conversation.",
      us: { state: "yes" },
      cells: {
        a2a: { state: "partial", note: "per-task history" },
        "codex-mcp-server": { state: "partial", note: "caller's context only" },
        "shared-file": { state: "yes" },
        "human-relay": { state: "no", note: "two chat windows" },
      },
    },
    {
      capability: "Available today",
      detail: "Usable end to end right now.",
      us: { state: "yes", note: "ChatGPT events not yet tried live" },
      cells: {
        a2a: { state: "yes" },
        "codex-mcp-server": { state: "yes" },
        "shared-file": { state: "yes" },
        "human-relay": { state: "yes" },
      },
    },
  ],
};
