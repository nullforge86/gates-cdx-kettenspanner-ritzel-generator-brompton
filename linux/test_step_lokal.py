"""Tests für den lokalen STEP-Bau der Linux-App (ohne GTK, ohne FreeCAD).

Aufruf im Projektordner:  python3 -m unittest discover -s linux -p 'test_*.py'
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_lokal  # noqa: E402

PROJEKT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Steht für freecadcmd: schreibt, was build_headless.py schreiben würde.
FAKE_FREECADCMD = """#!/usr/bin/env python3
import json, os, sys
skript = sys.argv[1]
assert os.path.isfile(skript), skript
assert os.path.isfile(os.path.join(os.environ['REPO_DIR'], '..', 'params.json'))
p = json.loads(os.environ['PARAMS_JSON'])
out = os.environ['OUTPUT_DIR']
if p.get('bauteil') == 'rolle':
    namen = ['spannrolle.step']
else:
    namen = ['ritzel_z%d.step' % p['zaehne'], 'riemenschutz_z%d.step' % p['zaehne']]
for n in namen:
    with open(os.path.join(out, n), 'w') as f:
        f.write('ISO-10303-21;')
"""


class StepLokal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_quellen_im_projekt(self):
        q = step_lokal.finde_freecad_quellen()
        self.assertEqual(q, os.path.join(PROJEKT, 'freecad'))

    def test_freecad_aus_umgebung(self):
        with mock.patch.dict(os.environ, {'RITZEL_FREECADCMD': '/opt/fc/freecadcmd --x'}):
            self.assertEqual(step_lokal.finde_freecad(), ('pfad', ['/opt/fc/freecadcmd', '--x']))

    def test_kein_freecad(self):
        with mock.patch.dict(os.environ, {'RITZEL_FREECADCMD': '', 'PATH': self.tmp.name,
                                          'XDG_DATA_HOME': self.tmp.name}), \
                mock.patch('os.path.isdir', return_value=False):
            self.assertIsNone(step_lokal.finde_freecad())

    def test_flatpak_befehl(self):
        argv, env = step_lokal.befehl(('flatpak', None), '/home/a b/x', {'zaehne': 14})
        self.assertEqual(env, {})
        self.assertEqual(argv[:3], ['flatpak', 'run', '--command=sh'])
        self.assertIn('--filesystem=/home/a b/x', argv)
        self.assertIn('--env=PARAMS_JSON={"zaehne": 14}', argv)
        self.assertEqual(argv[-1], '/home/a b/x/freecad/build_headless.py')
        self.assertIn(step_lokal.FLATPAK_ID, argv)

    def test_dateinamen_wie_web(self):
        self.assertEqual(step_lokal.dateiname({'zaehne': 16}), 'cdx_ritzel_buegel_z16_step.zip')
        self.assertEqual(step_lokal.dateiname({'bauteil': 'rolle', 'rolle_d': 44, 'rolle_b': 9.6}),
                         'spannrolle_d44_b10_step.zip')

    def test_packen_ohne_ergebnis(self):
        arbeit = step_lokal.bereite_arbeit(step_lokal.finde_freecad_quellen(), self.tmp.name)
        self.assertEqual(step_lokal.packe_step(arbeit, os.path.join(self.tmp.name, 'x.zip')), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, 'x.zip')))

    def _bau(self, params, basis):
        fake = os.path.join(self.tmp.name, 'freecadcmd')
        with open(fake, 'w') as f:
            f.write(FAKE_FREECADCMD)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        arbeit = step_lokal.bereite_arbeit(step_lokal.finde_freecad_quellen(), basis)
        argv, env = step_lokal.befehl(('pfad', [fake]), arbeit, params)
        subprocess.run(argv, env={**os.environ, **env}, check=True, stdin=subprocess.DEVNULL)
        ziel = os.path.join(basis, step_lokal.dateiname(params))
        return step_lokal.packe_step(arbeit, ziel), ziel

    def test_bau_ritzel_mit_leerzeichen_im_pfad(self):
        basis = os.path.join(self.tmp.name, 'Mein Cache (ä)')
        dateien, ziel = self._bau({'zaehne': 15, 'bohrung_d': 14}, basis)
        self.assertEqual(dateien, ['riemenschutz_z15.step', 'ritzel_z15.step'])
        with zipfile.ZipFile(ziel) as z:
            self.assertEqual(sorted(z.namelist()), dateien)

    def test_bau_rolle(self):
        dateien, _ = self._bau({'bauteil': 'rolle', 'rolle_d': 40, 'rolle_b': 10}, self.tmp.name)
        self.assertEqual(dateien, ['spannrolle.step'])

    def test_arbeitsordner_vollstaendig(self):
        arbeit = step_lokal.bereite_arbeit(step_lokal.finde_freecad_quellen(), self.tmp.name)
        for datei in ('build_headless.py', 'zahnrad_generator.py', 'rolle_generator.py',
                      'riemenschutz_generator.py', 'ritzel_params.py', 'zahnrad_params.py'):
            self.assertTrue(os.path.isfile(os.path.join(arbeit, 'freecad', datei)), datei)
        with open(os.path.join(arbeit, 'params.json'), encoding='utf-8') as f:
            self.assertIn('bauteile', json.load(f))


if __name__ == '__main__':
    unittest.main()
