"""Опциональная отправка отчёта в Telegram. Сама по себе ничего не шлёт."""

from __future__ import annotations

import os
from typing import Optional

import requests

API = "https://api.telegram.org/bot{token}/sendMessage"


def send_message(text: str, token: Optional[str] = None, chat_id: Optional[str] = None) -> dict:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError(
            "Не заданы TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID. "
            "Укажите их в переменных окружения или флагами --telegram-token / --telegram-chat-id."
        )
    resp = requests.post(
        API.format(token=token),
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Telegram API HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API: {data}")
    return data
