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
from datetime import date, datetime, timedelta
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

TICKTICK_TOKEN = os.environ["TICKTICK_TOKEN"]
TICKTICK_BASE = "https://api.ticktick.com/open/v1"

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_PM_BACKLOG = "22876284-e92f-4866-a908-3a3bda425637"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

VL_TZ = ZoneInfo("Asia/Vladivostok")
STATE_FILE = Path(__file__).parent.parent / "data" / "platrum_seen.json"
TT_NOTION_MAP_FILE = Path(__file__).parent.parent / "data" / "tt_notion_map.json"


def load_state() -> tuple[set, dict, set, dict, dict, dict]:
    """Return (seen, partial, seen_auditor, notion_map, date_map, tt_map).
    seen         = platrum IDs where Maxim is responsible — TT+Notion done
    partial      = {platrum_id: {"tt_done": bool, "attempts": int, ...cached fields}}
    seen_auditor = platrum IDs where Maxim is auditor — Notion done
    notion_map   = {platrum_id: notion_page_id} — for completion tracking
    date_map     = {platrum_id: "YYYY-MM-DD"} — last known finish_date for change detection
    tt_map       = {platrum_id: {"id": tt_task_id, "projectId": ...}} — for TickTick updates
    """
    if STATE_FILE.exists():
        raw = json.loads(STATE_FILE.read_text())
        if isinstance(raw, list):
            return set(raw), {}, set(), {}, {}, {}
        return (
            set(raw.get("seen", [])),
            raw.get("partial", {}),
            set(raw.get("seen_auditor", [])),
            raw.get("notion_map", {}),
            raw.get("date_map", {}),
            raw.get("tt_map", {}),
        )
    return set(), {}, set(), {}, {}, {}


def save_state(seen: set, partial: dict, seen_auditor: set, notion_map: dict, date_map: dict, tt_map: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(
        {
            "seen": sorted(seen),
            "partial": partial,
            "seen_auditor": sorted(seen_auditor),
            "notion_map": notion_map,
            "date_map": date_map,
            "tt_map": tt_map,
        },
        indent=2,
    ))


# Legacy helpers kept for callers
def load_seen() -> set:
    seen, _, _, _ = load_state()
    return seen


def save_seen(seen: set) -> None:
    _, partial, seen_auditor, notion_map, date_map, tt_map = load_state()
    save_state(seen, partial, seen_auditor, notion_map, date_map, tt_map)


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


def add_ticktick(name: str, description: str, start_iso: str | None, finish_iso: str, is_important: bool) -> dict | None:
    """Create a TickTick task. Returns {"id": ..., "projectId": ...} or None on failure."""
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
    if r.status_code == 200:
        data = r.json()
        return {"id": data.get("id"), "projectId": data.get("projectId")}
    return None


def _add_ticktick_with_retry(
    name: str, description: str, start_iso: str | None, finish_iso: str, is_important: bool,
    retries: int = 3, delay: float = 5.0,
) -> dict | None:
    for attempt in range(retries):
        try:
            result = add_ticktick(name, description, start_iso, finish_iso, is_important)
            if result:
                return result
        except Exception as exc:
            print(f"  attempt {attempt + 1} error: {exc}")
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def update_ticktick_date(tt_task_id: str, project_id: str | None, finish_iso: str) -> bool:
    """Update due date of an existing TickTick task."""
    body: dict = {"dueDate": finish_iso, "timeZone": "Asia/Vladivostok"}
    if project_id:
        body["projectId"] = project_id
    r = requests.post(
        f"{TICKTICK_BASE}/task/{tt_task_id}",
        headers={
            "Authorization": f"Bearer {TICKTICK_TOKEN}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    return r.status_code == 200


def _current_week_range() -> tuple[date, date]:
    today = datetime.now(VL_TZ).date()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end = week_start + timedelta(days=6)             # Sunday
    return week_start, week_end


def update_notion_deadline(page_id: str, deadline_date: str | None) -> bool:
    """Update deadline and recalculate status (Спринт неделя / Бэклог) on a Notion page."""
    week_start, week_end = _current_week_range()
    if deadline_date:
        d = date.fromisoformat(deadline_date)
        status = "Спринт неделя" if week_start <= d <= week_end else "Бэклог"
    else:
        status = "Бэклог"
    props: dict = {"Статус": {"select": {"name": status}}}
    if deadline_date:
        props["Дедлайн"] = {"date": {"start": deadline_date}}
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={"properties": props},
        timeout=15,
    )
    return r.status_code == 200


def auto_promote_backlog() -> int:
    """Move Notion tasks Бэклог → Спринт неделя if their deadline falls in the current week.
    Only promotes tasks where Максим is responsible — tasks of other employees stay as-is."""
    week_start, week_end = _current_week_range()
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_PM_BACKLOG}/query",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={
            "filter": {
                "and": [
                    {"property": "Статус", "select": {"equals": "Бэклог"}},
                    {"property": "Дедлайн", "date": {"on_or_after": week_start.isoformat()}},
                    {"property": "Дедлайн", "date": {"on_or_before": week_end.isoformat()}},
                    {"property": "Ответственный", "select": {"equals": "Максим"}},
                ]
            }
        },
        timeout=30,
    )
    if r.status_code != 200:
        print(f"  auto_promote_backlog query failed: {r.status_code}")
        return 0
    promoted = 0
    for page in r.json().get("results", []):
        page_id = page["id"]
        title_cells = page.get("properties", {}).get("Задача", {}).get("title", [])
        name = title_cells[0].get("plain_text", "") if title_cells else page_id
        rp = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers={
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={"properties": {"Статус": {"select": {"name": "Спринт неделя"}}}},
            timeout=15,
        )
        if rp.status_code == 200:
            promoted += 1
            print(f"  📅 Promoted to Спринт неделя: {name[:60]}")
        else:
            print(f"  ❌ Failed to promote: {name[:60]}")
    return promoted


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


def load_tt_notion_map() -> dict:
    """Load {tt_task_id: {"notion_page_id": ..., "project_id": ..., "title": ...}}."""
    if TT_NOTION_MAP_FILE.exists():
        return json.loads(TT_NOTION_MAP_FILE.read_text())
    return {}


def save_tt_notion_map(m: dict) -> None:
    TT_NOTION_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        TT_NOTION_MAP_FILE.write_text(json.dumps(m, indent=2))
    except PermissionError:
        # File may have been created by root; try to fix ownership via chmod
        import stat
        try:
            TT_NOTION_MAP_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            TT_NOTION_MAP_FILE.write_text(json.dumps(m, indent=2))
        except PermissionError:
            print(f"  ⚠️ Cannot write {TT_NOTION_MAP_FILE} — skipping map save (sync still works)")


def fetch_ticktick_projects() -> list[str]:
    """Get all TickTick project IDs."""
    r = requests.get(
        f"{TICKTICK_BASE}/project",
        headers={"Authorization": f"Bearer {TICKTICK_TOKEN}"},
        timeout=20,
    )
    if r.status_code == 200:
        return [p["id"] for p in r.json()]
    return []


TICKTICK_INBOX_ID = "inbox130591308"  # Inbox is not returned by GET /project


def fetch_ticktick_week_tasks() -> list[dict]:
    """Get all active TickTick tasks due this week (Mon–Sun).
    Returns list of {id, projectId, title, dueDate}."""
    week_start, week_end = _current_week_range()
    project_ids = [TICKTICK_INBOX_ID] + fetch_ticktick_projects()
    tasks = []
    seen_ids: set[str] = set()
    for pid in project_ids:
        r = requests.get(
            f"{TICKTICK_BASE}/project/{pid}/data",
            headers={"Authorization": f"Bearer {TICKTICK_TOKEN}"},
            timeout=20,
        )
        if r.status_code != 200:
            continue
        for t in r.json().get("tasks", []):
            tid = t.get("id", "")
            if not tid or tid in seen_ids:
                continue
            due = (t.get("dueDate") or t.get("startDate") or "")[:10]
            if not due:
                continue
            try:
                d = date.fromisoformat(due)
            except ValueError:
                continue
            if week_start <= d <= week_end and t.get("status", 0) != 2 and not t.get("completedTime"):
                seen_ids.add(tid)
                tasks.append({
                    "id": tid,
                    "projectId": t.get("projectId", pid),
                    "title": (t.get("title") or "").strip(),
                    "dueDate": due,
                })
    return tasks


def fetch_notion_sprint_tasks() -> dict[str, str]:
    """Get all Notion 'Спринт неделя' tasks.
    Returns {normalized_lower_title: page_id}."""
    result: dict[str, str] = {}
    cursor = None
    while True:
        body: dict = {
            "page_size": 100,
            "filter": {"property": "Статус", "select": {"equals": "Спринт неделя"}},
        }
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_PM_BACKLOG}/query",
            headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
            json=body, timeout=30,
        )
        d = r.json()
        for row in d.get("results", []):
            title_arr = row["properties"].get("Задача", {}).get("title", [])
            title = (title_arr[0].get("text", {}).get("content", "") if title_arr else "").strip()
            if title:
                result[title.lower()] = row["id"]
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return result



def sync_ticktick_to_notion_full(tt_notion_map: dict) -> tuple[int, int]:
    """Full TickTick → Notion sync for current week.
    Creates Notion pages for TickTick tasks missing there.
    Closes Notion pages when TickTick task is completed/deleted.
    Returns (created, closed)."""
    week_start, _ = _current_week_range()
    tt_tasks = fetch_ticktick_week_tasks()
    notion_sprint = fetch_notion_sprint_tasks()  # lower_title → page_id

    # Step 1: TickTick → Notion (create missing)
    created = 0
    tt_ids_active = {t["id"] for t in tt_tasks}
    for task in tt_tasks:
        title = task["title"]
        if not title:
            continue
        lower = title.lower()
        if lower in notion_sprint:
            # Already in Notion — populate map if not tracked yet
            if task["id"] not in tt_notion_map:
                tt_notion_map[task["id"]] = {
                    "notion_page_id": notion_sprint[lower],
                    "project_id": task["projectId"],
                    "title": title,
                }
            continue
        # Not in Notion — create
        page_id = create_notion_page(
            name=title,
            description="",
            deadline_date=task["dueDate"],
            is_important=False,
            status="Спринт неделя",
            responsible="Максим",
        )
        if page_id:
            tt_notion_map[task["id"]] = {
                "notion_page_id": page_id,
                "project_id": task["projectId"],
                "title": title,
            }
            notion_sprint[lower] = page_id
            created += 1
            print(f"  ➕ Создано в Notion из TickTick: {title[:60]}")

    # Step 2: update map — remove entries no longer active this week (task moved or done)
    # We don't close them here: TickTick REST API is unreliable for completed task status.
    # Closures happen via Platrum is_finished flag (main sync above).
    closed = 0
    for tt_id in list(tt_notion_map.keys()):
        if tt_id not in tt_ids_active:
            del tt_notion_map[tt_id]

    # Step 3: for tasks in Notion Спринт неделя with no TickTick pair in map,
    # add them to tt_notion_map by matching title — so next run Step 2 catches them
    for task in tt_tasks:
        tid = task["id"]
        if tid not in tt_notion_map:
            lower = task["title"].lower()
            if lower in notion_sprint:
                tt_notion_map[tid] = {
                    "notion_page_id": notion_sprint[lower],
                    "project_id": task["projectId"],
                    "title": task["title"],
                }

    return created, closed


def ticktick_is_completed(task_id: str, project_id: str) -> bool:
    """Check if a TickTick task is completed or gone.
    TickTick Open API returns 500 for completed tasks and 404 for deleted ones —
    both mean the task is no longer active."""
    try:
        r = requests.get(
            f"{TICKTICK_BASE}/task/{task_id}",
            headers={"Authorization": f"Bearer {TICKTICK_TOKEN}"},
            params={"projectId": project_id} if project_id else {},
            timeout=15,
        )
        if r.status_code == 404:
            return True
        if r.status_code == 200:
            return r.json().get("status") == 2
    except Exception as e:
        print(f"  ticktick_is_completed error: {e}")
    return False


def notion_find_by_title(title: str) -> list[tuple[str, str]]:
    """Find all active Notion pages matching title. Returns list of (page_id, status)."""
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_PM_BACKLOG}/query",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"},
        json={"filter": {"property": "Задача", "title": {"equals": title}}},
        timeout=15,
    )
    results = []
    for row in r.json().get("results", []):
        status = (row["properties"].get("Статус", {}).get("select") or {}).get("name", "")
        if status not in ("Выполнено", "Отменено"):
            results.append((row["id"], status))
    return results


def sync_ticktick_completions(tt_map: dict, task_map_by_id: dict) -> int:
    """Check tracked TickTick tasks — if completed, close matching Notion pages by title.
    Returns count of closed Notion pages."""
    closed = 0
    for platrum_id, tt_info in list(tt_map.items()):
        task_id = tt_info.get("id", "")
        project_id = tt_info.get("projectId", "")
        if not task_id:
            continue
        if not ticktick_is_completed(task_id, project_id):
            continue
        # TickTick task is done — find task name and close all matching Notion pages
        task = task_map_by_id.get(platrum_id)
        if not task:
            continue
        name = task.get("name", "")
        if not name:
            continue
        pages = notion_find_by_title(name)
        for page_id, status in pages:
            if notion_set_done(page_id):
                print(f"  ✅ Notion закрыт по TickTick: {name[:60]} (был: {status})")
                closed += 1
    return closed


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
    seen, partial, seen_auditor, notion_map, date_map, tt_map = load_state()
    tt_notion_map = load_tt_notion_map()
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

    # --- Auto-promote backlog tasks whose deadline is now in the current week ---
    promoted = auto_promote_backlog()

    # --- Full TickTick ↔ Notion sync (all tasks, not just Platrum-tracked) ---
    tt_created, tt_closed = sync_ticktick_to_notion_full(tt_notion_map)
    save_tt_notion_map(tt_notion_map)

    # --- Sync Platrum-tracked TickTick completions → Notion ---
    task_map_by_id = {str(t["id"]): t for t in tasks + auditor_tasks}
    tt_closed += sync_ticktick_completions(tt_map, task_map_by_id)

    # --- Check for date changes in already-tracked responsible tasks ---
    task_map = {str(t["id"]): t for t in tasks}
    updated_count = 0
    for platrum_id, known_date in list(date_map.items()):
        task = task_map.get(platrum_id)
        if not task or task.get("is_finished") or task.get("deletion_date"):
            continue
        if not task.get("finish_date"):
            continue
        finish_dt = datetime.fromisoformat(task["finish_date"].replace("Z", "+00:00"))
        new_deadline = finish_dt.astimezone(VL_TZ).date().isoformat()
        if new_deadline == known_date:
            continue

        # Date changed — update TickTick and Notion
        old_date = known_date
        date_map[platrum_id] = new_deadline
        name = task["name"]
        finish_iso = task["finish_date"].replace("Z", "+00:00")

        tt_info = tt_map.get(platrum_id)
        tt_updated = False
        if tt_info and tt_info.get("id"):
            tt_updated = update_ticktick_date(tt_info["id"], tt_info.get("projectId"), finish_iso)

        notion_page_id = notion_map.get(platrum_id)
        nt_updated = False
        if notion_page_id:
            nt_updated = update_notion_deadline(notion_page_id, new_deadline)

        tt_icon = "✅" if tt_updated else ("⚠️ нет ID" if not tt_info else "❌")
        nt_icon = "✅" if nt_updated else "❌"
        notify(
            f"📅 <b>Задача перенесена (Platrum)</b>\n"
            f"📌 {name}\n"
            f"🗓 {old_date} → {new_deadline}\n"
            f"{tt_icon} TickTick  {nt_icon} Notion"
        )
        print(f"Date updated [{platrum_id}]: {name[:60]}  {old_date} → {new_deadline}  tt={tt_updated} nt={nt_updated}")
        updated_count += 1

    # --- Retry partial (TickTick succeeded before, Notion failed) ---
    retried = []
    for task_id, info in list(partial.items()):
        # Use cached task data — task may already be gone from Platrum API
        name = info.get("name") or (task_map.get(task_id) or {}).get("name", task_id)
        description = info.get("description", "")
        deadline_date = info.get("deadline_date")
        is_important = info.get("is_important", False)

        # Fallback: if no cached data and task still in API, extract from there
        if not info.get("name") and task_map.get(task_id):
            task = task_map[task_id]
            name = task["name"]
            description = task.get("description") or ""
            is_important = task.get("is_important", False)
            finish_dt = datetime.fromisoformat(task["finish_date"].replace("Z", "+00:00"))
            deadline_date = finish_dt.astimezone(VL_TZ).date().isoformat()

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

        tt_result = _add_ticktick_with_retry(name, description, start_iso, finish_iso, is_important)
        tt_ok = tt_result is not None
        page_id = _create_notion_with_retry(
            name=name, description=description, deadline_date=deadline_date, is_important=is_important,
        )
        nt_ok = page_id is not None

        if tt_ok and nt_ok:
            seen.add(task_id)
            notion_map[task_id] = page_id
            date_map[task_id] = deadline_date
            tt_map[task_id] = tt_result
        elif tt_ok and not nt_ok:
            partial[task_id] = {
                "tt_done": True, "attempts": 1,
                "name": name, "description": description,
                "deadline_date": deadline_date, "is_important": is_important,
            }
            seen.add(task_id)
            date_map[task_id] = deadline_date
            tt_map[task_id] = tt_result
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

    save_state(seen, partial, seen_auditor, notion_map, date_map, tt_map)
    print(f"Done. {len(new_tasks)} new, {len(retried)} retried, {len(new_auditor)} auditor, {done_count} completed, {updated_count} date-updated, {promoted} promoted, {tt_created} tt-created-in-notion, {tt_closed} tt-closed.")


if __name__ == "__main__":
    main()
