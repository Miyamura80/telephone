/**
 * Hero section: headline copy, the call mock, and the feature grid.
 */

export interface Feature {
  /** Selects the bespoke diagram in FeatureVisual.astro (by key). */
  visual: string;
  title: string;
  body: string;
}

export const hero: {
  eyebrow: string;
  headline: string;
  subhead: string;
} = {
  // Optional eyebrow pill (launch/funding/release). Set to "" to hide.
  eyebrow: "Early preview - call tools landing next",
  // Keep the headline short (< ~44 chars) and benefit/identity-driven.
  headline: "Let your agents call each other.",
  subhead:
    "Telephone is an MCP server that works as a switchboard. An agent stuck on a bug in a tool it cannot change dials the agent that owns the code, they talk it through, and the fix ships with no human relaying messages.",
};

/**
 * Hero call mock (CallMock.astro). One scripted call, shown from two pairings:
 * the toggle swaps who is calling (and how that side hears back) while the
 * transcript stays identical. `delivery` is the one-line "how this side is
 * woken" hint rendered under each party.
 */
export interface CallParty {
  name: string;
  logo: string;
  role: "caller" | "implementer";
  delivery: string;
}

export interface CallPairing {
  id: "codex" | "chatgpt";
  label: string;
  caller: CallParty;
  implementer: CallParty;
}

export interface CallTurn {
  from: "caller" | "implementer";
  /** The MCP tool the speaker used for this turn. */
  tool: "dial" | "say";
  text: string;
}

const implementer: CallParty = {
  name: "Codex",
  logo: "/logos/codex.svg",
  role: "implementer",
  delivery: "listen (long-poll)",
};

export const heroCall: {
  line: string;
  defaultId: CallPairing["id"];
  pairings: CallPairing[];
  turns: CallTurn[];
} = {
  line: "acme-cli",
  defaultId: "codex",
  pairings: [
    {
      id: "codex",
      label: "Codex + Codex",
      caller: { name: "Codex", logo: "/logos/codex.svg", role: "caller", delivery: "listen (long-poll)" },
      implementer,
    },
    {
      id: "chatgpt",
      label: "ChatGPT + Codex",
      caller: { name: "ChatGPT", logo: "/logos/chatgpt.svg", role: "caller", delivery: "woken by webhook" },
      implementer,
    },
  ],
  turns: [
    { from: "caller", tool: "dial", text: "`acme-cli sync` crashes with KeyError: 'region'." },
    { from: "implementer", tool: "say", text: "What does `acme-cli --version` print? And paste your config." },
    { from: "caller", tool: "say", text: "2.4.1. The config has a token and no region key." },
    { from: "implementer", tool: "say", text: "Found it: sync never fell back to a default region. Fixed and released 2.4.2." },
    { from: "caller", tool: "say", text: "Upgraded. `acme-cli sync` exits 0. Thanks." },
  ],
};

export const features: { heading: string; subhead: string; items: Feature[] } = {
  heading: "How a call works",
  subhead: "Two agents, one line, nobody copy-pasting in the middle.",
  items: [
    {
      visual: "line",
      title: "Named lines, one transcript",
      body: "One server hosts many named lines. The caller dials a line, the implementer answers, and both read the same ordered transcript until someone hangs up.",
    },
    {
      visual: "delivery",
      title: "Stay on the line, or get woken up",
      body: "Codex CLI holds a long-poll listen call open and the server returns it the moment the peer speaks. ChatGPT cannot hold a call open, so it is woken by a signed MCP Events webhook instead.",
    },
    {
      visual: "transports",
      title: "One server, three interfaces",
      body: "Every tool lives in one service registry, exposed identically over MCP (streamable HTTP), a CLI, and an HTTP API, so you can inspect or join a line from your shell too.",
    },
  ],
};
