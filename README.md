# GSO Ops AI Tools

> The GSO team's internal platform for sharing, discovering, and celebrating AI tools built at Block.

**Features:** Tool tiles · Community spotlight · Leaderboard · Weekly Tool of the Week · AOL AI chat (Claude-powered) · Virtual cork board · Best practices · Databricks SSO

---

## Quick Start (Local Dev)

```bash
git clone https://github.com/mhoferblock/gso-ops-ai-tools
cd gso-ops-ai-tools
cp .env.example .env
# Optional: add your Anthropic API key to .env for AI features
./run.sh
# → Open http://localhost:8000
```

All Python dependencies (FastAPI, uvicorn, anthropic, pydantic, requests) are pre-installed in the Block Python environment. No npm or additional installs needed.

---

## Deploying to Databricks Apps (Production with SSO)

### Prerequisites

| Tool | Install |
|------|---------|
| Databricks CLI v0.200+ | `pip install databricks-cli` or download from releases |
| Databricks workspace | Must have Apps enabled (contact your workspace admin) |
| Unity Catalog | For persistent storage via Volumes |

---

### Step 1 — Authenticate the CLI

```bash
databricks configure --token
# Enter your workspace URL: https://<your-workspace>.azuredatabricks.net
# Enter your personal access token (Settings → Developer → Access tokens)
```

Verify:
```bash
databricks current-user me
```

---

### Step 2 — Create a Unity Catalog Volume (persistent storage)

The app stores its SQLite database in a Databricks Volume so data survives app restarts.

```sql
-- Run in a Databricks notebook or SQL editor
CREATE CATALOG IF NOT EXISTS main;
CREATE SCHEMA IF NOT EXISTS main.gso_ops;
CREATE VOLUME IF NOT EXISTS main.gso_ops.app_data;
```

> **Note:** If your workspace uses a different default catalog, replace `main` with your catalog name throughout.

---

### Step 3 — Create a secret scope for credentials

```bash
# Create the scope
databricks secrets create-scope gso-ops

# Add your Anthropic API key (enables AI summarization + chat)
databricks secrets put-secret gso-ops anthropic-api-key
# → Paste your key when prompted

# Generate and store a strong signing secret
python3 -c "import secrets; print(secrets.token_hex(32))" | \
  xargs -I{} databricks secrets put-secret gso-ops secret-key --string-value {}
```

---

### Step 4 — Review `app.yaml`

The `app.yaml` is pre-configured. Update the `DB_PATH` value if your catalog/schema differ from `main`/`gso_ops`:

```yaml
# app.yaml
env:
  - name: DB_PATH
    value: "/Volumes/main/gso_ops/app_data/gso_tools.db"   # ← update if needed
```

---

### Step 5 — Deploy the app

```bash
# From the repo root
databricks apps deploy gso-ops-ai-tools --source-code-path .
```

First deploy takes ~2 minutes. Subsequent deploys are faster.

---

### Step 6 — Open the app

```bash
databricks apps get gso-ops-ai-tools
# Look for the "url" field in the output
```

Or find it in your workspace: **Compute → Apps → gso-ops-ai-tools**.

---

### How SSO works

When a user visits the app URL on Databricks, the platform:
1. Requires them to authenticate with their Block/Databricks credentials (standard workspace SSO)
2. Injects their email address into every request via the `X-Forwarded-User` header
3. The app reads that header, auto-creates their account on first visit, and signs them in — **no username/password form needed**

The manual sign-in form is still present for local development but is never shown on Databricks because SSO auto-authenticates before the page renders.

---

### Updating the app

```bash
git pull
databricks apps deploy gso-ops-ai-tools --source-code-path .
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(empty)_ | Enables AI tool summarization and the AOL AI chat assistant |
| `SECRET_KEY` | _(random, ephemeral)_ | Token signing key — **set a real value in production** |
| `DB_PATH` | `./data/gso_tools.db` | Path to SQLite DB file — point to a Unity Catalog Volume in production |

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              Databricks App                  │
│                                              │
│  ┌──────────┐     ┌────────────────────────┐ │
│  │ FastAPI  │────▶│  SQLite on UC Volume   │ │
│  │ (Python) │     │  /Volumes/main/gso_ops │ │
│  └──────────┘     └────────────────────────┘ │
│       │                                      │
│       ├── /api/auth/sso  ← X-Forwarded-User  │
│       ├── /api/tools     ← CRUD              │
│       ├── /api/chat      ← SSE + Claude      │
│       └── /static        ← Vanilla JS SPA    │
│                                              │
│  ┌──────────────────────────────────────────┐ │
│  │  Databricks SSO  (workspace identity)   │ │
│  └──────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

**Tech stack:**
- **Backend:** FastAPI + SQLite (persistent via Databricks Volume)
- **Frontend:** Vanilla JS SPA, Tailwind CSS (CDN), Canvas Confetti
- **AI:** Anthropic Claude (tool summarization + AOL AI chat)
- **Auth:** Databricks SSO in production; username/password in local dev
- **Deploy:** Databricks Apps (`app.yaml`)

---

## API Reference

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | Local dev login (username + display name) |
| POST | `/api/auth/sso` | Databricks SSO auto-login (reads `X-Forwarded-User`) |
| GET | `/api/auth/me` | Current user info |
| PUT | `/api/auth/me` | Update profile |

### Tools
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tools` | List tools (`?sort=newest\|most_voted\|most_used&q=search`) |
| POST | `/api/tools` | Submit a new tool |
| DELETE | `/api/tools/{id}` | Delete your tool |
| POST | `/api/tools/{id}/vote` | Upvote a tool |
| POST | `/api/tools/{id}/click` | Track a tool open |
| GET | `/api/tools/featured` | Featured tools for homepage |

### Leaderboard & Weekly
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/leaderboard` | All leaderboard data |
| POST | `/api/weekly-vote/{tool_id}` | Cast weekly vote |
| GET | `/api/weekly-winner` | Latest weekly winner |

### Chat & Activity
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/chat` | Chat messages (SSE-compatible polling) |
| POST | `/api/chat` | Post a message |
| POST | `/api/chat/ask` | Ask AOL AI directly |
| GET | `/api/activity` | Activity feed |

### Cork Board
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/board` | All active board notes |
| POST | `/api/board` | Pin a new note |
| PUT | `/api/board/{id}/position` | Move a note |
| DELETE | `/api/board/{id}` | Remove your note |

### Other
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stats` | Platform stats |
| GET | `/api/config` | Server mode (SSO, AI enabled) |
| GET | `/api/health` | Liveness probe |
| POST | `/api/summarize` | Auto-summarize a tool URL via Claude |

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set env vars (copy and edit)
cp .env.example .env

# Start the server with hot-reload
./run.sh

# Or directly:
python3 -m uvicorn app.main:app --reload --port 8000
```

Database is created automatically at `./data/gso_tools.db` on first run.
