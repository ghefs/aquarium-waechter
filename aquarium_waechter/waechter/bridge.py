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

from .ghl import SENSOR_ARTS
from .mqtt import MqttError
from .settings import LEVEL_MODES

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
        mqtt.subscribe(f"{prefix}/_waechter/+")

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

    # ------------------------------------------------------------ Discovery
    def announce_waechter(self):
        dev = {"identifiers": ["aquarium_waechter"], "name": "Aquarium-Wächter",
               "manufacturer": "aquarium-waechter", "model": "Add-on"}
        for cmd, name, icon, eid in (("dashboard", "Dashboard neu erzeugen", "mdi:view-dashboard-edit",
                                      "button.aquarium_waechter_dashboard_neu"),
                                     ("telegram_test", "Telegram-Test senden", "mdi:send",
                                      "button.aquarium_waechter_telegram_test"),
                                     ("rescan", "Geräte neu einlesen", "mdi:refresh",
                                      "button.aquarium_waechter_neu_einlesen")):
            if cmd in self.commands:
                self._conf("button", f"aquarium_waechter_{cmd}", {
                    "unique_id": f"aquarium_waechter_{cmd}", "name": name, "icon": icon,
                    "command_topic": f"{self.prefix}/_waechter/{cmd}", "default_entity_id": eid,
                    "availability_topic": self.avail, "device": dev})
        self._pub(self.avail, "online", retain=True)

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

        ent("binary_sensor", "ghl_connected", {
            "name": "Verbindung", "device_class": "connectivity", "entity_category": "diagnostic",
            "state_topic": f"{base}/connected"}, f"binary_sensor.{uid}_verbindung", live=False)

        for it in dev.items:
            key, kind, name = it["key"], it["kind"], it["name"]
            s = dev.settings.get(key)
            cfg_t = lambda f: {"state_topic": f"{base}/cfg/{key}/{f}",
                               "command_topic": f"{base}/cfg/{key}/{f}/set", "entity_category": "config"}
            # Schalter "Abfragen" gibt es immer
            ent("switch", f"cfg_{key}_aktiv", {
                "name": f"{name} abfragen", "payload_on": "ON", "payload_off": "OFF",
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
            ent("binary_sensor", f"mon_{key}", {
                "name": f"{name} Störung", "device_class": "problem",
                "state_topic": f"{base}/mon/{key}", "json_attributes_topic": f"{base}/mon/{key}/attr"},
                f"binary_sensor.{uid}_{key}_stoerung")
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
        key, kind, name = it["key"], it["kind"], it["name"]

        def tpl(k):
            return f"{{{{ value_json.{k} if value_json.{k} is defined else none }}}}"

        if kind == "switch":
            out = [("binary_sensor", f"{key}_state", {
                "name": name, "device_class": "power", "value_template": tpl(f"{key}_state"),
                "payload_on": "ON", "payload_off": "OFF"})]
            if it["current"]:
                out.append(("sensor", f"{key}_current", {
                    "name": f"{name} Strom", "device_class": "current", "unit_of_measurement": "A",
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
                    "name": f"{name} Sollwert", "entity_category": "diagnostic",
                    "value_template": tpl(f"{key}_soll"),
                    **{k: v for k, v in base.items() if k != "state_class"}}))
            return out
        if kind == "level":
            return [("sensor", key, {"name": name, "value_template": tpl(key), "icon": "mdi:waves-arrow-up"})]
        if kind == "doser":
            return [("sensor", key, {
                "name": f"{name} Füllstand", "device_class": "volume_storage", "unit_of_measurement": "mL",
                "state_class": "measurement", "suggested_display_precision": 0, "icon": "mdi:cup-water",
                "value_template": tpl(key)})]
        if kind == "age":
            return [("sensor", "kh_last", {"name": "KH letzte Messung", "device_class": "timestamp",
                                           "value_template": tpl("kh_last_iso")})]
        return []

    @staticmethod
    def _config_entities(it, uid):
        key, kind, name, unit = it["key"], it["kind"], it["name"], it.get("unit") or ""

        def num(label, lo, hi, step, u, field):
            return ("number", f"cfg_{key}_{field}", {
                "name": f"{name} {label}", "min": lo, "max": hi, "step": step, "mode": "box",
                **({"unit_of_measurement": u} if u else {})})

        out = []
        if kind == "level":
            out.append(("select", f"cfg_{key}_mode", {"name": f"{name} Alarm", "options": LEVEL_MODES}))
        else:
            if kind != "switch" or it.get("current"):
                out.append(("switch", f"cfg_{key}_enabled", {
                    "name": f"{name} überwachen", "payload_on": "ON", "payload_off": "OFF",
                    "state_on": "ON", "state_off": "OFF"}))
            lo, hi, step = {"switch": (0, 20, 0.01), "doser": (0, 100000, 10),
                            "age": (0.5, 168, 0.5)}.get(kind, (-1000, 10000, 0.001))
            if kind in ("switch", "value", "doser") and (kind != "switch" or it.get("current")):
                out.append(num("Warnen unter" if kind == "doser" else "Minimum", lo, hi, step, unit, "min"))
            if kind in ("switch", "value", "age") and (kind != "switch" or it.get("current")):
                label = {"switch": "Maximum (0 = aus)", "age": "Alarm wenn älter als"}.get(kind, "Maximum")
                out.append(num(label, lo, hi, step, unit, "max"))
        if kind != "switch" or it.get("current"):
            out.append(num("Verzögerung", 0, 1440, 1, "min", "delay"))
        if kind == "value" and it.get("main"):
            out.append(("select", f"cfg_{key}_art", {"name": f"{name} Art", "options": list(SENSOR_ARTS),
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

    def monitor(self, dev, key, fault, watched, detail):
        base = f"{self.prefix}/{dev.uid}/mon/{key}"
        self._pub(base, "ON" if fault else "OFF", retain=True)
        self._pub(f"{base}/attr", {"detail": detail, "ueberwacht": watched}, retain=True)
