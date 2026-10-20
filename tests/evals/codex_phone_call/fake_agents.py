"""Scripted stand-ins for the two Codex agents (no LLM).

Same protocol and the same real side effects as the prompted agents: the
caller runs the real wrapper and reports real output; the implementer edits
the real checkout. Used by ``run_eval.py --agents fake`` to validate the
harness and the server end to end without Codex or an API key.

    python fake_agents.py {caller|implementer} <mcp-url> <line> <workdir>
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

FIX_OLD = 'os.environ.get("ACME_REGION") or profile["region"]'
FIX_NEW = 'os.environ.get("ACME_REGION") or profile.get("region", DEFAULT_REGION)'
REGRESSION_TEST = """

def test_region_defaults_when_missing(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[profile]\\ntoken = "t"\\n')
    monkeypatch.setenv("ACME_CONFIG", str(cfg))
    monkeypatch.delenv("ACME_REGION", raising=False)
    assert load_profile()["region"] == "us-east-1"
"""


async def call(session: ClientSession, tool: str, **args) -> dict:
    result = await session.call_tool(tool, args)
    if result.isError or result.structuredContent is None:
        raise RuntimeError(f"{tool} failed: {result.content}")
    print(f"{tool} -> {result.structuredContent['outcome']}", flush=True)
    return result.structuredContent


async def until_message(
    session: ClientSession, view: dict, line: str, seat: str
) -> dict:
    while view["outcome"] == "waiting":
        view = await call(session, "listen", line=line, seat=seat, wait_s=30)
    return view


def sh(*cmd: str, cwd: Path) -> str:
    run = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return (run.stdout + run.stderr).strip()


async def caller(session: ClientSession, line: str, workdir: Path) -> None:
    first = sh("./bin/acme-cli", "sync", cwd=workdir)
    view = await call(
        session,
        "dial",
        line=line,
        message=f"`acme-cli sync` fails for me:\n{first[-600:]}",
        wait_s=30,
    )
    seat = view["seat"]
    view = await until_message(session, view, line, seat)
    config = (workdir / ".acme" / "config.toml").read_text()
    version = sh("./bin/acme-cli", "--version", cwd=workdir)
    view = await call(
        session,
        "say",
        line=line,
        seat=seat,
        text=f"{version}\nconfig.toml:\n{config}ACME_REGION is not set.",
        wait_s=60,
    )
    view = await until_message(session, view, line, seat)
    retry = subprocess.run(
        ["./bin/acme-cli", "sync"], cwd=workdir, capture_output=True, text=True
    )
    ok = retry.returncode == 0
    await call(
        session,
        "say",
        line=line,
        seat=seat,
        text=f"Re-ran it: {retry.stdout.strip()[-200:]}",
        wait_s=0,
    )
    await call(session, "hang_up", line=line, seat=seat, reason="verified", resolved=ok)


async def implementer(session: ClientSession, line: str, workdir: Path) -> None:
    view = await call(session, "answer", line=line, wait_s=30)
    while view["outcome"] == "no_call":
        view = await call(session, "answer", line=line, wait_s=30)
    seat = view["seat"]
    view = await call(
        session,
        "say",
        line=line,
        seat=seat,
        text="What does `acme-cli --version` print, and what is in your config file?",
        wait_s=60,
    )
    view = await until_message(session, view, line, seat)
    config = workdir / "acme_cli" / "config.py"
    config.write_text(config.read_text().replace(FIX_OLD, FIX_NEW))
    tests = workdir / "tests" / "test_config.py"
    tests.write_text(tests.read_text() + REGRESSION_TEST)
    view = await call(
        session,
        "say",
        line=line,
        seat=seat,
        text="Fixed: a missing region now defaults to us-east-1. Please re-run `acme-cli sync`.",
        wait_s=60,
    )
    while view["outcome"] != "ended":
        view = await call(session, "listen", line=line, seat=seat, wait_s=30)


async def main(role: str, url: str, line: str, workdir: Path) -> None:
    headers = {"X-API-KEY": os.environ["TELEPHONE_API_KEY"]}
    async with (
        streamablehttp_client(url, headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        await (caller if role == "caller" else implementer)(session, line, workdir)


if __name__ == "__main__":
    role, url, line, workdir = sys.argv[1:5]
    asyncio.run(main(role, url, line, Path(workdir)))
