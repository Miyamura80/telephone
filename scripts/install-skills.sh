#!/usr/bin/env bash
# Downloads the official telephone-mcp agent skill from the telephone repository
# into .claude/skills/ (the same skill that is self-published on skills.sh).
set -euo pipefail

REPO="Miyamura80/telephone"
BRANCH="main"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"

dir=".claude/skills/telephone-mcp"
mkdir -p "${dir}"
echo "Downloading telephone-mcp skill..."
curl -fsSL -o "${dir}/SKILL.md" "${BASE_URL}/skills/telephone-mcp/SKILL.md"

echo "Installed telephone-mcp skill into .claude/skills/telephone-mcp/"
