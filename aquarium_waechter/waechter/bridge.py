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
from .extern import OFF as PLUG_OFF
from .report import OPTIONS as REPORT_OPTIONS
from .i18n import _, back
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
        mqtt.subscribe(f"{prefix}/_waechter/plug/+/set")
        mqtt.subscribe(f"{prefix}/_waechter/+/set")
        self.commands_set = {}  # Name -> Funktion(payload) (Schalter/Zahlen des Wächters)
        self.notifier = None   # für die Schalter „Push an …“
        self.on_push = None
        self.plugs = None      # Steckdosen aus HA (extern.HaPlugs)
        self.on_plug = None
        self.report_choice = lambda: "Sonntag"
        self.voltage = lambda: 230.0

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
        if parts[-1] == "set":
            payload = back(payload)  # angezeigte Auswahl (übersetzt) -> deutscher Schlüssel
        if len(parts) == 3 and parts[1] == "_waechter" and parts[2] in self.commands:
            self.commands[parts[2]]()
            return
        if len(parts) == 4 and parts[1] == "_waechter" and parts[3] == "set" and parts[2] in self.commands_set:
            self.commands_set[parts[2]](payload)
            return
        if len(parts) == 5 and parts[1] == "_waechter" and parts[2] == "push" and parts[4] == "set":
            if self.notifier and parts[3] in self.notifier.phones and self.on_push:
                self.on_push(parts[3], payload)
                self.publish_push()
            return
        if len(parts) == 5 and parts[1] == "_waechter" and parts[2] == "plug" and parts[4] == "set":
            if self.on_plug:
                self.on_plug(parts[3], payload)
                self.publish_plugs()
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
        elif field in ("mode", "art"):
            value = _(value)
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
            self._pub(f"{self.prefix}/{dev.uid}/leiste/{n}", _(v), retain=True)

    # ------------------------------------------------------------ Discovery
    def announce_waechter(self):
        dev = {"identifiers": ["aquarium_waechter"], "name": "Aquarium-Wächter",
               "manufacturer": "aquarium-waechter", "model": "Add-on"}
        for cmd, name, icon, eid in (("dashboard", _("Dashboard neu erzeugen"), "mdi:view-dashboard-edit",
                                      "button.aquarium_waechter_dashboard_neu"),
                                     ("telegram_test", _("Test-Alarm senden"), "mdi:send",
                                      "button.aquarium_waechter_telegram_test"),
                                     ("rescan", _("Geräte neu einlesen"), "mdi:refresh",
                                      "button.aquarium_waechter_neu_einlesen"),
                                     ("bericht", _("Wochenbericht jetzt senden"), "mdi:file-chart-outline",
                                      "button.aquarium_waechter_bericht")):
            if cmd in self.commands:
                self._conf("button", f"aquarium_waechter_{cmd}", {
                    "unique_id": f"aquarium_waechter_{cmd}", "name": name, "icon": icon,
                    "command_topic": f"{self.prefix}/_waechter/{cmd}", "default_entity_id": eid,
                    "availability_topic": self.avail, "device": dev})
        self._conf("binary_sensor", "aquarium_waechter_sammelstoerung", {
            "unique_id": "aquarium_waechter_sammelstoerung", "name": _("Sammelstörung"), "device_class": "problem",
            "state_topic": f"{self.prefix}/_waechter/sammel",
            "json_attributes_topic": f"{self.prefix}/_waechter/sammel/attr",
            "default_entity_id": "binary_sensor.aquarium_waechter_sammelstoerung",
            "availability_topic": self.avail, "device": dev})
        self._conf("switch", "aquarium_waechter_wartung", {
            "unique_id": "aquarium_waechter_wartung", "name": _("Wartung – Alarme pausieren"),
            "icon": "mdi:wrench-clock",
            "state_topic": f"{self.prefix}/_waechter/wartung",
            "command_topic": f"{self.prefix}/_waechter/wartung/set",
            "json_attributes_topic": f"{self.prefix}/_waechter/wartung/attr",
            "default_entity_id": "switch.aquarium_waechter_wartung",
            "availability_topic": self.avail, "device": dev})
        self._conf("number", "aquarium_waechter_wartung_dauer", {
            "unique_id": "aquarium_waechter_wartung_dauer", "name": _("Wartung – Dauer"),
            "icon": "mdi:timer-outline", "min": 5, "max": 480, "step": 5, "mode": "box",
            "unit_of_measurement": "min", "entity_category": "config",
            "state_topic": f"{self.prefix}/_waechter/wartung_dauer",
            "command_topic": f"{self.prefix}/_waechter/wartung_dauer/set",
            "default_entity_id": "number.aquarium_waechter_wartung_dauer",
            "availability_topic": self.avail, "device": dev})
        self._conf("select", "aquarium_waechter_wochenbericht", {
            "unique_id": "aquarium_waechter_wochenbericht", "name": _("Wochenbericht"),
            "icon": "mdi:calendar-week", "options": [_(o) for o in REPORT_OPTIONS], "entity_category": "config",
            "state_topic": f"{self.prefix}/_waechter/wochenbericht",
            "command_topic": f"{self.prefix}/_waechter/wochenbericht/set",
            "default_entity_id": "select.aquarium_waechter_wochenbericht",
            "availability_topic": self.avail, "device": dev})
        self._conf("number", "aquarium_waechter_spannung", {
            "unique_id": "aquarium_waechter_spannung", "name": _("Netzspannung (für Gesamtleistung)"),
            "icon": "mdi:sine-wave", "min": 90, "max": 260, "step": 1, "mode": "box",
            "unit_of_measurement": "V", "entity_category": "config",
            "state_topic": f"{self.prefix}/_waechter/spannung",
            "command_topic": f"{self.prefix}/_waechter/spannung/set",
            "default_entity_id": "number.aquarium_waechter_spannung",
            "availability_topic": self.avail, "device": dev})
        self.announce_plugs(dev)
        known = set(self.store_known("push"))
        if self.notifier:
            for phone in self.notifier.phones:
                self._conf("switch", f"aquarium_waechter_push_{phone}", {
                    "unique_id": f"aquarium_waechter_push_{phone}",
                    "name": _("Push an {p}", p=phone_name(phone)), "icon": "mdi:cellphone-message",
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

    def announce_plugs(self, dev):
        """Je Steckdose aus HA: übernehmen (ein ProfiLux: Schalter, mehrere: Auswahl)."""
        if not self.plugs:
            return
        names = [d.name for d in self.devices.values()]
        known = set(self.store_known("plugs"))
        for p in self.plugs.plugs:
            u = f"aquarium_waechter_plug_{p['slug']}"
            conf = {"unique_id": u, "name": _("{n} überwachen", n=p["name"]), "icon": "mdi:power-plug-outline",
                    "entity_category": "config",
                    "state_topic": f"{self.prefix}/_waechter/plug/{p['slug']}",
                    "command_topic": f"{self.prefix}/_waechter/plug/{p['slug']}/set",
                    "availability_topic": self.avail, "device": dev}
            if len(names) > 1:
                self._remove("switch", u)
                self._conf("select", u, {**conf, "options": [_(PLUG_OFF)] + names,
                                         "default_entity_id": f"select.{u}"})
            else:
                self._remove("select", u)
                self._conf("switch", u, {**conf, "default_entity_id": f"switch.{u}"})
            known.discard(p["slug"])
        for gone in known:  # Steckdose aus HA verschwunden
            self._remove("switch", f"aquarium_waechter_plug_{gone}")
            self._remove("select", f"aquarium_waechter_plug_{gone}")
        self.store_known("plugs", [p["slug"] for p in self.plugs.plugs])
        self.publish_plugs()

    def publish_plugs(self):
        if not self.plugs:
            return
        names = {d.uid: d.name for d in self.devices.values()}
        for p in self.plugs.plugs:
            owner = self.plugs.owner(p)
            if len(names) > 1:
                v = names.get(owner, _(PLUG_OFF))
            else:
                v = "ON" if owner in names else "OFF"
            self._pub(f"{self.prefix}/_waechter/plug/{p['slug']}", v, retain=True)

    def publish_voltage(self, volts):
        self._pub(f"{self.prefix}/_waechter/spannung", f"{volts:.0f}", retain=True)

    def publish_report(self, choice):
        self._pub(f"{self.prefix}/_waechter/wochenbericht", _(choice), retain=True)

    def publish_maintenance(self, on, until, minutes):
        self._pub(f"{self.prefix}/_waechter/wartung", "ON" if on else "OFF", retain=True)
        self._pub(f"{self.prefix}/_waechter/wartung_dauer", str(minutes), retain=True)
        bis = datetime.datetime.fromtimestamp(until).astimezone() if on else None
        self._pub(f"{self.prefix}/_waechter/wartung/attr", {
            "bis": bis.strftime("%H:%M") if bis else None,
            "bis_iso": bis.isoformat() if bis else None}, retain=True)

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
            "text": "\n".join(f"{label}: {detail}" for label, detail in faults) or _("Keine Störung")},
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
                        + [(c, sfx) for c, sfx, _x in self._measurements(it, uid)]
                        + [(c, sfx) for c, sfx, _x in self._config_entities(it, uid)])
        dev.settings.store.save()

        for cat in CATEGORIES:
            ent("switch", f"bereich_{cat}", {
                "name": _("Bereich {b} auslesen", b=_(CATEGORY_NAMES[cat])), "payload_on": "ON", "payload_off": "OFF",
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
                "name": _("Leiste {n}", n=n), "icon": "mdi:power-socket-eu", "entity_category": "config",
                "options": [_(o) for o in [NO_STRIP] + list(STRIP_TYPES)],
                "state_topic": f"{base}/leiste/{n}", "command_topic": f"{base}/leiste/{n}/set"},
                f"select.{uid}_leiste_{n}", live=False)

        ent("binary_sensor", "sammelstoerung", {
            "name": _("Sammelstörung"), "device_class": "problem", "state_topic": f"{base}/sammel",
            "json_attributes_topic": f"{base}/sammel/attr"},
            f"binary_sensor.{uid}_sammelstoerung", live=False)

        if dev.has_power():
            ent("sensor", "gesamt_leistung", {
                "name": _("Gesamtleistung (ca.)"), "device_class": "power", "unit_of_measurement": "W",
                "state_class": "measurement", "suggested_display_precision": 0, "state_topic": st,
                "value_template": tpl("gesamt_w")}, f"sensor.{uid}_gesamt_leistung")
            ent("sensor", "gesamt_energie", {
                "name": _("Energie (ca.)"), "device_class": "energy", "unit_of_measurement": "kWh",
                "state_class": "total_increasing", "suggested_display_precision": 2, "state_topic": st,
                "value_template": tpl("gesamt_kwh")}, f"sensor.{uid}_gesamt_energie")
        else:
            gone("sensor", "gesamt_leistung")
            gone("sensor", "gesamt_energie")

        ent("binary_sensor", "ghl_connected", {
            "name": _("Verbindung"), "device_class": "connectivity", "entity_category": "diagnostic",
            "state_topic": f"{base}/connected"}, f"binary_sensor.{uid}_verbindung", live=False)

        for it in dev.items:
            key, kind, name = it["key"], it["kind"], it["label"]
            s = dev.settings.get(key)
            cfg_t = lambda f: {"state_topic": f"{base}/cfg/{key}/{f}",
                               "command_topic": f"{base}/cfg/{key}/{f}/set", "entity_category": "config"}
            # Schalter "Abfragen" gibt es immer
            ent("switch", f"cfg_{key}_aktiv", {
                "name": f"{name} – {_('auslesen')}", "payload_on": "ON", "payload_off": "OFF",
                "state_on": "ON", "state_off": "OFF", "icon": "mdi:eye", **cfg_t("aktiv")},
                f"switch.{uid}_{key}_abfragen", live=False)

            measure = self._measurements(it, uid)
            cfgs = self._config_entities(it, uid)
            if not s["aktiv"]:
                for comp, suffix, _x in measure:
                    gone(comp, suffix)
                gone("binary_sensor", f"mon_{key}")
                for comp, suffix, _x in cfgs:
                    gone(comp, suffix)
                continue
            for comp, suffix, conf in measure:
                conf = {**conf, "state_topic": st}
                ent(comp, suffix, conf, f"{comp}.{uid}_{suffix}")
            if kind == "light":
                gone("binary_sensor", f"mon_{key}")
                continue
            ent("binary_sensor", f"mon_{key}", {
                "name": f"{name} – {_('Störung')}", "device_class": "problem",
                "state_topic": f"{base}/mon/{key}", "json_attributes_topic": f"{base}/mon/{key}/attr"},
                f"binary_sensor.{uid}_{key}_stoerung")
            if kind in ("age", "doser"):
                gone("number", f"cfg_{key}_delay")  # gab es bis 4.0.x
            if kind == "switch" and not it.get("current"):
                for suffix in ("enabled", "min", "max", "delay"):
                    gone("switch" if suffix == "enabled" else "number", f"cfg_{key}_{suffix}")
                gone("sensor", f"{key}_current")
            if kind == "doser" and not it.get("capacity"):
                for comp, suffix in (("switch", "enabled"), ("number", "min"), ("number", "tage")):
                    gone(comp, f"cfg_{key}_{suffix}")
            for comp, suffix, conf in cfgs:
                field = suffix.rsplit("_", 1)[1]
                ent(comp, suffix, {**conf, **cfg_t(field)},
                    f"{comp}.{uid}_{key}_{self._eid_suffix(field)}", live=False)
        self.publish_settings(dev)

    @staticmethod
    def _eid_suffix(field):
        return {"enabled": "ueberwachen", "min": "min", "max": "max", "delay": "verzoegerung",
                "mode": "alarm", "art": "art", "tage": "tage"}[field]

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
                    "name": f"{name} – {_('Strom')}", "device_class": "current", "unit_of_measurement": "A",
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
                    "name": f"{name} – {_('Sollwert')}", "entity_category": "diagnostic",
                    "value_template": tpl(f"{key}_soll"),
                    **{k: v for k, v in base.items() if k != "state_class"}}))
            return out
        if kind == "level":
            return [("sensor", key, {"name": name, "value_template": tpl(key), "icon": "mdi:waves-arrow-up"})]
        if kind == "plug":
            return []  # Zustand und Leistung kommen direkt von der HA-Steckdose
        if kind == "doser":
            out = [("sensor", key, {
                "name": f"{name} – {_('Füllstand')}", "device_class": "volume_storage", "unit_of_measurement": "mL",
                "state_class": "measurement", "suggested_display_precision": 0, "icon": "mdi:cup-water",
                "value_template": tpl(key)})]
            if it.get("capacity"):
                out.append(("sensor", f"{key}_pct", {
                    "name": f"{name} – {_('Füllstand')} %", "unit_of_measurement": "%", "state_class": "measurement",
                    "suggested_display_precision": 0, "icon": "mdi:cup-water",
                    "value_template": tpl(f"{key}_pct")}))
            if it.get("capacity") or it.get("records"):
                out.append(("sensor", f"{key}_verbrauch", {
                    "name": f"{name} – {_('Verbrauch')}", "unit_of_measurement": "mL/d", "state_class": "measurement",
                    "suggested_display_precision": 1, "icon": "mdi:chart-line-variant",
                    "value_template": tpl(f"{key}_verbrauch")}))
            if it.get("capacity"):
                out.append(("sensor", f"{key}_tage", {
                    "name": f"{name} – {_('reicht noch')}", "unit_of_measurement": "d", "device_class": "duration",
                    "state_class": "measurement", "suggested_display_precision": 0,
                    "icon": "mdi:calendar-clock", "value_template": tpl(f"{key}_tage")}))
            if it.get("records"):
                out.append(("sensor", f"{key}_letzte", {
                    "name": f"{name} – {_('letzte Dosierung')}", "device_class": "timestamp",
                    "value_template": tpl(f"{key}_last_iso")}))
                out.append(("sensor", f"{key}_dosiert", {
                    "name": f"{name} – {_('zuletzt dosiert')}", "device_class": "volume", "unit_of_measurement": "mL",
                    "suggested_display_precision": 1, "icon": "mdi:water-plus",
                    "value_template": tpl(f"{key}_dosiert")}))
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
        "enabled": "Alarm melden",
        ("switch", "min"): "Alarm, wenn AN und Strom unter",
        ("switch", "max"): "Alarm, wenn Strom über (0 = aus)",
        ("value", "min"): "Alarm, wenn Wert unter",
        ("value", "max"): "Alarm, wenn Wert über",
        ("doser", "min"): "Nachfüllen melden unter",
        ("doser", "tage"): "Nachfüllen melden, wenn Vorrat unter",
        ("plug", "min"): "Alarm, wenn AN und Leistung unter",
        ("plug", "max"): "Alarm, wenn Leistung über (0 = aus)",
        ("age", "max"): "Alarm, wenn keine Messung seit",
        "delay": "Alarm erst nach (Dauer)",
        "mode": "Alarm, wenn Sensor meldet",
        "art": "Sensor-Art (für Einheit)",
    }

    @classmethod
    def label_of(cls, kind, field):
        return _(cls.LABELS.get((kind, field)) or cls.LABELS.get(field))

    @classmethod
    def _config_entities(cls, it, uid):
        key, kind, name, unit = it["key"], it["kind"], it["label"], it.get("unit") or ""
        if kind == "light" or (kind == "switch" and not it.get("current")) \
                or (kind == "doser" and not it.get("capacity")):
            return []

        def num(lo, hi, step, u, field):
            return ("number", f"cfg_{key}_{field}", {
                "name": f"{name} – {cls.label_of(kind, field)}", "min": lo, "max": hi, "step": step,
                "mode": "box", **({"unit_of_measurement": u} if u else {})})

        out = []
        if kind == "level":
            out.append(("select", f"cfg_{key}_mode", {"name": f"{name} – {cls.label_of(kind, 'mode')}",
                                                      "options": [_(o) for o in LEVEL_MODES]}))
        else:
            out.append(("switch", f"cfg_{key}_enabled", {
                "name": f"{name} – {cls.label_of(kind, 'enabled')}", "payload_on": "ON", "payload_off": "OFF",
                "state_on": "ON", "state_off": "OFF", "icon": "mdi:bell-ring-outline"}))
            lo, hi, step = {"switch": (0, 20, 0.01), "doser": (0, 100000, 10), "plug": (0, 5000, 0.5),
                            "age": (0.5, 168, 0.5)}.get(kind, (-1000, 10000, 0.001))
            if kind in ("switch", "value", "doser", "plug"):
                out.append(num(lo, hi, step, unit, "min"))
            if kind == "doser":
                out.append(num(0, 60, 1, "d", "tage"))
            if kind in ("switch", "value", "age", "plug"):
                out.append(num(lo, hi, step, unit, "max"))
        if kind not in ("age", "doser"):
            out.append(num(0, 1440, 1, "min", "delay"))
        if kind == "value" and it.get("main"):
            out.append(("select", f"cfg_{key}_art", {"name": f"{name} – {cls.label_of(kind, 'art')}",
                                                     "options": [_(o) for o in SENSOR_ARTS],
                                                     "icon": "mdi:format-list-bulleted-type"}))
        return out

    # ------------------------------------------------------------ Zustände
    def values(self, dev, values):
        v = dict(values)
        for k in [k for k in v if k.endswith("_last")]:
            v[f"{k}_iso"] = datetime.datetime.fromtimestamp(v[k], datetime.timezone.utc).isoformat()
        self._pub(f"{self.prefix}/{dev.uid}/state", v)

    def connected(self, dev, ok):
        self._pub(f"{self.prefix}/{dev.uid}/connected", "ON" if ok else "OFF", retain=True)

    def monitor(self, dev, it, fault, watched, detail):
        base = f"{self.prefix}/{dev.uid}/mon/{it['key']}"
        self._pub(base, "ON" if fault else "OFF", retain=True)
        self._pub(f"{base}/attr", {"detail": detail, "ueberwacht": watched,
                                   "regel": rule(it, dev.settings.get(it["key"])),
                                   "bezeichnung": it["label"]}, retain=True)
