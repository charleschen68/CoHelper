#!/usr/bin/env bash
set -euo pipefail

readonly APP_PATH="dist/cohelper.app"
readonly DMG_PATH="dist/cohelper-0.1.0.dmg"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Missing $APP_PATH; run: pyinstaller --clean --noconfirm cohelper.spec" >&2
  exit 1
fi

rm -f "$DMG_PATH"
hdiutil create \
  -volname "cohelper" \
  -srcfolder "$APP_PATH" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

shasum -a 256 "$DMG_PATH"
