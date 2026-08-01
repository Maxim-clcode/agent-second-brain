#!/usr/bin/env python3
"""Weekly backup: Notion d-brain page → d-brain-features.md"""

import json
import os
import subprocess
import sys
from datetime import datetime

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_PAGE_ID = "3ab15823-eb16-8062-ae5c-dd297bb4c2f3"
OUTPUT_FILE = "/home/brain/projects/obsidian-vault/Documents/Личное/ИИ Асистент/d-brain-features.md"


def notion_get(path):
    cmd = [
        "curl", "-s",
        f"https://api.notion.com/v1/{path}",
        "-H", f"Authorization: Bearer {NOTION_TOKEN}",
        "-H", "Notion-Version: 2022-06-28",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(result.stdout)


def rich_text_to_str(rt_list):
    return "".join(item.get("plain_text", "") for item in rt_list)


def blocks_to_md(page_id, depth=0):
    """Recursively convert Notion blocks to markdown."""
    lines = []
    cursor = None
    while True:
        url = f"blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        data = notion_get(url)
        if data.get("object") == "error":
            break

        for block in data.get("results", []):
            bt = block.get("type", "")
            content = block.get(bt, {})
            rt = content.get("rich_text", [])
            text = rich_text_to_str(rt)

            if bt == "heading_1":
                lines.append(f"# {text}")
            elif bt == "heading_2":
                lines.append(f"## {text}")
            elif bt == "heading_3":
                lines.append(f"### {text}")
            elif bt == "paragraph":
                lines.append(text if text else "")
            elif bt == "bulleted_list_item":
                lines.append(f"- {text}")
            elif bt == "numbered_list_item":
                lines.append(f"1. {text}")
            elif bt == "code":
                lang = content.get("language", "")
                lines.append(f"```{lang}")
                lines.append(text)
                lines.append("```")
            elif bt == "divider":
                lines.append("---")
            elif bt == "callout":
                icon = content.get("icon", {}).get("emoji", "💡")
                lines.append(f"> {icon} {text}")
            elif bt == "toggle":
                lines.append(f"### {text}")
                if block.get("has_children"):
                    child_lines = blocks_to_md(block["id"], depth + 1)
                    lines.extend(child_lines)
            elif bt == "table":
                rows_data = notion_get(f"blocks/{block['id']}/children?page_size=100")
                first = True
                for row_block in rows_data.get("results", []):
                    cells = row_block.get("table_row", {}).get("cells", [])
                    row_texts = [rich_text_to_str(cell) for cell in cells]
                    lines.append("| " + " | ".join(row_texts) + " |")
                    if first:
                        lines.append("| " + " | ".join(["---"] * len(row_texts)) + " |")
                        first = False
                lines.append("")
            elif bt == "quote":
                lines.append(f"> {text}")

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return lines


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{now}] Syncing Notion → d-brain-features.md ...", file=sys.stderr)

    lines = blocks_to_md(NOTION_PAGE_ID)

    header = f"<!-- Автоматически скопировано из Notion {now}. Редактируй в Notion: https://app.notion.com/p/3ab15823eb168062ae5cdd297bb4c2f3 -->\n\n"
    content = header + "\n".join(lines) + "\n"

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[{now}] Done. Written {len(lines)} lines to {OUTPUT_FILE}", file=sys.stderr)

    # Git commit
    subprocess.run(
        ["git", "-C", "/home/brain/projects/obsidian-vault", "add", OUTPUT_FILE],
        capture_output=True
    )
    subprocess.run(
        ["git", "-C", "/home/brain/projects/obsidian-vault", "commit",
         "-m", f"auto: sync d-brain-features from Notion {now}"],
        capture_output=True
    )
    print(f"[{now}] Git commit done", file=sys.stderr)


if __name__ == "__main__":
    main()
