"""Load the acme-cli profile."""

import os
import tomllib
from pathlib import Path

DEFAULT_REGION = "us-east-1"


def config_path() -> Path:
    override = os.environ.get("ACME_CONFIG")
    return Path(override) if override else Path.home() / ".acme" / "config.toml"


def load_profile() -> dict:
    with config_path().open("rb") as fh:
        profile = tomllib.load(fh).get("profile", {})
    return {
        "token": profile["token"],
        "region": os.environ.get("ACME_REGION") or profile["region"],
    }
