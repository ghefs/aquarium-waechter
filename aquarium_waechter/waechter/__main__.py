"""Aquarium-Wächter: überwacht einen oder mehrere GHL ProfiLux 4 und meldet
Störungen per Telegram; Werte und Einstellungen per MQTT Discovery in HA.

Aufruf im Add-on:  python3 -m waechter /data/options.json
"""

import argparse
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from .bridge import Bridge
from .dashboard import Dashboard
from .extern import HaPlugs
from .forecast import Forecast
from .ghl import GhlClient, Inventory, ReadError, strips_from_choice
from .haws import HaWs
from .mqtt import Mqtt, MqttError
from . import i18n
from .i18n import _
from .notify import Notifier, ha_language
from . import report
from .settings import REMINDER, SettingsStore, evaluate, fmt

log = logging.getLogger("waechter")


def slug(name):
    s = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_") or "profilux"


class DeviceWorker(threading.Thread):
    def __init__(self, app, dev_cfg, uid, first):
        super().__init__(daemon=True, name=uid)
        self.app = app
        self.uid = uid
        self.name = dev_cfg["name"]
        self.host = dev_cfg["host"]
        self.cfg = dev_cfg
        self.ghl = GhlClient(self.host, int(dev_cfg.get("port", 10002)))
        self.settings = app.store.device(uid, first=first)
        self.inv = Inventory(self.ghl, dev_cfg, enabled=self.bereich, keep=self.kept)
        self.apply_leisten()
        app.store.unnamed_default[uid] = bool(dev_cfg.get("unbenannte_abfragen", False))
        self.items = []
        self.announced = False
        self.wake = threading.Event()
        self.faults = {}  # key -> {"since", "alerted", "last_alert"}
        self.unreachable_since = None
        self.unreachable_alerted = False
        self.active = []  # gemeldete Störungen [(Bezeichnung, Detail)] -> Sammelstörung
        self.last_values = {}
        self.dose_rate = {}  # key -> (ml/Tag, Zeitpunkt der Berechnung) aus dem Dosierprotokoll
        self._sig = None
        self.force_scan = False

    # vom Bridge aufgerufen
    def all_items(self):
        """Teile des ProfiLux + zugeordnete Steckdosen aus Home Assistant."""
        return self.inv.items(self.settings) + self.app.plugs.items_for(self.uid)

    def items_changed(self):
        self.items = self.all_items()
        self.announced = False
        self.app.dashboard_dirty.set()
        self.wake.set()

    def bereich(self, cat):
        """Bereich (Steckdosen, Licht …) lesen? Schalter in HA, Standard aus der Konfiguration."""
        return self.settings.bereich(cat, bool(self.cfg.get(cat, True)))

    def kept(self, key):
        """In HA ausdrücklich auf „Auslesen“ gestellt?"""
        return self.app.store.data["geraete"].get(self.uid, {}).get(key, {}).get("aktiv") is True

    def leisten(self):
        """Steckdosenleisten: Auswahl in HA, sonst Vorgabe (Text) aus der Add-on-Konfiguration."""
        return self.settings.leisten() or []

    def apply_leisten(self):
        chosen = self.leisten()
        if chosen:
            self.inv.strips = strips_from_choice(chosen)
        else:
            self.inv.set_strips(self.cfg.get("steckdosenleisten") or "")

    def leisten_changed(self):
        self.apply_leisten()
        self._sig = None
        self.items_changed()

    def bereich_changed(self):
        self.force_scan = True
        self.wake.set()

    def bridge_cfg(self, it):
        return Bridge._config_entities(it, self.uid)

    def notify(self, title, msg, urgent=False, kind=None):
        self.app.notifier.send(f"{self.name} · {title}", msg, urgent, kind)

    def scan(self):
        self.inv.scan()
        self.settings.named = {}
        self.items = self.all_items()
        for it in self.items:
            self.settings.named[it["key"]] = it["named"]
        self.check_renames()
        sig = self.inv.signature()
        if sig != self._sig:
            self._sig = sig
            self.announced = False
            self.app.dashboard_dirty.set()

    def check_renames(self):
        """Ein Steckplatz (Steckdose/Dosierer/Sensor) bekommt in GHL einen neuen
        Namen -> meist neu belegt. Entität und Verlauf bleiben (Schlüssel = Index),
        Einstellungen bleiben ebenfalls – aber wir melden es, damit Grenzwerte
        geprüft werden. Eine laufende Störung wird zurückgesetzt."""
        names = self.app.store.data.setdefault("namen", {}).setdefault(self.uid, {})
        # Sprache gewechselt -> Standardnamen („Steckdose 3“ / „Socket 3“) ändern sich, das ist keine Umbelegung
        same_lang = names.get("_sprache", "de") == i18n.LANG
        names["_sprache"] = i18n.LANG
        changed = []
        for it in self.items:
            old = names.get(it["key"])
            if old is not None and old != it["name"] and same_lang and it["kind"] != "plug":
                st = self.settings.get(it["key"])
                info = ""
                if st.get("enabled"):
                    lim = [f"{l} {fmt(st[f])}" for l, f in (("Min", "min"), ("Max", "max")) if st.get(f)]
                    info = _(" – Überwachung bleibt an ({lim}), bitte prüfen", lim=", ".join(lim) or _("Standardwerte"))
                prefix = it["label"].split(" · ")[0]
                changed.append(f"• {prefix}: {old} → {it['name']}{info}")
                self.faults.pop(it["key"], None)
            names[it["key"]] = it["name"]
        self.app.store.save()
        if changed:
            self.notify(_("Belegung geändert"), "\n".join(changed))

    def run(self):
        opts = self.app.opts
        interval = int(opts.get("intervall", 60))
        rescan = int(opts.get("rescan", 3600))
        last_scan = 0
        while True:
            start = time.time()
            plugs = {}
            self.ghl.new_round()
            try:
                if not self.inv.scanned or self.force_scan or time.time() - last_scan > rescan:
                    self.force_scan = False
                    self.scan()
                    last_scan = time.time()
                if not self.announced:
                    self.app.bridge.announce(self)
                    self.announced = True
                plugs = self.app.plugs.read(self.items)
                values = self.inv.read(self.items, self.settings)
                if self.ghl.down:
                    raise ReadError(self.ghl.down)
                self.reachable(True)
                values.update(plugs)
                self.add_forecast(values)
                self.add_power(values)
                self.last_values = values
                self.app.bridge.values(self, values)
                self.check(values)
            except ReadError as e:
                log.info("%s: nicht erreichbar: %s", self.name, e)
                self.reachable(False)
                try:
                    self.check(plugs, only="plug")  # Steckdosen aus HA weiter überwachen
                except Exception:
                    log.exception("%s: Fehler bei den HA-Steckdosen", self.name)
            except MqttError as e:
                log.info("%s: MQTT: %s", self.name, e)
                self.announced = False
            except Exception:
                log.exception("%s: unerwarteter Fehler", self.name)
            self.wake.wait(max(1, interval - (time.time() - start)))
            self.wake.clear()

    def has_power(self):
        """Gibt es etwas, dessen Leistung wir kennen (Steckdosen mit Strommessung, HA-Steckdosen)?"""
        return any((it["kind"] == "switch" and it.get("current")) or it["kind"] == "plug" for it in self.items)

    def add_power(self, values):
        """Gesamtleistung ≈ Summe der Ströme × Netzspannung + Leistung der HA-Steckdosen; Energie aufsummieren."""
        if not self.has_power():
            return
        volts = self.app.voltage()
        watts = 0.0
        for it in self.items:
            if not self.settings.get(it["key"])["aktiv"]:
                continue
            if it["kind"] == "switch" and it.get("current") and values.get(f"{it['key']}_state") == "ON":
                watts += (values.get(f"{it['key']}_current") or 0.0) * volts
            elif it["kind"] == "plug" and values.get(f"{it['key']}_state") == "ON":
                watts += values.get(f"{it['key']}_power") or 0.0
        values["gesamt_w"] = round(watts, 1)
        values["gesamt_kwh"] = self.app.forecast.energy(self.uid, watts, int(self.app.opts.get("intervall", 60)))

    def add_forecast(self, values):
        """Verbrauch je Tag und „reicht noch … Tage“ je Dosierbehälter.
        Bevorzugt aus dem Dosierprotokoll (ab Firmware 7.52), sonst aus dem Füllstandsverlauf."""
        now = time.time()
        budget = 1  # Protokoll höchstens eines Dosierers je Runde lesen (viele Abfragen)
        for it in self.items:
            key = it["key"]
            if it["kind"] != "doser" or key not in values or not self.settings.get(key)["aktiv"]:
                continue
            ml = values[key]
            rate = None
            if it.get("records"):
                r, t = self.dose_rate.get(key, (None, 0))
                if now - t > 1800 and budget:
                    budget -= 1
                    try:
                        res = self.inv.dosed_since(it, now - 7 * 86400)
                    except ReadError:
                        res = None
                    r = None
                    if res and now - res[1] >= 86400:
                        r = round(res[0] / ((now - res[1]) / 86400), 1)
                    self.dose_rate[key] = (r, now)
                rate = r
            if it.get("capacity"):
                fl_rate, _x = self.app.forecast.add(self.uid, key, ml, it["capacity"], now)
                rate = rate if rate is not None else fl_rate
            if rate is None:
                continue
            values[f"{key}_verbrauch"] = rate
            if it.get("capacity") and rate >= 0.5:
                values[f"{key}_tage"] = round(ml / rate, 1)

    def publish_summary(self):
        """Sammelstörung dieses Geräts (und über alle Geräte) nach HA melden."""
        faults = list(self.active)
        if self.unreachable_alerted:
            faults.insert(0, (_("ProfiLux nicht erreichbar"), _("keine Antwort von {h}", h=self.host)))
        try:
            self.app.bridge.summary(self.uid, faults)
            self.app.publish_summary()
        except MqttError:
            pass

    def reachable(self, ok):
        opts = self.app.opts
        try:
            self.app.bridge.connected(self, ok)
        except MqttError:
            pass
        if ok:
            was = self.unreachable_alerted
            if was:
                self.notify(_("Wieder erreichbar"), _("{h} antwortet wieder.", h=self.host), kind="ok")
            self.unreachable_since, self.unreachable_alerted = None, False
            if was:
                self.publish_summary()
            return
        now = time.time()
        self.unreachable_since = self.unreachable_since or now
        if (not self.unreachable_alerted and not self.app.maintenance()
                and now - self.unreachable_since >= int(opts.get("nicht_erreichbar_nach", 600))):
            self.unreachable_alerted = True
            self.app.log_event(self.name, _("ProfiLux nicht erreichbar"), "STÖRUNG")
            self.notify(_("ProfiLux nicht erreichbar"),
                        _("Seit {m} min keine Antwort von {h}.", m=int((now - self.unreachable_since) / 60),
                          h=self.host),
                        urgent=True)
            self.publish_summary()

    def check(self, values, only=None):
        self._check(values, only)
        by_key = {it["key"]: it for it in self.items}
        self.active = [(by_key[k]["label"], f.get("detail", "")) for k, f in self.faults.items()
                       if f.get("alerted") and k in by_key]
        self.publish_summary()

    def _check(self, values, only=None):
        now = time.time()
        reminder = int(self.app.opts.get("erinnerung", 3600))
        for it in self.items:
            if only and it["kind"] != only:
                continue
            key = it["key"]
            st = self.settings.get(key)
            watched, ok, detail = evaluate(it, st, values)
            if not st["aktiv"] or it["kind"] == "light":
                self.faults.pop(key, None)
                continue
            fault = watched and ok is False
            f = self.faults.get(key)
            if fault:
                if not f:
                    f = self.faults[key] = {"since": now, "alerted": False, "last": 0}
                f["detail"] = detail
                delay = float(st.get("delay", 0)) * 60
                every = REMINDER.get(it["kind"], reminder)
                if self.app.maintenance():
                    pass  # Wartung: nichts melden; steht die Störung danach noch an, kommt der Alarm dann
                elif not f["alerted"] and now - f["since"] >= delay:
                    f["alerted"], f["last"] = True, now
                    title = _("Nachfüllen") if it["kind"] == "doser" else _("STÖRUNG")
                    self.notify(f"{title}: {it['label']}", detail, urgent=it["kind"] != "doser")
                    self.app.log_event(self.name, it["label"], title)
                elif f["alerted"] and every and now - f["last"] >= every:
                    f["last"] = now
                    mins = int((now - f["since"]) / 60)
                    self.notify(_("Noch immer: {l}", l=it["label"]), _("{d} (seit {m} min)", d=detail, m=mins),
                                urgent=it["kind"] != "doser")
            elif f and ok is not None:
                if f["alerted"]:
                    self.notify(_("Wieder OK: {l}", l=it["label"]), detail, kind="ok")
                self.faults.pop(key, None)
            pending = bool(f) and not f["alerted"] if fault else False
            try:
                self.app.bridge.monitor(self, it, fault and not pending, watched, detail)
            except MqttError:
                pass


class App:
    def __init__(self, opts, data_dir):
        self.opts = opts
        self.data_dir = Path(data_dir)
        self.store = SettingsStore(self.data_dir / "einstellungen.json")
        # Sprache: Option „sprache“, sonst wie Home Assistant (nicht unterstützt -> Englisch).
        # HA beim Start nicht erreichbar -> zuletzt erkannte Sprache (Namen sollen nicht umspringen).
        choice = opts.get("sprache") or "auto"
        if choice == "auto":
            choice = ha_language(tries=24 if os.environ.get("SUPERVISOR_TOKEN") else 1) \
                or self.store.data.get("sprache") or "en"
        self.store.data["sprache"] = i18n.set_lang(choice)
        self.store.save()
        log.info("Sprache: %s", i18n.LANG)
        self.forecast = Forecast(self.data_dir / "verbrauch.json")
        self.plugs = HaPlugs(self.store)
        self.notifier = Notifier(opts.get("telegram_chat_id"), opts.get("telegram_token") or None,
                                 opts.get("app_push"), self.store)
        # Externer Broker (Optionen mqtt_*) hat Vorrang vor dem Mosquitto-Add-on
        m = {k: opts.get(f"mqtt_{k}") or (opts.get("mqtt") or {}).get(k) for k in ("host", "port", "user", "password")}
        self.mqtt = Mqtt(m["host"] or os.environ.get("MQTT_HOST", "core-mosquitto"),
                         int(m["port"] or os.environ.get("MQTT_PORT", 1883)),
                         (m["user"] if m["host"] else None) or os.environ.get("MQTT_USER"),
                         (m["password"] if m["host"] else None) or os.environ.get("MQTT_PASSWORD"),
                         will=("aquarium/status", "offline"))
        self.bridge = Bridge(self.mqtt)
        self.bridge.notifier = self.notifier
        self.bridge.on_push = self._push_changed
        self.bridge.plugs = self.plugs
        self.bridge.on_plug = self._plug_changed
        self.bridge.report_choice = self.report_choice
        self.bridge.voltage = self.voltage
        self.bridge.commands_set = {"spannung": self._set_voltage,
                                    "wartung": self._set_maintenance,
                                    "wartung_dauer": self._set_maintenance_minutes,
                                    "wochenbericht": self._set_report}
        self.dashboard_dirty = threading.Event()
        self.dashboard = Dashboard(HaWs(), self.data_dir / "dashboard.json") if opts.get("dashboard", True) else None
        self.bridge.commands["dashboard"] = lambda: self._dashboard(force=True)
        self.bridge.commands["rescan"] = self._rescan
        self.bridge.commands["bericht"] = lambda: self._send_report(manual=True)
        self.bridge.commands["telegram_test"] = lambda: self.notifier.send(
            _("Test"), _("Der Aquarium-Wächter kann dich erreichen. So sieht ein Alarm aus."), urgent=True)
        self.workers = []
        seen = set()
        for i, d in enumerate(opts.get("geraete") or []):
            uid = d.get("id") or slug(d["name"])
            while uid in seen:
                uid += "_2"
            seen.add(uid)
            self.workers.append(DeviceWorker(self, d, uid, first=(i == 0)))
        for w in self.workers:
            self.bridge.devices[w.uid] = w

    def publish_summary(self):
        """Sammelstörung über alle Geräte."""
        faults = []
        for w in self.workers:
            if w.unreachable_alerted:
                faults.append((f"{w.name} · {_('ProfiLux nicht erreichbar')}", _("keine Antwort von {h}", h=w.host)))
            faults += [(f"{w.name} · {label}", detail) for label, detail in w.active]
        self.bridge.summary("_waechter", faults)

    # ---------------------------------------------------------------- Ereignisse / Wochenbericht
    def log_event(self, device, label, kind):
        with self.store.lock:
            ev = self.store.data.setdefault("ereignisse", [])
            ev.append([time.time(), device, label, kind])
            cutoff = time.time() - 8 * 86400
            self.store.data["ereignisse"] = [e for e in ev if e[0] >= cutoff]
        self.store.save()

    def events(self):
        return list(self.store.data.get("ereignisse", []))

    def voltage(self):
        return float(self.store.data.get("spannung") or 230)

    def _set_voltage(self, raw):
        try:
            self.store.data["spannung"] = max(90.0, min(260.0, float(raw)))
            self.store.save()
        except ValueError:
            pass
        try:
            self.bridge.publish_voltage(self.voltage())
        except MqttError:
            pass

    def report_choice(self):
        v = self.store.data.get("wochenbericht")
        return v if v in report.OPTIONS else "Sonntag"

    def _set_report(self, raw):
        if raw in report.OPTIONS:
            self.store.data["wochenbericht"] = raw
            self.store.save()
            log.info("Wochenbericht: %s", raw)
        try:
            self.bridge.publish_report(self.report_choice())
        except MqttError:
            pass

    def _send_report(self, manual=False):
        def run():
            try:
                self.notifier.send(_("Wochenbericht"), report.build(self))
            except Exception:
                log.exception("Wochenbericht nicht erstellt")
        if not manual:
            self.store.data["bericht_zuletzt"] = time.strftime("%Y-%m-%d")
            self.store.save()
        threading.Thread(target=run, daemon=True, name="bericht").start()

    # ---------------------------------------------------------------- HA-Steckdosen
    def refresh_plugs(self):
        if self.plugs.refresh():
            try:
                self.bridge.announce_waechter()
            except MqttError:
                pass
            for w in self.workers:
                if w.inv.scanned:
                    w.items_changed()
            self.dashboard_dirty.set()

    def _plug_changed(self, slug, raw):
        names = {w.name: w.uid for w in self.workers}
        if raw == "ON":
            uid = self.workers[0].uid if self.workers else None
        else:
            uid = names.get(raw)
        res = self.plugs.assign(slug, uid)
        if res is None:
            return
        for w in self.workers:
            if w.uid in res:
                w.items_changed()

    # ---------------------------------------------------------------- Wartung
    def maintenance(self):
        return time.time() < float(self.store.data.get("wartung_bis") or 0)

    def maintenance_minutes(self):
        return int(self.store.data.get("wartung_dauer") or 30)

    def _set_maintenance(self, raw):
        on = str(raw).strip().upper() in ("ON", "TRUE", "1")
        if on:
            until = time.time() + self.maintenance_minutes() * 60
            self.store.data["wartung_bis"] = until
            self.store.save()
            self.notifier.send(_("Wartung"), _("Alarme pausiert bis {t} ({m} min). Danach wird wieder überwacht.",
                                               t=time.strftime("%H:%M", time.localtime(until)),
                                               m=self.maintenance_minutes()))
        else:
            self._end_maintenance(early=True)
        self._publish_maintenance()

    def _set_maintenance_minutes(self, raw):
        try:
            m = max(5, min(480, int(float(raw))))
        except ValueError:
            return
        self.store.data["wartung_dauer"] = m
        self.store.save()
        self._publish_maintenance()

    def _end_maintenance(self, early=False):
        if not self.store.data.get("wartung_bis"):
            return
        self.store.data["wartung_bis"] = 0
        self.store.save()
        self.notifier.send(_("Wartung beendet"), _("Überwachung wieder aktiv.") +
                           (" " + _("Noch anstehende Störungen werden jetzt gemeldet.") if not early else ""),
                           kind="ok")
        for w in self.workers:
            w.wake.set()

    def _publish_maintenance(self):
        until = float(self.store.data.get("wartung_bis") or 0)
        try:
            self.bridge.publish_maintenance(self.maintenance(), until, self.maintenance_minutes())
        except MqttError:
            pass

    def _push_changed(self, phone, raw):
        on = self.store.set_push(phone, raw)
        log.info("Push an %s: %s", phone, "an" if on else "aus")

    def _rescan(self):
        threading.Thread(target=self.refresh_plugs, daemon=True).start()
        before = list(self.notifier.phones)
        if self.notifier.refresh_phones() != before:
            try:
                self.bridge.announce_waechter()
            except MqttError:
                pass
            self.dashboard_dirty.set()
        for w in self.workers:
            w.force_scan = True
            w.wake.set()

    def _dashboard(self, force=False):
        if self.dashboard:
            threading.Thread(target=self.dashboard.update, args=(self.workers, force), daemon=True).start()

    def run(self):
        if not self.workers:
            log.error("Keine Geräte eingetragen (Add-on-Konfiguration 'geraete').")
        try:
            self.mqtt.connect()
        except OSError as e:
            log.warning("MQTT nicht erreichbar (%s) – versuche weiter", e)
        try:
            self.bridge.announce_waechter()
        except MqttError:
            pass
        self._publish_maintenance()
        self.refresh_plugs()
        try:
            self.bridge.publish_report(self.report_choice())
            self.bridge.publish_voltage(self.voltage())
        except MqttError:
            pass
        for w in self.workers:
            w.start()
        if self.opts.get("start_meldung", False):
            self.notifier.send(_("Add-on gestartet"), ", ".join(f"{w.name} ({w.host})" for w in self.workers))
        while True:
            self.mqtt.loop(5)
            if self.store.data.get("wartung_bis") and not self.maintenance():
                self._end_maintenance()
                self._publish_maintenance()
            if report.due(self.report_choice(), self.store.data.get("bericht_zuletzt")):
                self._send_report()
            if self.dashboard_dirty.is_set() and all(w.inv.scanned for w in self.workers):
                self.dashboard_dirty.clear()
                self._dashboard()


def main():
    p = argparse.ArgumentParser(prog="waechter")
    p.add_argument("options", nargs="?", default="/data/options.json")
    p.add_argument("--data", default=None, help="Ordner für Einstellungen (Standard: neben options)")
    p.add_argument("--debug", action="store_true")
    a = p.parse_args()
    opts = json.loads(Path(a.options).read_text(encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if a.debug or opts.get("debug") else logging.INFO,
                        format="%(asctime)s %(levelname)s [%(threadName)s] %(message)s", datefmt="%H:%M:%S")
    App(opts, a.data or Path(a.options).parent).run()


if __name__ == "__main__":
    main()
