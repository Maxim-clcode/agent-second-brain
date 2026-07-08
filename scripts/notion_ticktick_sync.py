#!/usr/bin/env python3
"""TickTick ↔ Notion continuous sync — runs every 10 min via dbrain-sync.timer.

For each active Notion PM Backlog task with a "Дедлайн":
  - Outside current week                                → status = Бэклог
  - In current Mon–Fri week + task exists in TickTick  → date-sync TT→Notion, status = Спринт неделя
  - In current Mon–Fri week + task missing from TickTick:
      Only if TickTick API sanity check passes (returns ≥1 task on today) →
        find free slot, create in TT, status = Спринт неделя
      If sanity check fails (API unreliable) → skip creation, only set status

Duration: Простая = 30 min, Средняя = 60 min (default).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays
import httpx

NOTIFY_STATE_FILE = Path(__file__).parent.parent / "data" / "tt_notify_state.json"


def load_notify_state(today: date) -> tuple[set[str], set[str]]:
    """Return (notified_today, muted_pages).
    notified_today — page_ids already notified today (auto-cleared on new day).
    muted_pages    — page_ids to never notify about (persistent).
    """
    if NOTIFY_STATE_FILE.exists():
        raw = json.loads(NOTIFY_STATE_FILE.read_text())
        notified = set(raw.get("ids", [])) if raw.get("date") == today.isoformat() else set()
        muted = set(raw.get("muted", []))
        return notified, muted
    return set(), set()


def save_notify_state(today: date, notified: set[str], muted: set[str]) -> None:
    NOTIFY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NOTIFY_STATE_FILE.write_text(json.dumps({
        "date": today.isoformat(),
        "ids": sorted(notified),
        "muted": sorted(muted),
    }))

# ── Config ──────────────────────────────────────────────────────────────────

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "ntn_c57737162465ObprBMHdaXLJ5mPvVPO8T3Hyk0mmTUJ6Eh")
NOTION_DB = "22876284-e92f-4866-a908-3a3bda425637"
TICKTICK_TOKEN = os.environ.get("TICKTICK_TOKEN", "tp_370240d2191b485496c72cc7c5522326")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8945688412:AAFf5U8JtSScWVT_ex7u2T5M9Zvwj2dKZ8Y")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "257352741")

VL = ZoneInfo("Asia/Vladivostok")
UTC = ZoneInfo("UTC")
RU_HOLIDAYS = holidays.Russia()

DURATION_MIN: dict[str, int] = {"Простая": 30, "Средняя": 60}
DEFAULT_DURATION = 60
BUFFER_MIN = 15  # gap between tasks
WORK_BLOCKS = [(9 * 60, 13 * 60), (14 * 60, 18 * 60)]  # minutes from midnight, excl. lunch

NOTION_HDRS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}
TT_HDRS = {
    "Authorization": f"Bearer {TICKTICK_TOKEN}",
    "Content-Type": "application/json",
}

# ── Date helpers ─────────────────────────────────────────────────────────────

def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in RU_HOLIDAYS


def current_week(today: date) -> tuple[date, date]:
    ws = today - timedelta(days=today.weekday())
    return ws, ws + timedelta(days=4)


def in_current_week(d: date, today: date) -> bool:
    ws, we = current_week(today)
    return ws <= d <= we

# ── Notion ───────────────────────────────────────────────────────────────────

def notion_get_all_active() -> list[dict]:
    results, cursor = [], None
    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = httpx.post(
            f"https://api.notion.com/v1/databases/{NOTION_DB}/query",
            headers=NOTION_HDRS, json=body, timeout=30,
        )
        data = r.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


def parse_task(page: dict) -> dict | None:
    props = page.get("properties", {})

    title_arr = props.get("Задача", {}).get("title", [])
    title = title_arr[0]["text"]["content"].strip() if title_arr else ""
    if not title:
        return None

    status = (props.get("Статус", {}).get("select") or {}).get("name", "")
    if status in ("Выполнено", "Отменено"):
        return None

    difficulty = (props.get("Сложность", {}).get("select") or {}).get("name", "")
    duration = DURATION_MIN.get(difficulty, DEFAULT_DURATION)

    date_prop = (props.get("Дедлайн", {}).get("date") or {})
    deadline = None
    raw = date_prop.get("start", "")
    if raw:
        try:
            deadline = date.fromisoformat(raw[:10])
        except ValueError:
            pass

    return {"id": page["id"], "title": title, "status": status, "deadline": deadline, "duration": duration}


def notion_set_status(page_id: str, status: str) -> bool:
    r = httpx.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HDRS,
        json={"properties": {"Статус": {"select": {"name": status}}}},
        timeout=15,
    )
    return r.status_code == 200


def notion_set_deadline(page_id: str, d: date) -> bool:
    r = httpx.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HDRS,
        json={"properties": {"Дедлайн": {"date": {"start": d.isoformat()}}}},
        timeout=15,
    )
    return r.status_code == 200

# ── TickTick ─────────────────────────────────────────────────────────────────

_tt_day_cache: dict[date, list[dict]] = {}
_tt_api_reliable: bool | None = None  # None = not checked yet


def _tt_fetch_day(d: date) -> list[dict]:
    s = datetime(d.year, d.month, d.day, 0, 0, tzinfo=VL).astimezone(UTC)
    e = datetime(d.year, d.month, d.day, 23, 59, tzinfo=VL).astimezone(UTC)
    try:
        r = httpx.post(
            "https://api.ticktick.com/open/v1/task/undone",
            headers=TT_HDRS,
            json={"startDate": s.isoformat(), "endDate": e.isoformat(), "timeZone": "Asia/Vladivostok"},
            timeout=20,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        return data if isinstance(data, list) else data.get("items", data.get("data", []))
    except Exception as exc:
        print(f"  TT fetch error {d}: {exc}")
        return []


def tt_api_sanity_check(today: date) -> bool:
    """Return True only if TickTick API is returning real data.

    Checks the past 7 workdays: if at least one day returned ≥1 task,
    the API is working. If every day returns 0, assume the endpoint is
    broken and skip all task-creation logic.
    """
    global _tt_api_reliable
    if _tt_api_reliable is not None:
        return _tt_api_reliable

    d = today
    for _ in range(10):
        if is_workday(d):
            tasks = _tt_fetch_day(d)
            if tasks:
                print(f"  TT API OK: {len(tasks)} tasks on {d}")
                _tt_api_reliable = True
                return True
        d -= timedelta(days=1)

    print("  ⚠️  TT API unreliable — returned 0 tasks across last 10 days. Skipping task creation.")
    _tt_api_reliable = False
    return False


def tt_day_tasks(d: date) -> list[dict]:
    if d in _tt_day_cache:
        return _tt_day_cache[d]
    items = _tt_fetch_day(d)
    _tt_day_cache[d] = items
    return items


def tt_parse_local_date(task: dict) -> date | None:
    raw = task.get("startDate") or task.get("dueDate")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(VL).date()
    except Exception:
        return None


def tt_parse_times(task: dict) -> tuple[time, time] | None:
    try:
        s = datetime.fromisoformat(task["startDate"].replace("Z", "+00:00")).astimezone(VL).time()
        e = datetime.fromisoformat(task["dueDate"].replace("Z", "+00:00")).astimezone(VL).time()
        return s, e
    except Exception:
        return None


def title_match(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    if a == b or a in b or b in a:
        return True
    aw, bw = set(a.split()), set(b.split())
    return len(aw) >= 3 and len(aw & bw) / len(aw) >= 0.7


def tt_find_on_day(notion_title: str, d: date) -> dict | None:
    for t in tt_day_tasks(d):
        if title_match(notion_title, t.get("title", "")):
            return t
    return None


def find_free_slot(d: date, duration_min: int) -> tuple[time, time] | None:
    """Find first free slot of duration_min on date d using cached day tasks."""
    tasks = tt_day_tasks(d)

    # Busy intervals in minutes from midnight (VL)
    busy: list[tuple[int, int]] = []
    for t in tasks:
        times = tt_parse_times(t)
        if times:
            s_min = times[0].hour * 60 + times[0].minute
            e_min = times[1].hour * 60 + times[1].minute
            busy.append((s_min, e_min))
    busy.sort()

    def to_time(m: int) -> time:
        return time(m // 60, m % 60)

    for block_start, block_end in WORK_BLOCKS:
        cursor = block_start
        for bs, be in busy:
            if be <= block_start or bs >= block_end:
                continue  # outside this block
            if bs - cursor >= duration_min + BUFFER_MIN:
                return to_time(cursor), to_time(cursor + duration_min)
            cursor = max(cursor, be + BUFFER_MIN)
        if block_end - cursor >= duration_min:
            return to_time(cursor), to_time(cursor + duration_min)

    return None


def tt_create_task(title: str, d: date, start: time, end: time) -> bool:
    s_dt = datetime(d.year, d.month, d.day, start.hour, start.minute, tzinfo=VL)
    e_dt = datetime(d.year, d.month, d.day, end.hour, end.minute, tzinfo=VL)
    try:
        r = httpx.post(
            "https://api.ticktick.com/open/v1/task",
            headers=TT_HDRS,
            json={
                "title": title,
                "startDate": s_dt.isoformat(),
                "dueDate": e_dt.isoformat(),
                "timeZone": "Asia/Vladivostok",
                "isAllDay": False,
            },
            timeout=15,
        )
        return r.status_code in (200, 201)
    except Exception as exc:
        print(f"  TT create error: {exc}")
        return False

# ── Telegram ─────────────────────────────────────────────────────────────────

def telegram_notify(text: str) -> None:
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as exc:
        print(f"  Telegram error: {exc}")

# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    now_vl = datetime.now(VL)
    today = now_vl.date()
    ws, we = current_week(today)
    notified_today, muted_pages = load_notify_state(today)

    print(f"=== notion_ticktick_sync {now_vl.strftime('%Y-%m-%d %H:%M')} VL | week {ws}–{we} ===")

    pages = notion_get_all_active()
    tasks = [parse_task(p) for p in pages]
    tasks = [t for t in tasks if t]
    print(f"Active tasks: {len(tasks)}")

    for task in tasks:
        title = task["title"]
        deadline: date | None = task["deadline"]
        status = task["status"]
        duration = task["duration"]
        page_id = task["id"]
        short = title[:45]

        if not deadline:
            continue

        if not in_current_week(deadline, today):
            # Outside current week → demote if needed
            if status == "Спринт неделя":
                if notion_set_status(page_id, "Бэклог"):
                    print(f"  ↓ Бэклог (out of week): {short}")
            continue

        # In current week → look for TickTick task
        tt = tt_find_on_day(title, deadline)

        if tt:
            # Sync date TickTick → Notion if shifted
            tt_date = tt_parse_local_date(tt)
            if tt_date and tt_date != deadline:
                new_in_week = in_current_week(tt_date, today)
                notion_set_deadline(page_id, tt_date)
                target = "Спринт неделя" if new_in_week else "Бэклог"
                notion_set_status(page_id, target)
                arrow = "↑" if target == "Спринт неделя" else "↓"
                print(f"  📅→Notion {deadline}→{tt_date} {arrow}{target}: {short}")
                deadline = tt_date
            elif status != "Спринт неделя":
                notion_set_status(page_id, "Спринт неделя")
                print(f"  ↑ Спринт неделя (exists in TT): {short}")
        else:
            # Not found in TickTick.
            if deadline < today:
                # Past days: skip silently, just keep status correct
                if status != "Спринт неделя":
                    notion_set_status(page_id, "Спринт неделя")
                continue

            if deadline == today:
                # Today: can't tell "never added" from "was closed by user".
                # Notify once per day, let user decide manually.
                if status != "Спринт неделя":
                    notion_set_status(page_id, "Спринт неделя")
                if page_id not in notified_today and page_id not in muted_pages:
                    telegram_notify(
                        f"📋 <b>Задача не в TickTick</b>\n"
                        f"«{title}» есть в Notion на сегодня, но не найдена в TickTick.\n"
                        f"Добавь вручную если нужно."
                    )
                    notified_today.add(page_id)
                    print(f"  📲 Уведомление отправлено (today, not in TT): {short}")
                continue

            # Future date: auto-create if API is reliable
            if not tt_api_sanity_check(today):
                if status != "Спринт неделя":
                    notion_set_status(page_id, "Спринт неделя")
                    print(f"  ↑ Спринт неделя (TT API unreliable, no create): {short}")
                continue

            slot = find_free_slot(deadline, duration)
            if slot:
                s_t, e_t = slot
                ok = tt_create_task(title, deadline, s_t, e_t)
                if ok:
                    _tt_day_cache.pop(deadline, None)  # invalidate cache
                    if status != "Спринт неделя":
                        notion_set_status(page_id, "Спринт неделя")
                    print(f"  ➕ TT created {deadline} {s_t.strftime('%H:%M')}–{e_t.strftime('%H:%M')}: {short}")
                else:
                    print(f"  ❌ TT create failed: {short}")
            else:
                if page_id not in notified_today and page_id not in muted_pages:
                    telegram_notify(
                        f"⚠️ <b>Конфликт расписания</b>\n"
                        f"«{title}» запланирована на {deadline.strftime('%d.%m')} ({duration} мин), "
                        f"но свободного слота нет.\n"
                        f"Скорректируй вручную."
                    )
                    notified_today.add(page_id)
                print(f"  ⚠️ No free slot: {short} ({deadline})")

    save_notify_state(today, notified_today, muted_pages)
    print("=== Done ===")


if __name__ == "__main__":
    main()
