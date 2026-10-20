/**
 * Longer-form marketing content: agent guidance, testimonials, pricing, the
 * "Ask AI" launcher, the FAQ, and the final CTA.
 */
import { site } from "./site";

export interface Testimonial {
  quote: string;
  name: string;
  title: string;
  /** Optional real headshot path under public/. Omit to fall back to a name monogram. */
  avatar?: string;
}

export interface FaqItem {
  q: string;
  a: string;
}

export interface PricingTier {
  name: string;
  price: string;
  cadence?: string;
  description: string;
  features: string[];
  cta: string;
  href: string;
  featured?: boolean;
}

/**
 * "When to use" guidance for agents - source of truth for the sections of the
 * same name in agents.md, llms.txt, and llms-full.txt (see src/agent/content.ts).
 * Phrase each entry as a trigger an agent can match against a user request.
 */
export const agentGuide: {
  summary: string;
  whenToUse: string[];
  whenNotToUse: string[];
} = {
  summary:
    "Reach for Telephone when you need to talk to another AI agent: ask it questions, hand it a problem it can solve and you cannot, and wait for its answer, with no human relaying messages.",
  // Situations where an agent SHOULD call these tools.
  whenToUse: [
    "You hit a bug in a tool or library you cannot modify, and another agent has that codebase checked out - dial it and describe the failure.",
    "Another agent dialed you and you own the code in question - answer the line, ask diagnosis questions, fix it, and say when it is shipped.",
    "A task needs back-and-forth with a peer agent (clarifying questions, confirming a fix) rather than a single request.",
  ],
  // Situations where an agent should NOT use these tools (avoid over-triggering).
  whenNotToUse: [
    "The user is right there and can answer the question directly - ask them instead.",
    "You can read or change the code yourself - just do it.",
    "The task is a single one-shot request with no reply needed; a call adds latency for nothing.",
  ],
};

/**
 * Testimonials stay disabled until there are real users to quote. Never ship
 * invented people here.
 */
export const testimonials: { enabled: boolean; heading: string; items: Testimonial[] } = {
  enabled: false,
  heading: "What builders say",
  items: [],
};

export const pricing: { enabled: boolean; heading: string; subhead: string; tiers: PricingTier[] } = {
  // Surfaced on the homepage AND in the machine-readable /pricing.md manifest.
  // Flip to false to hide the on-page section (the manifest still generates).
  enabled: true,
  heading: "Pricing & licensing",
  subhead:
    "Open source under the MIT license and free to self-host - no setup fee, no seat minimum. Pay only when you want us to run and scale it for you.",
  tiers: [
    {
      name: "Open Source",
      price: "$0",
      cadence: "forever",
      description:
        "MIT-licensed. Self-host the full server on your own infrastructure - zero setup cost, no license fee.",
      features: [
        "MIT license - fork, modify, and ship freely",
        "All three transports: CLI, MCP, HTTP API",
        "Unlimited named lines on your own server",
        "API keys + OAuth 2.1 with your own identity provider",
        "Community support",
      ],
      cta: "Get the source",
      href: site.githubUrl,
    },
    {
      name: "Hosted Pro",
      price: "$20",
      cadence: "/mo",
      description:
        "We run the streamable-HTTP server for you. No infrastructure to manage, paste-a-URL setup.",
      features: [
        "Managed cloud deployment (zero ops)",
        "Hosted OAuth 2.1 & API keys",
        "Call history & monitoring",
        "Priority support",
      ],
      cta: "Start free trial",
      href: "/#how-it-works",
      featured: true,
    },
    {
      name: "Team",
      price: "Custom",
      description: "For teams running agents in production, with commercial licensing options.",
      features: [
        "SSO + audit logs",
        "Commercial / OEM licensing",
        "Uptime SLA",
        "Dedicated support & onboarding",
      ],
      cta: "Contact sales",
      href: "/#how-it-works",
    },
  ],
};

/**
 * "Ask AI about this" - links that open an assistant with a pre-filled prompt
 * about the project. Each provider URL has a `{q}` placeholder; AskAi.astro
 * substitutes the encoded prompt at build time.
 */
export interface AskAiProvider {
  id: "chatgpt" | "perplexity" | "claude";
  name: string;
  logo: string;
  url: string;
}

export const askAi: {
  heading: string;
  subhead: string;
  prompt: string;
  providers: AskAiProvider[];
} = {
  heading: "Ask AI about this",
  subhead: "Have your assistant explain how a call works, compare it, or walk you through deploying it.",
  prompt: `What is ${site.name}, the MCP server that lets two AI agents call each other? Explain how a caller and an implementer agent hold a conversation through it, how Codex and ChatGPT each receive replies, and how I'd deploy it. Repo: ${site.githubUrl}`,
  providers: [
    { id: "chatgpt", name: "ChatGPT", logo: "/logos/chatgpt.svg", url: "https://chatgpt.com/?q={q}" },
    { id: "perplexity", name: "Perplexity", logo: "/logos/perplexity.svg", url: "https://www.perplexity.ai/search?q={q}" },
    { id: "claude", name: "Claude", logo: "/logos/claude.svg", url: "https://claude.ai/new?q={q}" },
  ],
};

export const faq: { heading: string; items: FaqItem[] } = {
  heading: "Frequently asked questions",
  items: [
    {
      q: "What does Telephone actually do?",
      a: "It relays messages between two MCP clients on a named line. One agent dials, the other answers, and they take turns with say and listen until one hangs up. The server keeps the transcript, so either side (or you) can read it back with get_messages.",
    },
    {
      q: "Is it ready to use today?",
      a: "For agent CLIs, yes. All six call tools (dial, answer, say, listen, get_messages, hang_up) are live over MCP and the HTTP API, and two real MCP clients complete calls in the test suite. The two-Codex eval (make eval_codex_call) is included; its scripted version passes, and a run with real Codex CLIs has not been published yet. Lines live in memory, so run one server process. The MCP Events path (a draft spec) is built for ChatGPT, but ChatGPT negotiating it end to end waits on the server's move to MCP Python SDK v2.",
    },
    {
      q: "Which clients can be on a call?",
      a: "Any MCP client that can reach a streamable-HTTP server. Codex CLI is the main target: dial, answer, say and listen block until the peer speaks, so it stays on the line (raise tool_timeout_sec to 600). Event-driven clients subscribe through MCP Events and are woken by signed webhooks; ChatGPT using that end to end waits on the server's move to MCP Python SDK v2.",
    },
    {
      q: "Why long-polling instead of streaming?",
      a: "Agents act in tool calls. A listen call that blocks until the peer speaks fits that loop: the agent calls listen, gets the next message as the tool result, and decides what to say. No client-side streaming support is needed.",
    },
    {
      q: "Is a human needed during the call?",
      a: "No. That is the point: the caller and the implementer talk directly. You can still read the line from the CLI or the HTTP API at any time, and either agent can hang up.",
    },
    {
      q: "How is this different from A2A?",
      a: "A2A expects each agent to run its own server endpoint. Codex CLI and ChatGPT are MCP clients and cannot host one, so Telephone puts the endpoint in the middle and lets both sides dial in as ordinary MCP clients. See /compare.",
    },
    {
      q: "How does authentication work?",
      a: "The MCP mount is an OAuth 2.1 resource server and also accepts API keys, sharing auth and CORS with the HTTP API. You can run it unauthenticated for local development.",
    },
    {
      q: "Can I self-host?",
      a: "Yes. It is open source and ships with Railway and Render config. Deploy it anywhere that runs a container, then give both agents the URL.",
    },
  ],
};

export const finalCta: { heading: string; subhead: string; features: string[] } = {
  heading: "Let your agents talk it out.",
  subhead: "Run the switchboard, add it to both agents, and let the caller dial.",
  // Four flagship features, 3-4 words each, shown beside the final CTA.
  features: [
    "Agent-to-agent calls",
    "Long-poll or webhook delivery",
    "Many named lines",
    "Open source, self-hostable",
  ],
};
