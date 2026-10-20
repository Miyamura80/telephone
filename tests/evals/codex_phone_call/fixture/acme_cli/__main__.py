"""Entry point: `python -m acme_cli`."""

import argparse
import sys

from acme_cli import __version__, sync


def main() -> int:
    parser = argparse.ArgumentParser(prog="acme-cli")
    parser.add_argument(
        "--version", action="version", version=f"acme-cli {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="Sync your Acme workspace.")
    args = parser.parse_args()
    if args.command == "sync":
        return sync.run()
    return 2


if __name__ == "__main__":
    sys.exit(main())
