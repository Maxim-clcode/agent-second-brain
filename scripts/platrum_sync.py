#!/usr/bin/env python3
"""Platrum → TickTick + Notion sync.

Runs every 5 min via systemd timer.
Finds new tasks where Максим is responsible + has a deadline,
creates them in TickTick (tag: platrum) and Notion (Источник: Platrum),
then sends a Telegram notification.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx as requests

PLATRUM_HOST = "atlantdv.platrum.ru"
PLATRUM_API_KEY = "DDF706F6-50D1-30BD47722A6D2CA91C0205D836"
MAXIM_ID = "3808bfa161526643ad65fbf82bfd3dfc"

# Platrum user_id → Notion "Ответственный" name
PLATRUM_USERS = {
    "3808bfa161526643ad65fbf82bfd3dfc": "Максим",
    "6bb8079dd02afcefd116704a35456538": "Никита",
    "dfa2df0a56f1c5624bccab225a39173b": "Кристина",
    "2c7d0cacbce544e550865ee90c1a2357": "Ника",
    "bfd4771e123941e14d9a1ac0578300da": "Саша",
}

TICKTICK_TOKEN = "tp_370240d2191b485496c72cc7c5522326"
TICKTICK_BASE = "https://api.ticktick.com/open/v1"

NOTION_TOKEN = "ntn_c57737162465ObprBMHdaXLJ5mPvVPO8T3Hyk0mmTUJ6Eh"
NOTION_PM_BACKLOG = "22876284-e92f-4866-a908-3a3bda425637"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

VL_TZ = ZoneInfo("Asia/Vladivostok")
STATE_FILE = Path(__file__).parent.parent / "data" / "platrum_seen.json"


def load_state() -> tuple[set, dict, set, dict]:
    """Return (seen, partial, seen_auditor, notion_map).
    seen         = platrum IDs where Maxim is responsible — TT+Notion done
    partial      = {platrum_id: {"tt_done": bool, "attempts": int}}
    seen_auditor = platrum IDs where Maxim is auditor — Notion done
    notion_map   = {platrum_id: notion_page_id} — for completion tracking
    """
    if STATE_FILE.exists():
        raw = json.loads(STATE_FILE.read_text())
        if isinstance(raw, list):
            return set(raw), {}, set(), {}
        return (
            set(raw.get("seen", [])),
            raw.get("partial", {}),
            set(raw.get("seen_auditor", [])),
            raw.get("notion_map", {}),
        )
    return set(), {}, set(), {}


def save_state(seen: set, partial: dict, seen_auditor: set, notion_map: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(
        {
            "seen": sorted(seen),
            "partial": partial,
            "seen_auditor": sorted(seen_auditor),
            "notion_map": notion_map,
        },
        indent=2,
    ))


# Legacy helpers kept for callers
def load_seen() -> set:
    seen, _, _, _ = load_state()
    return seen


def save_seen(seen: set) -> None:
    _, partial, seen_auditor, notion_map = load_state()
    save_state(seen, partial, seen_auditor, notion_map)


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


def platrum_auditor_tasks() -> list:
    """Tasks where Maxim is auditor but NOT responsible — handled by someone else."""
    r = requests.post(
        f"https://{PLATRUM_HOST}/tasks/api/task/list",
        headers={"Api-key": PLATRUM_API_KEY, "Content-Type": "application/json"},
        json={"auditor_user_ids": [MAXIM_ID]},
        timeout=30,
    )
    r.raise_for_status()
    all_tasks = r.json().get("data", [])
    return [
        t for t in all_tasks
        if MAXIM_ID in t.get("auditors", [])
        and MAXIM_ID not in t.get("responsible_user_ids", [])
    ]


def clean_description(text: str) -> str:
    """Strip HTML tags and truncate to Notion 2000-char limit."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:2000]


def resolve_responsible(responsible_user_ids: list) -> str | None:
    """Return first known team member name from responsible list, excluding Maxim."""
    for uid in responsible_user_ids:
        if uid != MAXIM_ID and uid in PLATRUM_USERS:
            return PLATRUM_USERS[uid]
    return None


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
        "content": clean_description(description),
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


def create_notion_page(
    name: str,
    description: str,
    deadline_date: str | None,
    is_important: bool,
    status: str = "Бэклог",
    responsible: str | None = None,
) -> str | None:
    """Create a Notion page and return its page_id, or None on failure."""
    priority = "P1" if is_important else "P2"
    props: dict = {
        "Задача": {"title": [{"text": {"content": name}}]},
        "Статус": {"select": {"name": status}},
        "Источник": {"select": {"name": "Platrum"}},
        "Приоритет": {"select": {"name": priority}},
        "Заметки": {"rich_text": [{"text": {"content": clean_description(description)}}]},
    }
    if deadline_date:
        props["Дедлайн"] = {"date": {"start": deadline_date}}
    if responsible:
        props["Ответственный"] = {"select": {"name": responsible}}
    r = requests.post(
        "https://api.notion.com/v1/pages",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={"parent": {"database_id": NOTION_PM_BACKLOG}, "properties": props},
        timeout=30,
    )
    if r.status_code == 200:
        return r.json().get("id")
    return None


def add_notion(
    name: str,
    description: str,
    deadline_date: str | None,
    is_important: bool,
    status: str = "Бэклог",
    responsible: str | None = None,
) -> bool:
    return create_notion_page(name, description, deadline_date, is_important, status, responsible) is not None


def notion_set_done(page_id: str) -> bool:
    """Move a Notion page to Выполнено."""
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={"properties": {"Статус": {"select": {"name": "Выполнено"}}}},
        timeout=15,
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


def _create_notion_with_retry(retries: int = 3, delay: float = 5.0, **kwargs) -> str | None:
    for attempt in range(retries):
        try:
            page_id = create_notion_page(**kwargs)
            if page_id:
                return page_id
        except Exception as exc:
            print(f"  attempt {attempt + 1} error: {exc}")
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def main():
    seen, partial, seen_auditor, notion_map = load_state()
    tasks = platrum_tasks()
    auditor_tasks = platrum_auditor_tasks()

    # First run: mark all existing as seen to avoid flooding
    if not seen and not partial:
        seen = {str(t["id"]) for t in tasks}
        seen_auditor = {str(t["id"]) for t in auditor_tasks}
        save_state(seen, {}, seen_auditor, {})
        print(f"First run: {len(seen)} responsible + {len(seen_auditor)} auditor tasks marked as seen.")
        return

    # Build combined map of all current Platrum tasks (incl. finished)
    all_task_map = {str(t["id"]): t for t in tasks + auditor_tasks}

    # --- Check completion: tasks we're tracking that became finished ---
    done_count = 0
    for platrum_id, notion_page_id in list(notion_map.items()):
        task = all_task_map.get(platrum_id)
        is_done = task is not None and (
            task.get("is_finished") or task.get("deletion_date") is not None
        )
        if is_done:
            if notion_set_done(notion_page_id):
                name = task["name"] if task else platrum_id
                print(f"  ✅ Выполнено в Notion [{platrum_id}]: {name[:60]}")
                del notion_map[platrum_id]
                # Also remove from seen sets so we don't track it further
                seen.discard(platrum_id)
                seen_auditor.discard(platrum_id)
                done_count += 1
            else:
                print(f"  ❌ Не удалось обновить Выполнено [{platrum_id}]")

    # --- Retry partial (TickTick succeeded before, Notion failed) ---
    task_map = {str(t["id"]): t for t in tasks}
    retried = []
    for task_id, info in list(partial.items()):
        task = task_map.get(task_id)
        if not task:
            del partial[task_id]
            continue

        name = task["name"]
        description = task.get("description") or ""
        is_important = task.get("is_important", False)
        finish_dt = datetime.fromisoformat(task["finish_date"].replace("Z", "+00:00"))
        finish_local = finish_dt.astimezone(VL_TZ)
        deadline_date = finish_local.date().isoformat()

        attempts = info.get("attempts", 0) + 1
        page_id = _create_notion_with_retry(
            name=name, description=description, deadline_date=deadline_date, is_important=is_important,
        )
        if page_id:
            seen.add(task_id)
            notion_map[task_id] = page_id
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

    # --- Process truly new responsible tasks ---
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
        page_id = _create_notion_with_retry(
            name=name, description=description, deadline_date=deadline_date, is_important=is_important,
        )
        nt_ok = page_id is not None

        if tt_ok and nt_ok:
            seen.add(task_id)
            notion_map[task_id] = page_id
        elif tt_ok and not nt_ok:
            partial[task_id] = {"tt_done": True, "attempts": 1}
            seen.add(task_id)
        else:
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

    # --- New auditor tasks ---
    new_auditor = [
        t for t in auditor_tasks
        if str(t["id"]) not in seen_auditor
        and not t.get("is_finished")
        and t.get("deletion_date") is None
    ]

    for task in new_auditor:
        task_id = str(task["id"])
        name = task["name"]
        description = task.get("description") or ""
        is_important = task.get("is_important", False)
        responsible = resolve_responsible(task.get("responsible_user_ids", []))

        finish_date = task.get("finish_date")
        deadline_date = None
        deadline_str = "без срока"
        if finish_date:
            finish_dt = datetime.fromisoformat(finish_date.replace("Z", "+00:00"))
            finish_local = finish_dt.astimezone(VL_TZ)
            deadline_date = finish_local.date().isoformat()
            deadline_str = finish_local.strftime("%d.%m.%Y")

        page_id = _create_notion_with_retry(
            name=name, description=description, deadline_date=deadline_date,
            is_important=is_important, status="Выполняют сотрудники", responsible=responsible,
        )

        if page_id:
            seen_auditor.add(task_id)
            notion_map[task_id] = page_id
            resp_label = responsible or "неизвестный"
            priority_icon = "🔴" if is_important else "🟠"
            notify(
                f"{priority_icon} <b>Задача на контроле (Platrum)</b>\n"
                f"📌 {name}\n"
                f"👤 Исполнитель: {resp_label}\n"
                f"📅 Срок: {deadline_str}"
            )
            print(f"Auditor task synced [{task_id}]: {name} → responsible={resp_label}")
        else:
            print(f"  Notion failed for auditor task [{task_id}]: {name}")

    save_state(seen, partial, seen_auditor, notion_map)
    print(f"Done. {len(new_tasks)} new, {len(retried)} retried, {len(new_auditor)} auditor, {done_count} completed.")


if __name__ == "__main__":
    main()
