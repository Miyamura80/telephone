"""Render the docs site's tool reference from the `@service` registry.

The tool surface has one source of truth - the registry - and the docs restated
it by hand. That drifted to 5 tools against a 34-tool registry with nothing
noticing, which is what issue #233 was about.

The first attempt at a fix parsed `docs/content/docs/mcp/tools.mdx` back into a
set of names and compared. That runs the pipeline backwards, and inferring
structured data from prose leaks: a pipe table inside a code fence, inside a
*deeply indented* code fence, or inside an MDX `{/* */}` comment all read as
live documentation, so a row could be deleted while a commented-out copy kept
the check green. Each was a separate patch to the same parser.

So: generate the region instead. Everything between the markers in
``TOOLS_PAGE`` is rendered from ``llm_tool_surface()`` plus the enhancer
registry, and ``--check`` fails when the file on disk disagrees. There is no
prose left to misread, and the "N tools" count is interpolated rather than
regex-matched, so it cannot go stale either.

Editorial input is the grouping in ``GROUPS`` below - which heading a tool sits
under, and the notes around each table. Descriptions come from the registry, so
the page shows exactly what an MCP client is told. A registered tool missing
from ``GROUPS`` is a hard error rather than a silent omission.

    make gen_tool_docs        # or: uv run python scripts/gen_tool_docs.py
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import Counter
from dataclasses import dataclass

from mcp_server.enhancers import discover_enhancers, get_enhancer
from mcp_server.server import llm_tool_surface

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOLS_PAGE = REPO_ROOT / "docs" / "content" / "docs" / "mcp" / "tools.mdx"

BEGIN_MARKER = "{/* BEGIN generated: tool reference - edit scripts/gen_tool_docs.py */}"
END_MARKER = "{/* END generated */}"

# Splits a description into sentences. The registry's descriptions are written
# for the LLM and run long; the table takes the first sentence, which is the
# summary, and leaves the operational detail to the tool's schema.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s")


@dataclass(frozen=True)
class Group:
    """One `## heading` plus its table, with optional prose either side."""

    heading: str
    tools: tuple[str, ...]
    intro: str = ""
    outro: str = ""


# Editorial grouping. Order here is the order on the page. Every tool in the
# surface must appear exactly once; `_check_grouping` raises if one is missing or
# listed twice, so adding a @service fails loudly here rather than vanishing.
GROUPS: tuple[Group, ...] = (
    Group(
        heading="Connection",
        tools=("gmail_connect", "gmail_status", "gmail_disconnect"),
    ),
    Group(
        heading="Inbox & threads",
        tools=(
            "gmail_curate_inbox",
            "gmail_list_inbox",
            "gmail_get_thread",
            "gmail_get_attachment",
            "gmail_mark_thread_read",
            "gmail_archive_thread",
            "gmail_mark_thread_done",
            "gmail_unmark_thread_done",
        ),
    ),
    Group(
        heading="Triage ledger",
        intro=(
            "Banked triage judgments, so the same thread is not re-reasoned "
            "every morning."
        ),
        tools=("inbox_get_curation", "inbox_search", "inbox_save_curation"),
    ),
    Group(
        heading="Drafts & sending",
        tools=(
            "gmail_compose",
            "gmail_reply_to_thread",
            "gmail_update_draft",
            "gmail_get_draft",
            "gmail_list_drafts",
            "gmail_discard_draft",
            "gmail_send",
            "gmail_add_attachment",
            "gmail_remove_attachment",
        ),
        outro=(
            '<Callout type="info">\n'
            "Drafts are written, not sent. `gmail_send` only ever sends a draft "
            "that already\nexists, so the user reviews the text in the composer "
            "before anything leaves the\naccount.\n"
            "</Callout>"
        ),
    ),
    Group(
        heading="PDF forms & signing",
        intro="See [PDF Forms](/docs/mcp/pdf-forms) for the full workflow.",
        tools=("pdf_open", "pdf_edit", "pdf_request_signature", "pdf_export"),
        outro=(
            '<Callout type="warn">\n'
            "The signature itself is a step only the user can complete by typing "
            "their own\nname in the signing UI. The assistant fills fields; it "
            "cannot sign on the user's\nbehalf.\n"
            "</Callout>"
        ),
    ),
    Group(
        heading="Push notifications & settings",
        intro=(
            "See [Gmail Webhook Setup](/docs/gmail-webhooks) for the event "
            "payloads and\nsignature scheme."
        ),
        tools=(
            "webhook_settings",
            "webhook_subscribe",
            "webhook_list",
            "webhook_rotate_secret",
            "webhook_unsubscribe",
            "gmail_watch_start",
            "gmail_watch_stop",
        ),
    ),
)


class DriftError(RuntimeError):
    """The grouping above no longer matches the registry."""


def _summary(description: str) -> str:
    """First sentence of a registry description, safe to put in a table cell."""
    first = SENTENCE_SPLIT.split(description.strip())[0].strip()
    # A pipe would end the cell early and silently corrupt the row.
    return first.replace("|", "\\|")


def _renders_ui(name: str) -> bool:
    """Whether the tool declares an MCP App, i.e. `@enhance(app_uri=...)`."""
    entry = get_enhancer(name)
    return entry is not None and entry.app_uri is not None


def _check_grouping(surface: dict[str, str]) -> None:
    grouped: list[str] = []
    for group in GROUPS:
        grouped.extend(group.tools)

    duplicates = {name for name in grouped if grouped.count(name) > 1}
    missing = set(surface) - set(grouped)
    ghosts = set(grouped) - set(surface)

    problems = []
    if missing:
        problems.append(
            f"registered but not in GROUPS: {', '.join(sorted(missing))}. "
            "Add each to a group in scripts/gen_tool_docs.py."
        )
    if ghosts:
        problems.append(
            f"in GROUPS but not registered: {', '.join(sorted(ghosts))}. "
            "Remove each from scripts/gen_tool_docs.py."
        )
    if duplicates:
        problems.append(
            f"listed in more than one group: {', '.join(sorted(duplicates))}"
        )
    if problems:
        raise DriftError("\n".join(f"❌ {problem}" for problem in problems))


def _render(surface: dict[str, str]) -> str:
    _check_grouping(surface)

    lines = [
        BEGIN_MARKER,
        "",
        f"Once connected, your assistant has {len(surface)} tools, covering Gmail "
        "(via the",
        "official API), PDF form-filling and signing, and webhook delivery. Tools "
        "marked",
        "**UI** render an interactive MCP App in clients that support",
        "it (an inbox dashboard, a draft composer, a signing surface) and fall back to",
        "plain structured output everywhere else.",
        "",
        "Every tool takes a `user_id` that the server injects from the authenticated",
        "principal - clients never pass it.",
        "",
    ]

    for group in GROUPS:
        lines.append(f"## {group.heading}")
        lines.append("")
        if group.intro:
            lines.append(group.intro)
            lines.append("")
        lines.append("| Tool | Description |")
        lines.append("|------|-------------|")
        for name in group.tools:
            marker = " **UI**" if _renders_ui(name) else ""
            lines.append(f"| `{name}`{marker} | {_summary(surface[name])} |")
        lines.append("")
        if group.outro:
            lines.append(group.outro)
            lines.append("")

    lines.append(END_MARKER)
    return "\n".join(lines)


def _splice(page: str, rendered: str) -> str:
    start = page.find(BEGIN_MARKER)
    # Searched from after the begin marker, not from the top: an end marker that
    # precedes the begin marker would otherwise splice backwards and overwrite
    # whatever sat between them. Duplicate markers are rejected for the same
    # reason - the second pair would be silently ignored while the first was
    # rewritten.
    end = page.find(END_MARKER, start + len(BEGIN_MARKER)) if start != -1 else -1
    if page.count(BEGIN_MARKER) > 1 or page.count(END_MARKER) > 1:
        raise DriftError(
            f"❌ {TOOLS_PAGE.relative_to(REPO_ROOT)} has more than one "
            "generated-region marker pair. Leave exactly one."
        )
    if start == -1 or end == -1:
        raise DriftError(
            f"❌ {TOOLS_PAGE.relative_to(REPO_ROOT)} is missing the generated-region "
            f"markers.\nExpected a {BEGIN_MARKER!r} line and a {END_MARKER!r} line."
        )
    return page[:start] + rendered + page[end + len(END_MARKER) :]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Still write changes, but exit non-zero when any were made. Used by the pre-commit hook.",
    )
    args = parser.parse_args()

    discover_enhancers()
    entries = llm_tool_surface()
    surface = {entry.name: entry.description for entry in entries}
    # `services.__init__` appends to `_registry` with no uniqueness guard, so two
    # @service calls sharing a name both register. Collapsing them into a dict
    # would hide that: the count would be short by one and the description picked
    # arbitrarily, from the page whose whole job is to be authoritative.
    if len(surface) != len(entries):
        counts = Counter(entry.name for entry in entries)
        duplicates = sorted(name for name, n in counts.items() if n > 1)
        print(
            f"❌ duplicate @service name(s) in the registry: {', '.join(duplicates)}",
            file=sys.stderr,
        )
        return 1

    try:
        updated = _splice(TOOLS_PAGE.read_text(encoding="utf-8"), _render(surface))
    except DriftError as error:
        print(error, file=sys.stderr)
        return 1

    rel = TOOLS_PAGE.relative_to(REPO_ROOT)
    previous = TOOLS_PAGE.read_text(encoding="utf-8")
    if updated == previous:
        print(f"✓ {rel} already up to date ({len(surface)} tools)")
        return 0

    TOOLS_PAGE.write_text(updated, encoding="utf-8")
    print(f"✓ wrote {len(surface)} tools to {rel}")
    if args.check:
        print(
            f"\n{rel} was regenerated from the @service registry. Review the diff "
            "and stage it.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
