#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -r macos/requirements.txt
.venv/bin/python -m PyInstaller --noconfirm macos/MessagerieLocale.spec
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "dist/Messagerie locale.app" "dist/MessagerieLocale-macOS-$(uname -m).zip"
