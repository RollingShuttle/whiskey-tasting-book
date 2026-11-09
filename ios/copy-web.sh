#!/bin/sh
# Xcode's "Copy web files" build phase: copies the phone client — docs/, the very files GitHub Pages
# serves — into the app bundle's web/ folder, where AppSchemeHandler serves them as app://local/.
# There is one copy of the page, in docs/; nothing in ios/ is a second one, so a web change is a
# rebuild away and nothing else.
#
# sw.js is left out on purpose: the bundle is the cache, and a service worker cannot register on the
# app:// scheme anyway. Everything else is copied, folders included (cp -R), so a file added to docs/
# later comes along instead of being silently dropped by a list that nobody remembered to extend.
set -e
SRC="$SRCROOT/../docs"
DEST="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH/web"

rm -rf "$DEST"
mkdir -p "$DEST"

for f in "$SRC"/*; do
  [ -e "$f" ] || continue            # an unmatched glob stays literal; an empty docs/ copies nothing
  case "$(basename "$f")" in
    sw.js) ;;                        # the PWA's service worker: not for the shell
    *) cp -R "$f" "$DEST/" ;;
  esac
done

echo "web files copied to $DEST"
ls "$DEST"
