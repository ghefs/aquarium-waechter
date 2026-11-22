"""Steuerung des ProfiLux / der Mitras-Leuchten aus Home Assistant (alles über die offizielle GHL-API, SET-Befehle).

Zwei getrennte Freigaben je Gerät (beide standardmäßig aus, die API muss dafür auf „Voller Zugriff“ stehen):

* ``steuerung``                  – Aktionen: Wartungsmodus, Futterpause, Wasserwechsel, Lichtszenen, Gewitter,
                                   Master-Helligkeit, Aktionen von KH-/ION-Director.
* ``einstellungen_schreiben``    – ändern dauerhaft Einstellungen im Gerät: Sollwerte (Sensoren, KH, ION),
                                   Lichtkurven, Namen. Mit Sicherheitsgrenzen (siehe LIMITS / MAX_STEP).

Jede Steuerung ist eine MQTT-Entität: ``<prefix>/<uid>/ctl/<id>`` (Zustand) und ``…/set`` (Befehl).
Der ProfiLux meldet den Zustand der Sonderfunktionen nicht zurück – die Schalter zeigen einen angenommenen Zustand.
"""

import logging
import re
import time

from .ghl import NackError, ReadError
from .i18n import _

log = logging.getLogger("waechter")

# Sonderfunktionen mit Umschalter: (API-Name, Anzahl)
SF = {"wartung": ("MAINTENANCE", 4), "futterpause": ("FEEDPAUSE", 4), "wasserwechsel": ("WATERCHANGE", 4)}
SF_MITRAS = ("wartung",)           # Futterpause und Wasserwechsel gibt es nur am ProfiLux
SCENES = 8
KURVE_MAX = 24                     # Kurvenpunkte 0 … 23

# Auswahl bei den Aktionen der Messgeräte (deutsche Schlüssel, werden übersetzt; Index = STARTACTION-Nummer)
NICHTS = "–"
KH_AKTIONEN = ["Stopp", "Messung starten", "Schlauch entlüften", "Messzelle spülen", "Messzelle leeren"]
ION_AKTIONEN = ["Stopp", "Messung starten", "Messzelle vorbereiten", "Messzelle ganz leeren", "Sensortest",
                "Messzelle befüllen"]
OPTIONEN = [NICHTS] + KH_AKTIONEN + [a for a in ION_AKTIONEN if a not in KH_AKTIONEN]

# Sicherheitsgrenzen für Sollwerte: Art -> (min, max, Schritt, größte Änderung je Schreibvorgang)
LIMITS = {
    "Temperatur": (10.0, 35.0, 0.1, 3.0),
    "pH": (6.0, 9.5, 0.01, 0.5),
    "Redox": (100.0, 600.0, 1.0, 100.0),
    "Leitwert (Meerwasser)": (30.0, 60.0, 0.1, 5.0),
    "Leitwert (Süßwasser)": (30.0, 3000.0, 1.0, None),
    "Sauerstoff": (1.0, 15.0, 0.1, 3.0),
}
LIMIT_KH = (3.0, 20.0, 0.1, 2.0)
PROZENT_SCHRITT = 0.2              # ohne feste Grenze: höchstens ±20 % des alten Wertes je Änderung
NAME_MAX = 16
REFRESH_S = 1800                   # Sollwerte/Kurven/Namen alle 30 min neu lesen


def _kvp(text):
    """'06:30=0, 07:00=20' -> [(390, 0.0), (420, 20.0)] oder ValueError."""
    punkte = []
    for teil in re.split(r"[;,\n]+", text.strip()):
        if not teil.strip():
            continue
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*=\s*(\d{1,3}(?:[.,]\d+)?)\s*%?\s*", teil)
        if not m:
            raise ValueError(teil.strip())
        h, mi, p = int(m[1]), int(m[2]), float(m[3].replace(",", "."))
        if h > 23 or mi > 59 or p > 100:
            raise ValueError(teil.strip())
        punkte.append((h * 60 + mi, p))
    return punkte


def kurve_text(punkte):
    return ", ".join(f"{t // 60:02d}:{t % 60:02d}={p:g}" for t, p in punkte)


def kurve_pruefen(punkte):
    """Zeiten streng aufsteigend, höchstens 24 Punkte, Helligkeit 0–100 %."""
    if not punkte:
        raise ValueError("leer")
    if len(punkte) > KURVE_MAX:
        raise ValueError(f">{KURVE_MAX}")
    zeiten = [t for t, _p in punkte]
    if zeiten != sorted(set(zeiten)):
        raise ValueError("Reihenfolge")
    return punkte


class Steuerung:
    """Alle Steuer-Entitäten eines Geräts (ProfiLux oder Mitras)."""

    def __init__(self, dev):
        self.dev = dev
        self.state = {}          # id -> Anzeigewert
        self.until = {}          # id -> Ende (Zeitstempel) von Funktionen mit Alarmpause
        self.curve_cache = {}    # Lichtkanal -> Kurve (für Rückgängig)
        self.last_refresh = 0.0
        self._denied = 0.0

    # ------------------------------------------------------------------ Hilfen
    @property
    def ghl(self):
        return self.dev.ghl

    def aktionen(self):
        return bool(self.dev.cfg.get("steuerung"))

    def schreiben(self):
        return bool(self.dev.cfg.get("einstellungen_schreiben"))

    def mitras(self):
        return self.dev.inv.model == "Mitras"

    def _num(self, name, default):
        v = self.dev.settings.store.data["geraete"].get(self.dev.uid, {}).get("_steuerung", {}).get(name)
        return default if v is None else v

    def _set_num(self, name, value):
        with self.dev.settings.store.lock:
            d = self.dev.settings.store.data["geraete"].setdefault(self.dev.uid, {})
            d.setdefault("_steuerung", {})[name] = value
        self.dev.settings.store.save()

    def _fehler(self, e):
        """Fehler beim Schreiben: -105 verständlich erklären (höchstens alle 6 Stunden melden)."""
        log.warning("%s: %s", self.dev.uid, e)
        if isinstance(e, NackError) and e.code == -105:
            if time.time() - self._denied > 6 * 3600:
                self._denied = time.time()
                self.dev.notify(_("Steuerung nicht möglich"),
                                _("Die GHL-API ist auf „Nur lesen“ gestellt. In GHL Connect unter System → GHL API „Voller Zugriff“ wählen, dann erneut versuchen."))
        else:
            self.dev.notify(_("Steuerung fehlgeschlagen"), str(e))

    # ------------------------------------------------------------------ Entitäten
    def controls(self):
        """Liste der Entitäten: dict(id, component, name, icon, config, enabled, level, extra)."""
        dev, out = self.dev, []

        def add(cid, comp, name, icon, level, config=False, enabled=True, **extra):
            out.append({"id": cid, "component": comp, "name": name, "icon": icon, "level": level,
                        "config": config, "enabled": enabled, "extra": extra})

        mitras = self.mitras()
        if self.aktionen():
            for func in SF:
                if mitras and func not in SF_MITRAS:
                    continue
                for n in range(1, SF[func][1] + 1):
                    name = {"wartung": _("Wartungsmodus {n}", n=n), "futterpause": _("Futterpause {n}", n=n),
                            "wasserwechsel": _("Wasserwechsel {n}", n=n)}[func]
                    add(f"{func}_{n}", "switch", name,
                        {"wartung": "mdi:wrench-clock", "futterpause": "mdi:fish",
                         "wasserwechsel": "mdi:water-sync"}[func], "aktion", enabled=n == 1)
            for n in range(1, SCENES + 1):
                add(f"lichtszene_{n}", "switch", _("Lichtszene {n}", n=n), "mdi:lightbulb-group", "aktion",
                    enabled=n == 1)
            add("lichtszene_fade", "number", _("Lichtszene – Überblendzeit"), "mdi:transition", "aktion",
                config=True, min=0, max=255, step=1, mode="box", unit_of_measurement="s")
            add("master", "number", _("Master-Helligkeit"), "mdi:brightness-percent", "aktion",
                min=0, max=100, step=1, mode="slider", unit_of_measurement="%")
            add("master_fade", "number", _("Master-Helligkeit – Überblendzeit"), "mdi:transition", "aktion",
                config=True, min=0, max=255, step=1, mode="box", unit_of_measurement="s")
            add("gewitter_dauer", "number", _("Gewitter – Dauer"), "mdi:weather-lightning", "aktion",
                config=True, min=1, max=60, step=1, mode="box", unit_of_measurement="min")
            add("gewitter_start", "button", _("Gewitter starten"), "mdi:weather-lightning", "aktion")
            add("gewitter_stop", "button", _("Gewitter beenden"), "mdi:weather-lightning-rainy", "aktion")
            if dev.inv.kh:
                add("kh_aktion", "select", _("KH-Director – Aktion"), "mdi:flask-outline", "aktion",
                    options=[NICHTS] + KH_AKTIONEN)
            for n in dev.inv.ions:
                add(f"ion_aktion_{n['i'] + 1}", "select", _("ION-Director {n} – Aktion", n=n["i"] + 1),
                    "mdi:flask-outline", "aktion", options=[NICHTS] + ION_AKTIONEN)
        if self.schreiben():
            for it in dev.items:
                if it["kind"] == "value" and it.get("main") and it.get("art") in LIMITS:
                    lo, hi, step, _m = LIMITS[it["art"]]
                    add(f"soll_sensor_{it['i']}", "number", _("{n} – Sollwert", n=it["name"]), "mdi:target",
                        "einstellung", config=True, min=lo, max=hi, step=step, mode="box")
            if dev.inv.kh:
                lo, hi, step, _m = LIMIT_KH
                add("soll_kh", "number", _("KH-Director – Sollwert"), "mdi:target", "einstellung", config=True,
                    min=lo, max=hi, step=step, mode="box", unit_of_measurement="dKH")
            for n in dev.inv.ions:
                add(f"soll_ion_{n['i'] + 1}", "number", _("ION-Director {n} – Sollwert", n=n["i"] + 1),
                    "mdi:target", "einstellung", config=True, min=0, max=100000, step=0.1, mode="box")
            for it in dev.items:
                if it["kind"] == "light":
                    add(f"kurve_{it['i']}", "text", _("{n} – Lichtkurve", n=it["name"]), "mdi:chart-bell-curve",
                        "einstellung", config=True, max=255, pattern=r"^[0-9:=,;.% \-]*$")
            if not mitras:
                for res, kinds in (("sensor", ("value",)), ("switchchannel", ("switch",)), ("illumination", ("light",)),
                                   ("doser", ("doser",)), ("flowsensor", ("value",)), ("levelsensor", ("level",))):
                    for it in dev.items:
                        if it["kind"] in kinds and "i" in it and self._res_of(it) == res:
                            add(f"name_{res}_{it['i']}", "text", _("{n} – Name im ProfiLux", n=it["name"]),
                                "mdi:rename", "einstellung", config=True, enabled=False, max=NAME_MAX)
                for t in getattr(dev.inv, "timers", []):
                    add(f"name_timer_{t['i']}", "text", _("Timer {n} – Name im ProfiLux", n=t["i"] + 1),
                        "mdi:rename", "einstellung", config=True, enabled=False, max=NAME_MAX)
        return out

    @staticmethod
    def _res_of(it):
        k = it["key"]
        return {"sensor": "sensor", "switch": "switchchannel", "light": "illumination", "doser": "doser",
                "flow": "flowsensor", "level": "levelsensor"}.get(k.split("_")[0])

    def initial_state(self, cid, comp):
        if comp == "switch":
            return "OFF"
        if comp == "select":
            return NICHTS
        if cid in ("lichtszene_fade", "master_fade"):
            return str(self._num(cid, 0))
        if cid == "gewitter_dauer":
            return str(self._num(cid, 10))
        return None

    # ------------------------------------------------------------------ Befehle
    def handle(self, cid, payload):
        """Eingehender Befehl aus HA. True = ausgeführt. Prüft die Freigabe der Gruppe."""
        known = {c["id"]: c for c in self.controls()}
        c = known.get(cid)
        if not c:
            log.info("%s: Steuerung %s nicht freigegeben/unbekannt – ignoriert", self.dev.uid, cid)
            return False
        try:
            ok = self._run(cid, str(payload).strip())
        except NackError as e:
            self._fehler(e)
            return False
        except ReadError as e:
            self._fehler(e)
            return False
        except ValueError as e:
            self.dev.notify(_("Steuerung fehlgeschlagen"), str(e))
            return False
        return ok

    def _run(self, cid, p):
        on = p.upper() in ("ON", "TRUE", "1")
        m = re.fullmatch(r"(wartung|futterpause|wasserwechsel)_(\d)", cid)
        if m:
            return self._sf(m[1], int(m[2]), on)
        m = re.fullmatch(r"lichtszene_(\d)", cid)
        if m:
            return self._scene(int(m[1]), on)
        if cid in ("lichtszene_fade", "master_fade", "gewitter_dauer"):
            lo, hi = (1, 60) if cid == "gewitter_dauer" else (0, 255)
            v = max(lo, min(hi, int(float(p))))
            self._set_num(cid, v)
            self.state[cid] = str(v)
            return True
        if cid == "master":
            v = max(0.0, min(100.0, float(p)))
            fade = int(self._num("master_fade", 0))
            self.ghl.set(f"SET ILLUMINATION MASTERBRIGHTNESS {v:g}" + (f", {fade}" if fade else ""))
            self.state[cid] = f"{v:g}"
            return True
        if cid == "gewitter_start":
            self.ghl.set(f"SET SPECIALFUNCTION THUNDERSTORM {int(self._num('gewitter_dauer', 10))}")
            self.dev.notify(_("Gewitter"), _("Gewitter gestartet."))
            return True
        if cid == "gewitter_stop":
            self.ghl.set("SET SPECIALFUNCTION THUNDERSTORM 0")
            return True
        if cid == "kh_aktion":
            return self._aktion("GET KHDIRECTOR", "KHDIRECTOR", KH_AKTIONEN, p, cid)
        m = re.fullmatch(r"ion_aktion_(\d)", cid)
        if m:
            return self._aktion(None, f"IONDIRECTOR[{int(m[1]) - 1}]", ION_AKTIONEN, p, cid)
        m = re.fullmatch(r"soll_sensor_(\d+)", cid)
        if m:
            art = next((it.get("art") for it in self.dev.items if it["key"] == f"sensor_{m[1]}"), None)
            return self._soll(f"SENSOR[{m[1]}]", float(p.replace(",", ".")), LIMITS[art], cid)
        if cid == "soll_kh":
            return self._soll("KHDIRECTOR", float(p.replace(",", ".")), LIMIT_KH, cid)
        m = re.fullmatch(r"soll_ion_(\d)", cid)
        if m:
            return self._soll(f"IONDIRECTOR[{int(m[1]) - 1}]", float(p.replace(",", ".")), (0.0, 100000.0, 0.1, None), cid)
        m = re.fullmatch(r"kurve_(\d+)", cid)
        if m:
            return self._kurve(int(m[1]), p, cid)
        m = re.fullmatch(r"name_(\w+?)_(\d+)", cid)
        if m:
            return self._name(m[1], int(m[2]), p, cid)
        return False

    # -- Sonderfunktionen mit Alarmpause
    def _sf(self, func, n, on):
        name = {"wartung": _("Wartungsmodus {n}", n=n), "futterpause": _("Futterpause {n}", n=n),
                "wasserwechsel": _("Wasserwechsel {n}", n=n)}[func]
        self.ghl.set(f"SET SPECIALFUNCTION {SF[func][0]}[{n - 1}] {1 if on else 0}")
        cid, app = f"{func}_{n}", self.dev.app
        if on:
            minutes = {"wartung": app.maintenance_minutes, "futterpause": app.futterpause_minutes,
                       "wasserwechsel": app.wasserwechsel_minutes}[func]()
            self.until[cid] = time.time() + minutes * 60
            self.state[cid] = "ON"
            app.start_pause(minutes)
            until = time.strftime("%H:%M", time.localtime(self.until[cid]))
            self.dev.notify(_("Wartung") if func == "wartung" else
                            (_("Futterpause") if func == "futterpause" else _("Wasserwechsel")),
                            _("{f} gestartet. Alarme pausiert bis {t} ({m} min). Danach wird wieder überwacht.",
                              f=name, t=until, m=minutes))
        else:
            self.until.pop(cid, None)
            self.state[cid] = "OFF"
            if not app.ctl_active():
                app._end_maintenance(early=True)
                app._publish_maintenance()
        log.info("%s: %s %s", self.dev.uid, name, "gestartet" if on else "beendet")
        return True

    def _scene(self, n, on):
        if on:
            fade = int(self._num("lichtszene_fade", 0))
            self.ghl.set(f"SET SPECIALFUNCTION LIGHTSCENE[{n - 1}] 1" + (f", {fade}" if fade else ""))
            for k in range(1, SCENES + 1):
                self.state[f"lichtszene_{k}"] = "ON" if k == n else "OFF"
        else:
            fade = int(self._num("lichtszene_fade", 0))
            self.ghl.set(f"SET SPECIALFUNCTION LIGHTSCENE[{n - 1}] 0" + (f", {fade}" if fade else ""))
            self.state[f"lichtszene_{n}"] = "OFF"
        return True

    # -- Messgeräte
    def _aktion(self, _unused, resource, aktionen, wahl, cid):
        if wahl == NICHTS or wahl not in aktionen:
            self.state[cid] = NICHTS
            return False
        self.ghl.set(f"SET {resource} STARTACTION {aktionen.index(wahl)}")
        self.state[cid] = NICHTS       # Aktion ausgelöst – die Auswahl springt zurück
        self.dev.notify(_("Messgerät"), _("Aktion „{a}“ gestartet. Läuft schon etwas, passiert nichts.", a=_(wahl)))
        return True

    # -- dauerhafte Einstellungen
    def _soll(self, resource, wert, limits, cid):
        lo, hi, _step, max_step = limits
        if not lo <= wert <= hi:
            raise ValueError(_("Sollwert außerhalb der erlaubten Grenzen ({lo} bis {hi}).", lo=f"{lo:g}", hi=f"{hi:g}"))
        alt = None
        try:
            alt = float(self.ghl.get(f"GET {resource} DESVALUE"))
        except (NackError, ValueError):
            pass
        if alt:
            erlaubt = max_step if max_step is not None else abs(alt) * PROZENT_SCHRITT
            if abs(wert - alt) > erlaubt + 1e-9:
                raise ValueError(_("Sollwert-Sprung zu groß: höchstens ±{d} je Änderung (jetzt {a}).",
                                   d=f"{erlaubt:g}", a=f"{alt:g}"))
        self.ghl.set(f"SET {resource} DESVALUE {wert:g}")
        self.ghl.cache.pop(f"GET {resource} DESVALUE", None)
        self.state[cid] = f"{wert:g}"
        log.info("%s: %s Sollwert %s -> %g", self.dev.uid, resource, f"{alt:g}" if alt is not None else "?", wert)
        return True

    def lese_kurve(self, i):
        n = int(float(self.ghl.get(f"GET ILLUMINATION[{i}] POINTCOUNT")))
        return [(int(float(self.ghl.get(f"GET ILLUMINATION[{i}] TIME[{p}]"))),
                 float(self.ghl.get(f"GET ILLUMINATION[{i}] BRIGHTNESS[{p}]"))) for p in range(min(n, KURVE_MAX))]

    def _kurve(self, i, text, cid):
        try:
            neu = kurve_pruefen(_kvp(text))
        except ValueError as e:
            raise ValueError(_("Lichtkurve ungültig („{t}“). Format: 06:30=0, 08:00=40 … höchstens 24 Punkte, Zeiten aufsteigend, 0–100 %.",
                               t=str(e)))
        alt = self.lese_kurve(i)
        try:
            for p, (t, pct) in enumerate(neu):
                self.ghl.set(f"SET ILLUMINATION[{i}] TIME[{p}] {t}")
                self.ghl.set(f"SET ILLUMINATION[{i}] BRIGHTNESS[{p}] {pct:g}")
            self.ghl.set(f"SET ILLUMINATION[{i}] POINTCOUNT {len(neu)}")
        except (NackError, ReadError):
            self._kurve_zurueck(i, alt)
            raise
        for k in [k for k in self.ghl.cache if f"ILLUMINATION[{i}]" in k]:
            self.ghl.cache.pop(k, None)
        gelesen = self.lese_kurve(i)
        if [(t, round(p)) for t, p in gelesen] != [(t, round(p)) for t, p in neu]:
            self._kurve_zurueck(i, alt)
            raise ValueError(_("Lichtkurve wurde nicht übernommen – alter Stand wiederhergestellt."))
        self.state[cid] = kurve_text(gelesen)
        log.info("%s: Lichtkurve %d geschrieben (%d Punkte)", self.dev.uid, i, len(neu))
        return True

    def _kurve_zurueck(self, i, alt):
        try:
            for p, (t, pct) in enumerate(alt):
                self.ghl.set(f"SET ILLUMINATION[{i}] TIME[{p}] {t}", retries=0)
                self.ghl.set(f"SET ILLUMINATION[{i}] BRIGHTNESS[{p}] {pct:g}", retries=0)
            self.ghl.set(f"SET ILLUMINATION[{i}] POINTCOUNT {len(alt)}", retries=0)
        except (NackError, ReadError) as e:
            log.error("%s: Lichtkurve %d konnte nicht wiederhergestellt werden: %s", self.dev.uid, i, e)

    def _name(self, res, i, text, cid):
        text = text.strip()
        if not re.fullmatch(r"[A-Za-z0-9 _.+\-/()#]{1,%d}" % NAME_MAX, text):
            raise ValueError(_("Name ungültig: höchstens {n} Zeichen, nur Buchstaben ohne Umlaute, Ziffern und einfache Satzzeichen.",
                               n=NAME_MAX))
        self.ghl.set(f'SET {res.upper()}[{i}] DESCRIPTION "{text}"')
        for k in [k for k in self.ghl.cache if f"{res.upper()}[{i}] DESCRIPTION" in k]:
            self.ghl.cache.pop(k, None)
        self.state[cid] = text
        self.dev.force_scan = True       # neuer Name -> Inventar neu einlesen
        self.dev.wake.set()
        return True

    # ------------------------------------------------------------------ Lesen / Anzeige
    def refresh(self, force=False):
        """Aktuelle Werte aus dem Gerät lesen (Master-Helligkeit, Sollwerte, Kurven, Namen)."""
        if not (self.aktionen() or self.schreiben()):
            return
        if not force and time.time() - self.last_refresh < REFRESH_S:
            return
        self.last_refresh = time.time()
        g = self.ghl

        def lese(cid, cmd, fmt=lambda v: f"{float(v):g}"):
            try:
                self.state[cid] = fmt(g.get(cmd))
            except (NackError, ValueError):
                pass

        for c in self.controls():
            cid = c["id"]
            if cid == "master":
                lese(cid, "GET ILLUMINATION MASTERBRIGHTNESS")
            elif cid.startswith("soll_sensor_"):
                lese(cid, f"GET SENSOR[{cid.split('_')[2]}] DESVALUE")
            elif cid == "soll_kh":
                lese(cid, "GET KHDIRECTOR DESVALUE")
            elif cid.startswith("soll_ion_"):
                lese(cid, f"GET IONDIRECTOR[{int(cid.split('_')[2]) - 1}] DESVALUE")
            elif cid.startswith("kurve_"):
                try:
                    k = self.lese_kurve(int(cid.split("_")[1]))
                    self.state[cid] = kurve_text(k)
                except (NackError, ValueError):
                    pass
            elif cid.startswith("name_"):
                _x, res, i = cid.split("_")
                lese(cid, f"GET {res.upper()}[{i}] DESCRIPTION", lambda v: v.strip())

    def expire(self):
        """Abgelaufene Anzeigen zurücksetzen (am Gerät wird nichts gesendet – es beendet die Funktion selbst)."""
        now = time.time()
        gone = [k for k, t in self.until.items() if t <= now]
        for k in gone:
            self.until.pop(k)
            self.state[k] = "OFF"
        return bool(gone)

    def clear_pauses(self):
        for k in list(self.until):
            self.until.pop(k)
            self.state[k] = "OFF"

    def active(self):
        now = time.time()
        return any(t > now for t in self.until.values())
