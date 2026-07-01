#!/usr/bin/env python3
"""Update pinned Telegram roadmap status messages.

Reads all roadmap JSON files, queries Notion for current task completion,
and edits the pinned message with updated progress.

Called after day summary completes.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx as requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
NOTION_TOKEN = "ntn_c57737162465ObprBMHdaXLJ5mPvVPO8T3Hyk0mmTUJ6Eh"
NOTION_PM_BACKLOG = "22876284-e92f-4866-a908-3a3bda425637"
ROADMAPS_DIR = Path(__file__).parent.parent / "data" / "roadmaps"
VL_TZ = ZoneInfo("Asia/Vladivostok")


def notion_query(filter_body: dict) -> list:
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_PM_BACKLOG}/query",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={"filter": filter_body, "page_size": 100},
        timeout=30,
    )
    return r.json().get("results", [])


def get_task_statuses(project_name: str) -> dict[str, str]:
    """Return {task_name: status} for all project tasks in PM Backlog."""
    pages = notion_query({
        "property": "Родительская задача",
        "select": {"equals": project_name},
    })
    result = {}
    for p in pages:
        props = p["properties"]
        title = props["Задача"]["title"]
        name = title[0]["text"]["content"] if title else ""
        status = props.get("Статус", {}).get("select", {})
        status_name = status.get("name", "Бэклог") if status else "Бэклог"
        if name:
            result[name] = status_name
    return result


def build_pin_text(roadmap: dict, task_statuses: dict[str, str]) -> str:
    project = roadmap["project"]
    finish_date = roadmap.get("finish_date", "")
    tasks = roadmap["tasks"]
    milestones = roadmap.get("milestones", [])

    # Unique task names preserving order
    seen = []
    unique_tasks = []
    for t in tasks:
        if t["name"] not in seen:
            seen.append(t["name"])
            unique_tasks.append(t)

    total = len(unique_tasks)
    done = sum(1 for t in unique_tasks if task_statuses.get(t["name"]) == "Выполнено")
    in_sprint = [t for t in unique_tasks if task_statuses.get(t["name"]) == "Спринт неделя"]
    backlog = [t for t in unique_tasks if task_statuses.get(t["name"], "Бэклог") == "Бэклог"]

    pct = int(done / total * 100) if total else 0
    bar_filled = int(pct / 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)

    finish_str = ""
    if finish_date:
        try:
            fd = datetime.strptime(finish_date, "%Y-%m-%d")
            finish_str = f"\n📅 Финиш: ~{fd.strftime('%d.%m.%Y')}"
        except Exception:
            finish_str = f"\n📅 Финиш: ~{finish_date}"

    lines = [
        f"🗺 <b>{project}</b>{finish_str}",
        "",
        f"[{bar}] {pct}% ({done}/{total} задач)",
    ]

    done_tasks = [t for t in unique_tasks if task_statuses.get(t["name"]) == "Выполнено"]
    if done_tasks:
        lines.append("")
        lines.append("✅ <b>Выполнено:</b>")
        for t in done_tasks:
            lines.append(f"  • {t['name']}")

    if in_sprint:
        lines.append("")
        lines.append("🔄 <b>Эта неделя:</b>")
        for t in in_sprint:
            lines.append(f"  • {t['name']}")

    if backlog:
        lines.append("")
        lines.append(f"📋 <b>Впереди:</b> {len(backlog)} задач")
        for t in backlog[:3]:
            lines.append(f"  • {t['name']}")
        if len(backlog) > 3:
            lines.append(f"  ...и ещё {len(backlog) - 3}")

    if milestones:
        today = datetime.now(VL_TZ).date()
        upcoming = []
        for ms in milestones:
            try:
                ms_date = datetime.strptime(ms["date"], "%Y-%m-%d").date()
                if ms_date >= today:
                    upcoming.append((ms_date, ms["label"]))
            except Exception:
                pass
        if upcoming:
            upcoming.sort()
            next_ms = upcoming[0]
            lines.append("")
            lines.append(f"🏁 Следующая точка: {next_ms[0].strftime('%d.%m')} — {next_ms[1]}")

    now_str = datetime.now(VL_TZ).strftime("%d.%m.%Y %H:%M")
    lines.append("")
    lines.append(f"<i>🕐 {now_str}</i>")

    return "\n".join(lines)


def send_and_pin(text: str) -> int | None:
    """Send a message and pin it. Returns message_id."""
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        },
        timeout=15,
    )
    if r.status_code != 200:
        print(f"sendMessage error: {r.text}")
        return None
    message_id = r.json()["result"]["message_id"]

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/pinChatMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "message_id": message_id,
            "disable_notification": True,
        },
        timeout=15,
    )
    return message_id


def edit_pin(message_id: int, text: str) -> bool:
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/editMessageText",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
        },
        timeout=15,
    )
    return r.status_code == 200


def process_roadmap(path: Path) -> None:
    roadmap = json.loads(path.read_text())
    project = roadmap["project"]
    task_statuses = get_task_statuses(project)
    text = build_pin_text(roadmap, task_statuses)

    pinned_id = roadmap.get("pinned_message_id")

    if pinned_id:
        ok = edit_pin(pinned_id, text)
        if ok:
            print(f"Updated pin [{pinned_id}]: {project}")
        else:
            # Message may have been deleted — send new pin
            print(f"Pin {pinned_id} not editable, sending new one...")
            new_id = send_and_pin(text)
            if new_id:
                roadmap["pinned_message_id"] = new_id
                path.write_text(json.dumps(roadmap, ensure_ascii=False, indent=2))
                print(f"New pin [{new_id}]: {project}")
    else:
        # First time — send and pin
        new_id = send_and_pin(text)
        if new_id:
            roadmap["pinned_message_id"] = new_id
            path.write_text(json.dumps(roadmap, ensure_ascii=False, indent=2))
            print(f"Pinned [{new_id}]: {project}")


def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("No Telegram credentials.")
        return

    roadmap_files = list(ROADMAPS_DIR.glob("*.json"))
    if not roadmap_files:
        print("No roadmap files found.")
        return

    for path in roadmap_files:
        try:
            process_roadmap(path)
        except Exception as e:
            print(f"Error processing {path.name}: {e}")


if __name__ == "__main__":
    main()
