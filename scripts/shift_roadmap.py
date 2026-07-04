#!/usr/bin/env python3
"""Cascade-shift roadmap tasks when a task is overdue.

Runs daily at 09:00 VL (alongside sync_sprint).
Logic:
  - Find the first task whose scheduled date < today AND Notion status != Выполнено
  - Calculate delay = workdays between that task's date and today
  - Shift ALL incomplete tasks starting from that date forward by delay workdays
  - Update TickTick, Notion statuses, roadmap JSON, pinned message
  - Notify Maksim via Telegram
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays
import httpx

NOTION_TOKEN = "ntn_c57737162465ObprBMHdaXLJ5mPvVPO8T3Hyk0mmTUJ6Eh"
NOTION_PM_BACKLOG = "22876284-e92f-4866-a908-3a3bda425637"
TICKTICK_TOKEN = "tp_370240d2191b485496c72cc7c5522326"
TELEGRAM_TOKEN = "8945688412:AAFf5U8JtSScWVT_ex7u2T5M9Zvwj2dKZ8Y"
TELEGRAM_CHAT_ID = "257352741"
ROADMAPS_DIR = Path(__file__).parent.parent / "data" / "roadmaps"
VL_TZ = ZoneInfo("Asia/Vladivostok")
RU_HOLIDAYS = holidays.Russia()


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in RU_HOLIDAYS


def add_workdays(d: date, n: int) -> date:
    """Add n workdays to date d, skipping weekends and RU holidays."""
    current = d
    added = 0
    while added < n:
        current += timedelta(days=1)
        if is_workday(current):
            added += 1
    return current


def workdays_between(start: date, end: date) -> int:
    """Count workdays from start (exclusive) to end (inclusive)."""
    count = 0
    d = start
    while d < end:
        d += timedelta(days=1)
        if is_workday(d):
            count += 1
    return count


def notion_get_status(page_id: str) -> str:
    r = httpx.get(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28"},
        timeout=15,
    )
    props = r.json().get("properties", {})
    return (props.get("Статус", {}).get("select") or {}).get("name", "")


def notion_update_status(page_id: str, status: str) -> bool:
    r = httpx.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={"properties": {"Статус": {"select": {"name": status}}}},
        timeout=15,
    )
    return r.status_code == 200


def ticktick_find_task(title: str, search_date: date) -> dict | None:
    """Search TickTick tasks around search_date (±7 days) by title."""
    start = search_date - timedelta(days=7)
    end = search_date + timedelta(days=30)
    try:
        r = httpx.post(
            "https://api.ticktick.com/open/v1/task/undone",
            headers={"Authorization": f"Bearer {TICKTICK_TOKEN}", "Content-Type": "application/json"},
            json={
                "startDate": datetime(start.year, start.month, start.day, tzinfo=ZoneInfo("UTC")).isoformat(),
                "endDate": datetime(end.year, end.month, end.day, 23, 59, tzinfo=ZoneInfo("UTC")).isoformat(),
                "timeZone": "Asia/Vladivostok",
            },
            timeout=20,
        )
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        title_l = title.lower().strip()
        for task in items:
            tt = task.get("title", "").lower().strip()
            if tt == title_l or title_l in tt or tt in title_l:
                return task
    except Exception as e:
        print(f"  TickTick search error: {e}")
    return None


def ticktick_update_task(task_id: str, project_id: str, new_date: date, start_time: str, end_time: str) -> bool:
    """Update task date in TickTick by task_id."""
    vl = ZoneInfo("Asia/Vladivostok")
    sh, sm = map(int, start_time.split(":"))
    eh, em = map(int, end_time.split(":"))
    start_dt = datetime(new_date.year, new_date.month, new_date.day, sh, sm, tzinfo=vl)
    end_dt = datetime(new_date.year, new_date.month, new_date.day, eh, em, tzinfo=vl)

    body: dict = {
        "id": task_id,
        "startDate": start_dt.isoformat(),
        "dueDate": end_dt.isoformat(),
        "isAllDay": False,
        "timeZone": "Asia/Vladivostok",
    }
    if project_id:
        body["projectId"] = project_id

    try:
        r = httpx.post(
            f"https://api.ticktick.com/open/v1/task/{task_id}",
            headers={"Authorization": f"Bearer {TICKTICK_TOKEN}", "Content-Type": "application/json"},
            json=body,
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  TickTick update error: {e}")
        return False


def ticktick_update_by_stored_id(ticktick_id: str, new_date: date, start_time: str, end_time: str) -> bool:
    """Update TickTick task using stored ID from roadmap JSON — no search needed."""
    return ticktick_update_task(ticktick_id, "", new_date, start_time, end_time)


def send_telegram(text: str) -> None:
    try:
        httpx.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as e:
        print(f"  Telegram send error: {e}")


def update_roadmap_pin() -> None:
    """Call update_roadmap_pin.py to refresh pinned messages."""
    import subprocess
    script = Path(__file__).parent / "update_roadmap_pin.py"
    venv_python = Path(__file__).parent.parent / ".venv" / "bin" / "python"
    try:
        subprocess.run(
            [str(venv_python), str(script)],
            env={
                "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
                "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
                "PATH": "/usr/bin:/bin",
            },
            timeout=60,
        )
    except Exception as e:
        print(f"  update_roadmap_pin error: {e}")


def current_week(today: date) -> tuple[date, date]:
    week_start = today - timedelta(days=today.weekday())
    return week_start, week_start + timedelta(days=4)


def process_roadmap(path: Path, today: date) -> bool:
    """Check roadmap for overdue tasks and cascade-shift if needed. Returns True if shifted."""
    roadmap = json.loads(path.read_text())
    project = roadmap["project"]
    tasks = roadmap["tasks"]

    # Check statuses from Notion for incomplete tasks
    overdue_date: date | None = None
    for task in tasks:
        try:
            task_date = date.fromisoformat(task["date"])
        except Exception:
            continue
        if task_date >= today:
            continue
        notion_id = task.get("notion_id")
        if not notion_id:
            continue
        status = notion_get_status(notion_id)
        if status in ("Выполнено", "Отменено"):
            continue
        # This task is overdue and not done
        if overdue_date is None or task_date < overdue_date:
            overdue_date = task_date

    if overdue_date is None:
        print(f"  [{project}] Всё в порядке — просрочен задач нет.")
        return False

    delay = workdays_between(overdue_date, today)
    if delay == 0:
        return False

    print(f"  [{project}] Просрочка от {overdue_date}, сдвиг: {delay} рабочих дн.")

    # Shift all incomplete tasks from overdue_date forward
    shifted_tasks = []
    for task in tasks:
        try:
            task_date = date.fromisoformat(task["date"])
        except Exception:
            shifted_tasks.append(task)
            continue

        notion_id = task.get("notion_id")
        status = notion_get_status(notion_id) if notion_id else "Бэклог"

        if task_date >= overdue_date and status not in ("Выполнено", "Отменено"):
            new_date = add_workdays(task_date, delay)
            task = dict(task)
            task["date"] = new_date.isoformat()

            # Update TickTick — prefer stored ticktick_id, fall back to name search
            stored_id = task.get("ticktick_id", "")
            if stored_id:
                ok = ticktick_update_by_stored_id(
                    stored_id, new_date,
                    task.get("start", "09:00"),
                    task.get("end", "10:00"),
                )
                print(f"    TickTick(id) {'✅' if ok else '❌'}: {task['name'][:50]} → {new_date}")
            else:
                tt_task = ticktick_find_task(task["name"], task_date)
                if tt_task:
                    ok = ticktick_update_task(
                        tt_task["id"],
                        tt_task.get("projectId", ""),
                        new_date,
                        task.get("start", "09:00"),
                        task.get("end", "10:00"),
                    )
                    print(f"    TickTick(search) {'✅' if ok else '❌'}: {task['name'][:50]} → {new_date}")
                    if ok:
                        task["ticktick_id"] = tt_task["id"]  # store for next time
                else:
                    print(f"    TickTick not found: {task['name'][:50]}")

            # Update Notion status if needed (re-evaluate week)
            if notion_id:
                week_start, week_end = current_week(today)
                if new_date <= week_end:
                    if status == "Бэклог":
                        notion_update_status(notion_id, "Спринт неделя")
                elif status == "Спринт неделя":
                    notion_update_status(notion_id, "Бэклог")

        shifted_tasks.append(task)

    # Shift milestones too
    milestones = roadmap.get("milestones", [])
    new_milestones = []
    for ms in milestones:
        try:
            ms_date = date.fromisoformat(ms["date"])
        except Exception:
            new_milestones.append(ms)
            continue
        if ms_date >= overdue_date:
            ms = dict(ms)
            ms["date"] = add_workdays(ms_date, delay).isoformat()
        new_milestones.append(ms)

    # Update finish_date
    finish_date = roadmap.get("finish_date")
    if finish_date:
        try:
            fd = date.fromisoformat(finish_date)
            if fd >= overdue_date:
                roadmap["finish_date"] = add_workdays(fd, delay).isoformat()
        except Exception:
            pass

    roadmap["tasks"] = shifted_tasks
    roadmap["milestones"] = new_milestones
    path.write_text(json.dumps(roadmap, ensure_ascii=False, indent=2))

    # Notify
    new_finish = roadmap.get("finish_date", "?")
    send_telegram(
        f"⚠️ <b>Дорожная карта сдвинулась</b>\n"
        f"📋 {project}\n\n"
        f"Задача с {overdue_date.strftime('%d.%m')} не выполнена → "
        f"все последующие задачи сдвинуты на <b>{delay} рабочих дн.</b>\n"
        f"📅 Новый финиш: ~{new_finish}"
    )
    return True


def force_shift_roadmap(path: Path, days: int, from_date: date | None = None) -> None:
    """Manually shift all incomplete tasks by N workdays from from_date (default: today)."""
    roadmap = json.loads(path.read_text())
    project = roadmap["project"]
    tasks = roadmap["tasks"]
    today = datetime.now(VL_TZ).date()
    anchor = from_date or today

    print(f"  [{project}] Force-shift {days} workdays from {anchor}")

    shifted_tasks = []
    for task in tasks:
        try:
            task_date = date.fromisoformat(task["date"])
        except Exception:
            shifted_tasks.append(task)
            continue

        notion_id = task.get("notion_id")
        status = notion_get_status(notion_id) if notion_id else "Бэклог"

        if task_date >= anchor and status not in ("Выполнено", "Отменено"):
            new_date = add_workdays(task_date, days)
            task = dict(task)
            task["date"] = new_date.isoformat()

            # Update TickTick — prefer stored ticktick_id, fall back to name search
            stored_id = task.get("ticktick_id", "")
            if stored_id:
                ok = ticktick_update_by_stored_id(
                    stored_id, new_date,
                    task.get("start", "09:00"),
                    task.get("end", "10:00"),
                )
                print(f"    TickTick(id) {'✅' if ok else '❌'}: {task['name'][:50]} → {new_date}")
            else:
                tt_task = ticktick_find_task(task["name"], task_date)
                if tt_task:
                    ok = ticktick_update_task(
                        tt_task["id"],
                        tt_task.get("projectId", ""),
                        new_date,
                        task.get("start", "09:00"),
                        task.get("end", "10:00"),
                    )
                    print(f"    TickTick(search) {'✅' if ok else '❌'}: {task['name'][:50]} → {new_date}")
                    if ok:
                        task["ticktick_id"] = tt_task["id"]  # store for next time
                else:
                    print(f"    TickTick not found: {task['name'][:50]}")

            # Update Notion status
            if notion_id:
                week_start, week_end = current_week(today)
                if new_date <= week_end and status == "Бэклог":
                    notion_update_status(notion_id, "Спринт неделя")
                elif new_date > week_end and status == "Спринт неделя":
                    notion_update_status(notion_id, "Бэклог")

        shifted_tasks.append(task)

    milestones = roadmap.get("milestones", [])
    new_milestones = []
    for ms in milestones:
        try:
            ms_date = date.fromisoformat(ms["date"])
        except Exception:
            new_milestones.append(ms)
            continue
        if ms_date >= anchor:
            ms = dict(ms)
            ms["date"] = add_workdays(ms_date, days).isoformat()
        new_milestones.append(ms)

    finish_date = roadmap.get("finish_date")
    if finish_date:
        try:
            fd = date.fromisoformat(finish_date)
            if fd >= anchor:
                roadmap["finish_date"] = add_workdays(fd, days).isoformat()
        except Exception:
            pass

    roadmap["tasks"] = shifted_tasks
    roadmap["milestones"] = new_milestones
    path.write_text(json.dumps(roadmap, ensure_ascii=False, indent=2))

    send_telegram(
        f"📅 <b>Дорожная карта перестроена</b>\n"
        f"📋 {project}\n\n"
        f"Все задачи с {anchor.strftime('%d.%m')} сдвинуты на <b>{days} рабочих дн.</b>\n"
        f"📅 Новый финиш: ~{roadmap.get('finish_date', '?')}"
    )


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="Slug имени файла дорожной карты (без .json)")
    parser.add_argument("--shift", type=int, help="Принудительный сдвиг на N рабочих дней")
    parser.add_argument("--from-date", help="Дата начала сдвига YYYY-MM-DD (по умолчанию — сегодня)")
    args = parser.parse_args()

    now_vl = datetime.now(VL_TZ)
    today = now_vl.date()

    if not ROADMAPS_DIR.exists():
        print("No roadmaps directory.")
        return

    from_date = date.fromisoformat(args.from_date) if args.from_date else None

    # Manual forced shift mode
    if args.shift is not None:
        paths = [ROADMAPS_DIR / f"{args.project}.json"] if args.project else list(ROADMAPS_DIR.glob("*.json"))
        for path in paths:
            if path.exists():
                print(f"\nForce-shifting: {path.name}")
                try:
                    force_shift_roadmap(path, args.shift, from_date)
                except Exception as e:
                    print(f"  Error: {e}")
        print("\nUpdating pinned messages...")
        update_roadmap_pin()
        return

    # Auto mode: check for overdue tasks
    if not is_workday(today):
        print(f"Not a workday ({today}) — skipping.")
        return

    any_shifted = False
    paths = [ROADMAPS_DIR / f"{args.project}.json"] if args.project else list(ROADMAPS_DIR.glob("*.json"))
    for path in paths:
        if not path.exists():
            continue
        print(f"\nChecking: {path.name}")
        try:
            shifted = process_roadmap(path, today)
            if shifted:
                any_shifted = True
        except Exception as e:
            print(f"  Error: {e}")

    if any_shifted:
        print("\nUpdating pinned messages...")
        update_roadmap_pin()


if __name__ == "__main__":
    main()
