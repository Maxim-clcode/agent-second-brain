# Agent Second Brain

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Stars](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Frepos%2Fsmixs%2Fagent-second-brain&query=%24.stargazers_count&label=stars&logo=github&color=blue)](https://github.com/smixs/agent-second-brain/stargazers)
[![Forks](https://img.shields.io/github/forks/smixs/agent-second-brain?style=flat&logo=github)](https://github.com/smixs/agent-second-brain/forks)

<p align="center">
  <img alt="Agent Second Brain" src="https://github.com/user-attachments/assets/c1b8a1db-bf4d-40d1-b54f-a7dbdce4afda" />
</p>

A voice-first AI assistant that turns your Telegram messages into an organized knowledge base. Send voice notes, text, photos, or forwarded messages — the agent classifies everything, creates tasks in Todoist, saves thoughts to an Obsidian vault, tracks your goals, and sends you a daily report. Your personal second brain that runs 24/7 on a $5 VPS.

[&#x1F1F7;&#x1F1FA; Читать на русском](README.ru.md)

---

## What's New in v2

- **Conversational Agent** — Talk to the bot like a real assistant. No need to press buttons — just send a voice note or text message and have a natural dialogue. Ask follow-up questions, clarify, change your mind. The agent remembers context within the session and has full access to your vault, goals, and Todoist.
- **Agent Memory** — Ebbinghaus forgetting curve with tiered decay. Memory cards move through tiers (core / active / warm / cold / archive) based on access patterns and time.
- **Vault Health** — Automated health scoring, Map of Content (MOC) generation, broken link repair, and orphan detection across your entire vault.
- **3-Phase Pipeline** — Processing is now split into CAPTURE, EXECUTE, and REFLECT phases with structured JSON handoff between each stage.
- **Processing Rules** — Configurable formatting rules for Obsidian markdown and weekly reflections.
- **Memory Config** — `.memory-config.json` file for fine-tuning decay rates, tier thresholds, and type inference rules.

---

## Architecture

```
Telegram (voice / text / photo / forwarded messages)
    |
    v
Telegram Bot (Node.js) --> Deepgram (voice-to-text)
    |
    v
Claude Code (--print --dangerously-skip-permissions)
    |
    v
+-- CAPTURE: classify entries --> JSON
+-- EXECUTE: Todoist tasks + vault writes --> JSON
+-- REFLECT: HTML report + MEMORY update
    |
    v
+-- Todoist (tasks)
+-- Obsidian vault (thoughts, CRM, goals)
+-- Telegram (HTML report)
+-- GitHub (git push)
```

The bot runs as a systemd service on a VPS. A daily timer (21:00 UTC) triggers the 3-phase processing pipeline via `scripts/process.sh`. Each phase receives the previous phase's output as structured JSON, ensuring reliable handoff and auditability.

---

## Skills Catalog

| Skill | Purpose |
|-------|---------|
| **dbrain-processor** | Core pipeline: classify voice/text entries, create tasks, save thoughts |
| **agent-memory** | Ebbinghaus decay engine: tiered search, creative recall, memory stats |
| **vault-health** | Health scoring, MOC generation, link repair, orphan detection |
| **graph-builder** | Knowledge graph analysis, entity relationships, domain mapping |
| **todoist-ai** | Task management via mcp-cli (Todoist MCP integration) |

Skills live in `vault/.claude/skills/` and are invoked automatically by Claude Code during processing.

---

## Quick Start

### 1. Fork this repo

Go to [github.com/smixs/agent-second-brain](https://github.com/smixs/agent-second-brain) and click **Fork**. Make the fork **private** — it will contain your personal data.

### 2. Clone to your machine

```bash
git clone https://github.com/YOUR_USERNAME/agent-second-brain.git
cd agent-second-brain
```

### 3. Fill in templates

Open the following files and replace the placeholders with your information:

- `vault/goals/0-vision-3y.md` — 3-year vision
- `vault/goals/1-yearly.md` — yearly goals
- `vault/goals/2-monthly.md` — monthly priorities
- `vault/goals/3-weekly.md` — weekly focus (ONE Big Thing)
- `vault/.claude/skills/dbrain-processor/references/about.md` — your profile
- `vault/.claude/skills/dbrain-processor/references/classification.md` — entry categories

### 4. Get API keys

| Token | Where to get it |
|-------|-----------------|
| Telegram Bot Token | [@BotFather](https://t.me/BotFather) in Telegram |
| Your Telegram ID | [@userinfobot](https://t.me/userinfobot) in Telegram |
| Deepgram API Key | [console.deepgram.com](https://console.deepgram.com/) |
| Todoist API Token | [todoist.com](https://todoist.com) → Settings → Integrations → Developer |

### 5. Deploy to VPS

Follow the detailed server setup guide: **[docs/vps-setup.md](docs/vps-setup.md)**

Short version:

```bash
ssh root@YOUR_SERVER_IP
# Create a user, install dependencies, clone repo, configure .env
curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/agent-second-brain/main/bootstrap.sh | bash
```

---

## Configuration

| File | Purpose |
|------|---------|
| `.env` | API tokens for Telegram, Deepgram, Todoist (copy from `.env.example`) |
| `.memory-config.json` | Decay rates, tier thresholds, type inference rules for agent memory |
| `mcp-config.json` | MCP server configuration (Todoist and other integrations) |
| `vault/.claude/CLAUDE.md` | Agent instructions, session bootstrap sequence, navigation rules |
| `vault/.claude/skills/dbrain-processor/references/about.md` | Your personal profile — helps the agent understand context |

All secrets go into `.env` which is gitignored. Never commit API tokens to the repository.

---

## Vault Structure

```
vault/
├── daily/              # Daily entries (voice transcripts, notes)
├── goals/              # Vision, yearly, monthly, weekly goals
├── business/
│   ├── crm/            # Client cards
│   └── network/        # Professional contacts
├── projects/
│   ├── clients/        # Client project notes
│   └── leads/          # Sales pipeline
├── thoughts/
│   ├── ideas/          # Ideas and brainstorms
│   ├── learnings/      # Lessons learned
│   └── reflections/    # Personal reflections
├── templates/          # Card templates (CRM, daily)
├── MOC/                # Maps of Content (auto-generated)
├── MEMORY.md           # Long-term memory (hot context)
└── .claude/            # Agent configuration
    ├── skills/         # 5 skills (processor, memory, health, graph, todoist)
    ├── rules/          # Formatting rules (obsidian-markdown, weekly-reflection)
    └── docs/           # Reference documentation
```

The vault follows a Zettelkasten-inspired structure. Each domain has an `_index.md` hub file. Navigation starts from hubs and follows wiki-links.

---

## How It Works

### 3-Phase Pipeline

The daily processing pipeline runs in three isolated phases:

1. **CAPTURE** — Reads `daily/YYYY-MM-DD.md`, classifies each entry (task, thought, idea, CRM update, goal progress), and outputs structured JSON.
2. **EXECUTE** — Takes the classification JSON, creates Todoist tasks, writes vault files, updates CRM cards, and outputs an execution report as JSON.
3. **REFLECT** — Generates an HTML summary report, updates `MEMORY.md` with key decisions, and sends the report to Telegram.

Each phase runs as a separate Claude Code invocation. JSON is passed between phases via files, ensuring clean boundaries and debuggability.

### Memory Decay

Agent memory follows the Ebbinghaus forgetting curve with linear decay:

- **Decay rate:** 0.015 per day
- **Floor:** 0.1 (memories never fully disappear)
- **Formula:** `strength = max(floor, initial - rate * days_since_access)`

Memory cards move through five tiers based on their current strength:

| Tier | Strength Range | Behavior |
|------|---------------|----------|
| **core** | 0.9 - 1.0 | Always loaded into context |
| **active** | 0.7 - 0.9 | Included in heartbeat searches |
| **warm** | 0.4 - 0.7 | Included in normal searches |
| **cold** | 0.1 - 0.4 | Only in deep searches |
| **archive** | 0.0 - 0.1 | Candidates for creative recall |

### Tiered Search

Different search depths retrieve different tiers:

- **heartbeat** — core + active (fast, low-cost)
- **normal** — core + active + warm (default)
- **deep** — all tiers (thorough)
- **creative** — random sampling from cold/archive (serendipity)

### Health Scoring

Vault health is scored on a 100-point scale:

```
health = 100 - orphan_penalty - broken_penalty - density_penalty - desc_penalty
```

- **orphan_penalty** — files with no incoming or outgoing links
- **broken_penalty** — wiki-links pointing to non-existent files
- **density_penalty** — low average link count per file
- **desc_penalty** — files missing retrieval filter descriptions in frontmatter

The vault-health skill auto-generates MOCs, repairs broken links, and suggests connections for orphans.

---

## Cost Breakdown

| Service | Cost | Purpose |
|---------|------|---------|
| Claude Pro | $20/mo | AI processing (Claude Code) |
| VPS | ~$5/mo | 24/7 bot hosting |
| Deepgram | Free ($200 credit) | Voice transcription |
| Todoist | Free / $4/mo Pro | Task management |
| **Total** | **~$25/mo** | |

---

## Bot Commands

The Telegram bot presents an inline keyboard with the following buttons:

| Button | Action |
|--------|--------|
| 📊 Status | Show today's entry count and processing status |
| ⚙️ Process | Run the 3-phase processing pipeline on demand |
| 📅 Week | Generate and send a weekly digest |
| ✨ Query | Ask the agent anything — it reads your vault and Todoist |
| ❓ Help | Show available commands and usage tips |

### Talk to it like a real assistant

Buttons are just shortcuts. You can **message the bot directly** — voice or text — and have a full conversation. Ask follow-up questions, clarify, argue, brainstorm. It remembers context within the session and has access to your entire vault, goals, and Todoist.

**What you can send:**

- **Voice notes** — transcribed via Deepgram, saved to daily file
- **Text** — links, quotes, quick thoughts
- **Photos** — saved with AI-generated descriptions
- **Forwarded messages** — saved with source attribution

**Example conversation:**

> You: "What did I write about the marketing project last week?"
> Bot: *finds and shows relevant entries*
> You: "Turn the second idea into a Todoist task for Monday"
> Bot: *creates the task*
> You: "Actually make it high priority and add a subtask for the presentation"
> Bot: *updates the task*

---

## License

[MIT](LICENSE)
