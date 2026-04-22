# GSO Ops AI Tools

> The community platform where the GSO team shares, discovers, and celebrates AI tools they've built.

## Features

- **Tool submission** — Drop a URL and Claude auto-writes the description
- **Community tiles** — Big, browsable tile grid sorted by newest / most voted / most used
- **Personal profiles** — Bio, photo initials, favorite AI project, your tool collection
- **Leaderboard** — Most-clicked (tracked on every open) and most-voted rankings
- **Weekly voting** — Vote for your favorite tool of the week; winner announced Monday with confetti
- **AOL AI Chat** — Retro AOL Instant Messenger–style community chat room
  - Start a message with `@AOL_AI` or `?` to ask the Claude-powered AI assistant
- **Activity feed** — Live RSS-style stream of new tools and votes
- **Best practices board** — Post and browse team AI best practices
- **Databricks-ready** — All data in SQLite (local dev) → swap to Delta Lake on Databricks

## Quick Start

```bash
git clone https://github.com/mhoferblock/gso-ops-ai-tools
cd gso-ops-ai-tools
cp .env.example .env
# Optional: add your Anthropic API key to .env for AI summarization + chat
./run.sh
# → Open http://localhost:8000
```

All Python dependencies (FastAPI, uvicorn, anthropic, pydantic, requests) are pre-installed in your Block Python environment. No npm or additional installs needed.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(empty)_ | Enables AI tool summarization and the AOL AI chat assistant |
| `SECRET_KEY` | `gso-ops-ai-tools-secret-2024` | JWT signing key — change in production |
| `PORT` | `8000` | Port to run on |

The app works fully without an Anthropic key — AI features gracefully degrade to placeholder text.

## Architecture

```
gso-ops-ai-tools/
├── app/
│   └── main.py          # FastAPI app, all routes, DB, AI service
├── web/
│   ├── index.html       # Single-page app (all sections)
│   └── static/
│       ├── app.js       # All JavaScript (routing, API client, UI)
│       └── styles.css   # Custom styles + AOL chat + animations
├── data/
│   └── gso_tools.db     # SQLite (auto-created, auto-seeded)
├── run.sh
├── requirements.txt
└── .env.example
```

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/auth/login` | Create/login user, get token |
| GET | `/api/tools` | List tools (sort, search, owner filter) |
| POST | `/api/tools` | Submit a new tool |
| DELETE | `/api/tools/:id` | Delete your tool |
| POST | `/api/tools/:id/click` | Track a tool open |
| POST | `/api/tools/:id/vote` | Toggle vote |
| GET | `/api/leaderboard` | Most used + most voted + weekly history |
| POST | `/api/weekly-vote/:id` | Cast weekly vote (one per week) |
| GET | `/api/winner` | Get current week winner |
| GET | `/api/users` | List all users |
| GET | `/api/users/:username` | User profile + their tools |
| GET | `/api/chat` | Get chat messages |
| POST | `/api/chat` | Post a chat message |
| POST | `/api/ask` | Ask AOL AI (Claude) |
| GET | `/api/feed` | Activity feed |
| GET | `/api/best-practices` | List best practices |
| POST | `/api/best-practices` | Post a best practice |
| POST | `/api/summarize` | Auto-summarize a tool URL via Claude |
| GET | `/api/stats` | Platform stats |

## Databricks Deployment

1. Create a Databricks App in your workspace
2. Replace SQLite with Delta Lake tables (schema in `app/main.py` `init_db()`)
3. Replace the simple JWT auth with Databricks SSO (Workspace identity flows automatically in Databricks Apps)
4. Set `ANTHROPIC_API_KEY` in your App environment variables
5. Deploy with `databricks apps deploy`

## Demo Users

The app seeds 6 demo users on first run. Click any name in the Sign In modal to log in as them:

| Username | Name | Focus |
|---|---|---|
| `sarah_j` | Sarah Johnson | Data analytics, anomaly detection |
| `marcus_t` | Marcus Thompson | Security operations, Claude workflows |
| `priya_k` | Priya Kapoor | Product ops, meeting automation |
| `devon_r` | Devon Rivera | Data engineering, NL-SQL |
| `alex_c` | Alex Chen | Strategy, competitive intelligence |
| `morgan_l` | Morgan Lee | Trust & Safety, risk signals |
