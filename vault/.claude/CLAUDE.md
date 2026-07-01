# Agent Second Brain

Voice-first personal assistant for capturing thoughts and managing tasks via Telegram.

## EVERY SESSION BOOTSTRAP

**Before doing anything else, read these files in order:**

1. `vault/MEMORY.md` — curated long-term memory (preferences, decisions, context)
2. `vault/daily/YYYY-MM-DD.md` — today's entries
3. `vault/daily/YYYY-MM-DD.md` — yesterday's entries (for continuity)
4. `vault/goals/3-weekly.md` — this week's ONE Big Thing
5. `vault/.session/handoff.md` — previous session context (if exists)

**Don't ask permission, just do it.** This ensures context continuity across sessions.

---

## SESSION END PROTOCOL

**Before ending a significant session, write to today's daily:**

```markdown
## HH:MM [text]
Session summary: [what was discussed/decided/created]
- Key decision: [if any]
- Created: [[link]] [if any files created]
- Next action: [if any]
```

**Also update `vault/MEMORY.md` if:**
- New key decision was made
- User preference discovered
- Important fact learned
- Active context changed significantly

**Update `vault/.session/handoff.md`:**
- Last Session: what was done
- Key Decisions: if any
- In Progress: unfinished work
- Next Steps: what to do next
- Observations: friction signals, patterns, ideas (type: `[friction]`, `[pattern]`, `[idea]`)

---

## Mission

Help user stay aligned with goals, capture valuable insights, and maintain clarity.

## Directory Structure

| Folder | Purpose |
|--------|---------|
| `daily/` | Raw daily entries (YYYY-MM-DD.md) |
| `goals/` | Goal cascade (3y → yearly → monthly → weekly) |
| `thoughts/` | Processed notes by category |
| `MOC/` | Maps of Content indexes |
| `attachments/` | Photos by date |
| `business/` | Business data (CRM, network, events) |
| `projects/` | Side projects (clients, leads) |

## Business Context

**Entry point:** `business/_index.md`

```
business/
├── _index.md       ← Start here (stats, overview)
├── crm/            ← Client records (companies + deals in one file)
├── network/        ← Company structure, partners
└── events/         ← Events, conferences
```

Search: `business/crm/{kebab-case}.md` (e.g. `acme-corp.md`, `client-b.md`)

## Projects Context

**Entry point:** `projects/_index.md`

```
projects/
├── _index.md       ← Start here
├── clients/        ← Project clients
├── leads/          ← Leads
└── projects/       ← Active projects
```

## Current Focus

See [[goals/3-weekly]] for this week's ONE Big Thing.
See [[goals/2-monthly]] for monthly priorities.

## Goals Hierarchy

```
goals/0-vision-3y.md    → 3-year vision by life areas
goals/1-yearly-YYYY.md  → Annual goals + quarterly breakdown
goals/2-monthly.md      → Current month's top 3 priorities
goals/3-weekly.md       → This week's focus + ONE Big Thing
```

## Entry Format

```markdown
## HH:MM [type]
Content
```

Types: `[voice]`, `[text]`, `[forward from: Name]`, `[photo]`

## Processing Workflow

Run daily processing via `/process` command or automatically at 21:00.

### 3-Phase Pipeline:
1. **CAPTURE** — Read daily entries → classify → JSON
2. **EXECUTE** — Save thoughts, update CRM → JSON
3. **REFLECT** — Generate HTML report, update MEMORY, record observations

Each phase = fresh Claude context for better quality.

## Card Template (autograph)

**Skill:** `.claude/skills/autograph/SKILL.md`

All new vault cards follow the autograph template:

```yaml
---
type: crm|lead|contact|project|personal|note
description: >-
  One line — what a searcher will see in results
tags: [tag1, tag2]        # 2-5 tags, lowercase
status: active|draft|pending|done|inactive
industry: FMCG            # for CRM/leads
region: US                 # ISO codes
created: YYYY-MM-DD
updated: YYYY-MM-DD
# Auto fields (don't edit manually):
last_accessed: YYYY-MM-DD
relevance: 0.85
tier: active
---
```

**Rules:**
- `description` — REQUIRED. Write as a search snippet, NOT "contact" or "crm"
- `tags` — REQUIRED. 2-5 tags, lowercase, hyphen-separated
- `status` ≠ `tier`: status = business status, tier = memory (automatic)
- One fact = one place (DRY). References via [[wikilinks]]
- Decay engine: `uv run .claude/skills/autograph/scripts/engine.py decay .`

## Skills & References

| Skill | Purpose |
|-------|---------|
| `dbrain-processor` | Main daily processing (3-phase pipeline) |
| `autograph` | Typed vault engine: schema enforcement, graph health, decay, MOC, dedup |

- **Processing:** `.claude/skills/dbrain-processor/SKILL.md`
- **Autograph:** `.claude/skills/autograph/SKILL.md`
- **Rules:** `.claude/rules/` (daily, thoughts, goals, obsidian-markdown, weekly-reflection)
- **Docs:** `.claude/docs/`

## Vault Graph (autograph)

**Purpose:** Analysis and maintenance of vault link structure.

**Usage:**
```bash
# Analyze vault
uv run vault/.claude/skills/autograph/scripts/graph.py health vault

# Result
vault/.graph/vault-graph.json  # JSON graph with stats
vault/.graph/report.md         # Human-readable report
```

**Domains:**
| Domain | Path | Hub |
|--------|------|-----|
| Personal | thoughts/, goals/, daily/ | MEMORY.md |
| Business | business/crm/, business/network/ | business/_index.md |
| Projects | projects/clients/, projects/leads/ | projects/_index.md |

## Available Agents

| Agent | Purpose |
|-------|---------|
| `note-organizer` | Organize vault, fix links |
| `inbox-processor` | GTD-style inbox processing |

## Path-Specific Rules

See `.claude/rules/` for format requirements:
- `daily-format.md` — daily files format
- `thoughts-format.md` — thought notes format
- `goals-format.md` — goals format
- `telegram-report.md` — HTML report format
- `obsidian-markdown.md` — Obsidian syntax rules
- `weekly-reflection.md` — weekly reflection template

## Report Format

Reports use Telegram HTML:
- `<b>bold</b>` for headers
- `<i>italic</i>` for metadata
- Only allowed tags: b, i, code, pre, a

## Quick Commands

| Command | Action |
|---------|--------|
| `/process` | Run daily processing |
| `/organize` | Organize vault |
| `/graph` | Analyze vault links |

## Task Management (PM Backlog)

**IMPORTANT:** When the user describes a task, idea, or something they need to do — ALWAYS use the `/pm-assistant` skill. Do NOT save tasks to vault files.

**The old database "Идеи и Задачи" has been deleted.** Never reference it.

All tasks go to **PM Backlog** (Notion Database ID: `22876284-e92f-4866-a908-3a3bda425637`).

### Triggers for `/pm-project` (roadmap — use INSTEAD of pm-assistant):
- User mentions a project they want to start or prioritize
- Phrases: "проект в приоритете", "взять в работу", "дорожная карта", "распланируй проект", "когда закончим", "расставь задачи по проекту", "покажи дорожную карту"
- User asks how long a project will take or wants a schedule for multiple weeks ahead
- User says they can't do a task or need to reschedule: "не успею", "не смогу", "сдвинь", "отпуск", "болею", "форс-мажор", "перенеси задачи", "перестрой даты", "сдвинь всё"

### Triggers for `/pm-assistant` (single task — use when NOT a multi-week project):
- User mentions a single task, idea, meeting, call, deadline
- Phrases: "нужно", "сделать", "задача", "напомни", "поставь", "запланируй", "добавь"
- Voice messages describing a single work item

### PM Backlog Fields
| Field | Values |
|-------|--------|
| `Задача` | Task title (required) |
| `Приоритет` | P1 / P2 / P3 / P4 |
| `Статус` | Бэклог / Спринт неделя / Выполняют сотрудники / Выполнено / Отменено |
| `Сложность` | Простая / Средняя / Сложная |
| `Дедлайн` | date |
| `Заметки` | context |
| `Ответственный` | Максим / Никита / Кристина / Ника |
| `Родительская задача` | parent task name (for subtasks only) |
| `Источник` | Platrum / Telegram / Ручной ввод |

### Дорожные карты проектов

Сохранённые дорожные карты: `/home/brain/projects/agent-second-brain/data/roadmaps/`

При планировании дня (`📅 Собрать план`): если в папке `roadmaps/` есть файлы —
прочитай их и проверь задачи по датам:
- Вычисли `WEEK_START` = понедельник текущей недели (YYYY-MM-DD)
- Вычисли `WEEK_END` = пятница текущей недели (YYYY-MM-DD)
- Найди задачи из дорожной карты, у которых `date >= WEEK_START` И `date <= WEEK_END`
- Только эти задачи (и только если их статус в PM Backlog сейчас «Бэклог») обнови на «Спринт недели»
- Задачи с `date > WEEK_END` — не трогать, они остаются «Бэклог» до своей недели

### Статус «Выполняют сотрудники»

Когда пользователь говорит что задачу делают другие — переноси статус на «Выполняют сотрудники».

**Триггерные фразы:**
- «поставил задачу», «передал», «делегировал», «отдал»
- «выполняют сотрудники», «делают сотрудники», «занимаются ребята»
- «передал своим», «поставил команде», «поручил»
- «взял подрядчик», «делает [имя]», «[имя] занимается»

**Действие:** найди задачу в PM Backlog по названию, обнови статус на «Выполняют сотрудники».
Если пользователь уточняет кто делает или на какой стадии — добавь это в тело страницы через:
```
curl -s -X PATCH "https://api.notion.com/v1/blocks/{PAGE_ID}/children" \
  -H "Authorization: Bearer ntn_c57737162465ObprBMHdaXLJ5mPvVPO8T3Hyk0mmTUJ6Eh" \
  -H "Notion-Version: 2022-06-28" \
  -H "Content-Type: application/json" \
  -d '{"children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": "👥 {ДАТА}: {КОНТЕКСТ}"}}]}}]}'
```

## Customization

For personal overrides: create `CLAUDE.local.md`

## Learnings (from experience)

1. **Don't rewrite working code** without reason (KISS, DRY, YAGNI)
2. **Don't add checks** that weren't there — let the agent decide
3. **Don't propose solutions** without studying git log/diff first
4. **Don't break architecture** (process.sh → Claude → skill is correct)
5. **Problems are usually simple** (e.g., sed one-liner for HTML fix)

---

*System Version: 3.0*
