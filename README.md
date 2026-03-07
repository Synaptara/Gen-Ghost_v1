# GhostCommander (Gen Ghost)

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Groq](https://img.shields.io/badge/LLM-Groq%20LLaMA-F55036)](https://groq.com/)
[![Discord Bot](https://img.shields.io/badge/Discord-Bot-5865F2?logo=discord&logoColor=white)](https://discord.com/developers/docs/intro)

GhostCommander is an autonomous Omni-Agent for Discord built in Python. It combines Groq-powered reasoning, persistent local memory, GitHub automation, job discovery, and system telemetry inside one command-driven bot.

## Features

- **Groq Brain:** Fast, low-cost reasoning with Groq-hosted LLaMA models.
- **Persistent Memory:** Uses a local SQLite database (`data/dev_stats.db`) and immutable `action_logs` for auditability.
- **Job Hunter:** Scrapes entry-level roles, bypasses ATS-heavy sources, deduplicates results, and injects qualified jobs into Notion.
- **Auto-Streaker:** Generates daily Python challenges, solves them, and commits autonomously to GitHub to maintain contribution streaks.
- **System Monitor:** Reports CPU/RAM status and recent GitHub Actions workflow states directly in Discord.
- **Discord-Native Workflow:** Slash commands, interactive confirmation views, reminders, and autonomous scheduled loops.

## Prerequisites

- Python 3.10+
- A Discord bot token
- A Groq API key
- A GitHub token
- A Notion API key

## Environment Variables

Create your runtime config file from the provided example:

```bash
cp .env.example .env
```

Then fill the values in `.env`:

- `DISCORD_TOKEN`
- `GROQ_API_KEY`
- `GITHUB_TOKEN`
- `NOTION_API_KEY`
- `NOTION_DATABASE_ID`
- `NOTION_JOB_DB_ID`
- `CHAT_CHANNEL_ID`
- `LOG_CHANNEL_ID`
- `FEED_CHANNEL_ID`
- `TRACKER_CHANNEL_ID`
- `JOB_ALERT_CHANNEL_ID`

## Installation (Native Python)

```bash
git clone https://github.com/<your-org-or-user>/Gen-Ghost_v1.git
cd Gen-Ghost_v1

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your real values

python main.py
```

## Running with Docker

```bash
docker compose up -d --build
```

The bot stores memory and audit logs in `data/dev_stats.db`. Keep `data/` mounted as a persistent volume so memory survives restarts.

## Project Layout

```text
.
|-- cogs/          # Discord cogs (commands, loops, automations)
|-- utils/         # Integrations, tools, scraping, UI helpers
|-- data/          # Runtime SQLite database (created at runtime)
|-- main.py        # Bot entrypoint
|-- requirements.txt
```

## Contributing

Contributions are welcome. Open an issue first for significant changes so scope can be agreed before implementation.

If you want to take on a feature request, comment:

`I'd like to work on this!`

This helps maintainers assign work and avoid duplicated effort.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
