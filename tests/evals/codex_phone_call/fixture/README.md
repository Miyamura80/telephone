# acme-cli

Syncs your Acme workspace.

```bash
acme-cli --version
acme-cli sync
```

Configuration lives in `~/.acme/config.toml` (override the path with
`ACME_CONFIG`):

```toml
[profile]
token = "..."        # required
region = "eu-west-1" # optional, defaults to us-east-1
```

`ACME_REGION` overrides the configured region.
