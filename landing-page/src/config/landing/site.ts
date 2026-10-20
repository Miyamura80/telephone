/**
 * Core product identity + pre-connect registry branding.
 *
 * Edit `site` to re-brand the whole landing page: every other config module
 * derives its links and copy from these values, so this is the one place to
 * point at a real product.
 */

export const site = {
  // TODO: product identity
  name: "Telephone",
  tagline: "A switchboard for AI agents",
  // Used for <title>, meta description, and OG tags.
  description:
    "Telephone is an MCP server that acts as a switchboard, so two AI agents (two Codex CLI sessions, or ChatGPT and Codex) can hold a two-way conversation with no human relaying messages.",
  // TODO: the canonical deployed URL (also set `site` in astro.config.mjs).
  url: "https://telephone.example.com",
  // TODO: links used across nav, footer, and CTAs.
  docsUrl: "https://docs.telephone.example.com",
  githubUrl: "https://github.com/Miyamura80/telephone",
  // TODO: the deployed streamable-HTTP MCP endpoint users add to their client.
  // This is the URL you paste / one-click-install into Codex, ChatGPT, etc.
  mcpUrl: "https://mcp.telephone.example.com/mcp",
  // TODO: the deployed HTTP API base URL (same backend, vanity host for REST).
  apiUrl: "https://api.telephone.example.com",
  // Server name used in client configs / deep links (no spaces).
  serverName: "telephone-mcp",
} as const;

/**
 * Pre-connect registry branding (SEP-2127 Server Card + MCP registry server.json).
 *
 * `scripts/gen-discovery.ts` reads this (plus `site`) at build time and writes
 * `public/.well-known/mcp/server-card.json` and `public/server.json`. Those are
 * what MCP registries, client "add server" directories, and AI crawlers read to
 * show your server's name, icon, and description BEFORE anyone connects.
 *
 * Title, description, website, repo URL, icon, and the MCP endpoint are all
 * derived from `site` above so you brand the product in one place. The fields
 * below have no marketing-copy equivalent, so they live here. (The advertised
 * `tools[]` surface is NOT here - it is generated from the Python `@service`
 * registry into `tool-surface.generated.json`; see `scripts/gen-discovery.ts`.)
 */
export const serverCard = {
  // Reverse-DNS registry identity, exactly one slash. Usually io.github.<owner>/<repo>.
  name: "io.github.Miyamura80/telephone",
  // SemVer - keep in step with pyproject.toml / server.json when you release.
  version: "0.1.1",
  // Concise capability summary (<=100 chars for the registry server.json schema).
  description: "A switchboard MCP server that lets two AI agents call each other and talk it out.",
  // repository.source value the MCP registry expects ("github" | "gitlab" | ...).
  repositorySource: "github",
} as const;
