#!/bin/bash
# NPT Install Script
# Symlinks the /npt skill to ~/.claude/skills/npt for global availability

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/.claude/skills/npt"
SKILL_DST="$HOME/.claude/skills/npt"

if [ ! -d "$SKILL_SRC" ]; then
  echo "Error: Skill source not found at $SKILL_SRC"
  exit 1
fi

mkdir -p "$HOME/.claude/skills"

if [ -L "$SKILL_DST" ]; then
  echo "Removing existing symlink at $SKILL_DST"
  rm "$SKILL_DST"
elif [ -d "$SKILL_DST" ]; then
  echo "Error: $SKILL_DST already exists and is not a symlink."
  echo "Remove it manually if you want to replace it."
  exit 1
fi

ln -s "$SKILL_SRC" "$SKILL_DST"
echo "Installed /npt skill globally."
echo "  Source: $SKILL_SRC"
echo "  Target: $SKILL_DST"
echo ""
echo "You can now use /npt in any Claude Code session."
