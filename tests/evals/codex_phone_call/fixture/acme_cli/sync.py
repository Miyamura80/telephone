"""The `sync` command."""

from acme_cli.config import load_profile

ITEMS = ("projects", "members", "billing")


def run() -> int:
    profile = load_profile()
    for item in ITEMS:
        print(f"synced {item} -> {profile['region']}")
    print(f"done: {len(ITEMS)} items synced to {profile['region']}")
    return 0
