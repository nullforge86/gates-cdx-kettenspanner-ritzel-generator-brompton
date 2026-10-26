"""
STEP-Bau ohne Internet: die Linux-App ruft das installierte FreeCAD auf.

Gebaut wird mit derselben Baulogik wie im Cloud-Bau (freecad/build_headless.py,
dort steckt der echte CAD-Körper). Dieses Modul kennt kein GTK: Es findet
FreeCAD und die Bau-Skripte, bereitet einen Arbeitsordner vor, setzt den
Befehl zusammen und packt das Ergebnis. Gestartet wird der Befehl in
ritzel-generator.py (asynchron, damit das Fenster nicht hängt).

FreeCAD wird in dieser Reihenfolge gesucht:
  1. RITZEL_FREECADCMD (ganzer Befehl, z. B. "/opt/freecad/bin/freecadcmd")
  2. freecadcmd / FreeCADCmd im PATH
  3. Flatpak org.freecad.FreeCAD (System oder Benutzer)
"""
import glob
import json
import os
import shlex
import shutil
import tempfile
import zipfile

FLATPAK_ID = 'org.freecad.FreeCAD'
HIER = os.path.dirname(os.path.abspath(__file__))

# Im Flatpak heißt das Kommandozeilen-Programm je nach Version freecadcmd
# oder FreeCADCmd — darum im Sandbox-Shell nachsehen statt raten.
_FLATPAK_SH = ('for b in freecadcmd FreeCADCmd; do '
               'command -v "$b" >/dev/null 2>&1 && exec "$b" "$@"; done; '
               'echo "freecadcmd im FreeCAD-Flatpak nicht gefunden" >&2; exit 127')


def finde_freecad_quellen(zusatz=()):
    """Ordner mit build_headless.py, dessen Elternordner params.json enthält.
    Reihenfolge wie bei web/: ausdrücklich gesetzt, Projekt, installierte Kopie."""
    for p in (os.environ.get('RITZEL_FREECAD'), *zusatz,
              os.path.join(HIER, '..', 'freecad'),
              '/usr/share/ritzel-generator/freecad'):
        if (p and os.path.isfile(os.path.join(p, 'build_headless.py'))
                and os.path.isfile(os.path.join(p, '..', 'params.json'))):
            return os.path.abspath(p)
    return None


def _flatpak_installiert():
    orte = [os.path.join(os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share'),
                         'flatpak', 'app', FLATPAK_ID),
            os.path.join('/var/lib/flatpak/app', FLATPAK_ID)]
    return shutil.which('flatpak') is not None and any(os.path.isdir(o) for o in orte)


def finde_freecad():
    """('pfad', [befehl]) | ('flatpak', None) | None"""
    eigen = os.environ.get('RITZEL_FREECADCMD', '').strip()
    if eigen:
        return ('pfad', shlex.split(eigen))
    for name in ('freecadcmd', 'FreeCADCmd'):
        pfad = shutil.which(name)
        if pfad:
            return ('pfad', [pfad])
    if _flatpak_installiert():
        return ('flatpak', None)
    return None


def beschreibe(freecad):
    if freecad is None:
        return 'nicht gefunden'
    art, befehl = freecad
    return f'Flatpak {FLATPAK_ID}' if art == 'flatpak' else befehl[0]


def bereite_arbeit(quellen, basis):
    """Kopiert Bau-Skripte und params.json in einen eigenen Ordner unter
    `basis`. So braucht ein Flatpak-FreeCAD nur Zugriff auf diesen einen
    Ordner, und /usr/share muss in der Sandbox nicht sichtbar sein."""
    os.makedirs(basis, exist_ok=True)
    arbeit = tempfile.mkdtemp(prefix='step-', dir=basis)
    ziel = os.path.join(arbeit, 'freecad')
    os.makedirs(ziel)
    for datei in glob.glob(os.path.join(quellen, '*.py')):
        shutil.copy2(datei, ziel)
    shutil.copy2(os.path.join(quellen, '..', 'params.json'), arbeit)
    os.makedirs(os.path.join(arbeit, 'output'))
    return arbeit


def befehl(freecad, arbeit, params):
    """Befehlszeile und zusätzliche Umgebungsvariablen für den Bau."""
    skript = os.path.join(arbeit, 'freecad', 'build_headless.py')
    env = {'PARAMS_JSON': json.dumps(params),
           'OUTPUT_DIR': os.path.join(arbeit, 'output'),
           'REPO_DIR': os.path.join(arbeit, 'freecad')}
    art, cmd = freecad
    if art == 'flatpak':
        argv = ['flatpak', 'run', '--command=sh', f'--filesystem={arbeit}']
        argv += [f'--env={k}={v}' for k, v in env.items()]
        argv += [FLATPAK_ID, '-c', _FLATPAK_SH, 'sh', skript]
        return argv, {}
    return [*cmd, skript], env


def dateiname(params):
    """Wie in der Web-Version (web/js/step.js), damit die Dateien gleich heißen."""
    if params.get('bauteil') == 'rolle':
        return f"spannrolle_d{params['rolle_d']:.0f}_b{params['rolle_b']:.0f}_step.zip"
    return f"cdx_ritzel_buegel_z{int(params['zaehne'])}_step.zip"


def packe_step(arbeit, zielpfad):
    """Alle .step aus dem Ausgabeordner als ZIP nach `zielpfad`.
    Gibt die Liste der eingepackten Dateinamen zurück (leer = nichts gebaut)."""
    dateien = sorted(glob.glob(os.path.join(arbeit, 'output', '*.step')))
    dateien = [d for d in dateien if os.path.getsize(d) > 0]
    if not dateien:
        return []
    teil = zielpfad + '.teil'
    with zipfile.ZipFile(teil, 'w', zipfile.ZIP_DEFLATED) as z:
        for d in dateien:
            z.write(d, os.path.basename(d))
    os.replace(teil, zielpfad)
    return [os.path.basename(d) for d in dateien]


def letzte_zeilen(text, n=12):
    zeilen = [z for z in (text or '').splitlines() if z.strip()]
    return '\n'.join(zeilen[-n:])
