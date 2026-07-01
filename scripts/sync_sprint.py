#!/usr/bin/env python3
"""Auto-promote Notion Backlog tasks to Спринт недели.

Runs every workday morning at 09:00 Vladivostok (23:00 UTC previous night).
Two complementary checks:
  1. Roadmap-based: tasks in roadmap JSON files scheduled this week → promote by notion_id
  2. Name-based: any Бэклог task whose name matches a TickTick task this week → promote
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
ROADMAPS_DIR = Path(__file__).parent.parent / "data" / "roadmaps"
VL_TZ = ZoneInfo("Asia/Vladivostok")
RU_HOLIDAYS = holidays.Russia()


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in RU_HOLIDAYS


def current_week(today: date) -> tuple[date, date]:
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=4)
    return week_start, week_end


def ticktick_tasks_for_day(d: date) -> list[str]:
    """Return list of task titles scheduled on this date in TickTick."""
    start = datetime(d.year, d.month, d.day, 0, 0, tzinfo=ZoneInfo("UTC"))
    end = datetime(d.year, d.month, d.day, 23, 59, tzinfo=ZoneInfo("UTC"))
    try:
        r = httpx.post(
            "https://api.ticktick.com/open/v1/task/undone",
            headers={"Authorization": f"Bearer {TICKTICK_TOKEN}", "Content-Type": "application/json"},
            json={"startDate": start.isoformat(), "endDate": end.isoformat(), "timeZone": "Asia/Vladivostok"},
            timeout=20,
        )
        data = r.json()
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        return [t.get("title", "").strip() for t in items if t.get("title")]
    except Exception as e:
        print(f"  TickTick error for {d}: {e}")
        return []


def get_ticktick_week_titles(week_start: date, week_end: date) -> set[str]:
    """All TickTick task titles scheduled anywhere in the current week."""
    titles = set()
    d = week_start
    while d <= week_end:
        if is_workday(d):
            for title in ticktick_tasks_for_day(d):
                titles.add(title.lower().strip())
        d += timedelta(days=1)
    return titles


def notion_get_backlog() -> list[dict]:
    """Get all Notion PM Backlog tasks with status Бэклог."""
    r = httpx.post(
        f"https://api.notion.com/v1/databases/{NOTION_PM_BACKLOG}/query",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={"filter": {"property": "Статус", "select": {"equals": "Бэклог"}}, "page_size": 100},
        timeout=30,
    )
    return r.json().get("results", [])


def notion_get_status(page_id: str) -> str:
    r = httpx.get(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28"},
        timeout=15,
    )
    props = r.json().get("properties", {})
    return (props.get("Статус", {}).get("select") or {}).get("name", "")


def notion_promote(page_id: str, name: str) -> bool:
    r = httpx.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={"properties": {"Статус": {"select": {"name": "Спринт неделя"}}}},
        timeout=15,
    )
    ok = r.status_code == 200
    print(f"  {'✅' if ok else '❌'} → Спринт недели: {name[:60]}")
    return ok


def names_match(notion_name: str, ticktick_titles: set[str]) -> bool:
    n = notion_name.lower().strip()
    for tt in ticktick_titles:
        if n in tt or tt in n:
            return True
        # Also check if at least 70% of words overlap
        n_words = set(n.split())
        t_words = set(tt.split())
        if len(n_words) >= 3 and len(n_words & t_words) / len(n_words) >= 0.7:
            return True
    return False


def main():
    now_vl = datetime.now(VL_TZ)
    today = now_vl.date()

    if not is_workday(today):
        print(f"Not a workday ({today}) — skipping.")
        return

    week_start, week_end = current_week(today)
    print(f"Week: {week_start} – {week_end}")

    # === CHECK 1: Roadmap-based (precise, by notion_id) ===
    promoted_ids = set()

    if ROADMAPS_DIR.exists():
        for roadmap_path in ROADMAPS_DIR.glob("*.json"):
            roadmap = json.loads(roadmap_path.read_text())
            project = roadmap.get("project", roadmap_path.stem)
            for task in roadmap.get("tasks", []):
                try:
                    task_date = date.fromisoformat(task["date"])
                except Exception:
                    continue
                if week_start <= task_date <= week_end:
                    notion_id = task.get("notion_id")
                    if notion_id and notion_id not in promoted_ids:
                        current_status = notion_get_status(notion_id)
                        if current_status == "Бэклог":
                            if notion_promote(notion_id, task["name"]):
                                promoted_ids.add(notion_id)

    # === CHECK 2: Name-based (for non-roadmap tasks) ===
    print("\nFetching TickTick tasks for the week...")
    ticktick_titles = get_ticktick_week_titles(week_start, week_end)
    print(f"Found {len(ticktick_titles)} TickTick titles this week")

    backlog_tasks = notion_get_backlog()
    print(f"Found {len(backlog_tasks)} tasks in Notion Бэклог")

    for page in backlog_tasks:
        page_id = page["id"]
        if page_id in promoted_ids:
            continue
        title_arr = page["properties"].get("Задача", {}).get("title", [])
        name = title_arr[0]["text"]["content"] if title_arr else ""
        if not name:
            continue
        if names_match(name, ticktick_titles):
            if notion_promote(page_id, name):
                promoted_ids.add(page_id)

    print(f"\nDone. Promoted {len(promoted_ids)} task(s) to Спринт недели.")


if __name__ == "__main__":
    main()
