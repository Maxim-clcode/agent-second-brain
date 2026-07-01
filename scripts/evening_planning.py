import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
import httpx as requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

VDK = ZoneInfo("Asia/Vladivostok")
RU_HOLIDAYS = holidays.Russia()

DAY_NAMES = {0: "понедельник", 1: "вторник", 2: "среду", 3: "четверг", 4: "пятницу"}


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in RU_HOLIDAYS


def next_workday(from_date: date) -> date:
    d = from_date + timedelta(days=1)
    while not is_workday(d):
        d += timedelta(days=1)
    return d


now = datetime.now(VDK)
today = now.date()

# Don't run on weekends or holidays
if not is_workday(today):
    print("Not a workday — skipping.")
    exit(0)

next_day = next_workday(today)
day_label = DAY_NAMES[next_day.weekday()]

message = (
    f"🌆 Рабочий день заканчивается!\n\n"
    f"Готов набросать план на <b>{day_label}</b>?"
)

reply_markup = json.dumps({
    "inline_keyboard": [[
        {"text": f"📅 Да, планируем {day_label}!", "callback_data": f"plan_date:{next_day}"},
        {"text": "⏰ Позже", "callback_data": "plan_skip"},
    ]]
})

resp = requests.post(
    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
    json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
    },
)
print(resp.json())
