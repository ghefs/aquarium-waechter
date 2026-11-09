"""Vorrat der Dosierbehälter: Verbrauch je Tag aus dem Füllstandsverlauf und
„reicht noch … Tage“.

Je Behälter werden höchstens alle 30 min Stichproben (Zeit, ml) gemerkt, bis zu
14 Tage. Steigt der Füllstand deutlich (nachgefüllt), beginnt die Messreihe neu –
bis wieder genug Daten da sind, gilt der zuletzt bekannte Verbrauch weiter.
Verbrauch = Abnahme über die Messreihe (höchstens 7 Tage, mindestens 24 h).
"""

import json
import logging
import threading
import time
from pathlib import Path

log = logging.getLogger("waechter")

SAMPLE_EVERY = 1800
KEEP = 14 * 86400
WINDOW = 7 * 86400
MIN_SPAN = 86400


class Forecast:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = {}  # "uid/key" -> {"s": [[t, ml], ...], "rate": ml/Tag oder None}
        self._saved = 0
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.error("Verbrauchsdaten nicht lesbar (%s) – beginne neu", e)

    def add(self, uid, key, ml, capacity, now=None):
        """Neuen Füllstand merken; -> (Verbrauch ml/Tag, reicht noch Tage) oder (None, None)."""
        now = now or time.time()
        with self.lock:
            d = self.data.setdefault(f"{uid}/{key}", {"s": [], "rate": None})
            s = d["s"]
            refill = max(20.0, 0.05 * (capacity or 0))
            if s and ml > s[-1][1] + refill:
                s.clear()  # nachgefüllt -> neue Messreihe, alter Verbrauch gilt weiter
            if not s or now - s[-1][0] >= SAMPLE_EVERY:
                s.append([now, ml])
            elif ml < s[-1][1]:
                s[-1][1] = ml  # innerhalb der halben Stunde: niedrigsten Stand behalten
            while s and now - s[0][0] > KEEP:
                s.pop(0)
            win = [p for p in s if now - p[0] <= WINDOW] or s[-1:]
            span = win[-1][0] - win[0][0]
            if span >= MIN_SPAN:
                d["rate"] = max(0.0, (win[0][1] - win[-1][1]) / span * 86400)
            rate = d["rate"]
        if now - self._saved > 600:
            self.save()
        if rate is None:
            return None, None
        if rate < 0.5:  # praktisch kein Verbrauch -> keine sinnvolle Prognose
            return round(rate, 1), None
        return round(rate, 1), round(ml / rate, 1)

    def energy(self, uid, watts, interval):
        """Energie aufsummieren (kWh, nur steigend). Lücken (Neustart, ProfiLux weg) zählen
        höchstens mit zwei Abfrageintervallen."""
        now = time.time()
        with self.lock:
            d = self.data.setdefault(f"{uid}/_energie", {"kwh": 0.0, "t": now})
            dt = min(now - d["t"], 2 * interval)
            d["kwh"] += max(0.0, watts) * max(0.0, dt) / 3600000
            d["t"] = now
            kwh = round(d["kwh"], 3)
        if now - self._saved > 600:
            self.save()
        return kwh

    def save(self):
        self._saved = time.time()
        with self.lock:
            text = json.dumps(self.data)
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.path)
        except OSError as e:
            log.error("Verbrauchsdaten nicht speicherbar: %s", e)
