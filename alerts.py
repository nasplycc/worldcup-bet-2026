from __future__ import annotations

import os
from typing import Any


def send_telegram(text: str, config: dict[str, Any]) -> bool:
    alerts = config.get("alerts", {})
    if not alerts.get("telegram_enabled"):
        return False

    token = os.getenv(alerts.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
    chat_id = os.getenv(alerts.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID"), "")
    if not token or not chat_id:
        return False

    try:
        import requests

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True},
            timeout=20,
        )
        return resp.ok
    except Exception:
        return False
