"""Wochenbericht: Messwerte der letzten 7 Tage (Mittel, Minimum, Maximum aus der
Langzeitstatistik von Home Assistant), Vorrat der Dosierbehälter und die
Störungen der Woche – als eine Meldung (Telegram / HA-App)."""

import datetime
import logging
import time

from .haws import HaError, HaWs
from .i18n import _
from .settings import fmt

log = logging.getLogger("waechter")

DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
OPTIONS = ["Aus"] + DAYS
HOUR = 18  # Versand gegen 18 Uhr


def _num(v, unit=""):
    return fmt(round(v, 2), unit)


def stats(ha, ids, start, end):
    """-> {statistic_id: (mittel, min, max)} aus den Tageswerten."""
    if not ids:
        return {}
    res = ha.call("recorder/statistics_during_period", start_time=start.isoformat(),
                  end_time=end.isoformat(), statistic_ids=ids, period="day",
                  types=["mean", "min", "max"]) or {}
    out = {}
    for sid, rows in res.items():
        means = [r["mean"] for r in rows if r.get("mean") is not None]
        mins = [r["min"] for r in rows if r.get("min") is not None]
        maxs = [r["max"] for r in rows if r.get("max") is not None]
        if means:
            out[sid] = (sum(means) / len(means), min(mins or means), max(maxs or means))
    return out


def build(app, ha=None, now=None):
    now = now or time.time()
    end = datetime.datetime.fromtimestamp(now).astimezone()
    start = end - datetime.timedelta(days=7)
    lines = [f"📊 {start.strftime(_('%d.%m.'))} – {end.strftime(_('%d.%m.%Y'))}"]
    ha = ha or HaWs()
    events = [e for e in app.events() if e[0] >= now - 7 * 86400]
    try:
        for w in app.workers:
            items = [it for it in w.items if w.settings.get(it["key"])["aktiv"]]
            lines.append(f"\n🐠 {w.name}")
            values = [it for it in items if it["kind"] == "value"]
            ids = [f"sensor.{w.uid}_{it['key']}" for it in values]
            power_id = f"sensor.{w.uid}_gesamt_leistung"
            try:
                st = stats(ha, ids + ([power_id] if w.has_power() else []), start, end)
            except HaError as e:
                log.info("Wochenbericht: Statistik nicht abfragbar: %s", e)
                st = {}
            for it, sid in zip(values, ids):
                unit = it.get("unit") or ""
                if sid in st:
                    mean, lo, hi = st[sid]
                    lines.append(f"• {it['label']}: Ø {_num(mean, unit)} ({_num(lo)} – {_num(hi)})")
                elif w.last_values.get(it["key"]) is not None:
                    lines.append(f"• {it['label']}: {_num(w.last_values[it['key']], unit)} ({_('noch kein Verlauf')})")
            if power_id in st:
                mean = st[power_id][0]
                lines.append("⚡ " + _("Gesamtleistung: Ø {w} W, ca. {kwh} kWh in 7 Tagen",
                                       w=_num(round(mean)), kwh=_num(round(mean * 168 / 1000, 1))))
            dosers = [it for it in items if it["kind"] == "doser"]
            if dosers:
                lines.append(_("Dosierer:"))
            for it in dosers:
                v = w.last_values
                parts = []
                if it.get("capacity") and v.get(it["key"]) is not None:
                    pct = v.get(f"{it['key']}_pct")
                    parts.append(f"{v[it['key']]:.0f} ml" + (f" ({pct:.0f} %)" if pct is not None else ""))
                if v.get(f"{it['key']}_verbrauch") is not None:
                    parts.append(_("{v} ml/Tag", v=_num(v[it["key"] + "_verbrauch"])))
                if v.get(f"{it['key']}_tage") is not None:
                    parts.append(_("reicht ca. {d} Tage", d=_num(round(v[it["key"] + "_tage"]))))
                if parts:
                    lines.append(f"• {it['label']}: " + ", ".join(parts))
            mine = [e for e in events if e[1] == w.name]
            if mine:
                count = {}
                for e in mine:
                    count[e[2]] = count.get(e[2], 0) + 1
                lines.append(_("Störungen/Hinweise: {n}", n=len(mine)))
                lines += [f"• {label}" + (f" ({n}×)" if n > 1 else "") for label, n in count.items()]
            else:
                lines.append("✅ " + _("Keine Störungen"))
    finally:
        ha.close()
    return "\n".join(lines)


def due(choice, last_sent, now=None):
    """Ist der Bericht jetzt fällig? choice = Wochentag oder "Aus"."""
    if choice not in DAYS:
        return False
    t = datetime.datetime.fromtimestamp(now or time.time())
    return t.weekday() == DAYS.index(choice) and t.hour >= HOUR and last_sent != t.strftime("%Y-%m-%d")
