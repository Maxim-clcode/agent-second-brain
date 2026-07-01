import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import holidays
import httpx as requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

VDK = ZoneInfo("Asia/Vladivostok")
RU_HOLIDAYS = holidays.Russia()
now = datetime.now(VDK)
today = now.date()

if today.weekday() >= 5 or today in RU_HOLIDAYS:
    print("Not a workday — skipping.")
    exit(0)

reply_markup = json.dumps({
    "inline_keyboard": [[
        {"text": "📊 Да, подводим итог", "callback_data": "summary_start"},
        {"text": "⏰ Позже", "callback_data": "plan_skip"},
    ]]
})

resp = requests.post(
    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
    json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "🌇 Рабочий день заканчивается!\n\nПодведём итог — что удалось сегодня?",
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
    },
)
print(resp.json())
