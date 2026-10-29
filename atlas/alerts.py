"""
Alertas: Telegram (resumen diario, operaciones, errores, pausas) y healthchecks.io (latido de la corrida diaria).

Si faltan las variables de entorno no se envía nada (se deja en `events`). Nunca se escriben tokens en logs:
los errores de red se reportan sin la URL.
"""
from __future__ import annotations

import logging

import requests

from atlas.config import secret

log = logging.getLogger("atlas.alerts")


class Alerter:
    def __init__(self, store=None, telegram: bool = True, healthchecks: bool = True, post=requests.post,
                 get=requests.get):
        self.store, self.telegram, self.healthchecks = store, telegram, healthchecks
        self._post, self._get = post, get

    def send(self, text: str, level: str = "info") -> bool:
        if self.store is not None:
            self.store.log_event(level, "alert", text)
        token, chat = secret("TELEGRAM_BOT_TOKEN"), secret("TELEGRAM_CHAT_ID")
        if not (self.telegram and token and chat):
            return False
        try:
            r = self._post(f"https://api.telegram.org/bot{token}/sendMessage",
                           json={"chat_id": chat, "text": f"[Atlas] {text}"[:4000]}, timeout=10)
            return r.status_code == 200
        except requests.RequestException as e:
            log.warning("telegram falló: %s", type(e).__name__)
            return False

    def ping(self, ok: bool = True, body: str = "") -> bool:
        url = secret("HEALTHCHECKS_URL")
        if not (self.healthchecks and url):
            return False
        try:
            r = self._post(url.rstrip("/") + ("" if ok else "/fail"), data=body[:10_000].encode(), timeout=10)
            return r.status_code == 200
        except requests.RequestException as e:
            log.warning("healthchecks falló: %s", type(e).__name__)
            return False
