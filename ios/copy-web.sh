#!/bin/sh
# Xcode's "Copy web files" build phase: copies the phone client — docs/ — into the app bundle's
# web/ folder, where AppSchemeHandler serves it as app://local/. There is one copy of the page, in
# docs/; nothing in ios/ is a second one, so a change to it is a rebuild away and nothing else.
#
# sw.js is left out on purpose: it is not part of the app. It is what the retired web address
# serves to end old Home Screen installs. Everything else is copied, folders included (cp -R), so
# a file added to docs/ later comes along instead of being silently dropped by a list that nobody
# remembered to extend.
set -e
SRC="$SRCROOT/../docs"
DEST="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH/web"

rm -rf "$DEST"
mkdir -p "$DEST"

for f in "$SRC"/*; do
  [ -e "$f" ] || continue            # an unmatched glob stays literal; an empty docs/ copies nothing
  case "$(basename "$f")" in
    sw.js) ;;                        # the old web address's last service worker: not for the app
    *) cp -R "$f" "$DEST/" ;;
  esac
done

echo "web files copied to $DEST"
ls "$DEST"
