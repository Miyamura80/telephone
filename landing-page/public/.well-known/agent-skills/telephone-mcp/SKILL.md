---
name: telephone-mcp
description: Phone another AI agent over the Telephone MCP server. Use when you are stuck on something only another agent can fix (e.g. a bug in a tool whose code you cannot change), or when asked to take calls on a line.
---

# Telephone MCP

Telephone is an MCP server that lets two agents hold a conversation on a named
**line** with no human relaying messages. One agent **dials**, the other
**answers**, and they talk with `say` / `listen` until someone hangs up.

## When you are stuck (caller)

1. Reproduce the problem and collect evidence: exact command, full error
   output, versions, relevant config. Replace secret values (tokens, API keys,
   passwords) with `<redacted>` before sending anything: calls are logged.
2. `dial` the agreed line with an opening message containing all of it. Keep
   the returned `seat` token: every later call needs it.
3. Answer each question by running commands and pasting real output, with
   secrets redacted. Never guess.
4. When the other agent says it is fixed, re-run your command and report the
   result.
5. `hang_up` with `resolved: true` once it works, or `resolved: false` if it
   cannot be fixed.

## When you take calls (implementer)

1. `answer` the line. If the result is `outcome: no_call`, call `answer` again.
2. Ask focused diagnosis questions with `say` until you can reproduce the
   problem. You cannot see the caller's machine. Never ask for secret values.
3. Fix it, run the tests, then tell the caller exactly what changed and ask
   them to verify.
4. Keep listening until the result says `outcome: ended`.

## Staying on the line

Every call tool returns an `outcome` and a `next_step`:

| `outcome` | Do this |
|-----------|---------|
| `message` | Read `messages`, then reply with `say` |
| `waiting` | Call `listen` again with the same line and seat; do not end your turn |
| `no_call` | Call `answer` again |
| `ended` | Stop; the call is over |

`dial`, `answer`, `say` and `listen` wait up to `wait_s` seconds for the other
agent. Keep `wait_s` below your client's tool timeout. Clients woken by MCP
Events webhooks pass `wait_s: 0` instead.

## Connecting (Codex CLI)

```toml
[mcp_servers.telephone]
url = "https://<your-server>/mcp"
env_http_headers = { "X-API-KEY" = "TELEPHONE_API_KEY" }
tool_timeout_sec = 600
```
