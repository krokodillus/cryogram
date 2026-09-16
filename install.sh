#!/bin/sh
# Installs Cryogram for you alone, in your own program folder, and starts it; nothing is installed system-wide.

# The one line: curl -fsSL https://cryogram.app/install.sh | sh

# Run beside an unzipped copy of Cryogram it installs that copy; run on its own it downloads the latest release.

set -eu

REPO="${CRYOGRAM_RELEASE_URL:-https://github.com/krokodillus/cryogram}"
# The one file read from the main branch: the Builder AI tab's model list, so it moves without a release
RAW="${CRYOGRAM_RAW_URL:-https://raw.githubusercontent.com/krokodillus/cryogram/main}"
CLAUDE_PAGE="https://docs.claude.com/en/docs/claude-code/setup"
CODEX_PAGE="https://developers.openai.com/codex/cli"

say() { printf '%s\n' "$*"; }
die() { printf 'Cryogram was not installed. %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Where it goes, and the launcher for this computer; every path is inside your home folder
os=$(uname -s)
case "$os" in
  Darwin)
    DEFAULT_DIR="$HOME/Applications/Cryogram"
    LAUNCHER="Start Cryogram.command"
    ICON="Launchpad and your Applications folder"
    ;;
  *)
    say "This installer is for the Mac. On Windows, run this in PowerShell instead:"
    say "  irm https://cryogram.app/install.ps1 | iex"
    exit 1
    ;;
esac
DIR="${CRYOGRAM_INSTALL_DIR:-$DEFAULT_DIR}"

# A folder you cloned yourself is yours to update with git
[ -d "$DIR/.git" ] && die "$DIR is a git clone. Update it with git pull instead."

# The copy beside this script, when the script is a file and Cryogram is unzipped around it
here=""
if [ -f "$0" ]; then
  here=$(cd "$(dirname "$0")" && pwd)
  if [ ! -f "$here/boot.py" ] || [ ! -f "$here/backend/server.py" ]; then here=""; fi
fi
if [ -n "$here" ] && [ -d "$DIR" ] && [ "$here" = "$(cd "$DIR" && pwd)" ]; then
  die "$here is already the installed copy. To update it, run the install line instead."
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM
mkdir "$tmp/unpacked"

if [ -n "$here" ]; then
  VERSION=$(sed -n '/^VERSION = "/{s/^VERSION = "\([^"]*\)".*/\1/p;q;}' "$here/backend/config.py")
  [ -n "$VERSION" ] || die "$here does not have the shape of a Cryogram release."
  say ""
  say "Installing Cryogram $VERSION from $here..."
  new="$tmp/unpacked/cryogram-$VERSION"
  cp -R "$here" "$new" || die "The copy could not be made."
else
  have curl || die "This needs curl to download anything, and it is not on this computer."
  have unzip || die "This needs unzip to open the download, and it is not on this computer."
  # GitHub answers releases/latest with a redirect to the tag's page, and the tag is the last part of that address
  latest=$(curl -fsSL -o /dev/null -w '%{url_effective}' "$REPO/releases/latest") \
    || die "Could not reach $REPO to find the latest release."
  TAG=${latest##*/}
  case "$TAG" in v[0-9]*) ;; *) die "Could not tell the latest release from $REPO (got '$TAG')." ;; esac
  VERSION=${TAG#v}
  say ""
  say "Downloading Cryogram $VERSION from $REPO..."
  curl -fsSL "$REPO/archive/refs/tags/$TAG.zip" -o "$tmp/cryogram.zip" || die "The download failed."
  unzip -q "$tmp/cryogram.zip" -d "$tmp/unpacked" || die "The download could not be unpacked."
  # GitHub's archive holds one folder named after the repository and the tag
  new=""
  for d in "$tmp/unpacked"/*/; do new="${d%/}"; done
  [ -n "$new" ] && [ -f "$new/boot.py" ] && [ -f "$new/backend/server.py" ] \
    || die "The download does not have the shape of a Cryogram release."
fi

# Today's model list for the Builder AI tab, from the main branch; when that read fails the release's own copy stays
if have curl; then
  curl -fsSL "$RAW/builder-models.json" -o "$tmp/builder-models.json" 2>/dev/null \
    && mv "$tmp/builder-models.json" "$new/builder-models.json"
fi

# A copy already here: ask a running one to stop, then carry its private Python and its environment over
if [ -f "$DIR/boot.py" ]; then
  old_py="$DIR/python/bin/python3"
  [ -x "$old_py" ] || old_py=$(command -v python3 2>/dev/null || true)
  if [ -n "$old_py" ] && "$old_py" "$DIR/boot.py" status >/dev/null 2>&1; then
    say ""
    say "Cryogram is running, asking it to stop..."
    "$old_py" "$DIR/boot.py" stop >/dev/null 2>&1 \
      || die "Cryogram did not stop, so the files were left as they are. Quit it from the bottom of the app's sidebar, then run this again."
  fi
  for keep in python .venv; do
    [ -e "$DIR/$keep" ] && mv "$DIR/$keep" "$new/$keep"
  done
  rm -rf "$DIR"
  say ""
  say "The previous version was replaced."
fi
mkdir -p "$(dirname "$DIR")"
mv "$new" "$DIR"
chmod +x "$DIR/$LAUNCHER" "$DIR/start.sh" 2>/dev/null || true
say ""
say "Cryogram $VERSION is in $DIR."

# Whether Cryogram gets an app icon; the answer is remembered, and CRYOGRAM_ICON=yes or no answers for a script
icon="${CRYOGRAM_ICON:-}"
if [ -z "$icon" ] && ( : </dev/tty ) 2>/dev/null; then
  say ""
  printf '%s ' "Add Cryogram to $ICON? [Y/n]"
  read -r answer </dev/tty || answer=""
  case "$answer" in [nN]*) icon=no ;; *) icon=yes ;; esac
fi
if [ "$icon" = no ]; then
  setup_flag="--no-icon"
  START_FROM="by double-clicking \"$LAUNCHER\" in $DIR, or by typing cryogram in a terminal"
else
  setup_flag="--icon"
  START_FROM="from $ICON"
fi

# The environment before the builder question, so a window closed early still leaves a working install
say ""
say "Setting up Cryogram's own environment (a minute or two)..."
# Its Python question reads the terminal when there is one; with none it runs as it is
if ( : </dev/tty ) 2>/dev/null; then (cd "$DIR" && sh "./start.sh" setup "$setup_flag" </dev/tty); else (cd "$DIR" && sh "./start.sh" setup "$setup_flag"); fi \
  && { say ""; if [ "$icon" = no ]; then say "The command cryogram works in a new terminal."; else say "Cryogram is in $ICON now, and the command cryogram works in a new terminal."; fi; } \
  || { say ""; say "The setup did not finish. Start Cryogram by double-clicking \"$LAUNCHER\" in $DIR to try again."; }

# The program the Builder Agent runs on has to be on this computer, sign-in or API key alike; the setup screen checks it
present() {
  command -v "$1" >/dev/null 2>&1 && return 0
  for c in "$HOME/.claude/local/$1" "$HOME/.local/bin/$1" "/opt/homebrew/bin/$1" "/usr/local/bin/$1"; do
    [ -x "$c" ] && return 0
  done
  return 1
}
state() { if present "$1"; then say "installed"; else say "not installed"; fi; }
open_page() {
  say ""
  say "Opening $1"
  [ -n "${CRYOGRAM_NO_BROWSER:-}" ] && return 0
  if have open; then open "$1" >/dev/null 2>&1 || true
  elif have xdg-open; then xdg-open "$1" >/dev/null 2>&1 || true
  fi
}
choice="${CRYOGRAM_BUILDER:-}"
if [ -z "$choice" ] && ( : </dev/tty ) 2>/dev/null; then
  say ""
  say "Cryogram's Builder Agent runs on one of two programs on this computer:"
  say "  1. Claude Code, with a Claude subscription or an Anthropic API key  ($(state claude))"
  say "  2. Codex, with a ChatGPT subscription or an OpenAI API key          ($(state codex))"
  printf '%s ' "Which will you use? [1/2, or Enter to decide later in the app]"
  read -r answer </dev/tty || answer=""
  case "$answer" in 1) choice=claude ;; 2) choice=codex ;; esac
fi
missing=""
case "$choice" in
  claude)
    if present claude; then say "Claude Code is installed."
    else
      missing="Claude Code"
      say ""
      say "Claude Code is not installed yet. Install it from the page opening in your browser, sign in there, and Cryogram's setup screen will check the connection."
      open_page "$CLAUDE_PAGE"
    fi ;;
  codex)
    if present codex; then say "Codex is installed."
    else
      missing="Codex"
      say ""
      say "Codex is not installed yet. Install it from the page opening in your browser, sign in there, and Cryogram's setup screen will check the connection."
      open_page "$CODEX_PAGE"
    fi ;;
esac

if [ -n "${CRYOGRAM_NO_START:-}" ]; then
  say ""
  say "Start it by double-clicking \"$LAUNCHER\" in $DIR."
  exit 0
fi
# With curl | sh the script is on stdin, so the answer is read from the terminal
say ""
if [ -n "$missing" ]; then
  say "You can close this window and start Cryogram $START_FROM, or continue here when you have installed $missing."
  printf '%s ' "Press Enter when you have logged in to $missing to complete the setup."
else
  printf '%s ' "Press Enter to start Cryogram now, or close this window and start it $START_FROM later."
fi
if read -r _ </dev/tty 2>/dev/null; then
  cd "$DIR"
  exec sh "./start.sh"
fi
say ""
say "To start it later: start Cryogram $START_FROM."
