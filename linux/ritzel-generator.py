#!/usr/bin/env python3
"""
Ritzel-Generator als richtiges GTK-Programm für Linux.

Formular, Reiter und Knöpfe bestehen aus echten GTK-Bausteinen und folgen dem
Systemdesign (Cinnamon: Farben, Schrift, Hell und Dunkel). Keine Webseite im
Fenster — bis auf die 3D-Vorschau, die in einer WebView zeichnet.

Gerechnet wird mit der Logik der Web-App (web/). Sie läuft im Hintergrund, und
diese Oberfläche ruft sie auf: Die Felder liest die Oberfläche aus der Seite
aus (so gibt es nur EINE Quelle: params.json), Änderungen schreibt sie dorthin
zurück, Geometrie, STL-Export und STEP-Bau bleiben unverändert.

STEP baut die App lokal mit dem installierten FreeCAD (step_lokal.py,
freecad/build_headless.py) — ohne Internet, ohne Cloud-Dienst und ohne
GitHub-Release. Die STEP-Knöpfe der Seite (Release-Download, Cloud-Bau)
werden darum hier nie angeklickt.

Dieselbe Bauweise wie linux-native/ in der Schreibhilfe.
"""
import functools
import http.server
import json
import os
import shutil
import sys
import threading
import time
from urllib.parse import urlparse

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('WebKit2', '4.1')
from gi.repository import Gtk, Gdk, Gio, GLib, WebKit2   # noqa: E402

TITEL = 'Gates CDX Riemenspanner-Ritzel Generator'
HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import step_lokal   # noqa: E402

# Alles außer dem Zeichenbereich der Seite ausblenden — die Bedienung
# übernimmt diese Oberfläche.
SEITE_CSS = """
header, nav#tabs, #form, #printview, #loader { display: none !important; }
html, body { height: 100%; margin: 0; }
#genview { display: block !important; flex: 1 1 auto !important; height: 100vh !important; }
#viewport { width: 100% !important; height: 100vh !important; flex: none !important;
            min-height: 0 !important; order: 0 !important; }
"""

DRUCK_CSS = """
body { font: 14px/1.5 system-ui, sans-serif; margin: 0; padding: 18px 26px; max-width: 900px;
       color: #222; background: #fff; }
@media (prefers-color-scheme: dark) { body { color: #ddd; background: #1e1f22; } a { color: #8ab4f8; } }
h2 { margin-top: 0; } h3 { margin: 1.4em 0 .4em; } h4 { margin: 1em 0 .3em; }
.specs { display: grid; gap: 6px; margin: 0; } .spec { display: grid; grid-template-columns: 200px 1fr; gap: 12px; }
.spec dt { font-weight: 600; } .spec dd { margin: 0; } .spec dd span { display: block; opacity: .75; font-size: 90%; }
.fieldtest, .warn, .disclaimer { padding: 10px 14px; border-left: 4px solid #e08a0d; background: rgba(128,128,128,.12); border-radius: 4px; }
.disclaimer { font-size: 88%; opacity: .85; } ul.ticks { list-style: none; padding-left: 0; }
"""

JS_AUSLESEN = """(function(){
  const out = {sections: [], footer: []};
  document.querySelectorAll('#form fieldset').forEach(fs => {
    if (fs.hidden) return;
    const rows = [];
    fs.querySelectorAll('.row').forEach(r => {
      const l = r.querySelector('label'), i = r.querySelector('input');
      if (!i) return;
      const n = r.nextElementSibling;
      rows.push({id: i.id, label: l ? l.textContent.trim() : i.id, value: parseFloat(i.value),
                 note: n && n.classList.contains('rownote') ? n.textContent.trim() : '',
                 step: parseFloat(i.step) || 1,
                 min: i.min !== '' ? parseFloat(i.min) : null,
                 max: i.max !== '' ? parseFloat(i.max) : null});
    });
    const hints = [...fs.querySelectorAll('.hint')].map(h => h.textContent.trim()).filter(Boolean);
    out.sections.push({id: fs.id, legend: ((fs.querySelector('legend') || {}).textContent || '').trim(), rows, hints});
  });
  for (const id of ['hint2', 'hint3']) { const e = document.getElementById(id); if (e && e.textContent.trim()) out.footer.push(e.textContent.trim()); }
  return JSON.stringify(out);
})()"""

JS_ZUSTAND = """(function(){
  const g = id => document.getElementById(id);
  const vis = e => !!e && e.style.display !== 'none' && !e.hidden;
  const t = id => g(id) ? g(id).textContent.trim() : '';
  const hint = id => ({v: vis(g(id)), t: t(id), always: !!g(id) && g(id).className.indexOf('hint') < 0});
  return JSON.stringify({
    ready: !!document.querySelector('#form fieldset:not([hidden]) .row input'),
    stats: t('stats'), loader: t('loader'), stl: t('stlbtn'),
    step: {v: vis(g('stepbtn')), t: t('stepbtn')}, stephint: hint('stephint'),
    build: {v: vis(g('stepbuildbtn')), t: t('stepbuildbtn'), dis: !!g('stepbuildbtn') && g('stepbuildbtn').disabled},
    buildhint: hint('stepbuildhint'), status: hint('stepbuildstatus'),
    buegel: {v: !!g('buegelrow') && !g('buegelrow').hidden, t: t('buegellbl'), c: !!g('buegelchk') && g('buegelchk').checked},
    tabs: [t('tab-gen'), t('tab-rolle'), t('tab-print')],
    sprache: document.documentElement.lang || 'de',
    lang: t('lang-toggle'), hintson: document.body.classList.contains('hinweise'),
    hintsbtn: g('hintsbtn') ? g('hintsbtn').title : ''
  });
})()"""


# Eingaben fürs lokale STEP so, wie params() in web/js/fields.js sie liest:
# nur das sichtbare Bauteil, Zähne ganzzahlig und in den Grenzen. Felder, die
# das Formular nicht zeigt, ergänzt build_headless.py aus params.json.
JS_PARAMS = """(function(){
  const p = {};
  document.querySelectorAll('#form fieldset:not([hidden]) .row input[type=number]').forEach(i => {
    const v = parseFloat(i.value);
    if (Number.isFinite(v)) p[i.id] = v;
  });
  const z = document.getElementById('zaehne');
  if (z && !z.closest('fieldset').hidden && 'zaehne' in p) {
    let v = Math.round(p.zaehne);
    if (z.min !== '') v = Math.max(parseFloat(z.min), v);
    if (z.max !== '') v = Math.min(parseFloat(z.max), v);
    p.zaehne = v;
  }
  return JSON.stringify(p);
})()"""

NEGATIV_ERLAUBT = {'speichen_schwung'}

BUNDLES = ('index.html', 'js/shell.bundle.js', 'js/viewer.bundle.js')


def finde_web():
    # Reihenfolge: ausdrücklich gesetzt, dann das Projekt neben diesem Skript
    # (Start aus dem Quellordner), zuletzt die installierte Kopie. Ein
    # Quellordner ohne gebaute Bundles zählt nicht: dort bliebe das Fenster leer.
    for p in (os.environ.get('RITZEL_WEB'),
              os.path.join(HIER, '..', 'web'),
              '/usr/share/ritzel-generator/web'):
        if p and all(os.path.isfile(os.path.join(p, b)) for b in BUNDLES):
            return os.path.abspath(p)
    return None


def startfehler(text):
    print(text, file=sys.stderr)
    d = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE,
                          text='Ritzel-Generator kann nicht starten')
    d.format_secondary_text(text)
    d.run()
    d.destroy()
    return 1


def finde_icon():
    for p in ('/usr/share/icons/hicolor/512x512/apps/ritzel-generator.png',
              os.path.join(HIER, 'freecad-starter', 'icon.png')):
        if os.path.isfile(p):
            return p
    return None


class Leise(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


def starte_server(verzeichnis):
    handler = functools.partial(Leise, directory=verzeichnis)
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def downloads():
    ordner = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) \
        or os.path.expanduser('~')
    os.makedirs(ordner, exist_ok=True)
    return ordner


def freier_name(ordner, name):
    name = os.path.basename(name) or 'download'
    stamm, ende = os.path.splitext(name)
    pfad, n = os.path.join(ordner, name), 1
    while os.path.exists(pfad):
        pfad = os.path.join(ordner, f'{stamm} ({n}){ende}')
        n += 1
    return pfad


def js(view, code, callback=None):
    """JavaScript in der WebView ausführen; callback bekommt den Text-Rückgabewert."""
    def fertig(ansicht, res, _):
        try:
            wert = ansicht.run_javascript_finish(res).get_js_value().to_string()
        except Exception:
            wert = None
        if callback:
            callback(wert)
    view.run_javascript(code, None, fertig, None)


def js_text(s):
    return json.dumps(s)


class App(Gtk.Window):
    def __init__(self, port):
        super().__init__(title=TITEL)
        self.port = port
        self.bauteil = 'gen'          # gen | rolle | print
        self.hinweise_an = False
        self.zeilen = {}              # id -> SpinButton
        self.hinweis_labels = []
        self.fertig = False
        self.druck_html = None
        self.fehlgeschlagen = set()   # Downloads, die „failed“ gemeldet haben
        self.freecad = step_lokal.finde_freecad()
        self.freecad_quellen = step_lokal.finde_freecad_quellen()
        self.bau = None               # laufender lokaler STEP-Bau
        self.connect('destroy', self.aufraeumen)
        self.set_default_size(1320, 840)
        self.set_icon_name('ritzel-generator')
        icon = finde_icon()
        if icon and not Gtk.IconTheme.get_default().has_icon('ritzel-generator'):
            self.set_icon_from_file(icon)

        self.baue_kopfzeile()
        self.baue_inhalt()
        self.show_all()
        self.stack.set_visible_child_name('gen')
        self.web.load_uri(f'http://127.0.0.1:{port}/')
        GLib.timeout_add(700, self.abfragen)

    # ── Kopfzeile ───────────────────────────────────────────────────
    def baue_kopfzeile(self):
        bar = Gtk.HeaderBar(show_close_button=True, title='Ritzel-Generator',
                            subtitle='Riemenspanner-Ritzel für Gates CDX / Brompton')
        self.set_titlebar(bar)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.get_style_context().add_class('linked')
        self.reiter = {}
        erster = None
        for name, text in (('gen', 'Ritzel'), ('rolle', 'Spannrolle'), ('print', 'Druck-Empfehlungen')):
            b = Gtk.RadioButton.new_with_label_from_widget(erster, text)
            b.set_mode(False)
            if erster is None:
                erster = b
            b.connect('toggled', self.reiter_gewechselt, name)
            b.set_sensitive(False)        # erst, wenn die Seite das Formular gebaut hat
            box.pack_start(b, False, False, 0)
            self.reiter[name] = b
        bar.set_custom_title(box)

        self.sprache_btn = Gtk.Button(label='DE / EN')
        self.sprache_btn.set_tooltip_text('Sprache wechseln / switch language')
        self.sprache_btn.connect('clicked', self.sprache_wechseln)
        bar.pack_end(self.sprache_btn)

        self.hinweis_btn = Gtk.ToggleButton()
        self.hinweis_btn.set_image(Gtk.Image.new_from_icon_name('dialog-information-symbolic', Gtk.IconSize.BUTTON))
        self.hinweis_btn.set_tooltip_text('Erklärungen einblenden')
        self.hinweis_btn.connect('toggled', self.hinweise_umschalten)
        bar.pack_end(self.hinweis_btn)

    # ── Inhalt ──────────────────────────────────────────────────────
    def baue_inhalt(self):
        s = WebKit2.Settings()
        s.set_enable_webgl(True)
        s.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.ALWAYS)
        s.set_enable_developer_extras(False)
        ucm = WebKit2.UserContentManager()
        ucm.add_style_sheet(WebKit2.UserStyleSheet(
            SEITE_CSS, WebKit2.UserContentInjectedFrames.ALL_FRAMES,
            WebKit2.UserStyleLevel.USER, None, None))
        self.web = WebKit2.WebView.new_with_user_content_manager(ucm)
        self.web.set_settings(s)
        self.web.connect('decide-policy', self.on_policy)
        self.web.connect('context-menu', lambda *a: True)
        self.web.get_context().connect('download-started', self.on_download)

        # Links: Kennzahlen + Formular, rechts die Vorschau
        links = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        links.set_size_request(380, -1)
        self.stats = Gtk.Label(xalign=0, wrap=True, use_markup=True)
        self.stats.set_margin_start(12); self.stats.set_margin_end(12); self.stats.set_margin_top(10)
        links.pack_start(self.stats, False, False, 0)

        self.form_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ('start', 'end'):
            getattr(self.form_box, f'set_margin_{m}')(12)
        self.form_scroll.add(self.form_box)
        links.pack_start(self.form_scroll, True, True, 0)

        unten = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for m in ('start', 'end', 'bottom'):
            getattr(unten, f'set_margin_{m}')(12)
        self.buegel_chk = Gtk.CheckButton(label='Riemenschutz-Bügel anzeigen')
        self.buegel_chk.connect('toggled', self.buegel_umschalten)
        self.stl_btn = Gtk.Button(label='ZIP herunterladen')
        self.stl_btn.get_style_context().add_class('suggested-action')
        self.stl_btn.connect('clicked', lambda *_: self.klick('stlbtn'))
        # Beide STEP-Knöpfe bauen lokal mit FreeCAD: „STEP-ZIP herunterladen“
        # (Standardwerte) und „STEP für diese Werte bauen“ (eigene Werte, Rolle).
        self.step_btn = Gtk.Button(label='STEP herunterladen')
        self.step_btn.connect('clicked', self.lokal_klick)
        self.build_btn = Gtk.Button(label='STEP bauen')
        self.build_btn.connect('clicked', self.lokal_klick)
        self.step_hint = self.neues_hinweislabel()
        self.build_hint = self.neues_hinweislabel()
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.status.get_style_context().add_class('dim-label')
        self.status.set_selectable(True)
        for w in (self.buegel_chk, self.stl_btn, self.step_btn, self.step_hint,
                  self.build_btn, self.build_hint, self.status):
            unten.pack_start(w, False, False, 0)
        links.pack_start(unten, False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.pack1(links, False, False)
        paned.pack2(self.web, True, False)
        paned.set_position(400)

        # Zweite Seite: Druck-Empfehlungen (reiner Text)
        self.druck = WebKit2.WebView()
        self.druck.connect('decide-policy', self.on_policy)
        self.druck.connect('context-menu', lambda *a: True)

        self.stack = Gtk.Stack()
        self.stack.add_named(paned, 'gen')
        self.stack.add_named(self.druck, 'print')
        self.add(self.stack)

    def neues_hinweislabel(self):
        l = Gtk.Label(xalign=0, wrap=True)
        l.get_style_context().add_class('dim-label')
        return l

    # ── Formular aus der Seite aufbauen ─────────────────────────────
    def formular_lesen(self):
        js(self.web, JS_AUSLESEN, self.formular_bauen)

    def formular_bauen(self, text):
        if not text:
            return
        daten = json.loads(text)
        for kind in self.form_box.get_children():
            self.form_box.remove(kind)
        self.zeilen.clear()
        self.hinweis_labels = []
        for sec in daten['sections']:
            rahmen = Gtk.Frame(label=sec['legend'])
            rahmen.get_label_widget().set_markup(f"<b>{GLib.markup_escape_text(sec['legend'])}</b>")
            gitter = Gtk.Grid(row_spacing=6, column_spacing=10)
            for m in ('start', 'end', 'top', 'bottom'):
                getattr(gitter, f'set_margin_{m}')(10)
            zeile = 0                       # Gitterzeile: Zusätze brauchen eine eigene
            for r in sec['rows']:
                lab = Gtk.Label(label=r['label'], xalign=0, wrap=True)
                lab.set_hexpand(True)
                step = r['step']
                stellen = len(f'{step:.6f}'.rstrip('0').split('.')[1]) if '.' in f'{step:.6f}'.rstrip('0') else 0
                # Ohne Grenze aus der Seite: Maße und Anzahlen sind nie negativ,
                # nur der Speichen-Schwung (Winkel) darf es sein.
                lo = r['min'] if r['min'] is not None else (-1e6 if r['id'] in NEGATIV_ERLAUBT else 0)
                hi = r['max'] if r['max'] is not None else 1e6
                wert = r['value'] if isinstance(r['value'], (int, float)) and r['value'] == r['value'] else lo
                adj = Gtk.Adjustment(value=wert, lower=lo, upper=hi, step_increment=step, page_increment=step * 10)
                spin = Gtk.SpinButton(adjustment=adj, digits=max(stellen, 1 if step < 1 else 0))
                spin.set_width_chars(7)
                spin.set_alignment(1.0)
                spin.connect('value-changed', self.wert_geaendert, r['id'])
                gitter.attach(lab, 0, zeile, 1, 1)
                gitter.attach(spin, 1, zeile, 1, 1)
                self.zeilen[r['id']] = spin
                zeile += 1
                if r.get('note'):           # Zusatz („0 = …“) als graue Zeile unter dem Feld
                    zusatz = self.neues_hinweislabel()
                    zusatz.set_text(r['note'])
                    zusatz.set_margin_bottom(2)
                    gitter.attach(zusatz, 0, zeile, 2, 1)
                    zeile += 1
            ende = zeile
            for h in sec['hints']:
                l = self.neues_hinweislabel()
                l.set_text(h)
                l.set_no_show_all(True)
                l.set_visible(self.hinweise_an)
                gitter.attach(l, 0, ende, 2, 1)
                ende += 1
                self.hinweis_labels.append(l)
            rahmen.add(gitter)
            self.form_box.pack_start(rahmen, False, False, 0)
        for text in daten['footer']:
            l = self.neues_hinweislabel()
            l.set_text(text)
            l.set_no_show_all(True)
            l.set_visible(self.hinweise_an)
            self.form_box.pack_start(l, False, False, 0)
            self.hinweis_labels.append(l)
        self.form_box.show_all()
        for l in self.hinweis_labels:
            l.set_visible(self.hinweise_an)

    def wert_geaendert(self, spin, feld_id):
        wert = spin.get_value()
        if self.bau is None:
            self.setze_status('')         # alte STEP-Meldung passt nicht mehr zu den Werten
        js(self.web, f"""(function(id,v){{var i=document.getElementById(id); if(!i) return '';
            i.value=v; i.dispatchEvent(new Event('input',{{bubbles:true}}));
            i.dispatchEvent(new Event('change',{{bubbles:true}})); return '';}})({js_text(feld_id)},{wert})""")

    # ── Bedienung ───────────────────────────────────────────────────
    def klick(self, element_id):
        js(self.web, f"(function(){{var e=document.getElementById({js_text(element_id)}); if(e) e.click(); return '';}})()")

    def buegel_umschalten(self, chk):
        if getattr(self, '_sperre', False):
            return
        an = 'true' if chk.get_active() else 'false'
        js(self.web, f"""(function(){{var c=document.getElementById('buegelchk'); if(!c) return '';
            c.checked={an}; c.dispatchEvent(new Event('change',{{bubbles:true}})); return '';}})()""")

    def reiter_gewechselt(self, knopf, name):
        if not knopf.get_active() or not self.fertig:
            return
        self.bauteil = name
        if self.bau is None:
            self.setze_status('')         # Meldung des letzten STEP-Baus gehört zum alten Reiter
        if name == 'print':
            self.stack.set_visible_child_name('print')
            self.hinweis_btn.set_sensitive(False)
            self.druck_laden()
            return
        self.stack.set_visible_child_name('gen')
        self.hinweis_btn.set_sensitive(True)
        web_id = 'tab-gen' if name == 'gen' else 'tab-rolle'
        js(self.web, f"(function(){{document.getElementById('{web_id}').click(); return '';}})()",
           lambda _: self.formular_lesen())

    def sprache_wechseln(self, *_):
        js(self.web, "(function(){document.getElementById('lang-toggle').click(); return '';})()",
           lambda _: (self.formular_lesen(), self.druck_laden()))

    def hinweise_umschalten(self, knopf):
        self.hinweise_an = knopf.get_active()
        for l in self.hinweis_labels:
            l.set_visible(self.hinweise_an)
        js(self.web, f"document.body.classList.toggle('hinweise', {str(self.hinweise_an).lower()}) && ''")

    def druck_laden(self):
        def zeigen(html):
            if html is None:
                return
            self.druck.load_html(f'<html><head><meta charset="utf-8"><style>{DRUCK_CSS}</style></head>'
                                 f'<body>{html}</body></html>', f'http://127.0.0.1:{self.port}/')
        js(self.web, "document.getElementById('printview').innerHTML", zeigen)

    # ── Regelmäßig den Zustand der Seite übernehmen ─────────────────
    def abfragen(self):
        js(self.web, JS_ZUSTAND, self.zustand)
        return True

    def zustand(self, text):
        if not text:
            return
        z = json.loads(text)
        erstmals = z['ready'] and not self.fertig
        if erstmals:
            self.fertig = True
            for knopf in self.reiter.values():
                knopf.set_sensitive(True)
            self.formular_lesen()
            self.druck_laden()
        # Solange die 3D-Vorschau lädt (oder nicht laden kann), steht ihr
        # Ladezustand oben — das Formular ist trotzdem schon bedienbar.
        self.stats.set_text(z['stats'] or z['loader'])
        self.stl_btn.set_label(z['stl'].lstrip('💾 ').strip() or 'ZIP herunterladen')
        self.step_zustand(z)
        self.buegel_chk.set_visible(z['buegel']['v'] and self.bauteil == 'gen')
        self.buegel_chk.set_label(z['buegel']['t'])
        self._sperre = True
        self.buegel_chk.set_active(z['buegel']['c'])
        self._sperre = False
        for name, text in zip(('gen', 'rolle', 'print'), z['tabs']):
            if text:
                self.reiter[name].set_label(text)
        if z['hintsbtn']:
            self.hinweis_btn.set_tooltip_text(z['hintsbtn'])

    # ── Fremde Links, Downloads ─────────────────────────────────────
    def on_policy(self, web, decision, typ):
        if typ in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
                   WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            u = urlparse(uri)
            if u.scheme in ('http', 'https') and u.hostname != '127.0.0.1':
                decision.ignore()
                try:
                    Gtk.show_uri_on_window(self, uri, Gdk.CURRENT_TIME)
                except GLib.Error as e:
                    self.meldung(f'Link konnte nicht geöffnet werden:\n{uri}\n\n{e.message}',
                                 Gtk.MessageType.ERROR)
                return True
        return False

    def on_download(self, ctx, download):
        download.connect('decide-destination', self.on_ziel)
        download.connect('finished', self.on_fertig)
        download.connect('failed', self.on_fehler)

    def on_ziel(self, download, vorschlag):
        pfad = freier_name(downloads(), vorschlag or 'download')
        # Ziel als URI: Leerzeichen und Sonderzeichen müssen kodiert sein,
        # sonst scheitert z. B. „datei (1).zip“.
        download.set_destination(GLib.filename_to_uri(pfad, None))
        return True

    def meldung(self, text, art=Gtk.MessageType.INFO):
        d = Gtk.MessageDialog(transient_for=self, modal=False, message_type=art,
                              buttons=Gtk.ButtonsType.OK, text=text)
        d.connect('response', lambda w, r: w.destroy())
        d.show()

    def on_fertig(self, download):
        # WebKit meldet „finished“ auch nach einem Fehler (nach „failed“).
        if download in self.fehlgeschlagen:
            self.fehlgeschlagen.discard(download)
            return
        ziel = download.get_destination()
        if ziel:
            self.meldung(f'Gespeichert unter:\n{GLib.filename_from_uri(ziel)[0]}')

    def on_fehler(self, download, fehler):
        self.fehlgeschlagen.add(download)
        self.meldung(f'Download fehlgeschlagen: {fehler.message}', Gtk.MessageType.ERROR)

    # ── STEP lokal mit FreeCAD bauen ────────────────────────────────
    def lokal_moeglich(self):
        return self.freecad is not None and self.freecad_quellen is not None

    def step_zustand(self, z):
        """Die STEP-Knöpfe folgen der Seite nur darin, OB Standardwerte
        vorliegen (dann zeigt sie den Release-Knopf). Gebaut wird immer lokal;
        Cloud-Status und „nur für Standardwerte“-Hinweise der Seite gelten hier nicht."""
        de = z['sprache'] != 'en'
        standard = z['step']['v'] and self.bauteil == 'gen'
        sichtbar = self.bauteil != 'print'
        self.step_btn.set_visible(sichtbar and standard)
        self.build_btn.set_visible(sichtbar and not standard)
        if self.bau is None:
            self.step_btn.set_label(z['step']['t'].lstrip('📐 ').strip() or 'STEP')
            self.build_btn.set_label('STEP für diese Werte bauen' if de else 'Build STEP for these values')
        for knopf in (self.step_btn, self.build_btn):
            knopf.set_sensitive(self.lokal_moeglich() or self.bau is not None)
        if self.lokal_moeglich():
            erklaerung = (f'Exakter CAD-Körper, lokal gebaut mit FreeCAD ({step_lokal.beschreibe(self.freecad)}), '
                          'ohne Internet. Mit Verrundungen dauert das einige Minuten.' if de else
                          f'Exact CAD body, built locally with FreeCAD ({step_lokal.beschreibe(self.freecad)}), '
                          'no internet needed. With fillets this takes a few minutes.')
            dauernd = False
        elif self.freecad_quellen is None:
            erklaerung = ('STEP nicht möglich: Die FreeCAD-Bau-Skripte (freecad/) fehlen.' if de else
                          'STEP not possible: the FreeCAD build scripts (freecad/) are missing.')
            dauernd = True
        else:
            erklaerung = (('STEP braucht FreeCAD. Installieren mit:\n' if de else
                           'STEP needs FreeCAD. Install it with:\n')
                          + f'flatpak install flathub {step_lokal.FLATPAK_ID}')
            dauernd = True
        aktiv = self.step_hint if standard else self.build_hint
        anderer = self.build_hint if standard else self.step_hint
        anderer.set_visible(False)
        aktiv.set_text(erklaerung)
        aktiv.set_visible(sichtbar and (dauernd or self.hinweise_an))
        if self.bau is None and not self.status.get_text():
            self.status.set_visible(False)

    def setze_status(self, text):
        self.status.set_text(text)
        self.status.set_visible(bool(text))

    def lokal_klick(self, *_):
        if self.bau is not None:
            self.bau['abgebrochen'] = True
            self.bau['proc'].force_exit()
            return
        if not self.lokal_moeglich():
            return
        js(self.web, JS_PARAMS, self.lokal_starten)

    def lokal_starten(self, text):
        if not text or self.bau is not None:
            return
        params = json.loads(text)
        if self.bauteil == 'rolle':
            params['bauteil'] = 'rolle'
        try:
            arbeit = step_lokal.bereite_arbeit(
                self.freecad_quellen, os.path.join(GLib.get_user_cache_dir(), 'ritzel-generator'))
            argv, env = step_lokal.befehl(self.freecad, arbeit, params)
            launcher = Gio.SubprocessLauncher.new(
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
            # stdin leer: freecadcmd soll nach dem Skript nicht auf Eingaben warten.
            launcher.set_stdin_file_path(os.devnull)
            for k, v in env.items():
                launcher.setenv(k, v, True)
            launcher.set_cwd(arbeit)
            proc = launcher.spawnv(argv)
        except (OSError, GLib.Error) as e:
            self.meldung(f'FreeCAD konnte nicht gestartet werden:\n{e}', Gtk.MessageType.ERROR)
            return
        self.bau = {'proc': proc, 'arbeit': arbeit, 'params': params,
                    'start': time.monotonic(), 'abgebrochen': False}
        for knopf in (self.step_btn, self.build_btn):
            knopf.set_label('STEP-Bau abbrechen')
        self.lokal_takt()
        GLib.timeout_add_seconds(1, self.lokal_takt)
        proc.communicate_utf8_async(None, None, self.lokal_ausgabe, self.bau)

    def lokal_ausgabe(self, proc, res, bau):
        try:
            bau['ausgabe'] = proc.communicate_utf8_finish(res)[1]
        except GLib.Error as e:
            bau['ausgabe'] = str(e)
        proc.wait_async(None, self.lokal_fertig, bau)   # erst nach Prozessende auswerten

    def lokal_takt(self):
        if self.bau is None:
            return False
        s = int(time.monotonic() - self.bau['start'])
        self.setze_status(f'FreeCAD baut den CAD-Körper … {s // 60}:{s % 60:02d}')
        return True

    def lokal_fertig(self, proc, res, bau):
        try:
            proc.wait_finish(res)
        except GLib.Error:
            pass
        ausgabe = bau.get('ausgabe') or ''
        if self.bau is bau:
            self.bau = None
        if not self.get_realized():         # Fenster schon zu: nur aufräumen
            shutil.rmtree(bau['arbeit'], ignore_errors=True)
            return
        self.abfragen()                     # Knopf-Beschriftungen zurücksetzen
        try:
            if bau['abgebrochen']:
                self.setze_status('STEP-Bau abgebrochen.')
                return
            ziel = freier_name(downloads(), step_lokal.dateiname(bau['params']))
            dateien = step_lokal.packe_step(bau['arbeit'], ziel)
            if proc.get_if_exited() and proc.get_exit_status() == 0 and dateien:
                self.setze_status('STEP gespeichert: ' + os.path.basename(ziel))
                self.meldung(f'STEP gespeichert unter:\n{ziel}')
                return
            if dateien:                      # Teil gebaut, aber Fehlercode: nicht weitergeben
                os.remove(ziel)
            log = os.path.join(GLib.get_user_cache_dir(), 'ritzel-generator', 'letzter-step-bau.log')
            with open(log, 'w', encoding='utf-8') as f:
                f.write(ausgabe or '')
            self.setze_status('STEP-Bau fehlgeschlagen.')
            self.meldung('STEP-Bau mit FreeCAD fehlgeschlagen.\n\n'
                         f'{step_lokal.letzte_zeilen(ausgabe)}\n\nVollständiges Protokoll:\n{log}',
                         Gtk.MessageType.ERROR)
        except OSError as e:
            self.meldung(f'STEP konnte nicht gespeichert werden:\n{e}', Gtk.MessageType.ERROR)
        finally:
            shutil.rmtree(bau['arbeit'], ignore_errors=True)

    def aufraeumen(self, *_):
        # Fenster zu während eines Baus: FreeCAD beenden, Arbeitsordner weg
        # (die Hauptschleife endet gleich, lokal_fertig käme nicht mehr dran).
        if self.bau is not None:
            self.bau['abgebrochen'] = True
            self.bau['proc'].force_exit()
            shutil.rmtree(self.bau['arbeit'], ignore_errors=True)


def main():
    GLib.set_prgname('ritzel-generator')
    GLib.set_application_name(TITEL)
    web = finde_web()
    if web is None:
        return startfehler('Die Oberfläche (web/ mit shell.bundle.js und viewer.bundle.js) fehlt.\n'
                           'Im Projektordner zuerst bauen:  npm install && npm run build:shell '
                           '&& npm run build:viewer\noder das Paket neu installieren.')
    port = starte_server(web)
    app = Gtk.Application(application_id='de.ritzelgenerator.native')
    app.fenster = None

    def aktivieren(app):
        if app.fenster is None:
            app.fenster = App(port)
            app.add_window(app.fenster)
            app.fenster.stack.set_visible_child_name('gen')
        app.fenster.present()      # zweiter Start: Fenster nach vorn

    app.connect('activate', aktivieren)
    return app.run([sys.argv[0]])


if __name__ == '__main__':
    sys.exit(main())
