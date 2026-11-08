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

from .bridge import Bridge
from .ghl import CATEGORY_NAMES
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
            "🔴 **{{ s.attributes.bezeichnung or s.name }}**: {{ s.attributes.detail }}\n\n{% endif %}{% endfor %}"
            "{% if ns.n == 0 %}✅ Keine Störung{% endif %}")


def _md_watched(uid):
    """Liste aller überwachten Punkte: Regel im Klartext + aktueller Zustand."""
    return ("{% set ns = namespace(n=0) %}"
            f"{{% for s in states.binary_sensor if s.entity_id.startswith('binary_sensor.{uid}_') "
            "and s.entity_id.endswith('_stoerung') and s.attributes.get('ueberwacht') %}"
            "{% set ns.n = ns.n + 1 %}"
            "{{ '🔴' if s.state == 'on' else '🟢' }} **{{ s.attributes.bezeichnung or s.name }}** – "
            "jetzt: {{ s.attributes.detail }}  \n<small>{{ s.attributes.regel }}</small>\n\n{% endfor %}"
            "{% if ns.n == 0 %}Noch nichts überwacht – auf der Einstellungsseite „Alarm per Telegram“ "
            "einschalten.{% endif %}")


HELP = (
    "**So funktionieren die Einstellungen**\n\n"
    "- **Auslesen** – der Wert wird vom ProfiLux gelesen und in Home Assistant angezeigt.\n"
    "- **Alarm per Telegram** – nur wenn an, kommt bei einer Grenzverletzung eine Meldung.\n"
    "- **Alarm, wenn …** – die Grenzen. Die Zeile *So wird überwacht* fasst sie in einem Satz zusammen.\n"
    "- **Alarm erst nach (Dauer)** – kurze Ausreißer werden ignoriert; der Alarm kommt erst, "
    "wenn die Störung so lange anhält.\n"
    "- **Bereiche** – ganze Gruppen (z. B. Licht) gar nicht erst lesen.\n\n"
    "Kürzel vorne im Namen: *Steckdose 2.3* = Leiste 2, Dose 3 · *Dosierer 4* · *Niveau 1* · "
    "*pH*, *Temperatur* … = Sensor-Art.")


def _strips(switches):
    """Steckdosen nach Leiste gruppieren -> [(Titel, [items])]."""
    groups = {}
    for it in switches:
        groups.setdefault(it.get("strip") or "", []).append(it)
    out = []
    for n, (strip, its) in enumerate(groups.items()):
        title = "Steckdosen" if not strip else f"Leiste {its[0]['label'].split()[1].split('.')[0]} – {strip}"
        out.append((title, its))
    return out


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
                {"type": "tile", "entity": e("sensor", it["key"]), "name": it["label"]} for it in values]})
        # Verläufe: je Einheit ein Diagramm (beide pH-Sonden zusammen, Leitwert getrennt
        # von Salinität ...). KH misst nur alle paar Stunden -> 7 Tage.
        groups = {}
        for it in values:
            label = {"temperature": "Temperatur", "ph": "pH"}.get(it.get("device_class")) \
                or ("KH" if it["key"] == "kh" else None) \
                or {"mS/cm": "Leitwert", "µS/cm": "Leitwert", "PSU": "Salinität", "mV": "Redox",
                    "mg/l": "Sauerstoff"}.get(it.get("unit")) \
                or (it["label"] if it["key"].startswith("ion_") else it["label"].split(" · ")[0])
            groups.setdefault(label, []).append(it)
        for label, its in groups.items():
            hours = 168 if label == "KH" else 48
            cards.append({"type": "history-graph", "title": f"{label} – {hours // 24} Tage" if hours > 24 else label,
                          "hours_to_show": hours,
                          "entities": [{"entity": e("sensor", it["key"]), "name": it["name"]} for it in its]})
        for title, its in _strips([it for it in items if it["kind"] == "switch"]):
            cards.append({"type": "entities", "title": title, "state_color": True, "entities": [
                {"entity": e("sensor", f"{it['key']}_current") if it["current"]
                 else e("binary_sensor", f"{it['key']}_state"), "name": it["label"]}
                for it in its]})
        dosers = [it for it in items if it["kind"] == "doser"]
        if dosers:
            rows = []
            for it in dosers:
                rows.append({"entity": e("sensor", it["key"]), "name": it["label"]})
            cards.append({"type": "entities", "title": "Dosierbehälter", "entities": rows})
            pct = [it for it in dosers if it.get("capacity")]
            cards.append({"type": "history-graph", "hours_to_show": 336,
                          "title": "Füllstände in % – 14 Tage" if pct else "Füllstände – 14 Tage",
                          "entities": [{"entity": e("sensor", f"{it['key']}_pct" if pct else it["key"]),
                                        "name": it["name"]} for it in (pct or dosers)]})
        levels = [it for it in items if it["kind"] == "level"]
        if levels:
            cards.append({"type": "entities", "title": "Niveau",
                          "entities": [{"entity": e("sensor", it["key"]), "name": it["label"]} for it in levels]})
        lights = [it for it in items if it["kind"] == "light"]
        if lights:
            cards.append({"type": "history-graph", "title": "Licht – Helligkeit", "hours_to_show": 24,
                          "entities": [{"entity": e("sensor", it["key"]), "name": it["label"]} for it in lights]})
        cards.append({"type": "markdown", "title": "So wird überwacht", "content": _md_watched(uid)})
        views.append({"title": dev.name, "path": uid, "icon": "mdi:fishbowl", "cards": cards})

        # Einstellungen: alle gefundenen Teile, "Auslesen" immer sichtbar
        sections = [{"type": "grid", "cards": [
            {"type": "heading", "heading": "Kurz erklärt"},
            {"type": "markdown", "content": HELP},
            {"type": "heading", "heading": "Steckdosenleisten", "heading_style": "subtitle"},
            {"type": "markdown", "content": (
                "Steckdosen in der Reihenfolge der Kanäle auf Leisten aufteilen, z. B. "
                "`Alt=4 (ohne Strom), PAB=6` → Kanal 1–4 = Leiste 1, 5–10 = Leiste 2. "
                "*(ohne Strom)* = Leiste misst keinen Strom → kein Strom-Alarm. Leer = durchzählen.")},
            {"type": "entities", "entities": [{"entity": e("text", "steckdosenleisten"), "name": "Aufteilung"}]},
            {"type": "heading", "heading": "Bereiche", "heading_style": "subtitle"},
            {"type": "entities", "state_color": True, "entities": [
                {"entity": e("switch", f"bereich_{cat}"), "name": name}
                for cat, name in CATEGORY_NAMES.items()]}]}]
        kinds = [("value", "Messwerte"), ("age", "KH-Director"), ("doser", "Dosierbehälter"),
                 ("level", "Niveausensoren"), ("light", "Licht")]
        blocks = [(t, its) for t, its in _strips([it for it in dev.items if it["kind"] == "switch"])]
        blocks += [(t, [it for it in dev.items if it["kind"] == k]) for k, t in kinds]
        for title, its in blocks:
            rows = []
            for it in its:
                k = it["key"]
                rows.append({"type": "section", "label": it["label"]})
                rows.append({"entity": e("switch", f"{k}_abfragen"), "name": "Auslesen"})
                if not dev.settings.get(k)["aktiv"] or it["kind"] == "light":
                    continue
                rows.append({"type": "attribute", "entity": e("binary_sensor", f"{k}_stoerung"),
                             "attribute": "regel", "name": "So wird überwacht"})
                cfg = dev.bridge_cfg(it)
                for comp, suf, field in (("switch", "ueberwachen", "enabled"), ("select", "alarm", "mode"),
                                         ("number", "min", "min"), ("number", "max", "max"),
                                         ("number", "verzoegerung", "delay"), ("select", "art", "art")):
                    if any(c == comp and s.endswith(f"_{field}") for c, s, _ in cfg):
                        rows.append({"entity": e(comp, f"{k}_{suf}"),
                                     "name": Bridge.label_of(it["kind"], field)})
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
                                              "button.aquarium_waechter_telegram_test",
                                              "button.aquarium_waechter_neu_einlesen"]}]})
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
