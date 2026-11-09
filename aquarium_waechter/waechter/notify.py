"""Benachrichtigung per Telegram (direkt, unabhängig vom HA-Core) und per Push an die
Home-Assistant-App (notify.mobile_app_* über die Supervisor-API)."""

import json
import logging
import os
import re
import urllib.parse
import urllib.request

log = logging.getLogger("waechter")


def telegram_token_from_ha(path="/homeassistant/.storage/core.config_entries"):
    """Token aus der Telegram-Integration von HA lesen (Add-on mit
    homeassistant_config:ro) – so muss er nur an einer Stelle gepflegt werden."""
    try:
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)["data"]["entries"]
        for e in entries:
            if e.get("domain") == "telegram_bot" and not e.get("disabled_by"):
                d = e.get("data", {})
                token = d.get("api_key") or d.get("token")
                if token:
                    log.info("Telegram-Token aus HA-Integration '%s' übernommen", e.get("title"))
                    return token
    except (OSError, ValueError, KeyError) as e:
        log.info("Telegram-Token aus HA nicht lesbar: %s", e)
    return None


HA_API = "http://supervisor/core/api"


def _ha(method, path, body=None):
    req = urllib.request.Request(
        HA_API + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ.get('SUPERVISOR_TOKEN', '')}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def ha_language(tries=24):
    """Sprache von Home Assistant (z. B. "de", "en-GB" -> "en"); None, wenn nicht abfragbar."""
    import time
    for attempt in range(tries):  # beim Hochfahren startet das Add-on oft vor Home Assistant
        try:
            return (_ha("GET", "/config") or {}).get("language")
        except Exception as e:
            if attempt == tries - 1:
                log.info("Sprache von Home Assistant nicht abfragbar: %s", e)
            else:
                time.sleep(5)
    return None


def app_devices():
    """Alle Handys mit HA-App (Dienste notify.mobile_app_*)."""
    try:
        for d in _ha("GET", "/services"):
            if d.get("domain") == "notify":
                return sorted(s for s in d.get("services", {}) if s.startswith("mobile_app_"))
    except Exception as e:
        log.info("HA-App-Geräte nicht abfragbar: %s", e)
    return []


def push_data(urgent, kind):
    """Störung = kritische Benachrichtigung (klingelt trotz Stumm/Nicht stören),
    Wieder OK = normal, Hinweis = leise."""
    if urgent:
        return {"push": {"sound": {"name": "default", "critical": 1, "volume": 1.0},
                         "interruption-level": "critical"},
                "ttl": 0, "priority": "high", "channel": "alarm_stream",
                "importance": "high", "tag": None}
    if kind == "ok":
        return {"push": {"interruption-level": "active"}, "ttl": 0, "priority": "high"}
    return {"push": {"interruption-level": "passive"}, "channel": "Aquarium-Hinweise"}


def phone_name(service):
    """mobile_app_iphone_von_max -> iphone von max"""
    return service.removeprefix("mobile_app_").replace("_", " ")


class Notifier:
    def __init__(self, chat_ids, token=None, app_push=None, store=None):
        self.chat_ids = [c.strip() for c in str(chat_ids or "").split(",") if c.strip()]
        # tolerant: Komma/Leerzeichen, "notify."-Präfix, versehentlich mitkopiertes "app_push:"
        self.store = store
        self.preset = [w.removeprefix("notify.") for w in re.split(r"[,;\s]+", str(app_push or ""))
                       if w and w.rstrip(":") != "app_push"]
        self.token = token or (telegram_token_from_ha() if self.chat_ids else None)
        if self.chat_ids and not self.token:
            log.error("Telegram: Chat-ID gesetzt, aber kein Token (Option telegram_token "
                      "oder Telegram-Integration in HA einrichten)")
        self.phones = []
        self.refresh_phones()
        if not self.chat_ids and not self.preset and not self.phones:
            log.warning("Keine Benachrichtigung eingestellt (telegram_chat_id / app_push) – Meldungen nur im Log")

    def refresh_phones(self):
        """Handys mit HA-App suchen. Ein-/Ausschalten je Handy in HA (Schalter
        „Push an …“); die Option app_push gibt nur die Vorgabe."""
        found = app_devices()
        for a in self.preset:
            if found and a not in found:
                log.error("app_push: '%s' gibt es nicht – vorhanden: %s", a, ", ".join(found))
        self.phones = found or [a for a in self.preset]
        if self.phones:
            log.info("Handys mit HA-App: %s – Push an: %s", ", ".join(self.phones),
                     ", ".join(self.apps) or "keins (in HA unter „Push an …“ einschalten)")
        return self.phones

    def push_on(self, phone):
        v = self.store.push(phone) if self.store else None
        return (phone in self.preset) if v is None else v

    @property
    def apps(self):
        return [p for p in self.phones if self.push_on(p)]

    def send(self, title, message, urgent=False, kind=None):
        """kind: None | "ok" – steuert Symbol und Dringlichkeit."""
        log.warning("%s: %s", title, message)
        icon = "🚨" if urgent else ("✅" if kind == "ok" else "ℹ️")
        for app in self.apps:
            data = push_data(urgent, kind)
            data = {k: v for k, v in data.items() if v is not None}
            data["group"] = "aquarium"
            try:
                _ha("POST", f"/services/notify/{app}",
                    {"title": f"{icon} {title}", "message": message, "data": data})
            except Exception as e:
                log.error("Push an %s fehlgeschlagen: %s", app, e)
        if not (self.chat_ids and self.token):
            return
        for chat in self.chat_ids:
            try:
                body = urllib.parse.urlencode({
                    "chat_id": chat,
                    "text": f"{icon} {title}\n{message}",
                    "disable_notification": "false" if urgent or kind == "ok" else "true",
                }).encode()
                url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                urllib.request.urlopen(url, data=body, timeout=15).read()
            except Exception as e:
                log.error("Telegram an %s fehlgeschlagen: %s", chat, str(e).replace(self.token, "***"))
