# Deployment

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Miyamura80/telephone)

Both targets provision the backend (FastAPI + MCP at `/mcp`) as a Docker service plus a managed Postgres database, run Alembic migrations, and prompt for the required secrets. The landing page (`landing-page/`) deploys separately and is not included.

> **Forking this repo?** The Render button points at the canonical repo; change its `repo=` URL to your fork. Railway has no published Telephone template yet: deploy from the repo directly, or publish your own template from your project (see below).

## Render

Driven by [`render.yaml`](../render.yaml). The database and `SESSION_SECRET_KEY` are wired automatically; you're prompted for the WorkOS secrets. After the first deploy, set `MCP_PUBLIC_URL` to `https://<your-render-host>/mcp`, then redeploy.

## Railway

The committed [`railway.json`](../railway.json) pins the Docker build, pre-deploy migrations, and health check, so the template inherits them. To re-generate or update the template: **project Settings → Generate Template from Project → Publish** (dashboard only; the CLI can't publish templates).

### Forking the Railway template? Variable map for the backend service

The template has two services: a **Postgres** service (use Railway's standard Postgres defaults) and the **backend**. Set the backend's variables as follows.

**Auto-resolve / auto-generate** (paste as defaults so it deploys with zero input):

| Variable | Value |
|---|---|
| `DEV_ENV` | `prod` |
| `BACKEND_DB_URI` | `${{Postgres.DATABASE_URL}}` (private URL; matches your Postgres service name) |
| `MCP_PUBLIC_URL` | `https://${{RAILWAY_PUBLIC_DOMAIN}}/mcp` |
| `SESSION_SECRET_KEY` | `${{ secret(32) }}` |

Railway generates `secret(...)` values **once** and persists them, so redeploys don't invalidate the session secret.

**Leave empty; the deployer must paste their own** (per-deployment credentials that can't be pre-baked):

`WORKOS_CLIENT_ID` · `WORKOS_API_KEY` · `WORKOS_AUTHKIT_DOMAIN`

**Gotchas:**

- `DEV_ENV` must be `prod`. The Dockerfile sets it, but an empty template var *overrides* it back to blank, silently disabling the prod config overlay.
- The backend service needs a **public domain** enabled, or `${{RAILWAY_PUBLIC_DOMAIN}}` resolves to empty and `MCP_PUBLIC_URL` breaks.
- After deploy, point your WorkOS redirect/resource at `MCP_PUBLIC_URL`. (Inherent to OAuth; can't be automated.)

## Optional integrations

See [`.env.example`](../.env.example) for the full list of optional integrations (LLM keys, Stripe, LangFuse, Telegram, etc.). Add any you need from the platform dashboard after the first deploy.
