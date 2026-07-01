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

if today.weekday() != 4 or today in RU_HOLIDAYS:
    print("Not Friday or holiday — skipping.")
    exit(0)

reply_markup = json.dumps({
    "inline_keyboard": [[
        {"text": "📋 Да, подводим итог недели", "callback_data": "weekly_start"},
        {"text": "⏰ Позже", "callback_data": "plan_skip"},
    ]]
})

resp = requests.post(
    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
    json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "📋 <b>Конец рабочей недели!</b>\n\nПодведём итоги — посмотрим что сделано, как дела у сотрудников, и что можно улучшить?",
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
    },
)
print(resp.json())
