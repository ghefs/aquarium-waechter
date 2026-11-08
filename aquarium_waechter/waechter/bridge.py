"""Home-Assistant-Anbindung per MQTT Discovery.

Je ProfiLux ein HA-Gerät. Je Item:
  * Messwert-Entität(en)
  * binary_sensor "<Name> Störung"           (default_entity_id <uid>_<key>_stoerung)
  * Einstellungen (entity_category config): Abfragen, Überwachen, Min, Max,
    Verzögerung, Alarm (Niveau), Art (Sensor)
Nicht abgefragte Items haben nur den Schalter "Abfragen" – alles andere wird
aus HA entfernt (leere Discovery-Nachricht).
"""

import datetime
import logging

from .notify import phone_name
from .ghl import CATEGORIES, CATEGORY_NAMES, MAX_STRIPS, NO_STRIP, SENSOR_ARTS, STRIP_TYPES
from .mqtt import MqttError
from .settings import LEVEL_MODES, rule

log = logging.getLogger("waechter")


class Bridge:
    def __init__(self, mqtt, prefix="aquarium", discovery="homeassistant"):
        self.mqtt = mqtt
        self.prefix = prefix
        self.discovery = discovery
        self.avail = f"{prefix}/status"
        self.devices = {}      # uid -> DeviceWorker
        self.commands = {}     # Name -> Funktion (Knöpfe des Wächters)
        mqtt.on_message = self._on_message
        mqtt.on_connect = self._on_connect
        mqtt.subscribe(f"{prefix}/+/cfg/+/+/set")
        mqtt.subscribe(f"{prefix}/+/bereich/+/set")
        mqtt.subscribe(f"{prefix}/+/leiste/+/set")
        mqtt.subscribe(f"{prefix}/_waechter/+")
        mqtt.subscribe(f"{prefix}/_waechter/push/+/set")
        self.notifier = None   # für die Schalter „Push an …“
        self.on_push = None

    # ------------------------------------------------------------ eingehend
    def _on_connect(self):
        try:
            self.mqtt.publish(self.avail, "online", retain=True)
        except MqttError:
            return
        for dev in self.devices.values():
            dev.announced = False  # nach Broker-Neustart alles neu melden
            dev.wake.set()

    def _on_message(self, topic, payload):
        parts = topic.split("/")
        if len(parts) == 3 and parts[1] == "_waechter" and parts[2] in self.commands:
            self.commands[parts[2]]()
            return
        if len(parts) == 5 and parts[1] == "_waechter" and parts[2] == "push" and parts[4] == "set":
            if self.notifier and parts[3] in self.notifier.phones and self.on_push:
                self.on_push(parts[3], payload)
                self.publish_push()
            return
        if len(parts) == 5 and parts[2] == "leiste" and parts[4] == "set" and parts[3].isdigit():
            dev, n = self.devices.get(parts[1]), int(parts[3])
            if dev and 1 <= n <= MAX_STRIPS and (payload in STRIP_TYPES or payload == NO_STRIP):
                dev.settings.set_leiste(n, payload)
                self.publish_strips(dev)
                dev.leisten_changed()
            return
        if len(parts) == 5 and parts[2] == "bereich" and parts[4] == "set" and parts[3] in CATEGORIES:
            dev = self.devices.get(parts[1])
            if dev:
                value = dev.settings.set_bereich(parts[3], payload)
                self._pub(f"{self.prefix}/{dev.uid}/bereich/{parts[3]}", "ON" if value else "OFF", retain=True)
                dev.bereich_changed()
            return
        if len(parts) != 6 or parts[2] != "cfg" or parts[5] != "set":
            return
        dev = self.devices.get(parts[1])
        if not dev:
            return
        key, field = parts[3], parts[4]
        if dev.inv.scanned and key not in {it["key"] for it in dev.items}:
            log.info("Einstellung für unbekannten Punkt ignoriert: %s/%s", dev.uid, key)
            return
        value = dev.settings.set(key, field, payload)
        if value is None:
            return
        self.publish_field(dev, key, field, value)
        if field in ("aktiv", "art"):
            dev.items_changed()  # Entitäten neu melden, Dashboard neu

    # ------------------------------------------------------------ Helfer
    def _pub(self, topic, payload, retain=False):
        self.mqtt.publish(topic, payload, retain=retain)

    def _conf(self, component, uid, conf):
        self._pub(f"{self.discovery}/{component}/{uid}/config", conf, retain=True)

    def _remove(self, component, uid):
        self._pub(f"{self.discovery}/{component}/{uid}/config", "", retain=True)

    def publish_field(self, dev, key, field, value):
        if field in ("enabled", "aktiv"):
            value = "ON" if value else "OFF"
        self._pub(f"{self.prefix}/{dev.uid}/cfg/{key}/{field}", value, retain=True)

    def publish_settings(self, dev):
        for it in dev.items:
            for field, value in dev.settings.get(it["key"]).items():
                if field == "art" and value is None and it.get("main"):
                    value = it.get("art")  # nur geschätzt -> Schätzung anzeigen
                if value is not None:
                    self.publish_field(dev, it["key"], field, value)
        for cat in CATEGORIES:
            self._pub(f"{self.prefix}/{dev.uid}/bereich/{cat}", "ON" if dev.bereich(cat) else "OFF", retain=True)
        self.publish_strips(dev)

    def publish_strips(self, dev):
        chosen = dev.leisten()
        for n in range(1, MAX_STRIPS + 1):
            v = chosen[n - 1] if n <= len(chosen) else NO_STRIP
            self._pub(f"{self.prefix}/{dev.uid}/leiste/{n}", v, retain=True)

    # ------------------------------------------------------------ Discovery
    def announce_waechter(self):
        dev = {"identifiers": ["aquarium_waechter"], "name": "Aquarium-Wächter",
               "manufacturer": "aquarium-waechter", "model": "Add-on"}
        for cmd, name, icon, eid in (("dashboard", "Dashboard neu erzeugen", "mdi:view-dashboard-edit",
                                      "button.aquarium_waechter_dashboard_neu"),
                                     ("telegram_test", "Test-Alarm senden", "mdi:send",
                                      "button.aquarium_waechter_telegram_test"),
                                     ("rescan", "Geräte neu einlesen", "mdi:refresh",
                                      "button.aquarium_waechter_neu_einlesen")):
            if cmd in self.commands:
                self._conf("button", f"aquarium_waechter_{cmd}", {
                    "unique_id": f"aquarium_waechter_{cmd}", "name": name, "icon": icon,
                    "command_topic": f"{self.prefix}/_waechter/{cmd}", "default_entity_id": eid,
                    "availability_topic": self.avail, "device": dev})
        self._conf("binary_sensor", "aquarium_waechter_sammelstoerung", {
            "unique_id": "aquarium_waechter_sammelstoerung", "name": "Sammelstörung", "device_class": "problem",
            "state_topic": f"{self.prefix}/_waechter/sammel",
            "json_attributes_topic": f"{self.prefix}/_waechter/sammel/attr",
            "default_entity_id": "binary_sensor.aquarium_waechter_sammelstoerung",
            "availability_topic": self.avail, "device": dev})
        known = set(self.store_known("push"))
        if self.notifier:
            for phone in self.notifier.phones:
                self._conf("switch", f"aquarium_waechter_push_{phone}", {
                    "unique_id": f"aquarium_waechter_push_{phone}",
                    "name": f"Push an {phone_name(phone)}", "icon": "mdi:cellphone-message",
                    "state_topic": f"{self.prefix}/_waechter/push/{phone}",
                    "command_topic": f"{self.prefix}/_waechter/push/{phone}/set",
                    "default_entity_id": f"switch.aquarium_waechter_push_{phone}",
                    "availability_topic": self.avail, "device": dev})
                known.discard(phone)
            for gone in known:  # Handy aus HA entfernt -> Schalter weg
                self._remove("switch", f"aquarium_waechter_push_{gone}")
            self.store_known("push", self.notifier.phones)
            self.publish_push()
        self._pub(self.avail, "online", retain=True)

    def publish_push(self):
        for phone in self.notifier.phones:
            self._pub(f"{self.prefix}/_waechter/push/{phone}",
                      "ON" if self.notifier.push_on(phone) else "OFF", retain=True)

    def store_known(self, name, value=None):
        """Bekannte Handys merken (zum Aufräumen alter Schalter)."""
        st = self.notifier.store if self.notifier else None
        if not st:
            return []
        if value is None:
            return st.data.get("bekannt", {}).get(name, [])
        st.data.setdefault("bekannt", {})[name] = list(value)
        st.save()
        return value

    def summary(self, uid, faults):
        """Sammelstörung: an, sobald eine überwachte Störung gemeldet ist. Für eigene
        Automationen (Lampe, Sirene, Sprachausgabe …)."""
        base = f"{self.prefix}/{uid}/sammel"
        self._pub(base, "ON" if faults else "OFF", retain=True)
        self._pub(f"{base}/attr", {
            "anzahl": len(faults),
            "stoerungen": [f"{label}: {detail}" for label, detail in faults],
            "text": "\n".join(f"{label}: {detail}" for label, detail in faults) or "Keine Störung"},
            retain=True)

    def announce(self, dev):
        uid, base, inv = dev.uid, f"{self.prefix}/{dev.uid}", dev.inv
        device = {"identifiers": [f"ghl_{uid}"], "name": dev.name, "manufacturer": "GHL",
                  "model": "ProfiLux", "sw_version": inv.meta.get("firmware"),
                  "configuration_url": f"http://{dev.host}"}
        st = f"{base}/state"
        avail_live = {"availability": [{"topic": self.avail},
                                       {"topic": f"{base}/connected", "payload_available": "ON",
                                        "payload_not_available": "OFF"}],
                      "availability_mode": "all"}
        avail_cfg = {"availability_topic": self.avail}

        def tpl(k):
            return f"{{{{ value_json.{k} if value_json.{k} is defined else none }}}}"

        def ent(component, suffix, conf, eid=None, live=True):
            u = f"{uid}_{suffix}"
            c = {"unique_id": u, "device": device, **(avail_live if live else avail_cfg), **conf}
            if eid:
                c["default_entity_id"] = eid
            self._conf(component, u, c)

        def gone(component, suffix):
            self._remove(component, f"{uid}_{suffix}")

        # Teile, die es nicht mehr gibt (Bereich abgeschaltet, Steckplatz geleert) -> aus HA entfernen
        known = dev.settings.store.data.setdefault("entitaeten", {}).setdefault(uid, {})
        current = {it["key"] for it in dev.items}
        for key in [k for k in known if k not in current]:
            for comp, suffix in known.pop(key):
                gone(comp, suffix)
            log.info("%s: %s entfernt (nicht mehr vorhanden)", uid, key)
        for it in dev.items:
            k = it["key"]
            known[k] = ([("switch", f"cfg_{k}_aktiv"), ("binary_sensor", f"mon_{k}")]
                        + [(c, sfx) for c, sfx, _ in self._measurements(it, uid)]
                        + [(c, sfx) for c, sfx, _ in self._config_entities(it, uid)])
        dev.settings.store.save()

        for cat in CATEGORIES:
            ent("switch", f"bereich_{cat}", {
                "name": f"Bereich {CATEGORY_NAMES[cat]} auslesen", "payload_on": "ON", "payload_off": "OFF",
                "state_on": "ON", "state_off": "OFF", "icon": "mdi:format-list-checks", "entity_category": "config",
                "state_topic": f"{base}/bereich/{cat}", "command_topic": f"{base}/bereich/{cat}/set"},
                f"switch.{uid}_bereich_{cat}", live=False)

        gone("text", "steckdosenleisten")  # gab es kurz in 4.1.0
        # So viele Auswahlfelder wie gewählt + eins frei (höchstens MAX_STRIPS)
        shown = min(MAX_STRIPS, len(dev.leisten()) + 1)
        for n in range(1, MAX_STRIPS + 1):
            if n > shown:
                gone("select", f"leiste_{n}")
                continue
            ent("select", f"leiste_{n}", {
                "name": f"Leiste {n}", "icon": "mdi:power-socket-eu", "entity_category": "config",
                "options": [NO_STRIP] + list(STRIP_TYPES),
                "state_topic": f"{base}/leiste/{n}", "command_topic": f"{base}/leiste/{n}/set"},
                f"select.{uid}_leiste_{n}", live=False)

        ent("binary_sensor", "sammelstoerung", {
            "name": "Sammelstörung", "device_class": "problem", "state_topic": f"{base}/sammel",
            "json_attributes_topic": f"{base}/sammel/attr"},
            f"binary_sensor.{uid}_sammelstoerung", live=False)

        ent("binary_sensor", "ghl_connected", {
            "name": "Verbindung", "device_class": "connectivity", "entity_category": "diagnostic",
            "state_topic": f"{base}/connected"}, f"binary_sensor.{uid}_verbindung", live=False)

        for it in dev.items:
            key, kind, name = it["key"], it["kind"], it["label"]
            s = dev.settings.get(key)
            cfg_t = lambda f: {"state_topic": f"{base}/cfg/{key}/{f}",
                               "command_topic": f"{base}/cfg/{key}/{f}/set", "entity_category": "config"}
            # Schalter "Abfragen" gibt es immer
            ent("switch", f"cfg_{key}_aktiv", {
                "name": f"{name} – auslesen", "payload_on": "ON", "payload_off": "OFF",
                "state_on": "ON", "state_off": "OFF", "icon": "mdi:eye", **cfg_t("aktiv")},
                f"switch.{uid}_{key}_abfragen", live=False)

            measure = self._measurements(it, uid)
            cfgs = self._config_entities(it, uid)
            if not s["aktiv"]:
                for comp, suffix, _ in measure:
                    gone(comp, suffix)
                gone("binary_sensor", f"mon_{key}")
                for comp, suffix, _ in cfgs:
                    gone(comp, suffix)
                continue
            for comp, suffix, conf in measure:
                conf = {**conf, "state_topic": st}
                ent(comp, suffix, conf, f"{comp}.{uid}_{suffix}")
            if kind == "light":
                gone("binary_sensor", f"mon_{key}")
                continue
            ent("binary_sensor", f"mon_{key}", {
                "name": f"{name} – Störung", "device_class": "problem",
                "state_topic": f"{base}/mon/{key}", "json_attributes_topic": f"{base}/mon/{key}/attr"},
                f"binary_sensor.{uid}_{key}_stoerung")
            if kind in ("age", "doser"):
                gone("number", f"cfg_{key}_delay")  # gab es bis 4.0.x
            if kind == "switch" and not it.get("current"):
                for suffix in ("enabled", "min", "max", "delay"):
                    gone("switch" if suffix == "enabled" else "number", f"cfg_{key}_{suffix}")
                gone("sensor", f"{key}_current")
            for comp, suffix, conf in cfgs:
                field = suffix.rsplit("_", 1)[1]
                ent(comp, suffix, {**conf, **cfg_t(field)},
                    f"{comp}.{uid}_{key}_{self._eid_suffix(field)}", live=False)
        self.publish_settings(dev)

    @staticmethod
    def _eid_suffix(field):
        return {"enabled": "ueberwachen", "min": "min", "max": "max", "delay": "verzoegerung",
                "mode": "alarm", "art": "art"}[field]

    @staticmethod
    def _measurements(it, uid):
        """-> [(component, suffix, conf)] ohne state_topic."""
        key, kind, name = it["key"], it["kind"], it["label"]

        def tpl(k):
            return f"{{{{ value_json.{k} if value_json.{k} is defined else none }}}}"

        if kind == "switch":
            out = [("binary_sensor", f"{key}_state", {
                "name": name, "device_class": "power", "value_template": tpl(f"{key}_state"),
                "payload_on": "ON", "payload_off": "OFF"})]
            if it["current"]:
                out.append(("sensor", f"{key}_current", {
                    "name": f"{name} – Strom", "device_class": "current", "unit_of_measurement": "A",
                    "state_class": "measurement", "suggested_display_precision": 3,
                    "value_template": tpl(f"{key}_current")}))
            return out
        if kind == "value":
            base = {"state_class": "measurement", "suggested_display_precision": 2}
            if it.get("unit"):
                base["unit_of_measurement"] = it["unit"]
            if it.get("device_class"):
                base["device_class"] = it["device_class"]
            if not it.get("unit") and not it.get("device_class") and key.startswith("sensor_"):
                base["suggested_display_precision"] = 3
            out = [("sensor", key, {"name": name, "value_template": tpl(key), **base})]
            if it.get("main") or key == "kh":
                out.append(("sensor", f"{key}_soll", {
                    "name": f"{name} – Sollwert", "entity_category": "diagnostic",
                    "value_template": tpl(f"{key}_soll"),
                    **{k: v for k, v in base.items() if k != "state_class"}}))
            return out
        if kind == "level":
            return [("sensor", key, {"name": name, "value_template": tpl(key), "icon": "mdi:waves-arrow-up"})]
        if kind == "doser":
            out = [("sensor", key, {
                "name": f"{name} – Füllstand", "device_class": "volume_storage", "unit_of_measurement": "mL",
                "state_class": "measurement", "suggested_display_precision": 0, "icon": "mdi:cup-water",
                "value_template": tpl(key)})]
            if it.get("capacity"):
                out.append(("sensor", f"{key}_pct", {
                    "name": f"{name} – Füllstand %", "unit_of_measurement": "%", "state_class": "measurement",
                    "suggested_display_precision": 0, "icon": "mdi:cup-water",
                    "value_template": tpl(f"{key}_pct")}))
            return out
        if kind == "light":
            return [("sensor", key, {"name": name, "unit_of_measurement": "%", "state_class": "measurement",
                                     "suggested_display_precision": 0, "icon": "mdi:lightbulb-on-outline",
                                     "value_template": tpl(key)})]
        if kind == "age":
            return [("sensor", "kh_last", {"name": name, "device_class": "timestamp",
                                           "value_template": tpl("kh_last_iso")})]
        return []

    # Wortlaut der Einstellungen – so steht es in HA und im Dashboard
    LABELS = {
        "enabled": "Alarm per Telegram",
        ("switch", "min"): "Alarm, wenn AN und Strom unter",
        ("switch", "max"): "Alarm, wenn Strom über (0 = aus)",
        ("value", "min"): "Alarm, wenn Wert unter",
        ("value", "max"): "Alarm, wenn Wert über",
        ("doser", "min"): "Nachfüllen melden unter",
        ("age", "max"): "Alarm, wenn keine Messung seit",
        "delay": "Alarm erst nach (Dauer)",
        "mode": "Alarm, wenn Sensor meldet",
        "art": "Sensor-Art (für Einheit)",
    }

    @classmethod
    def label_of(cls, kind, field):
        return cls.LABELS.get((kind, field)) or cls.LABELS.get(field)

    @classmethod
    def _config_entities(cls, it, uid):
        key, kind, name, unit = it["key"], it["kind"], it["label"], it.get("unit") or ""
        if kind == "light" or (kind == "switch" and not it.get("current")):
            return []

        def num(lo, hi, step, u, field):
            return ("number", f"cfg_{key}_{field}", {
                "name": f"{name} – {cls.label_of(kind, field)}", "min": lo, "max": hi, "step": step,
                "mode": "box", **({"unit_of_measurement": u} if u else {})})

        out = []
        if kind == "level":
            out.append(("select", f"cfg_{key}_mode", {"name": f"{name} – {cls.label_of(kind, 'mode')}",
                                                      "options": LEVEL_MODES}))
        else:
            out.append(("switch", f"cfg_{key}_enabled", {
                "name": f"{name} – {cls.label_of(kind, 'enabled')}", "payload_on": "ON", "payload_off": "OFF",
                "state_on": "ON", "state_off": "OFF", "icon": "mdi:bell-ring-outline"}))
            lo, hi, step = {"switch": (0, 20, 0.01), "doser": (0, 100000, 10),
                            "age": (0.5, 168, 0.5)}.get(kind, (-1000, 10000, 0.001))
            if kind in ("switch", "value", "doser"):
                out.append(num(lo, hi, step, unit, "min"))
            if kind in ("switch", "value", "age"):
                out.append(num(lo, hi, step, unit, "max"))
        if kind not in ("age", "doser"):
            out.append(num(0, 1440, 1, "min", "delay"))
        if kind == "value" and it.get("main"):
            out.append(("select", f"cfg_{key}_art", {"name": f"{name} – {cls.label_of(kind, 'art')}",
                                                     "options": list(SENSOR_ARTS),
                                                     "icon": "mdi:format-list-bulleted-type"}))
        return out

    # ------------------------------------------------------------ Zustände
    def values(self, dev, values):
        v = dict(values)
        if "kh_last" in v:
            v["kh_last_iso"] = datetime.datetime.fromtimestamp(v["kh_last"], datetime.timezone.utc).isoformat()
        self._pub(f"{self.prefix}/{dev.uid}/state", v)

    def connected(self, dev, ok):
        self._pub(f"{self.prefix}/{dev.uid}/connected", "ON" if ok else "OFF", retain=True)

    def monitor(self, dev, it, fault, watched, detail):
        base = f"{self.prefix}/{dev.uid}/mon/{it['key']}"
        self._pub(base, "ON" if fault else "OFF", retain=True)
        self._pub(f"{base}/attr", {"detail": detail, "ueberwacht": watched,
                                   "regel": rule(it, dev.settings.get(it["key"])),
                                   "bezeichnung": it["label"]}, retain=True)
