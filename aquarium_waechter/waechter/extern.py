"""Steckdosen aus Home Assistant (Shelly Plug S, andere WLAN-/Zigbee-Steckdosen mit
Leistungsmessung) in die Überwachung übernehmen.

Gefunden wird jedes HA-Gerät, das einen Schalter (switch.*) UND einen
Leistungssensor (sensor.*, device_class power) hat. Auf der Einstellungsseite
wird je Steckdose gewählt, zu welchem Aquarium (ProfiLux) sie gehört. Danach ist
sie dort ein ganz normales Teil: Dashboard, „Alarm melden“, Grenzen in W,
Verzögerung, Wartung, Sammelstörung.

Das Add-on schaltet die Steckdosen nicht – es liest nur.
"""

import logging
import re
import threading
import urllib.parse

from .haws import HaError, HaWs
from .notify import _ha

log = logging.getLogger("waechter")

OFF = "Nicht übernehmen"
PLUG_PLATFORMS = {"shelly", "tasmota", "tplink", "tuya", "localtuya", "zha", "zwave_js", "esphome",
                  "matter", "fritz", "fritzbox", "mystrom", "meross_lan", "sonoff", "homematicip_cloud",
                  "homematic", "deconz", "hue", "mqtt", "z2m", "smartthings", "wemo"}


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _common(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _is_power(attrs):
    return attrs.get("device_class") == "power" or attrs.get("unit_of_measurement") in ("W", "kW")


def find_plugs(entities, devices, states=None):
    """Registry-Einträge -> [{"entity", "power", "name", "slug"}], sortiert nach Name.
    states: entity_id -> Attribute (die Registry-Liste enthält keine device_class)."""
    devs = {d["id"]: d for d in devices}
    states = states or {}
    by_dev = {}
    for e in entities:
        if e.get("disabled_by") or e.get("hidden_by") or e.get("entity_category") or not e.get("device_id"):
            continue
        dom = e["entity_id"].split(".", 1)[0]
        if dom == "switch":
            by_dev.setdefault(e["device_id"], ([], []))[0].append(e)
        elif dom == "sensor" and ((e.get("device_class") or e.get("original_device_class")) == "power"
                                  or _is_power(states.get(e["entity_id"], {}))):
            by_dev.setdefault(e["device_id"], ([], []))[1].append(e)
    out = []
    for dev_id, (switches, powers) in by_dev.items():
        if not switches or not powers:
            continue
        d = devs.get(dev_id, {})
        dev_name = d.get("name_by_user") or d.get("name") or ""
        for sw in switches:
            obj = sw["entity_id"].split(".", 1)[1]
            pw = max(powers, key=lambda p: _common(obj, p["entity_id"].split(".", 1)[1]))
            # Nur echte Steckdosen/Relais: Leistungssensor heißt wie der Schalter
            # (switch.pumpe -> sensor.pumpe_leistung) oder bekannte Steckdosen-Integration
            # mit genau einem Schalter und einem Leistungssensor.
            matches = pw["entity_id"].split(".", 1)[1].startswith(obj)
            simple = len(switches) == 1 and len(powers) == 1 and sw.get("platform") in PLUG_PLATFORMS
            if not (matches or simple):
                continue
            name = states.get(sw["entity_id"], {}).get("friendly_name") or (
                dev_name if len(switches) == 1 else
                f"{dev_name} {sw.get('name') or sw.get('original_name') or obj}".strip())
            out.append({"entity": sw["entity_id"], "power": pw["entity_id"],
                        "name": name or obj, "slug": _slug(obj)})
    return sorted(out, key=lambda p: p["name"].lower())


class HaPlugs:
    def __init__(self, store):
        self.store = store
        self.ha = HaWs()
        self.lock = threading.Lock()
        self.plugs = []  # [{"entity", "power", "name", "slug"}]

    def refresh(self):
        """Steckdosen in HA suchen. -> True, wenn sich etwas geändert hat."""
        try:
            entities = self.ha.call("config/entity_registry/list")
            devices = self.ha.call("config/device_registry/list")
            states = {s["entity_id"]: s.get("attributes", {}) for s in self.ha.call("get_states") or []}
        except (HaError, OSError) as e:
            log.info("Steckdosen in Home Assistant nicht abfragbar: %s", e)
            return False
        finally:
            self.ha.close()
        found = find_plugs(entities or [], devices or [], states)
        with self.lock:
            changed = found != self.plugs
            self.plugs = found
        if changed:
            log.info("Steckdosen mit Leistungsmessung in HA: %s",
                     ", ".join(p["name"] for p in found) or "keine")
        return changed

    def assigned(self):
        return self.store.data.setdefault("ha_steckdosen", {})

    def owner(self, plug):
        return self.assigned().get(plug["entity"])

    def assign(self, slug, uid):
        """-> (alter, neuer Besitzer) oder None, wenn unbekannt."""
        plug = next((p for p in self.plugs if p["slug"] == slug), None)
        if not plug:
            return None
        old = self.owner(plug)
        if uid:
            self.assigned()[plug["entity"]] = uid
        else:
            self.assigned().pop(plug["entity"], None)
        self.store.save()
        log.info("HA-Steckdose %s -> %s", plug["entity"], uid or "nicht übernommen")
        return old, uid

    def items_for(self, uid):
        with self.lock:
            plugs = [p for p in self.plugs if self.owner(p) == uid]
        return [{"key": f"ha_{p['slug']}", "kind": "plug", "label": p["name"], "name": p["name"],
                 "named": True, "entity": p["entity"], "power": p["power"], "unit": "W"} for p in plugs]

    @staticmethod
    def read(items):
        """Zustände aus HA. Kein Eintrag = HA nicht abfragbar; *_state None = nicht erreichbar."""
        values = {}
        for it in items:
            if it["kind"] != "plug":
                continue
            try:
                sw = _ha("GET", "/states/" + urllib.parse.quote(it["entity"]))
                pw = _ha("GET", "/states/" + urllib.parse.quote(it["power"]))
            except Exception as e:
                log.debug("HA-Steckdose %s nicht lesbar: %s", it["entity"], e)
                continue
            state = (sw or {}).get("state")
            values[f"{it['key']}_state"] = {"on": "ON", "off": "OFF"}.get(state)
            try:
                values[f"{it['key']}_power"] = float((pw or {}).get("state"))
            except (TypeError, ValueError):
                values[f"{it['key']}_power"] = None
        return values
