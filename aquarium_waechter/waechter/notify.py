"""Benachrichtigung per Telegram (direkt, unabhängig vom HA-Core)."""

import json
import logging
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


class Notifier:
    def __init__(self, chat_ids, token=None):
        self.chat_ids = [c.strip() for c in str(chat_ids or "").split(",") if c.strip()]
        self.token = token or (telegram_token_from_ha() if self.chat_ids else None)
        if self.chat_ids and not self.token:
            log.error("Telegram: Chat-ID gesetzt, aber kein Token (Option telegram_token "
                      "oder Telegram-Integration in HA einrichten)")
        if not self.chat_ids:
            log.warning("Telegram aus: keine Chat-ID eingestellt – Meldungen nur im Log")

    def send(self, title, message, urgent=False, kind=None):
        """kind: None | "ok" – steuert nur das Symbol."""
        log.warning("%s: %s", title, message)
        if not (self.chat_ids and self.token):
            return
        icon = "🚨" if urgent else ("✅" if kind == "ok" else "ℹ️")
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
