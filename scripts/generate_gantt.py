#!/usr/bin/env python3
"""Generate a Gantt chart PNG for a project roadmap and send it to Telegram."""

import json
import os
import sys
import tempfile
from datetime import datetime, date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import matplotlib.dates as mdates
import httpx as requests

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ROADMAPS_DIR = Path(__file__).parent.parent / "data" / "roadmaps"

PRIORITY_COLORS = {
    "P1": "#E74C3C",  # red
    "P2": "#E67E22",  # orange
    "P3": "#3498DB",  # blue
    "P4": "#95A5A6",  # gray
}
PLATRUM_COLOR = "#F39C12"  # amber for Platrum tasks
BG_COLOR = "#1E1E2E"
GRID_COLOR = "#2D2D44"
TEXT_COLOR = "#E0E0E0"
TODAY_COLOR = "#00FF88"
MILESTONE_COLOR = "#FFD700"


def load_roadmap(project_slug: str) -> dict:
    path = ROADMAPS_DIR / f"{project_slug}.json"
    if not path.exists():
        # Try finding by partial name
        matches = list(ROADMAPS_DIR.glob("*.json"))
        for m in matches:
            data = json.loads(m.read_text())
            if project_slug.lower() in data.get("project", "").lower():
                return data
        raise FileNotFoundError(f"Roadmap not found: {project_slug}")
    return json.loads(path.read_text())


def generate_gantt(roadmap: dict) -> bytes:
    tasks = roadmap["tasks"]
    milestones = roadmap.get("milestones", [])
    project_name = roadmap["project"]
    finish_date = roadmap.get("finish_date", "")

    # Parse task dates
    parsed = []
    for t in tasks:
        try:
            task_date = datetime.strptime(t["date"], "%Y-%m-%d").date()
            start_dt = datetime.strptime(f"{t['date']} {t['start']}", "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(f"{t['date']} {t['end']}", "%Y-%m-%d %H:%M")
            parsed.append({**t, "start_dt": start_dt, "end_dt": end_dt, "task_date": task_date})
        except Exception:
            continue

    if not parsed:
        raise ValueError("No valid tasks to plot")

    # Sort by start time
    parsed.sort(key=lambda x: x["start_dt"])

    # Group tasks by unique name (merge if same task split across days)
    seen_names = []
    unique_tasks = []
    for t in parsed:
        if t["name"] not in seen_names:
            seen_names.append(t["name"])
            unique_tasks.append(t)

    n_tasks = len(unique_tasks)
    fig_height = max(6, n_tasks * 0.5 + 3)
    fig, ax = plt.subplots(figsize=(14, fig_height))

    # Dark background
    fig.patch.set_facecolor(BG_COLOR)
    ax.set_facecolor(BG_COLOR)

    all_dates = [t["start_dt"] for t in parsed] + [t["end_dt"] for t in parsed]
    min_date = min(all_dates)
    max_date = max(all_dates)
    padding = (max_date - min_date) * 0.05
    ax.set_xlim(min_date - padding, max_date + padding)

    # Draw tasks
    for i, t in enumerate(unique_keys := unique_tasks):
        y = n_tasks - 1 - i
        color = PRIORITY_COLORS.get(t.get("priority", "P3"), "#3498DB")

        # Find all slots for this task name
        slots = [p for p in parsed if p["name"] == t["name"]]
        for slot in slots:
            duration_hours = (slot["end_dt"] - slot["start_dt"]).seconds / 3600
            bar = ax.barh(
                y, mdates.date2num(slot["end_dt"]) - mdates.date2num(slot["start_dt"]),
                left=mdates.date2num(slot["start_dt"]),
                height=0.6,
                color=color,
                alpha=0.85,
                edgecolor="white",
                linewidth=0.5,
            )

        # Task label
        label = t["name"]
        if len(label) > 35:
            label = label[:33] + "…"
        priority = t.get("priority", "")
        ax.text(
            mdates.date2num(min_date) - (mdates.date2num(max_date) - mdates.date2num(min_date)) * 0.01,
            y,
            f"{label}  [{priority}]",
            va="center", ha="right",
            fontsize=8.5, color=TEXT_COLOR,
            fontfamily="monospace",
        )

    # Milestones
    for ms in milestones:
        try:
            ms_dt = datetime.strptime(ms["date"], "%Y-%m-%d")
            ax.axvline(x=mdates.date2num(ms_dt), color=MILESTONE_COLOR, linestyle="--", linewidth=1.2, alpha=0.8)
            ax.text(
                mdates.date2num(ms_dt), n_tasks - 0.3,
                f"◆ {ms['label']}",
                rotation=45, fontsize=7.5, color=MILESTONE_COLOR, ha="left",
            )
        except Exception:
            pass

    # Today line
    today = datetime.now()
    if min_date <= today <= max_date:
        ax.axvline(x=mdates.date2num(today), color=TODAY_COLOR, linewidth=1.5, alpha=0.9, linestyle="-")
        ax.text(
            mdates.date2num(today), -0.8,
            "сегодня", fontsize=7.5, color=TODAY_COLOR, ha="center",
        )

    # Axes formatting
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))  # every Monday
    ax.xaxis.set_minor_locator(mdates.DayLocator())

    ax.set_yticks(range(n_tasks))
    ax.set_yticklabels([])  # labels drawn manually above
    ax.tick_params(colors=TEXT_COLOR, labelsize=8)
    ax.spines[:].set_color(GRID_COLOR)

    ax.grid(axis="x", color=GRID_COLOR, linewidth=0.5, alpha=0.6)
    ax.set_ylim(-1.2, n_tasks)

    # Title
    finish_str = f" → {finish_date}" if finish_date else ""
    ax.set_title(
        f"🗺  {project_name}{finish_str}",
        color=TEXT_COLOR, fontsize=13, fontweight="bold", pad=12,
    )

    # Legend
    legend_patches = [
        mpatches.Patch(color=c, label=f"Приоритет {p}")
        for p, c in PRIORITY_COLORS.items()
    ]
    legend_patches.append(mpatches.Patch(color=TODAY_COLOR, label="Сегодня"))
    legend_patches.append(mpatches.Patch(color=MILESTONE_COLOR, label="Ключевая точка"))
    ax.legend(
        handles=legend_patches,
        loc="lower right", fontsize=7.5,
        facecolor=BG_COLOR, edgecolor=GRID_COLOR,
        labelcolor=TEXT_COLOR,
    )

    plt.tight_layout(rect=[0.22, 0, 1, 1])

    buf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    plt.savefig(buf.name, dpi=150, bbox_inches="tight", facecolor=BG_COLOR)
    plt.close()
    with open(buf.name, "rb") as f:
        data = f.read()
    os.unlink(buf.name)
    return data


def send_photo(image_bytes: bytes, caption: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("No Telegram credentials — skipping send.")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
        data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
        files={"photo": ("gantt.png", image_bytes, "image/png")},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"Telegram error: {r.text}")


def main():
    if len(sys.argv) < 2:
        print("Usage: generate_gantt.py <project-slug>")
        sys.exit(1)

    project_slug = sys.argv[1]
    roadmap = load_roadmap(project_slug)
    image = generate_gantt(roadmap)

    project = roadmap["project"]
    finish = roadmap.get("finish_date", "")
    n_tasks = len(roadmap["tasks"])
    finish_str = f"\n📅 Финиш: ~{finish}" if finish else ""
    caption = f"🗺 <b>Дорожная карта: {project}</b>{finish_str}\n📌 Задач: {n_tasks}"

    send_photo(image, caption)
    print(f"Gantt chart sent for: {project}")


if __name__ == "__main__":
    main()
