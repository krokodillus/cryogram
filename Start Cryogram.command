#!/bin/bash
# Cryogram launcher.
case "$0" in */AppTranslocation/*)
  echo "macOS is running Cryogram from a temporary copy."
  echo "Move the Cryogram folder out of Downloads (Desktop works), then double-click again."
  read -r -p "Press Enter to close." _; exit 1;;
esac
xattr -dr com.apple.quarantine . 2>/dev/null
cd "$(dirname "$0")"
py_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; }
# A private Python downloaded on an earlier start lives in ./python and wins.
PY=""
if [ -x python/bin/python3 ] && py_ok python/bin/python3; then
  PY="./python/bin/python3"
else
  PROBE="${PYTHON:-python3}"
  STUB=""
  # A Mac without the developer tools answers python3 with an install dialog, not an interpreter, so that counts as no Python
  if [ "$(uname -s)" = "Darwin" ] && [ -z "${PYTHON:-}" ] \
      && [ "$(command -v python3 2>/dev/null)" = "/usr/bin/python3" ] \
      && ! xcode-select -p >/dev/null 2>&1; then
    STUB="1"
  fi
  if [ -z "$STUB" ] && command -v "$PROBE" >/dev/null 2>&1 && py_ok "$PROBE"; then
    PY="$PROBE"
  fi
fi
if [ -z "$PY" ]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64)  PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.12.7+20241016-aarch64-apple-darwin-install_only.tar.gz"; PBS_SHA="4c18852bf9c1a11b56f21bcf0df1946f7e98ee43e9e4c0c5374b2b3765cf9508" ;;
    Darwin-x86_64) PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.12.7+20241016-x86_64-apple-darwin-install_only.tar.gz"; PBS_SHA="60c5271e7edc3c2ab47440b7abf4ed50fbc693880b474f74f05768f5b657045a" ;;
    *)             PBS_URL=""; PBS_SHA="" ;;
  esac
  if [ -z "$PBS_SHA" ]; then
    echo "Cryogram needs Python 3.10 or newer. Install it from python.org, then try again."
    read -r -p "Press Enter to close." _; exit 1
  fi
  echo "Cryogram needs Python 3.10 or newer, and none was found on this computer."
  echo "It can download a private copy just for this folder (about 40 MB) - nothing else on your computer changes."
  printf "Download it now? [Y/n] "
  read -r ANSWER </dev/tty 2>/dev/null || ANSWER=""
  case "$ANSWER" in
    [nN]*) echo "Okay - install Python 3.10 or newer from python.org, then try again."; read -r -p "Press Enter to close." _; exit 1 ;;
  esac
  echo "Downloading Python..."
  rm -rf python python.tar.gz
  if ! curl -fL --progress-bar -o python.tar.gz "$PBS_URL"; then
    echo "The download didn't finish - usually this means no internet connection. Connect and try again."
    read -r -p "Press Enter to close." _; exit 1
  fi
  if command -v shasum >/dev/null 2>&1; then
    GOT="$(shasum -a 256 python.tar.gz | cut -d' ' -f1)"
  else
    GOT="$(sha256sum python.tar.gz | cut -d' ' -f1)"
  fi
  # The expected checksum is written into this script at release time, so a tampered or truncated download is refused
  if [ "$GOT" != "$PBS_SHA" ]; then
    echo "The downloaded file doesn't match its expected checksum - not using it. Try again later."
    rm -f python.tar.gz
    read -r -p "Press Enter to close." _; exit 1
  fi
  tar -xzf python.tar.gz && rm -f python.tar.gz
  PY="./python/bin/python3"
  if ! py_ok "$PY"; then
    echo "The downloaded Python doesn't run on this computer. Install Python 3.10 or newer from python.org instead."
    read -r -p "Press Enter to close." _; exit 1
  fi
fi
# Everything else, the app's own environment, the server and the cryogram command, is boot.py's job
exec "$PY" boot.py "$@"
