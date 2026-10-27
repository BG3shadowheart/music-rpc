#!/bin/sh
# Copy the shared sources into each browser package.
# The generated copies are committed so the folders can be loaded directly with
# "Load unpacked"; re-run this script after editing anything in extension/src/.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for flavour in chromium firefox; do
    cp "$here/src/content.js" "$here/$flavour/content.js"
    cp "$here/src/background.js" "$here/$flavour/background.js"
done
echo "Extension sources copied to extension/chromium and extension/firefox."
