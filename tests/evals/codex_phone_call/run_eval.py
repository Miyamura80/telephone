"""Eval: a stuck Codex phones the Codex that owns the code, with no human.

Builds a throwaway workspace with two sides:

- ``impl/``   - a git checkout of the ``acme-cli`` fixture (planted bug). The
  implementer agent runs here with write access.
- ``caller/`` - an ``acme-cli`` wrapper plus a config that triggers the bug. The
  caller agent runs here with a read-only sandbox, so it cannot change the
  code. The source is not in its workspace, but the wrapper runs it from
  ``impl/``, so a determined agent could read it; the prompt forbids that and
  the transcript shows whether the fix came from the call.

Starts the telephone server, launches both agents on the same line, waits for
the call to end, then scores it. Pass means: the caller hung up with
``resolved=true``, the implementer's first message was a question,
``acme-cli sync`` now succeeds with the caller's config, and the fixture's
tests pass.

    uv run python tests/evals/codex_phone_call/run_eval.py              # two Codex CLIs
    uv run python tests/evals/codex_phone_call/run_eval.py --agents fake  # scripted, no LLM

Needs the ``codex`` CLI on PATH and logged in for ``--agents codex``.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FIXTURE = HERE / "fixture"
TRANSCRIPTS = REPO / ".telephone" / "transcripts"
CALLER_CONFIG = '[profile]\ntoken = "acme_live_7f3a"\n'


@dataclass
class Report:
    line: str
    agents: str
    passed: bool = False
    resolved: bool | None = None
    ended_reason: str | None = None
    caller_messages: int = 0
    implementer_messages: int = 0
    cli_ok: bool = False
    cli_output: str = ""
    fixture_tests_ok: bool = False
    impl_diff: str = ""
    duration_s: float = 0.0
    failures: list[str] = field(default_factory=list)


# -- workspace ------------------------------------------------------------------


def build_workspace(root: Path) -> tuple[Path, Path]:
    impl = root / "impl"
    shutil.copytree(FIXTURE, impl)
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        [
            "-c",
            "user.name=eval",
            "-c",
            "user.email=eval@example.com",
            "commit",
            "-qm",
            "acme-cli 2.4.1",
        ],
    ):
        subprocess.run(["git", *args], cwd=impl, check=True)

    caller = root / "caller"
    (caller / "bin").mkdir(parents=True)
    (caller / ".acme").mkdir()
    (caller / ".acme" / "config.toml").write_text(CALLER_CONFIG)
    wrapper = caller / "bin" / "acme-cli"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'export ACME_CONFIG="${{ACME_CONFIG:-{caller}/.acme/config.toml}}"\n'
        f'PYTHONPATH="{impl}" exec "{sys.executable}" -m acme_cli "$@"\n'
    )
    wrapper.chmod(0o755)
    return impl, caller


# -- server ---------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def seed_api_key() -> str:
    # Imported here, after BACKEND_DB_URI is set, so the engine binds to it.
    import api_server.server  # noqa: F401, PLC0415 - registers every ORM model
    from api_server.auth.api_key_auth import create_api_key  # noqa: PLC0415
    from db.base import Base  # noqa: PLC0415
    from db.engine import _init_engine, use_db_session  # noqa: PLC0415

    Base.metadata.create_all(_init_engine())
    with use_db_session() as session:
        key, _ = create_api_key(session, user_id="eval", name="eval", scopes=["*"])
    return key


def start_server(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api_server.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO,
        env=os.environ.copy(),
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early ({proc.returncode})")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2):
                return proc
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    proc.terminate()
    raise TimeoutError("telephone server did not become healthy")


# -- agents ---------------------------------------------------------------------


def codex_command(
    prompt: str, cwd: Path, sandbox: str, url: str, model: str | None
) -> list[str]:
    cmd = [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--cd",
        str(cwd),
        "--sandbox",
        sandbox,
        "-c",
        'approval_policy="never"',
        "-c",
        f'mcp_servers.telephone.url="{url}"',
        "-c",
        'mcp_servers.telephone.env_http_headers={"X-API-KEY"="TELEPHONE_API_KEY"}',
        "-c",
        "mcp_servers.telephone.tool_timeout_sec=600",
        "-c",
        "mcp_servers.telephone.startup_timeout_sec=30",
    ]
    if model:
        cmd += ["-m", model]
    return [*cmd, prompt]


def launch_agents(
    args, line: str, impl: Path, caller: Path, url: str, key: str, logs: Path
):
    env = {**os.environ, "TELEPHONE_API_KEY": key}
    procs = []
    if args.agents == "codex":
        prompts = {
            name: (HERE / "prompts" / f"{name}.md").read_text().replace("{line}", line)
            for name in ("implementer", "caller")
        }
        plan = [
            (
                "implementer",
                codex_command(
                    prompts["implementer"], impl, "workspace-write", url, args.model
                ),
                impl,
            ),
            (
                "caller",
                codex_command(prompts["caller"], caller, "read-only", url, args.model),
                caller,
            ),
        ]
    else:
        fake = [sys.executable, str(HERE / "fake_agents.py")]
        plan = [
            ("implementer", [*fake, "implementer", url, line, str(impl)], impl),
            ("caller", [*fake, "caller", url, line, str(caller)], caller),
        ]
    for name, cmd, cwd in plan:
        log = (logs / f"{name}.jsonl").open("w")
        procs.append(
            (
                name,
                subprocess.Popen(
                    cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT
                ),
            )
        )
        time.sleep(3)  # let the implementer start listening first
    return procs


# -- scoring --------------------------------------------------------------------


def score(report: Report, impl: Path, caller: Path) -> None:
    entries = []
    for path in sorted(TRANSCRIPTS.glob(f"{report.line}__*.jsonl")):
        entries += [json.loads(row) for row in path.read_text().splitlines()]
    messages = [e for e in entries if e["event"] == "message"]
    report.caller_messages = sum(m["sender"] == "caller" for m in messages)
    report.implementer_messages = sum(m["sender"] == "implementer" for m in messages)
    implementer_texts = [m["text"] for m in messages if m["sender"] == "implementer"]
    asked_first = bool(implementer_texts) and "?" in implementer_texts[0]
    ended = [e for e in entries if e["event"] == "ended"]
    if ended:
        report.resolved = ended[-1].get("resolved")
        report.ended_reason = ended[-1].get("reason")

    env = {k: v for k, v in os.environ.items() if k != "ACME_REGION"}
    run = subprocess.run(
        [str(caller / "bin" / "acme-cli"), "sync"],
        capture_output=True,
        text=True,
        env=env,
    )
    report.cli_ok = run.returncode == 0
    report.cli_output = (run.stdout + run.stderr)[-2000:]
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=impl,
        capture_output=True,
        text=True,
        env={**env, "PYTHONPATH": str(impl)},
    )
    report.fixture_tests_ok = tests.returncode == 0
    report.impl_diff = subprocess.run(
        ["git", "diff", "HEAD"], cwd=impl, capture_output=True, text=True
    ).stdout

    checks = {
        "caller never dialed": bool(messages),
        "call not resolved by the caller": report.resolved is True,
        "implementer did not open with a question": asked_first,
        "implementer never reported a fix": report.implementer_messages >= 2,
        "acme-cli sync still fails": report.cli_ok,
        "fixture tests fail": report.fixture_tests_ok,
        "implementer changed nothing": bool(report.impl_diff.strip()),
    }
    report.failures = [name for name, ok in checks.items() if not ok]
    report.passed = not report.failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--agents", choices=("codex", "fake"), default="codex")
    parser.add_argument("--model", default=None, help="Codex model override.")
    parser.add_argument(
        "--timeout", type=float, default=1200.0, help="Seconds to wait for the call."
    )
    parser.add_argument(
        "--workdir", type=Path, default=None, help="Keep the workspace here."
    )
    args = parser.parse_args()
    if args.agents == "codex" and shutil.which("codex") is None:
        print("codex CLI not found on PATH (npm i -g @openai/codex)", file=sys.stderr)
        return 2

    root = args.workdir or Path(tempfile.mkdtemp(prefix="telephone-eval-"))
    root.mkdir(parents=True, exist_ok=True)
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    os.environ["BACKEND_DB_URI"] = f"sqlite:///{root / 'telephone.db'}"
    os.environ.setdefault("DEV_ENV", "dev")
    line = f"acme-cli-{secrets.token_hex(3)}"
    report = Report(line=line, agents=args.agents)

    impl, caller = build_workspace(root)
    key = seed_api_key()
    port = _free_port()
    server = start_server(port)
    started = time.monotonic()
    procs = []
    try:
        procs = launch_agents(
            args, line, impl, caller, f"http://127.0.0.1:{port}/mcp", key, logs
        )
        deadline = started + args.timeout
        while any(p.poll() is None for _, p in procs) and time.monotonic() < deadline:
            time.sleep(2)
    finally:
        for _, proc in procs:
            if proc.poll() is None:
                proc.terminate()
        server.terminate()
        server.wait(timeout=15)
    report.duration_s = round(time.monotonic() - started, 1)
    score(report, impl, caller)

    (root / "report.json").write_text(json.dumps(asdict(report), indent=2))
    verdict = "PASS" if report.passed else "FAIL"
    print(
        f"{verdict}  line={line}  resolved={report.resolved}  "
        f"turns={report.caller_messages}+{report.implementer_messages}  "
        f"cli_ok={report.cli_ok}  tests_ok={report.fixture_tests_ok}  {report.duration_s}s"
    )
    for failure in report.failures:
        print(f"  - {failure}")
    print(f"workspace: {root}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
