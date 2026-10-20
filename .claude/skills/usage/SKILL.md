---
name: usage
description: How to use the CLI, API, and MCP interfaces. Use this skill when interacting with the tool as an end user.
---
# Usage Guide

This skill teaches you how to use the three interfaces provided by this project.

## CLI

```bash
# Install
pip install telephone-mcp

# Basic usage
telephone --help                  # see all commands
telephone greet Alice             # run a command
telephone config show             # view configuration
telephone doctor                  # check system health

# Global flags (go before the subcommand)
telephone --verbose greet Alice   # detailed output
telephone --format json config show  # JSON output
telephone --dry-run greet Bob     # preview without executing
telephone --version               # print version
```

## Server (HTTP API + MCP)

```bash
# Start the server: HTTP API and MCP (streamable HTTP) on one port.
telephone-serve

# Default http://localhost:8080. See /docs for OpenAPI, /mcp for MCP.
```

## MCP

The MCP server exposes the same services as CLI tools via the Model Context Protocol.

**Primary transport: streamable HTTP at `/mcp`** (started by `telephone-serve`).
Stdio is supported via `telephone-mcp` for local Claude Desktop / dev only.

```bash
# Legacy stdio transport
telephone-mcp

# Debug with the MCP inspector (stdio)
mcp dev mcp_server/server.py
```

### Connecting MCP to your editor

Remote (preferred - works on Claude Desktop 0.7+, Cursor, etc.):

```json
{
  "mcpServers": {
    "telephone": {
      "url": "https://YOUR-DEPLOYMENT/mcp",
      "headers": { "X-API-KEY": "sk_..." }
    }
  }
}
```

Local stdio (legacy):

```json
{
  "mcpServers": {
    "telephone": {
      "command": "telephone-mcp"
    }
  }
}
```

## Updating

```bash
telephone update    # check for updates and upgrade
```
