import { createElement } from "react";
import { docs } from "fumadocs-mdx:collections/server";
import { loader } from "fumadocs-core/source";
import { i18n } from "@/lib/i18n";
import { ChatGPTIcon, ClaudeIcon } from "@/components/icons";
import { mdxBodyToMarkdown } from "@/lib/mdx-to-markdown";

// Custom SVG icons resolved from a page's `icon:` frontmatter field. Add an
// entry here, then set `icon: <key>` in the page frontmatter to show it in the
// sidebar.
const iconMap = {
  claude: ClaudeIcon,
  chatgpt: ChatGPTIcon,
} as const;

export const source = loader({
  baseUrl: "/docs",
  source: docs.toFumadocsSource(),
  i18n,
  icon(icon) {
    if (icon && icon in iconMap) {
      return createElement(iconMap[icon as keyof typeof iconMap]);
    }
  },
});

export function getPageImage(page: ReturnType<typeof source.getPage> & {}) {
  const allSegments = page.url.split("/").filter(Boolean);
  // Strip locale and "docs" prefix for the slug param (they're separate route params)
  const docSegments = allSegments.filter(
    (s) => s !== page.locale && s !== "docs",
  );
  return {
    url: `/og/${allSegments.join("/")}/og.png`,
    segments: [...docSegments, "og.png"],
  };
}

export async function getLLMText(
  page: ReturnType<typeof source.getPage> & {}
): Promise<string> {
  const raw = await page.data.getText("raw");
  const body = mdxBodyToMarkdown(raw);
  const heading = `# ${page.data.title}`;
  const description = page.data.description ?? "";
  return [heading, description, body].filter(Boolean).join("\n\n");
}
