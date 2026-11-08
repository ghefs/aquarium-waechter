"""Automatisches Lovelace-Dashboard (eigenes Dashboard, url_path "aquarium-waechter").

Schutz vor Überschreiben von Handarbeit: Nach jedem Schreiben wird ein
Fingerabdruck (Hash) der geschriebenen Konfiguration gespeichert. Vor dem
nächsten automatischen Schreiben wird die aktuelle Konfiguration aus HA gelesen;
weicht sie vom Fingerabdruck ab, hat jemand von Hand geändert -> NICHT
überschreiben (nur Log). Mit force=True (Knopf "Dashboard neu erzeugen") wird
trotzdem geschrieben.
"""

import hashlib
import json
import logging
from pathlib import Path

from .haws import HaError

log = logging.getLogger("waechter")

URL_PATH = "aquarium-waechter"
TITLE = "Aquarium"


def _hash(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _md_faults(uid):
    return ("{% set ns = namespace(n=0) %}"
            f"{{% for s in states.binary_sensor if s.entity_id.startswith('binary_sensor.{uid}_') "
            "and s.entity_id.endswith('_stoerung') and s.attributes.get('ueberwacht') %}"
            "{% if s.state == 'on' %}{% set ns.n = ns.n + 1 %}"
            "🔴 **{{ s.name | replace(' Störung','') }}**: {{ s.attributes.detail }}\n\n{% endif %}{% endfor %}"
            "{% if ns.n == 0 %}✅ Keine Störung{% endif %}")


def build(workers):
    views = []
    for dev in workers:
        uid, items = dev.uid, [it for it in dev.items if dev.settings.get(it["key"])["aktiv"]]
        e = lambda comp, suffix: f"{comp}.{uid}_{suffix}"
        cards = [{"type": "tile", "entity": e("binary_sensor", "verbindung"), "name": "Verbindung"},
                 {"type": "markdown", "title": "Störungen", "content": _md_faults(uid)}]
        values = [it for it in items if it["kind"] == "value"]
        if values:
            cards.append({"type": "grid", "columns": 2, "square": False, "cards": [
                {"type": "tile", "entity": e("sensor", it["key"]), "name": it["name"]} for it in values]})
        # Verläufe: je Einheit ein Diagramm (beide pH-Sonden zusammen, Leitwert getrennt
        # von Salinität ...). KH misst nur alle paar Stunden -> 7 Tage.
        groups = {}
        for it in values:
            label = {"temperature": "Temperatur", "ph": "pH"}.get(it.get("device_class")) \
                or ("KH" if it["key"] == "kh" else None) \
                or {"mS/cm": "Leitwert", "µS/cm": "Leitwert", "PSU": "Salinität", "mV": "Redox",
                    "mg/l": "Sauerstoff"}.get(it.get("unit")) or it["name"]
            groups.setdefault(label, []).append(it)
        for label, its in groups.items():
            hours = 168 if label == "KH" else 48
            cards.append({"type": "history-graph", "title": f"{label} – {hours // 24} Tage" if hours > 24 else label,
                          "hours_to_show": hours,
                          "entities": [{"entity": e("sensor", it["key"]), "name": it["name"]} for it in its]})
        switches = [it for it in items if it["kind"] == "switch"]
        if switches:
            cards.append({"type": "entities", "title": "Steckdosen", "entities": [
                {"entity": e("sensor", f"{it['key']}_current") if it["current"]
                 else e("binary_sensor", f"{it['key']}_state"), "name": it["name"]}
                for it in switches]})
        dosers = [it for it in items if it["kind"] == "doser"]
        if dosers:
            cards.append({"type": "entities", "title": "Dosierbehälter",
                          "entities": [{"entity": e("sensor", it["key"]), "name": it["name"]} for it in dosers]})
            cards.append({"type": "history-graph", "title": "Füllstände – 14 Tage", "hours_to_show": 336,
                          "entities": [{"entity": e("sensor", it["key"]), "name": it["name"]} for it in dosers]})
        levels = [it for it in items if it["kind"] == "level"]
        if levels:
            cards.append({"type": "entities", "title": "Niveau",
                          "entities": [{"entity": e("sensor", it["key"]), "name": it["name"]} for it in levels]})
        views.append({"title": dev.name, "path": uid, "icon": "mdi:fishbowl", "cards": cards})

        # Einstellungen: alle gefundenen Teile, Abfragen-Schalter immer sichtbar
        groups = {"switch": "Steckdosen", "value": "Messwerte", "level": "Niveau", "doser": "Dosierer",
                  "age": "KH-Messung"}
        sections = []
        for kind, title in groups.items():
            rows = []
            for it in dev.items:
                if it["kind"] != kind:
                    continue
                k = it["key"]
                rows.append({"type": "section", "label": it["name"]})
                rows.append({"entity": e("switch", f"{k}_abfragen"), "name": "Abfragen"})
                if not dev.settings.get(k)["aktiv"]:
                    continue
                for comp, suf, label in (("switch", "ueberwachen", "Überwachen"),
                                         ("select", "alarm", "Alarm"), ("select", "art", "Art"),
                                         ("number", "min", None), ("number", "max", None),
                                         ("number", "verzoegerung", "Verzögerung")):
                    if any(c == comp and s.endswith(f"_{suf}") for c, s, _ in dev.bridge_cfg(it)):
                        row = {"entity": e(comp, f"{k}_{suf}")}
                        if label:
                            row["name"] = label
                        rows.append(row)
            if rows:
                sections.append({"type": "grid", "cards": [
                    {"type": "heading", "heading": title},
                    {"type": "entities", "entities": rows, "state_color": True}]})
        sections.append({"type": "grid", "cards": [
            {"type": "heading", "heading": "Wächter"},
            {"type": "markdown", "content": (
                "Dieses Dashboard erzeugt der Aquarium-Wächter automatisch. "
                "**Änderungen von Hand** werden erkannt und dann nicht mehr überschrieben – "
                "für eigene Anpassungen das Dashboard besser kopieren. "
                "Mit dem Knopf unten wird es neu erzeugt (Handänderungen gehen verloren).")},
            {"type": "entities", "entities": ["button.aquarium_waechter_dashboard_neu",
                                              "button.aquarium_waechter_telegram_test"]}]})
        views.append({"title": f"{dev.name} – Einstellungen", "path": f"{uid}-einstellungen",
                      "icon": "mdi:tune", "type": "sections", "max_columns": 4, "sections": sections})
    return {"title": TITLE, "views": views}


class Dashboard:
    def __init__(self, ha, state_file):
        self.ha = ha
        self.state_file = Path(state_file)

    def _last_hash(self):
        try:
            return json.loads(self.state_file.read_text()).get("hash")
        except (OSError, ValueError):
            return None

    def update(self, workers, force=False):
        cfg = build(workers)
        try:
            boards = self.ha.call("lovelace/dashboards/list")
            if not any(b.get("url_path") == URL_PATH for b in boards):
                self.ha.call("lovelace/dashboards/create", url_path=URL_PATH, title=TITLE,
                             icon="mdi:fishbowl", show_in_sidebar=True, require_admin=False, mode="storage")
                log.info("Dashboard '%s' angelegt", URL_PATH)
                current = None
            else:
                try:
                    current = self.ha.call("lovelace/config", url_path=URL_PATH)
                except HaError:
                    current = None  # angelegt, aber noch leer
            last = self._last_hash()
            if current is not None and not force:
                if _hash(current) == _hash(cfg):
                    return "unverändert"
                if last is None or _hash(current) != last:
                    log.warning("Dashboard '%s' wurde von Hand geändert – wird nicht überschrieben. "
                                "Neu erzeugen: Knopf 'Dashboard neu erzeugen'.", URL_PATH)
                    return "handgeändert"
            self.ha.call("lovelace/config/save", url_path=URL_PATH, config=cfg)
            self.state_file.write_text(json.dumps({"hash": _hash(cfg)}))
            log.info("Dashboard '%s' aktualisiert", URL_PATH)
            return "geschrieben"
        except (HaError, OSError) as e:
            log.error("Dashboard nicht aktualisiert: %s", e)
            return "fehler"
