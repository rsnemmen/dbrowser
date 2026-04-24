#!/usr/bin/env bash
set -euo pipefail

# ── helpers ───────────────────────────────────────────────────────────────────
BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

say()  { printf "${GREEN}==>${RESET} ${BOLD}%s${RESET}\n" "$*"; }
warn() { printf "${YELLOW}warning:${RESET} %s\n" "$*" >&2; }
die()  { printf "${RED}error:${RESET} %s\n" "$*" >&2; exit 1; }

# Works when stdin is a pipe from curl: reads from /dev/tty, falls back to default.
prompt() {
    local msg="$1" default="${2:-n}"
    local answer
    printf '%s' "$msg"
    if [ -t 0 ]; then
        read -r answer
    elif [ -e /dev/tty ]; then
        read -r answer < /dev/tty
    else
        answer="$default"
    fi
    printf '%s' "${answer:-$default}"
}

# ── OS check ──────────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Linux|Darwin) ;;
    *) die "Unsupported OS: $OS. Only Linux and macOS are supported." ;;
esac

# ── dep: curl ─────────────────────────────────────────────────────────────────
command -v curl >/dev/null 2>&1 || die "curl is required but not found on PATH."

# ── bootstrap uv ──────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

command -v uv >/dev/null 2>&1 \
    || die "uv is not on PATH after install. Add \$HOME/.local/bin to your PATH and re-run."

# ── install dbrowser ──────────────────────────────────────────────────────────
say "Installing dbrowser..."
uv tool install --python 3.11 --force git+https://github.com/rsnemmen/dbrowser.git

# ── rclone check ─────────────────────────────────────────────────────────────
if ! command -v rclone >/dev/null 2>&1; then
    warn "rclone is not installed. dbrowser requires rclone on PATH."
    answer="$(prompt "Install rclone now? [y/N]: " "n")"
    answer_lower="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
    case "$answer_lower" in
        y|yes)
            say "Installing rclone (requires sudo)..."
            if curl -fsSL https://rclone.org/install.sh | sudo bash; then
                say "rclone installed."
            else
                warn "rclone installation failed. Install it manually:"
                printf "  Official:  https://rclone.org/install/\n"
                printf "  Homebrew:  brew install rclone\n\n"
            fi
            ;;
        *)
            printf "\nInstall rclone before running dbrowser:\n"
            printf "  Official:  https://rclone.org/install/\n"
            printf "  Homebrew:  brew install rclone\n\n"
            ;;
    esac
fi

# ── PATH reminder ─────────────────────────────────────────────────────────────
if ! command -v dbrowser >/dev/null 2>&1; then
    warn "\$HOME/.local/bin is not on your PATH."
    printf "Add this line to your shell rc (~/.bashrc, ~/.zshrc, etc.) and restart your shell:\n"
    printf "  export PATH=\"\$HOME/.local/bin:\$PATH\"\n\n"
fi

# ── done ──────────────────────────────────────────────────────────────────────
say "dbrowser installed!"
printf "\nRun ${BOLD}dbrowser${RESET} to start.\n"
printf "On first launch it will walk you through connecting your Dropbox via rclone.\n"
printf "\nTo uninstall: ${BOLD}uv tool uninstall dbrowser${RESET}\n"
