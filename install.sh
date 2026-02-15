#!/bin/bash
# NPT Install Script
# Installs the NPT skill for Claude Code and/or Codex by symlinking into:
#   - Claude: ~/.claude/skills/npt
#   - Codex:  ~/.codex/skills/npt

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

usage() {
  cat <<'EOF'
Usage:
  ./install.sh [--both|--claude|--codex]

Defaults to --both.

Installs by symlinking:
  Claude -> ~/.claude/skills/npt
  Codex  -> ~/.codex/skills/npt
EOF
}

INSTALL_CLAUDE=1
INSTALL_CODEX=1

case "${1:-}" in
  ""|"--both")
    ;;
  "--claude")
    INSTALL_CODEX=0
    ;;
  "--codex")
    INSTALL_CLAUDE=0
    ;;
  "-h"|"--help")
    usage
    exit 0
    ;;
  *)
    echo "Error: unknown option: $1"
    usage
    exit 1
    ;;
esac

install_link() {
  local src="$1"
  local dst="$2"
  local parent
  parent="$(dirname "$dst")"

  if [ ! -d "$src" ]; then
    echo "Error: skill source not found at $src"
    exit 1
  fi

  mkdir -p "$parent"

  if [ -L "$dst" ]; then
    echo "Removing existing symlink at $dst"
    rm "$dst"
  elif [ -e "$dst" ]; then
    echo "Error: $dst already exists and is not a symlink."
    echo "Remove it manually if you want to replace it."
    exit 1
  fi

  ln -s "$src" "$dst"
  echo "Installed: $dst -> $src"
}

if [ "$INSTALL_CLAUDE" -eq 1 ]; then
  install_link "$SCRIPT_DIR/.claude/skills/npt" "$HOME/.claude/skills/npt"
fi

if [ "$INSTALL_CODEX" -eq 1 ]; then
  install_link "$SCRIPT_DIR/.codex/skills/npt" "$HOME/.codex/skills/npt"
fi

echo ""
echo "Install complete."
