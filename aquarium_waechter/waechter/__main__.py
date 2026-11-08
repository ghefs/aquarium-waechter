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
from .ghl import GhlClient, Inventory, ReadError, strips_from_choice
from .haws import HaWs
from .mqtt import Mqtt, MqttError
from .notify import Notifier
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
        self._sig = None
        self.force_scan = False

    # vom Bridge aufgerufen
    def items_changed(self):
        self.items = self.inv.items(self.settings)
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
        self.items = self.inv.items(self.settings)
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
        changed = []
        for it in self.items:
            old = names.get(it["key"])
            if old is not None and old != it["name"]:
                st = self.settings.get(it["key"])
                info = ""
                if st.get("enabled"):
                    lim = [f"{l} {fmt(st[f])}" for l, f in (("Min", "min"), ("Max", "max")) if st.get(f)]
                    info = f" – Überwachung bleibt an ({', '.join(lim) or 'Standardwerte'}), bitte prüfen"
                prefix = it["label"].split(" · ")[0]
                changed.append(f"• {prefix}: {old} → {it['name']}{info}")
                self.faults.pop(it["key"], None)
            names[it["key"]] = it["name"]
        self.app.store.save()
        if changed:
            self.notify("Belegung geändert", "\n".join(changed))

    def run(self):
        opts = self.app.opts
        interval = int(opts.get("intervall", 60))
        rescan = int(opts.get("rescan", 3600))
        last_scan = 0
        while True:
            start = time.time()
            self.ghl.new_round()
            try:
                if not self.inv.scanned or self.force_scan or time.time() - last_scan > rescan:
                    self.force_scan = False
                    self.scan()
                    last_scan = time.time()
                if not self.announced:
                    self.app.bridge.announce(self)
                    self.announced = True
                values = self.inv.read(self.items, self.settings)
                if self.ghl.down:
                    raise ReadError(self.ghl.down)
                self.reachable(True)
                self.last_values = values
                self.app.bridge.values(self, values)
                self.check(values)
            except ReadError as e:
                log.info("%s: nicht erreichbar: %s", self.name, e)
                self.reachable(False)
            except MqttError as e:
                log.info("%s: MQTT: %s", self.name, e)
                self.announced = False
            except Exception:
                log.exception("%s: unerwarteter Fehler", self.name)
            self.wake.wait(max(1, interval - (time.time() - start)))
            self.wake.clear()

    def publish_summary(self):
        """Sammelstörung dieses Geräts (und über alle Geräte) nach HA melden."""
        faults = list(self.active)
        if self.unreachable_alerted:
            faults.insert(0, ("ProfiLux nicht erreichbar", f"keine Antwort von {self.host}"))
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
                self.notify("Wieder erreichbar", f"{self.host} antwortet wieder.", kind="ok")
            self.unreachable_since, self.unreachable_alerted = None, False
            if was:
                self.publish_summary()
            return
        now = time.time()
        self.unreachable_since = self.unreachable_since or now
        if (not self.unreachable_alerted and not self.app.maintenance()
                and now - self.unreachable_since >= int(opts.get("nicht_erreichbar_nach", 600))):
            self.unreachable_alerted = True
            self.notify("ProfiLux nicht erreichbar",
                        f"Seit {int((now - self.unreachable_since) / 60)} min keine Antwort von {self.host}.",
                        urgent=True)
            self.publish_summary()

    def check(self, values):
        self._check(values)
        by_key = {it["key"]: it for it in self.items}
        self.active = [(by_key[k]["label"], f.get("detail", "")) for k, f in self.faults.items()
                       if f.get("alerted") and k in by_key]
        self.publish_summary()

    def _check(self, values):
        now = time.time()
        reminder = int(self.app.opts.get("erinnerung", 3600))
        for it in self.items:
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
                    title = "Nachfüllen" if it["kind"] == "doser" else "STÖRUNG"
                    self.notify(f"{title}: {it['label']}", detail, urgent=it["kind"] != "doser")
                elif f["alerted"] and every and now - f["last"] >= every:
                    f["last"] = now
                    mins = int((now - f["since"]) / 60)
                    self.notify(f"Noch immer: {it['label']}", f"{detail} (seit {mins} min)",
                                urgent=it["kind"] != "doser")
            elif f and ok is not None:
                if f["alerted"]:
                    self.notify(f"Wieder OK: {it['label']}", detail, kind="ok")
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
        self.bridge.commands_set = {"wartung": self._set_maintenance,
                                    "wartung_dauer": self._set_maintenance_minutes}
        self.dashboard_dirty = threading.Event()
        self.dashboard = Dashboard(HaWs(), self.data_dir / "dashboard.json") if opts.get("dashboard", True) else None
        self.bridge.commands["dashboard"] = lambda: self._dashboard(force=True)
        self.bridge.commands["rescan"] = self._rescan
        self.bridge.commands["telegram_test"] = lambda: self.notifier.send(
            "Test", "Der Aquarium-Wächter kann dich erreichen. So sieht ein Alarm aus.", urgent=True)
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
                faults.append((f"{w.name} · ProfiLux nicht erreichbar", f"keine Antwort von {w.host}"))
            faults += [(f"{w.name} · {label}", detail) for label, detail in w.active]
        self.bridge.summary("_waechter", faults)

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
            self.notifier.send("Wartung", f"Alarme pausiert bis {time.strftime('%H:%M', time.localtime(until))} "
                               f"({self.maintenance_minutes()} min). Danach wird wieder überwacht.")
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
        self.notifier.send("Wartung beendet", "Überwachung wieder aktiv." +
                           (" Noch anstehende Störungen werden jetzt gemeldet." if not early else ""), kind="ok")
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
        for w in self.workers:
            w.start()
        if self.opts.get("start_meldung", False):
            self.notifier.send("Wächter gestartet", ", ".join(f"{w.name} ({w.host})" for w in self.workers))
        while True:
            self.mqtt.loop(5)
            if self.store.data.get("wartung_bis") and not self.maintenance():
                self._end_maintenance()
                self._publish_maintenance()
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
