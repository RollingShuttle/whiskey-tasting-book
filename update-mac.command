#!/bin/bash
# Double-click this file to pull the latest code from GitHub onto this Mac. When it says it is done,
# go back to Xcode and press Cmd-R: the build copies docs/ in fresh, so that is the new version.
#
# Picking a Team or bundle identifier in Xcode edits ios/TastingBookShell.xcodeproj/project.pbxproj;
# --autostash sets any such local edit aside for the pull and puts it back afterwards, so the pull
# never refuses over it and the Mac's own signing choice is never lost.
cd "$(dirname "$0")" || exit 1
echo "Pulling the latest code from GitHub..."
echo
if git pull --ff-only --autostash; then
  echo
  echo "Done. Back in Xcode, press Cmd-R to run."
else
  echo
  echo "The pull did not finish. Take a screenshot of this window and send it to Claude."
fi
echo
echo "-- Signing settings on this Mac (send these lines if asked for them) --"
grep -E "DEVELOPMENT_TEAM|PRODUCT_BUNDLE_IDENTIFIER" ios/TastingBookShell.xcodeproj/project.pbxproj | sort -u
echo
echo "You can close this window."
