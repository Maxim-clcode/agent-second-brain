#!/usr/bin/env python3
"""Platrum → TickTick + Notion sync.

Runs every 5 min via systemd timer.
Finds new tasks where Максим is responsible + has a deadline,
creates them in TickTick (tag: platrum) and Notion (Источник: Platrum),
then sends a Telegram notification.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx as requests

PLATRUM_HOST = "atlantdv.platrum.ru"
PLATRUM_API_KEY = "DDF706F6-50D1-30BD47722A6D2CA91C0205D836"
MAXIM_ID = "3808bfa161526643ad65fbf82bfd3dfc"

TICKTICK_TOKEN = "tp_370240d2191b485496c72cc7c5522326"
TICKTICK_BASE = "https://api.ticktick.com/open/v1"

NOTION_TOKEN = "ntn_c57737162465ObprBMHdaXLJ5mPvVPO8T3Hyk0mmTUJ6Eh"
NOTION_PM_BACKLOG = "22876284-e92f-4866-a908-3a3bda425637"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

VL_TZ = ZoneInfo("Asia/Vladivostok")
STATE_FILE = Path(__file__).parent.parent / "data" / "platrum_seen.json"


def load_state() -> tuple[set, dict]:
    """Return (seen_ids, partial_ids).
    seen   = both TT+Notion confirmed
    partial = {task_id: {"tt_done": bool, "attempts": int}}
    """
    if STATE_FILE.exists():
        raw = json.loads(STATE_FILE.read_text())
        if isinstance(raw, list):
            # Legacy format: plain list of IDs — migrate
            return set(raw), {}
        return set(raw.get("seen", [])), raw.get("partial", {})
    return set(), {}


def save_state(seen: set, partial: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"seen": sorted(seen), "partial": partial}, indent=2))


# Legacy helpers kept for callers
def load_seen() -> set:
    seen, _ = load_state()
    return seen


def save_seen(seen: set) -> None:
    _, partial = load_state()
    save_state(seen, partial)


def _with_retry(fn, *args, retries: int = 3, delay: float = 5.0, **kwargs) -> bool:
    for attempt in range(retries):
        try:
            if fn(*args, **kwargs):
                return True
        except Exception as exc:
            print(f"  attempt {attempt + 1} error: {exc}")
        if attempt < retries - 1:
            time.sleep(delay)
    return False


def platrum_tasks() -> list:
    r = requests.post(
        f"https://{PLATRUM_HOST}/tasks/api/task/list",
        headers={"Api-key": PLATRUM_API_KEY, "Content-Type": "application/json"},
        json={"responsible_user_ids": [MAXIM_ID]},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def add_ticktick(name: str, description: str, start_iso: str | None, finish_iso: str, is_important: bool) -> bool:
    priority = 3 if is_important else 1  # TickTick: 0=none,1=low,3=medium,5=high

    has_time = start_iso and start_iso != finish_iso
    body: dict = {
        "title": name,
        "dueDate": finish_iso,
        "timeZone": "Asia/Vladivostok",
        "isAllDay": not has_time,
        "priority": priority,
        "tags": ["platrum"],
        "content": description or "",
    }
    if has_time:
        body["startDate"] = start_iso

    r = requests.post(
        f"{TICKTICK_BASE}/task",
        headers={
            "Authorization": f"Bearer {TICKTICK_TOKEN}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    return r.status_code == 200


def add_notion(name: str, description: str, deadline_date: str, is_important: bool) -> bool:
    priority = "P1" if is_important else "P2"
    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={
            "parent": {"database_id": NOTION_PM_BACKLOG},
            "properties": {
                "Задача": {"title": [{"text": {"content": name}}]},
                "Статус": {"select": {"name": "Бэклог"}},
                "Источник": {"select": {"name": "Platrum"}},
                "Приоритет": {"select": {"name": priority}},
                "Дедлайн": {"date": {"start": deadline_date}},
                "Заметки": {"rich_text": [{"text": {"content": description or ""}}]},
            },
        },
        timeout=30,
    )
    return r.status_code == 200


def notify(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10,
    )


def main():
    seen, partial = load_state()
    tasks = platrum_tasks()

    # First run: mark all existing as seen to avoid flooding
    if not seen and not partial:
        seen = {str(t["id"]) for t in tasks}
        save_state(seen, {})
        print(f"First run: {len(seen)} tasks marked as seen. Will notify about new ones next time.")
        return

    # --- Retry partial (TickTick succeeded before, Notion failed) ---
    task_map = {str(t["id"]): t for t in tasks}
    retried = []
    for task_id, info in list(partial.items()):
        task = task_map.get(task_id)
        if not task:
            # Task no longer in Platrum (deleted/finished) — drop
            del partial[task_id]
            continue

        name = task["name"]
        description = task.get("description") or ""
        is_important = task.get("is_important", False)
        finish_dt = datetime.fromisoformat(task["finish_date"].replace("Z", "+00:00"))
        finish_local = finish_dt.astimezone(VL_TZ)
        deadline_date = finish_local.date().isoformat()

        attempts = info.get("attempts", 0) + 1
        nt_ok = _with_retry(add_notion, name, description, deadline_date, is_important)
        if nt_ok:
            seen.add(task_id)
            del partial[task_id]
            print(f"Retried Notion OK [{task_id}]: {name}")
            notify(f"✅ <b>Notion синхронизирован (retry #{attempts})</b>\n📌 {name}")
        else:
            partial[task_id]["attempts"] = attempts
            if attempts >= 5:
                notify(f"🚨 <b>Notion sync failed {attempts}x</b> — задача потеряна:\n📌 {name}")
                del partial[task_id]
                seen.add(task_id)
            print(f"Retry Notion FAIL [{task_id}] attempt {attempts}: {name}")
        retried.append(task_id)

    # --- Process truly new tasks ---
    new_tasks = [
        t for t in tasks
        if str(t["id"]) not in seen
        and str(t["id"]) not in partial
        and t.get("finish_date")
        and not t.get("is_finished")
        and t.get("deletion_date") is None
    ]

    if not new_tasks and not retried:
        print("No new Platrum tasks with deadlines.")
        save_state(seen, partial)
        return

    for task in new_tasks:
        task_id = str(task["id"])
        name = task["name"]
        description = task.get("description") or ""
        is_important = task.get("is_important", False)

        finish_dt = datetime.fromisoformat(task["finish_date"].replace("Z", "+00:00"))
        finish_local = finish_dt.astimezone(VL_TZ)
        deadline_date = finish_local.date().isoformat()
        deadline_str = finish_local.strftime("%d.%m.%Y")
        finish_iso = task["finish_date"].replace("Z", "+00:00")
        start_iso = task["start_date"].replace("Z", "+00:00") if task.get("start_date") else None

        tt_ok = _with_retry(add_ticktick, name, description, start_iso, finish_iso, is_important)
        nt_ok = _with_retry(add_notion, name, description, deadline_date, is_important)

        if tt_ok and nt_ok:
            seen.add(task_id)
        elif tt_ok and not nt_ok:
            # TickTick done, Notion failed — retry Notion next run
            partial[task_id] = {"tt_done": True, "attempts": 1}
            seen.add(task_id)  # prevent re-adding to TickTick
        else:
            # TickTick failed — retry everything next run (don't add to seen)
            print(f"  TickTick failed for [{task_id}] — will retry next run")

        tt_icon = "✅" if tt_ok else "❌"
        nt_icon = "✅" if nt_ok else "❌"
        priority_icon = "🔴" if is_important else "🟠"

        notify(
            f"{priority_icon} <b>Новая задача из Platrum</b>\n"
            f"📌 {name}\n"
            f"📅 Срок: {deadline_str}\n"
            f"{tt_icon} TickTick  {nt_icon} Notion"
            + ("" if (tt_ok and nt_ok) else "\n⚠️ Неполная синхронизация — повторю при следующем запуске")
        )
        print(f"Synced [{task_id}]: {name} tt={tt_ok} nt={nt_ok}")

    save_state(seen, partial)
    print(f"Done. {len(new_tasks)} new task(s), {len(retried)} retried.")


if __name__ == "__main__":
    main()
