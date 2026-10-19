/**
 * Support & contact: the self-serve troubleshooting and the human channels a
 * user reaches when something breaks or they have a question.
 *
 * Data-driven like the rest of the config: this one module feeds the `/support`
 * page, its `/help` and `/contact` aliases, the `/support.md` twin, and the
 * `## Support` section of llms.txt / agents.md / llms-full.txt. Point it at a
 * real inbox (see the TODO below) and it is live everywhere.
 *
 * Order is deliberate: troubleshooting first (most "issues" are setup, and an
 * agent or user should self-serve before opening a ticket), then the channels.
 */
import { site } from "./site";

// TODO: your real support inbox. This placeholder is intentionally not a live
// address - replace it (e.g. support@yourdomain.com) before you ship, or the
// "Email support" channel points nowhere.
export const supportEmail = "support@example.com";

export interface TroubleshootItem {
  /** Phrased as the symptom a user (or agent) would search for. */
  q: string;
  /**
   * Plain-text answer. NO markdown backticks or link syntax: this string
   * renders verbatim into the HTML page as well as into support.md, so markup
   * that survives to the browser shows up as literal characters.
   */
  a: string;
  /** Optional deep link the answer points at. */
  href?: string;
  hrefLabel?: string;
}

export interface SupportChannel {
  /** Link / button text. */
  cta: string;
  href: string;
  /** Terse routing hint - which door this is. Keep it to a few words. */
  blurb: string;
  /**
   * Reused mark for the CTA: a logo in public/logos/, or the built-in "mail"
   * glyph. Only rendered for the `primary` channel (the one drawn as a button);
   * omit it on secondary channels, which render as plain text links.
   */
  icon?: "github" | "mail";
  /**
   * Exactly one channel is `primary`: it renders as the single accent CTA.
   * Every other channel is a quiet secondary link (see the design rule "one
   * CTA per view"). This template's audience is developers, so GitHub Issues
   * leads and email is the fallback.
   */
  primary: boolean;
}

export const support: {
  title: string;
  issuesHeading: string;
  troubleshooting: TroubleshootItem[];
  channelsHeading: string;
  channels: SupportChannel[];
} = {
  title: "Support & contact",
  issuesHeading: "Common issues",
  // TODO: replace with the issues your users actually hit. These cover the
  // failure modes common to any remote MCP server.
  troubleshooting: [
    {
      q: "The server will not connect, or my client rejects the URL",
      a: `${site.name} is a remote streamable-HTTP server, not a local stdio one. Add the endpoint ${site.mcpUrl} exactly, with no trailing path of your own. The server name is ${site.serverName}.`,
      href: "/#how-it-works",
      hrefLabel: "How it works",
    },
    {
      q: "Authentication fails, or my client keeps asking me to reconnect",
      a: `The MCP endpoint is an OAuth 2.1 resource server: your client completes consent in the browser (or you present an API key). If it loops, reconnect from your client, or revoke and re-grant access, then try again. See auth.md for the full recipe.`,
      href: "/auth.md",
      hrefLabel: "Agent auth (auth.md)",
    },
    {
      q: "The tools do not appear after I connect",
      a: `Once your client finishes auth, the tools register automatically - list them with the MCP tools/list method (most clients do this for you). If nothing shows, the connection did not complete: remove the server and add it again.`,
    },
  ],
  channelsHeading: "Still stuck?",
  channels: [
    {
      cta: "Open a GitHub issue",
      href: `${site.githubUrl}/issues`,
      blurb: "Public bugs and feature requests",
      icon: "github",
      primary: true,
    },
    {
      // TODO: keep this in step with `supportEmail` above.
      cta: "Email support",
      href: `mailto:${supportEmail}`,
      blurb: "Billing, account, or anything private",
      primary: false,
    },
  ],
};
