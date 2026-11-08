"""Einstellungen je Gerät und Item (bedienbar aus HA, gespeichert im Add-on)
sowie die Bewertung der Messwerte."""

import datetime
import json
import logging
import threading
import time
from pathlib import Path

from .ghl import NO_STRIP, SENSOR_ARTS

log = logging.getLogger("waechter")

LEVEL_MODES = ["Aus", "Alarm bei 0", "Alarm bei 1"]

DEFAULTS = {
    "switch": {"aktiv": None, "enabled": False, "min": 0.05, "max": 0.0, "delay": 2.0},
    "value": {"aktiv": None, "enabled": False, "min": 0.0, "max": 0.0, "delay": 10.0, "art": None},
    "level": {"aktiv": None, "mode": "Aus", "delay": 1.0},
    "doser": {"aktiv": None, "enabled": False, "min": 500.0, "delay": 0.0},
    # KH-Director misst typ. alle 4–5 h -> Warnung nach 7 h ohne Messung, standardmäßig an
    "age": {"aktiv": None, "enabled": True, "max": 7.0, "delay": 0.0},
    # Licht: nur anzeigen – die Helligkeit ändert sich über den Tag, ein Alarm wäre sinnlos
    "light": {"aktiv": None},
}

# Erinnerung bei anhaltender Störung: Behälter nur 1x täglich
REMINDER = {"doser": 24 * 3600}


def kind_of(key):
    if key == "kh_age":
        return "age"
    for prefix in ("switch", "level", "doser", "light"):
        if key.startswith(prefix + "_"):
            return prefix
    return "value"


class DeviceSettings:
    """Sicht auf die Einstellungen eines Geräts."""

    def __init__(self, store, dev_id):
        self.store, self.dev_id = store, dev_id
        self.named = {}  # key -> bool (für Standard von "aktiv")

    def get(self, key):
        d = {**DEFAULTS[kind_of(key)], **self.store.data["geraete"].get(self.dev_id, {}).get(key, {})}
        if d["aktiv"] is None:
            d["aktiv"] = self.named.get(key, True) or self.store.unnamed_default.get(self.dev_id, False)
        return d

    def set(self, key, field, raw):
        return self.store.set(self.dev_id, key, field, raw)

    # Bereiche (Steckdosen, Licht …) – in HA schaltbar, Standard aus der Add-on-Konfiguration
    def bereich(self, cat, default=True):
        v = self.store.data["geraete"].get(self.dev_id, {}).get("_bereiche", {}).get(cat)
        return default if v is None else v

    def leisten(self):
        """Auswahl je Leiste (Liste von Produktnamen) oder None = noch nichts gewählt."""
        v = self.store.data["geraete"].get(self.dev_id, {}).get("_leisten")
        return v if isinstance(v, list) else None

    def set_leiste(self, n, choice):
        """Leiste n (1-basiert) auf ein Produkt setzen; danach leere Einträge am Ende weg."""
        with self.store.lock:
            dev = self.store.data["geraete"].setdefault(self.dev_id, {})
            cur = dev.get("_leisten") if isinstance(dev.get("_leisten"), list) else []
            cur = cur + [NO_STRIP] * (n - len(cur))
            cur[n - 1] = choice
            while cur and cur[-1] == NO_STRIP:
                cur.pop()
            dev["_leisten"] = cur
        self.store.save()
        log.info("Steckdosenleiste %s/%d: %s", self.dev_id, n, choice)
        return cur

    def set_bereich(self, cat, raw):
        value = str(raw).strip().upper() in ("ON", "TRUE", "1")
        with self.store.lock:
            dev = self.store.data["geraete"].setdefault(self.dev_id, {})
            dev.setdefault("_bereiche", {})[cat] = value
        self.store.save()
        log.info("Bereich %s/%s: %s", self.dev_id, cat, "an" if value else "aus")
        return value

    def setdefault(self, key, field, value):
        dev = self.store.data["geraete"].setdefault(self.dev_id, {})
        if field not in dev.get(key, {}):
            dev.setdefault(key, {})[field] = value
            self.store.save()


class SettingsStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.unnamed_default = {}
        self.data = {"version": 4, "geraete": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if "geraete" in data:
                self.data = data
            else:  # Version 3: flach, ein Gerät -> wird beim ersten Gerät eingehängt
                self.data["_v3"] = data
            log.info("Einstellungen geladen: %s", self.path)
        except FileNotFoundError:
            log.info("Noch keine Einstellungen – Standardwerte (%s)", self.path)
        except (OSError, ValueError) as e:
            log.error("Einstellungen nicht lesbar (%s) – Standardwerte", e)

    def device(self, dev_id, first=False):
        if first and "_v3" in self.data and dev_id not in self.data["geraete"]:
            self.data["geraete"][dev_id] = self.data.pop("_v3")
            log.info("Einstellungen von Version 3 auf Gerät '%s' übernommen", dev_id)
            self.save()
        return DeviceSettings(self, dev_id)

    def set(self, dev_id, key, field, raw):
        kind = kind_of(key)
        if field not in DEFAULTS[kind]:
            return None
        try:
            if field in ("enabled", "aktiv"):
                value = str(raw).strip().upper() in ("ON", "TRUE", "1")
            elif field == "mode":
                if raw not in LEVEL_MODES:
                    return None
                value = raw
            elif field == "art":
                if raw not in SENSOR_ARTS:
                    return None
                value = raw
            else:
                value = round(float(raw), 4)
        except ValueError:
            return None
        with self.lock:
            entry = self.data["geraete"].setdefault(dev_id, {}).setdefault(key, {})
            old = entry.get(field)
            entry[field] = value
        self.save()
        log.info("Einstellung %s/%s.%s: %s -> %s", dev_id, key, field, old, value)
        return value

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self.path)
            except OSError as e:
                log.error("Einstellungen nicht speicherbar: %s", e)


def fmt(v, unit=""):
    """Zahl deutsch formatiert, ohne überflüssige Nullen: 0,25 A"""
    return f"{v:.3f}".rstrip("0").rstrip(".").replace(".", ",") + (f" {unit}" if unit else "")


de = fmt


def rule(item, st):
    """Klartext, wann ein Alarm kommt – steht im Dashboard neben den Einstellungen."""
    kind, unit = item["kind"], item.get("unit", "")
    if not st["aktiv"]:
        return "Wird nicht ausgelesen."
    if kind == "light":
        return "Nur Anzeige (kein Alarm – die Helligkeit ändert sich über den Tag)."
    later = f", wenn das länger als {de(st['delay'])} min anhält" if st.get("delay") else " (sofort)"
    if kind == "switch":
        if not item.get("current"):
            return "Nur Anzeige an/aus – diese Steckdose misst keinen Strom, daher kein Alarm möglich."
        if not st["enabled"]:
            return "Kein Alarm (Überwachung aus)."
        txt = f"Alarm, wenn die Steckdose AN ist, aber weniger als {de(st['min'], 'A')} fließen (Gerät läuft nicht)"
        if st["max"] > 0:
            txt += f" oder mehr als {de(st['max'], 'A')}"
        return txt + later + "."
    if kind == "level":
        if st["mode"] == "Aus":
            return "Kein Alarm (Auswahl „Alarm bei“ steht auf Aus)."
        return f"Alarm, wenn der Niveausensor {st['mode'][-1]} meldet{later}."
    if kind == "age":
        if not st["enabled"]:
            return "Kein Alarm (Überwachung aus)."
        return (f"Alarm, wenn der KH-Director länger als {de(st['max'], 'h')} nicht gemessen hat "
                "(sowie bei Leck- oder Messbereichsalarm, ab ProfiLux-Firmware 7.52).")
    if kind == "doser":
        if not st["enabled"]:
            return "Kein Alarm (Überwachung aus)."
        return f"Meldung „Nachfüllen“, wenn weniger als {de(st['min'], 'ml')} im Behälter sind (1× täglich)."
    if not st["enabled"]:
        return "Kein Alarm (Überwachung aus)."
    if st["min"] >= st["max"]:
        return "⚠️ Grenzen ungültig: Minimum muss kleiner als Maximum sein."
    return f"Alarm, wenn der Wert unter {de(st['min'], unit)} oder über {de(st['max'], unit)} liegt{later}."


def evaluate(item, st, values):
    """-> (watched, ok, detail); ok=None = kein Wert."""
    key, kind, unit = item["key"], item["kind"], item.get("unit", "")
    if not st["aktiv"]:
        return False, True, "nicht abgefragt"
    if kind == "switch":
        watched = st["enabled"] and item.get("current", False)
        state, cur = values.get(f"{key}_state"), values.get(f"{key}_current")
        if state is None:
            return watched, None, "kein Wert"
        if state == "OFF":
            return watched, True, "aus"
        if cur is None:
            return watched, True, "an"
        if cur < st["min"]:
            return watched, False, f"Steckdose an, aber nur {fmt(cur, 'A')} (Minimum {fmt(st['min'], 'A')})"
        if st["max"] > 0 and cur > st["max"]:
            return watched, False, f"Strom zu hoch: {fmt(cur, 'A')} (Maximum {fmt(st['max'], 'A')})"
        return watched, True, f"an, {fmt(cur, 'A')}"
    if kind == "light":
        v = values.get(key)
        return False, True if v is not None else None, "kein Wert" if v is None else fmt(v, "%")
    if kind == "level":
        watched = st["mode"] != "Aus"
        v = values.get(key)
        if v is None:
            return watched, None, "kein Wert"
        if watched and v == int(st["mode"][-1]):
            return watched, False, f"Niveausensor meldet {v}"
        return watched, True, f"Zustand {v}"
    if kind == "age":
        watched, t = st["enabled"], values.get("kh_last")
        status = values.get("kh_status") or 0
        if status & 128:
            return watched, False, "KH-Director meldet LECK-Alarm!"
        if status & 32:
            return watched, False, "KH-Director meldet Messbereichsalarm (Wert außerhalb des Bereichs)"
        if t is None:
            return watched, None, "kein Wert"
        age = (time.time() - t) / 3600
        when = datetime.datetime.fromtimestamp(t).strftime("%d.%m. %H:%M")
        if age > st["max"]:
            return watched, False, (f"Letzte Messung vor {fmt(round(age, 1), 'h')} ({when}) – "
                                    f"Reagenz/Referenz leer oder Director gestört?")
        return watched, True, f"vor {fmt(round(age, 1), 'h')} ({when})"
    if kind == "doser":
        watched, v = st["enabled"], values.get(key)
        if v is None:
            return watched, None, "kein Wert"
        if not watched:
            return watched, True, f"{v:.0f} ml"
        if v < st["min"]:
            pct = values.get(f"{key}_pct")
            rest = f"noch {v:.0f} ml" + (f" ({pct:.0f} %)" if pct is not None else "")
            return watched, False, f"Behälter fast leer: {rest} (Meldung unter {st['min']:.0f} ml)"
        return watched, True, f"{v:.0f} ml"
    watched, v = st["enabled"], values.get(key)
    status = values.get(f"{key}_status") or 0
    if status & 1536:  # Sensorfehler / Kommunikationsfehler laut ProfiLux (ab Firmware 7.52)
        return watched, False, "ProfiLux meldet Sensorfehler – Sonde/Kabel prüfen"
    if v is None:
        return watched, None, "kein Wert"
    note = " (ProfiLux meldet Alarm)" if status & 256 else ""
    if not watched:
        return watched, True, fmt(v, unit) + note
    if st["min"] >= st["max"]:
        return watched, True, f"{fmt(v, unit)} (Grenzen ungültig: Min ≥ Max)"
    if v < st["min"]:
        return watched, False, f"{fmt(v, unit)} – unter Minimum {fmt(st['min'], unit)}"
    if v > st["max"]:
        return watched, False, f"{fmt(v, unit)} – über Maximum {fmt(st['max'], unit)}"
    return watched, True, fmt(v, unit)
