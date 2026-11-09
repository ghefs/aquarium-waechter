"""Automatisches Lovelace-Dashboard (eigenes Dashboard, url_path "aquarium-waechter").

Schutz vor Überschreiben von Handarbeit: Nach jedem Schreiben wird ein
Fingerabdruck (Hash) der geschriebenen Konfiguration gespeichert. Vor dem
nächsten automatischen Schreiben wird die aktuelle Konfiguration aus HA gelesen;
weicht sie vom Fingerabdruck ab, hat jemand von Hand geändert -> NICHT
überschreiben (nur Log). Mit force=True (Knopf "Dashboard neu erzeugen") wird
trotzdem geschrieben.
"""

import hashlib
import re
import json
import logging
from pathlib import Path

from .bridge import Bridge
from .ghl import CATEGORY_NAMES, MAX_STRIPS, strips_summary
from .haws import HaError
from .i18n import _, dec

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
            "{% if ns.n == 0 %}✅ " + _("Keine Störung") + "{% endif %}")


def _md_watched(uid):
    """Liste aller überwachten Punkte: Regel im Klartext + aktueller Zustand."""
    return ("{% set ns = namespace(n=0) %}"
            f"{{% for s in states.binary_sensor if s.entity_id.startswith('binary_sensor.{uid}_') "
            "and s.entity_id.endswith('_stoerung') and s.attributes.get('ueberwacht') %}"
            "{% set ns.n = ns.n + 1 %}"
            "{{ '🔴' if s.state == 'on' else '🟢' }} **{{ s.attributes.bezeichnung or s.name }}** – "
            + _("jetzt") + ": {{ s.attributes.detail }}  \n<small>{{ s.attributes.regel }}</small>\n\n{% endfor %}"
            "{% if ns.n == 0 %}" + _("Noch nichts überwacht – auf der Einstellungsseite „Alarm melden“ einschalten.")
            + "{% endif %}")


def _md_rules(uid, its):
    """Regeln eines Blocks im Klartext (aus dem Attribut "regel" der Störungs-Sensoren)."""
    lines = []
    for it in its:
        ent = f"binary_sensor.{uid}_{it['key']}_stoerung"
        lines.append(f"{{{{ '🟢' if is_state_attr('{ent}', 'ueberwacht', true) else '⚪' }}}} "
                     f"**{_n(it['label'])}**  \n{{{{ state_attr('{ent}', 'regel') or '–' }}}}")
    return "**" + _("So wird überwacht") + "** (🟢 = " + _("Alarm aktiv") + ")\n\n" + "\n\n".join(lines)


HELP = (
    "**So funktionieren die Einstellungen**\n\n"
    "- **Schalter neben dem Namen** (👁) – auslesen: der Wert wird vom ProfiLux gelesen und in Home Assistant angezeigt.\n"
    "- **Alarm melden** – nur wenn an, kommt bei einer Grenzverletzung eine Meldung (Push an die HA-App und/oder Telegram).\n"
    "- **Alarm, wenn …** – die Grenzen. Der Kasten *So wird überwacht* fasst sie je Teil in einem Satz zusammen.\n"
    "- **Alarm erst nach (Dauer)** – kurze Ausreißer werden ignoriert; der Alarm kommt erst, "
    "wenn die Störung so lange anhält.\n"
    "- **Bereiche** – ganze Gruppen (z. B. Licht) gar nicht erst lesen.\n\n"
    "Kürzel vorne im Namen: *SD 2.3* = Steckdose an Leiste 2, Dose 3 · *DP 4* = Dosierpumpe 4 · *Niveau 1* · "
    "*pH*, *Temperatur* … = Sensor-Art.")


def _md_dosers(uid, dosers):
    """Tabelle: Behälter | Füllstand | reicht noch | Verbrauch | letzte Dosierung (ab Firmware 7.52)."""
    def val(ent, fmt_):
        return (f"{{% set v = states('{ent}') %}}"
                f"{{{{ '–' if v in ['unknown', 'unavailable', 'none', ''] else {fmt_} }}}}")
    rec = any(it.get("records") for it in dosers)
    head = "| " + " | ".join(_(h) for h in ["Behälter", "Füllstand", "reicht noch", "Verbrauch"]
                             + (["zuletzt"] if rec else [])) + " |"
    rep = "|replace('.', ',')" if dec() == "," else ""
    days_u, per_u = _("Tage"), _("ml/Tag")
    rows = [head, "|:--|--:|--:|--:|" + ("--:|" if rec else "")]
    for it in dosers:
        b = f"sensor.{uid}_{it['key']}"
        fill = val(f"{b}_pct", "(v|float(0))|round(0)|int ~ ' %'") if it.get("capacity") else "–"
        days = val(f"{b}_tage", f"(v|float(0))|round(0)|int ~ ' {days_u}'") if it.get("capacity") else "–"
        per = val(f"{b}_verbrauch", f"(v|float(0))|round(1)|string{rep} ~ ' {per_u}'") \
            if it.get("capacity") or it.get("records") else "–"
        row = f"| {_n(it['label'])} | {fill} | {days} | {per} |"
        if rec:
            last = (val(f"{b}_letzte", f"as_timestamp(v)|timestamp_custom('{_('%d.%m. %H:%M')}')")
                    + " " + val(f"{b}_dosiert", f"'(' ~ (v|float(0))|round(1)|string{rep} ~ ' ml)'")
                    if it.get("records") else "–")
            row += f" {last} |"
        rows.append(row)
    return "\n".join(rows)


SHORT = {
    "enabled": "Alarm melden",
    ("switch", "min"): "Min. Strom (wenn an)", ("switch", "max"): "Max. Strom (0 = aus)",
    ("plug", "min"): "Min. Leistung (wenn an)", ("plug", "max"): "Max. Leistung (0 = aus)",
    ("value", "min"): "Minimum", ("value", "max"): "Maximum",
    ("doser", "min"): "Nachfüllen unter", ("doser", "tage"): "… oder Vorrat unter",
    ("age", "max"): "Keine Messung seit",
    "delay": "Erst nach (Dauer)", "mode": "Alarm bei", "art": "Sensor-Art",
}


def _n(label):
    """Kürzel im Dashboard, damit Namen nicht abgeschnitten werden:
    „Steckdose 2.3 · Heizer“ -> „SD 2.3 · Heizer“, „Dosierer 8 · …“ -> „DP 8 · …“."""
    for full, short in ((_("Steckdose {n}", n=""), _("SD")), (_("Dosierer {n}", n=""), _("DP"))):
        if label.startswith(full):
            return short + " " + label[len(full):]
    return label


def _short(kind, field):
    """Kurze Beschriftung im Dashboard (lange Fassung steht im Entitätsnamen)."""
    v = SHORT.get((kind, field)) or SHORT.get(field)
    return _(v) if v else Bridge.label_of(kind, field)


def _strips(switches):
    """Steckdosen nach Leiste gruppieren -> [(Titel, [items])]."""
    groups = {}
    for it in switches:
        groups.setdefault(it.get("strip_no", 0), []).append(it)
    out = []
    for no, its in sorted(groups.items(), key=lambda g: g[0] or 999):
        if no:
            title = _("Leiste {n} – {s}", n=no, s=its[0]["strip"])
        else:
            title = _("Steckdosen ohne Leiste") if len(groups) > 1 else _("Steckdosen")
        out.append((title, its))
    return out


def build(workers):
    views = []
    for dev in workers:
        uid, items = dev.uid, [it for it in dev.items if dev.settings.get(it["key"])["aktiv"]]
        e = lambda comp, suffix: f"{comp}.{uid}_{suffix}"
        cards = [{"type": "grid", "columns": 3, "square": False, "cards": [
                     {"type": "tile", "entity": e("binary_sensor", "verbindung"), "name": _("Verbindung")},
                     {"type": "tile", "entity": e("binary_sensor", "sammelstoerung"), "name": _("Sammelstörung")},
                     {"type": "tile", "entity": "switch.aquarium_waechter_wartung", "name": _("Wartung"),
                      "tap_action": {"action": "toggle"}}]},
                 {"type": "conditional",
                  "conditions": [{"condition": "state", "entity": "switch.aquarium_waechter_wartung", "state": "on"}],
                  "card": {"type": "markdown", "content":
                           "🔧 **" + _("Wartung – Alarme pausiert bis {t}.",
                                       t="{{ state_attr('switch.aquarium_waechter_wartung', 'bis') }}") + "** "
                           + _("Danach wird wieder überwacht; anstehende Störungen kommen dann.")}},
                 {"type": "markdown", "title": _("Störungen"), "content": _md_faults(uid)}]
        if dev.has_power():
            cards.append({"type": "grid", "columns": 2, "square": False, "cards": [
                {"type": "tile", "entity": e("sensor", "gesamt_leistung"), "name": _("Gesamtleistung")},
                {"type": "tile", "entity": e("sensor", "gesamt_energie"), "name": _("Energie")}]})
        values = [it for it in items if it["kind"] == "value"]
        if values:
            cards.append({"type": "grid", "columns": 2, "square": False, "cards": [
                {"type": "tile", "entity": e("sensor", it["key"]), "name": _n(it["label"])} for it in values]})
        # Verläufe: je Einheit ein Diagramm (beide pH-Sonden zusammen, Leitwert getrennt
        # von Salinität ...). KH misst nur alle paar Stunden -> 7 Tage.
        groups = {}
        for it in values:
            label = {"temperature": _("Temperatur"), "ph": "pH"}.get(it.get("device_class")) \
                or ("KH" if it["key"] == "kh" else None) \
                or {"mS/cm": _("Leitwert"), "µS/cm": _("Leitwert"), "PSU": _("Salinität"), "mV": _("Redox"),
                    "mg/l": _("Sauerstoff")}.get(it.get("unit")) \
                or (_n(it["label"]) if it["key"].startswith("ion_") else _n(it["label"]).split(" · ")[0])
            groups.setdefault(label, []).append(it)
        for label, its in groups.items():
            hours = 168 if label == "KH" else 48
            cards.append({"type": "history-graph",
                          "title": _("{l} – {d} Tage", l=label, d=hours // 24) if hours > 24 else label,
                          "hours_to_show": hours,
                          "entities": [{"entity": e("sensor", it["key"]), "name": it["name"]} for it in its]})
        for title, its in _strips([it for it in items if it["kind"] == "switch"]):
            if any(it["current"] for it in its):
                # Leiste mit Strommessung: je Dose Zustand (an/aus) und Strom nebeneinander
                tiles = []
                for it in its:
                    tiles.append({"type": "tile", "entity": e("binary_sensor", f"{it['key']}_state"),
                                  "name": _n(it["label"])})
                    tiles.append({"type": "tile", "name": _("Strom"), "entity": e("sensor", f"{it['key']}_current")}
                                 if it["current"] else {"type": "markdown", "content": _("ohne Strommessung")})
                cards.append({"type": "grid", "title": title, "columns": 2, "square": False, "cards": tiles})
            else:
                cards.append({"type": "entities", "title": title, "state_color": True, "entities": [
                    {"entity": e("binary_sensor", f"{it['key']}_state"), "name": _n(it["label"])} for it in its]})
        dosers = [it for it in items if it["kind"] == "doser"]
        if dosers:
            pct = [it for it in dosers if it.get("capacity")]
            nocap = [it for it in dosers if not it.get("capacity")]
            cards.append({"type": "markdown", "title": _("Dosierbehälter"), "content": _md_dosers(uid, dosers)})
            if pct and nocap:
                cards.append({"type": "markdown", "content": (
                    _("**Ohne Füllstand** (im ProfiLux keine Behältergröße eingetragen): ")
                    + ", ".join(_n(it["label"]) for it in nocap))})
            cards.append({"type": "history-graph", "hours_to_show": 336,
                          "title": _("Füllstände in % – 14 Tage") if pct else _("Füllstände – 14 Tage"),
                          "entities": [{"entity": e("sensor", f"{it['key']}_pct" if pct else it["key"]),
                                        "name": it["name"]} for it in (pct or dosers)]})
        plugs = [it for it in items if it["kind"] == "plug"]
        if plugs:
            tiles = []
            for it in plugs:
                tiles.append({"type": "tile", "entity": it["entity"], "name": _n(it["label"]),
                              "tap_action": {"action": "more-info"},
                              "icon_tap_action": {"action": "more-info"}})  # nicht versehentlich schalten
                tiles.append({"type": "tile", "entity": it["power"], "name": _("Leistung")})
            cards.append({"type": "grid", "title": _("Steckdosen aus Home Assistant"), "columns": 2,
                          "square": False, "cards": tiles})
        levels = [it for it in items if it["kind"] == "level"]
        if levels:
            cards.append({"type": "entities", "title": _("Niveau"),
                          "entities": [{"entity": e("sensor", it["key"]), "name": _n(it["label"])} for it in levels]})
        lights = [it for it in items if it["kind"] == "light"]
        if lights:
            cards.append({"type": "history-graph", "title": _("Licht – Helligkeit"), "hours_to_show": 24,
                          "entities": [{"entity": e("sensor", it["key"]), "name": _n(it["label"])} for it in lights]})
        cards.append({"type": "markdown", "title": _("So wird überwacht"), "content": _md_watched(uid)})
        views.append({"title": dev.name, "path": uid, "icon": "mdi:fishbowl", "cards": cards})

        # Einstellungen: Masonry-Ansicht (Karten werden lückenlos gestapelt), kurze Beschriftungen
        cards = [
            {"type": "markdown", "title": _("Kurz erklärt"), "content": _(HELP)},
            {"type": "vertical-stack", "cards": [
                {"type": "markdown", "title": _("Steckdosenleisten"), "content": (
                    _("**Leiste 1** = erste Steckdosen, **Leiste 2** die nächsten usw. – Reihenfolge wie die "
                      "Steckdosennummern in GHL Connect (meist zuerst STDL am S-Anschluss, dann PAB).") + "\n\n"
                    + ("**" + _("Ergebnis") + ":** " + strips_summary(dev.inv.strips) if dev.inv.strips
                       else "*" + _("Noch keine Leisten gewählt – Steckdosen werden einfach durchgezählt.") + "*"))},
                {"type": "entities", "entities": [
                    {"entity": e("select", f"leiste_{n}")}
                    for n in range(1, min(MAX_STRIPS, len(dev.leisten()) + 1) + 1)]
                    + [{"entity": "number.aquarium_waechter_spannung", "name": _("Netzspannung")}]}]},
            {"type": "entities", "title": _("Bereiche"), "state_color": True, "entities": [
                {"type": "text", "name": "⏳", "text": _("Nach dem Umschalten 1–3 min warten, Seite neu laden")}]
                + [{"entity": e("switch", f"bereich_{cat}"), "name": _(name)}
                   for cat, name in CATEGORY_NAMES.items()]}]
        kinds = [("value", _("Messwerte")), ("age", "KH-Director"), ("doser", _("Dosierbehälter")),
                 ("level", _("Niveausensoren")), ("light", _("Licht")), ("plug", _("Steckdosen aus HA – Alarm"))]
        blocks = [(t, its) for t, its in _strips([it for it in dev.items if it["kind"] == "switch"])]
        blocks += [(t, [it for it in dev.items if it["kind"] == k]) for k, t in kinds]
        for title, its in blocks:
            if not its:
                continue
            settable = [it for it in its if dev.settings.get(it["key"])["aktiv"] and dev.bridge_cfg(it)]
            if not settable:
                # nur Anzeige (Leiste ohne Strommessung, Licht …): je Teil eine Zeile „auslesen“
                note = (_("Nur Anzeige – kein Alarm möglich.") if its[0]["kind"] in ("switch", "light")
                        else _("Schalter = auslesen."))
                cards.append({"type": "entities", "title": title, "state_color": True, "entities": [
                    {"type": "text", "name": "ℹ️", "text": note}] + [
                    {"entity": e("switch", f"{it['key']}_abfragen"), "name": _n(it["label"])}
                    for it in its if it["kind"] != "plug"]})
                continue
            rows = []
            for it in its:
                k = it["key"]
                if rows:
                    rows.append({"type": "divider"})
                if it["kind"] == "plug":
                    rows.append({"type": "section", "label": _n(it["label"])})
                else:  # Name des Teils = Schalter „auslesen“
                    rows.append({"entity": e("switch", f"{k}_abfragen"), "name": _n(it["label"]),
                                 "icon": "mdi:eye-outline"})
                if it not in settable:
                    continue
                cfg = dev.bridge_cfg(it)
                for comp, suf, field in (("switch", "ueberwachen", "enabled"), ("select", "alarm", "mode"),
                                         ("number", "min", "min"), ("number", "tage", "tage"),
                                         ("number", "max", "max"),
                                         ("number", "verzoegerung", "delay"), ("select", "art", "art")):
                    if any(c == comp and s.endswith(f"_{field}") for c, s, _x in cfg):
                        rows.append({"entity": e(comp, f"{k}_{suf}"), "name": _short(it["kind"], field)})
            cards.append({"type": "vertical-stack", "cards": [
                {"type": "entities", "title": title, "entities": rows, "state_color": True},
                {"type": "markdown", "content": _md_rules(uid, settable)}]})
        ha_plugs = workers[0].app.plugs.plugs if workers else []
        comp = "select" if len(workers) > 1 else "switch"
        cards.append({"type": "vertical-stack", "cards": [
            {"type": "markdown", "title": _("Steckdosen aus Home Assistant"), "content": (
                _("Weitere Pumpen, externes Licht … an einer **Shelly Plug S** oder einer anderen Steckdose "
                  "mit Leistungsmessung? Einschalten – dann steht sie im Dashboard und wird wie eine "
                  "ProfiLux-Steckdose überwacht (Leistung in W, Alarm auch, wenn sie nicht erreichbar ist). "
                  "Das Add-on schaltet sie nicht.")
                + ("" if ha_plugs else "\n\n*" + _("Keine Steckdose mit Leistungsmessung in Home Assistant "
                                                    "gefunden. Neu eingerichtet? → „Geräte neu einlesen“.") + "*"))}]
            + ([{"type": "entities", "state_color": True, "entities": [
                {"entity": f"{comp}.aquarium_waechter_plug_{p['slug']}", "name": p["name"]}
                for p in ha_plugs]}] if ha_plugs else [])})
        cards.append({"type": "entities", "title": _("Wochenbericht"), "entities": [
            {"type": "text", "name": "ℹ️", "text": _("gegen 18 Uhr: Werte, Vorrat, Störungen")},
            "select.aquarium_waechter_wochenbericht", "button.aquarium_waechter_bericht"]})
        cards.append({"type": "vertical-stack", "cards": [
            {"type": "markdown", "title": _("Wartung"), "content": _(
                "Am Becken arbeiten? **Wartung** einschalten – für die eingestellte Dauer kommen "
                "**keine Alarme**, danach schaltet sie sich selbst ab; steht eine Störung dann noch an, "
                "wird sie gemeldet. Am ProfiLux wird nichts geschaltet.")},
            {"type": "entities", "state_color": True, "entities": [
                "switch.aquarium_waechter_wartung", "number.aquarium_waechter_wartung_dauer"]}]})
        phones = workers[0].app.notifier.phones if workers else []
        if phones:
            cards.append({"type": "vertical-stack", "cards": [
                {"type": "markdown", "title": _("Alarm aufs Handy"), "content": _(
                    "Push an die **Home-Assistant-App** je Handy einschalten. Störungen kommen als "
                    "**kritische Benachrichtigung** (klingelt auch bei „Nicht stören“ – am iPhone einmal "
                    "*Kritische Hinweise* erlauben). Neues Handy? → „Geräte neu einlesen“.")},
                {"type": "entities", "state_color": True, "entities": [
                    {"entity": f"switch.aquarium_waechter_push_{p}"} for p in phones]}]})
        cards.append({"type": "vertical-stack", "cards": [
            {"type": "markdown", "title": "Add-on", "content": (
                _("Dieses Dashboard erzeugt das Add-on automatisch. **Änderungen von Hand** werden erkannt "
                  "und nicht überschrieben – für eigene Anpassungen besser kopieren. "
                  "„Dashboard neu erzeugen“ verwirft Handänderungen.") + "\n\n🍖 "
                + _("**Dieses Add-on ist kostenlos.** Gefällt es dir? "
                    "[Spendier mir einen Schweinebraten](https://buymeacoffee.com/gutgeschaltet)")
                + " · [⭐ GitHub](https://github.com/ghefs/aquarium-waechter)")},
            {"type": "entities", "entities": ["button.aquarium_waechter_dashboard_neu",
                                              "button.aquarium_waechter_telegram_test",
                                              "button.aquarium_waechter_neu_einlesen"]}]})
        views.append({"title": f"{dev.name} – {_('Einstellungen')}", "path": f"{uid}-einstellungen",
                      "icon": "mdi:tune", "cards": cards})
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
