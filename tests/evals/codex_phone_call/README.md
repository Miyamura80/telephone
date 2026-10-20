# Eval: Codex phones Codex

A Codex agent that hits a bug in a CLI it cannot change phones the Codex agent
that owns the CLI's code, over the Telephone MCP server, with no human in the
loop.

```
 caller/  (codex exec --sandbox read-only)         impl/  (codex exec --sandbox workspace-write)
 ./bin/acme-cli sync -> KeyError: 'region'           git checkout of fixture/ (planted bug)
        |                                                   ^
        |  dial / say / listen / hang_up                    |  answer / say / listen
        v                                                   |
 +------------------------------------------------------------------+
 |            telephone-serve  (one process, /mcp, line acme-cli-xx) |
 +------------------------------------------------------------------+
```

The planted bug: `acme-cli` documents `region` as optional, but `load_profile`
indexes it directly. It only fires when the config has no `region` and
`ACME_REGION` is unset, which is the caller's setup and not the
implementer's, so the implementer has to ask before it can reproduce.

## Run

```bash
make eval_codex_call            # two real Codex CLIs (needs `codex` on PATH, logged in)
make eval_codex_call ARGS="--model <model> --workdir /tmp/run1"
make eval_codex_call_fake       # scripted agents, no LLM: validates harness + server
```

Each run writes `report.json`, both agents' `codex exec --json` logs under
`logs/`, and the call transcript under `.telephone/transcripts/`.

## Scoring

A run passes only if all of these hold:

| Check | Source |
|-------|--------|
| The caller dialed | transcript |
| The caller hung up with `resolved: true` | transcript `ended` event |
| The implementer's first message is a question | transcript |
| The implementer sent a second message (the fix report) | transcript |
| `acme-cli sync` now succeeds with the caller's config | re-run after the call |
| The fixture's tests pass | `pytest tests` in `impl/` |
| The implementer changed the code | `git diff` in `impl/` |

Not in `make ci`: it needs Codex, an OpenAI account, and a few minutes.
