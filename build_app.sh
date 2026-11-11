#!/bin/zsh
# Builds "~/Applications/Live Subtitles.app" – a menu bar app that runs the translator.
# Re-run this if you move the project folder.
set -e
cd "$(dirname "$0")"
HERE="$PWD"
APP="$HOME/Applications/Live Subtitles.app"

# Quit a running copy first.
pkill -f "$HERE/menubar.py" 2>/dev/null || true
pkill -f "$HERE/app.py" 2>/dev/null || true
pkill -f "$HERE/live_server.py" 2>/dev/null || true

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Tiny native launcher (macOS won't reliably launch script-based bundles on Apple Silicon).
LAUNCHER_SRC="$(mktemp -d)/launcher.c"
cat > "$LAUNCHER_SRC" <<EOF
#include <unistd.h>
int main(void) {
    char *argv[] = {"$HERE/.venv/bin/python", "$HERE/app.py", 0};
    execv(argv[0], argv);
    return 1;
}
EOF
clang -O2 -o "$APP/Contents/MacOS/LiveSubtitles" "$LAUNCHER_SRC"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Live Subtitles</string>
  <key>CFBundleDisplayName</key><string>Live Subtitles</string>
  <key>CFBundleIdentifier</key><string>com.livesubtitles.app</string>
  <key>CFBundleExecutable</key><string>LiveSubtitles</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1.1</string>
</dict>
</plist>
EOF

# Draw a simple app icon (speech bubble with "ES→EN").
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
PYTHONWARNINGS=ignore .venv/bin/python - "$ICONSET" <<'PY'
import sys
from AppKit import (NSImage, NSBezierPath, NSColor, NSFont, NSBitmapImageRep,
                    NSString, NSFontAttributeName, NSForegroundColorAttributeName,
                    NSPNGFileType, NSMakeRect, NSMakePoint, NSGraphicsContext)
out = sys.argv[1]
for size in (16, 32, 64, 128, 256, 512, 1024):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, "NSDeviceRGBColorSpace", 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    s = size / 1024
    bg = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(80*s, 80*s, 864*s, 864*s), 190*s, 190*s)
    NSColor.colorWithRed_green_blue_alpha_(0.85, 0.19, 0.15, 1).setFill(); bg.fill()
    bubble = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(190*s, 330*s, 644*s, 420*s), 90*s, 90*s)
    tail = NSBezierPath.bezierPath()
    tail.moveToPoint_(NSMakePoint(330*s, 340*s)); tail.lineToPoint_(NSMakePoint(270*s, 220*s)); tail.lineToPoint_(NSMakePoint(440*s, 340*s)); tail.closePath()
    NSColor.whiteColor().setFill(); bubble.fill(); tail.fill()
    attrs = {NSFontAttributeName: NSFont.boldSystemFontOfSize_(150*s),
             NSForegroundColorAttributeName: NSColor.colorWithRed_green_blue_alpha_(0.85, 0.19, 0.15, 1)}
    text = NSString.stringWithString_("ES→EN")
    w = text.sizeWithAttributes_(attrs).width
    text.drawAtPoint_withAttributes_(NSMakePoint((1024*s - w) / 2, 450*s), attrs)
    NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(NSPNGFileType, None)
    for name, px in ((f"icon_{size}x{size}.png", size), (f"icon_{size//2}x{size//2}@2x.png", size)):
        if name.startswith("icon_8x8"):
            continue
        data.writeToFile_atomically_(f"{out}/{name}", True)
PY
rm -f "$ICONSET"/icon_1024x1024.png
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
touch "$APP"

echo "Built: $APP"

