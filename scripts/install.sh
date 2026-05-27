#!/usr/bin/env sh
# gptme installer — https://gptme.ai/install.sh
#
# Usage:
#   curl -sSf https://gptme.ai/install.sh | sh
#   curl -sSf https://gptme.ai/install.sh | sh -s -- --dev
#   curl -sSf https://gptme.ai/install.sh | sh -s -- --extras browser,datascience
#
# Options:
#   --dev         Install latest git master instead of the PyPI release
#   --extras E    Comma-separated list of extras (default: browser)
#   --no-extras   Install without any extras
#   --yes         Non-interactive (skip confirmation prompts)
#   --help        Show this help

set -e

SCRIPT_URL="https://gptme.ai/install.sh"

usage() {
  cat <<EOF
gptme installer

USAGE:
  curl -sSf $SCRIPT_URL | sh
  curl -sSf $SCRIPT_URL | sh -s -- [OPTIONS]

OPTIONS:
  --dev         Install latest git master instead of the PyPI release
  --extras E    Comma-separated extras to include (default: browser)
                Available: browser, datascience, server, all
  --no-extras   Install without any extras
  --yes, -y     Non-interactive (skip confirmation prompts)
  --help, -h    Show this help

EXAMPLES:
  # Default install (gptme with browser support)
  curl -sSf $SCRIPT_URL | sh

  # Install git master with no extras
  curl -sSf $SCRIPT_URL | sh -s -- --dev --no-extras

  # Install with datascience extras
  curl -sSf $SCRIPT_URL | sh -s -- --extras datascience
EOF
}

# --- helpers ---
say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

has() { command -v "$1" >/dev/null 2>&1; }

confirm() {
  [ "$YES" -eq 1 ] && return 0
  # When piped from curl, stdin is the script — read from the terminal directly.
  printf '%s [y/N] ' "$1"
  read -r ans </dev/tty 2>/dev/null || { warn "Could not read from terminal; assuming yes."; return 0; }
  case "$ans" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# --- defaults ---
DEV=0
EXTRAS="browser"
NO_EXTRAS=0
YES=0

# --- parse args ---
while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h)   usage; exit 0 ;;
    --dev)       DEV=1; shift ;;
    --no-extras) NO_EXTRAS=1; shift ;;
    --yes|-y)    YES=1; shift ;;
    --extras)
      if [ -z "$2" ] || [ "${2#-}" != "$2" ]; then
        die "--extras requires a value (e.g. --extras browser,datascience)"
      fi
      EXTRAS="$2"; shift 2 ;;
    --extras=*)  EXTRAS="${1#--extras=}"; shift ;;
    *)
      printf 'Unknown option: %s\nRun with --help for usage.\n' "$1" >&2
      exit 1 ;;
  esac
done

# --- build package spec ---
if [ "$DEV" -eq 1 ]; then
  PKG_BASE="git+https://github.com/gptme/gptme.git"
else
  PKG_BASE="gptme"
fi

if [ "$NO_EXTRAS" -eq 1 ] || [ -z "$EXTRAS" ]; then
  PKG="$PKG_BASE"
else
  PKG="${PKG_BASE}[${EXTRAS}]"
fi

# --- detect installer ---
say "Detecting package manager..."

if has uv; then
  INSTALLER="uv"
  ok "Found uv"
elif has pipx; then
  INSTALLER="pipx"
  ok "Found pipx"
elif has pip3; then
  INSTALLER="pip3"
  warn "Neither uv nor pipx found; falling back to pip install --user"
  warn "We recommend uv (https://docs.astral.sh/uv/) for better isolation"
elif has pip; then
  INSTALLER="pip"
  warn "Neither uv nor pipx found; falling back to pip install --user"
  warn "We recommend uv (https://docs.astral.sh/uv/) for better isolation"
else
  die "No supported package manager found. Install uv (https://docs.astral.sh/uv/) and retry."
fi

# --- show plan ---
say "Installing: gptme"
if [ "$DEV" -eq 1 ]; then
  printf '  source:   git master\n'
else
  printf '  source:   PyPI (latest release)\n'
fi
if [ "$NO_EXTRAS" -eq 0 ] && [ -n "$EXTRAS" ]; then
  printf '  extras:   %s\n' "$EXTRAS"
fi
printf '  via:      %s\n\n' "$INSTALLER"

if ! confirm "Proceed with installation?"; then
  say "Installation cancelled."
  exit 0
fi

# --- install ---
say "Installing gptme..."

case "$INSTALLER" in
  uv)
    uv tool install "$PKG"
    ;;
  pipx)
    # --force handles re-installs cleanly (plain install exits 1 if already installed)
    pipx install --force "$PKG"
    ;;
  pip3|pip)
    $INSTALLER install --user "$PKG"
    ;;
esac

# --- post-install ---
say "Installation complete!"

# Check gptme is on PATH
if has gptme; then
  VERSION=$(gptme --version 2>/dev/null | head -1 || echo "unknown")
  ok "gptme installed: $VERSION"
else
  warn "gptme not found on PATH after installation."
  warn "You may need to add the install location to your PATH:"
  if [ "$INSTALLER" = "uv" ] || [ "$INSTALLER" = "pip3" ] || [ "$INSTALLER" = "pip" ]; then
    warn "  export PATH=\"\$HOME/.local/bin:\$PATH\""
  elif [ "$INSTALLER" = "pipx" ]; then
    warn "  pipx ensurepath && source ~/.bashrc"
  fi
fi

# Playwright install hint if browser extra was requested
if [ "$NO_EXTRAS" -eq 0 ] && [ -n "$EXTRAS" ] && printf '%s' "$EXTRAS" | grep -q browser; then
  printf '\n'
  say "Browser support requires Playwright browsers:"
  printf '  playwright install chromium\n\n'
fi

printf '\nRun \033[1mgptme\033[0m to get started.\n'
printf 'Docs: https://gptme.org/docs/getting-started.html\n'
