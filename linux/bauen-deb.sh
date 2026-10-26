#!/usr/bin/env bash
# Baut das installierbare Linux-Paket: linux/dist/ritzel-generator_<version>_all.deb
# Aufruf im Projektordner:  linux/bauen-deb.sh      (braucht Node + npm, dpkg-deb)
# Vorher einmal: npm install
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="1.0.$(git rev-list --count HEAD 2>/dev/null || echo 0)"
PKG=ritzel-generator
STAGE="$ROOT/linux/dist/stage"
OUT="$ROOT/linux/dist/${PKG}_${VERSION}_all.deb"

if [ ! -d node_modules/esbuild ]; then
  echo "node_modules fehlt — bitte zuerst im Projektordner: npm install" >&2
  exit 1
fi
npm run build:shell >/dev/null
npm run build:viewer >/dev/null

rm -rf "$ROOT/linux/dist"; mkdir -p "$STAGE"
install -d "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/lib/$PKG" \
           "$STAGE/usr/share/$PKG" "$STAGE/usr/share/applications" \
           "$STAGE/usr/share/icons/hicolor/512x512/apps"

# Oberfläche: nur, was die Seite zur Laufzeit braucht
cp -r web "$STAGE/usr/share/$PKG/web"
rm -rf "$STAGE/usr/share/$PKG/web/buegel"

install -m 755 linux/ritzel-generator.py "$STAGE/usr/lib/$PKG/ritzel-generator.py"
install -m 644 linux/step_lokal.py "$STAGE/usr/lib/$PKG/step_lokal.py"

# Lokaler STEP-Bau: FreeCAD-Baulogik + gemeinsame Parameter (params.json muss
# neben freecad/ liegen, so erwartet es zahnrad_params.py)
install -d "$STAGE/usr/share/$PKG/freecad"
install -m 644 freecad/*.py "$STAGE/usr/share/$PKG/freecad/"
install -m 644 params.json "$STAGE/usr/share/$PKG/params.json"
printf '#!/bin/sh\nexec python3 /usr/lib/%s/ritzel-generator.py "$@"\n' "$PKG" > "$STAGE/usr/bin/$PKG"
chmod 755 "$STAGE/usr/bin/$PKG"
install -m 644 web/icons/icon-512.png "$STAGE/usr/share/icons/hicolor/512x512/apps/$PKG.png"

cat > "$STAGE/usr/share/applications/$PKG.desktop" <<D
[Desktop Entry]
Type=Application
Name=Ritzel-Generator
GenericName=Gates CDX Ritzel Generator
Comment=Riemenspanner-Ritzel für Gates CDX / Brompton konfigurieren
Exec=$PKG
Icon=$PKG
Terminal=false
Categories=Graphics;Engineering;
StartupWMClass=$PKG
D

cat > "$STAGE/DEBIAN/control" <<C
Package: $PKG
Version: $VERSION
Section: graphics
Priority: optional
Architecture: all
Depends: python3, python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1
Suggests: flatpak
Maintainer: Kay Siebke <nullforge86@gmail.com>
Description: Gates CDX Riemenspanner-Ritzel Generator
 Eigenständige Linux-Anwendung (eigenes Fenster, läuft offline) zum
 Konfigurieren von Ritzeln und Spannrollen mit 3D-Vorschau und STL-Export.
 STEP-Dateien baut die Anwendung lokal mit einem installierten FreeCAD
 (z. B. Flatpak org.freecad.FreeCAD), ohne Internet oder Cloud-Dienst.
C

chmod -R u=rwX,go=rX "$STAGE"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
rm -rf "$STAGE"
echo "Fertig: $OUT"
