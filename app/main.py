"""
GSO Ops AI Tools — FastAPI backend
All-in-one: database, seed data, AI service, and API routes.
"""
import os, json, re, base64, sqlite3, asyncio
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Header, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests as http_requests

try:
    import anthropic as _anthropic
    _ai_client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
except Exception:
    _ai_client = None

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent.parent
DATA_DIR  = BASE_DIR / "data"
WEB_DIR   = BASE_DIR / "web"
DB_PATH   = DATA_DIR / "gso_tools.db"
SECRET    = os.getenv("SECRET_KEY", "gso-ops-ai-tools-secret-2024")

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Database ─────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def row(r):
    if r is None:
        return None
    d = dict(r)
    for k in ("tags",):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                d[k] = []
    return d

def rows(rs):
    return [row(r) for r in rs]

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            email TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            favorite_ai_project TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            description TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            thumbnail_url TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            click_count INTEGER DEFAULT 0,
            vote_count INTEGER DEFAULT 0,
            is_featured INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            tool_id INTEGER NOT NULL REFERENCES tools(id),
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, tool_id)
        );
        CREATE TABLE IF NOT EXISTS weekly_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            tool_id INTEGER NOT NULL REFERENCES tools(id),
            week_start TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, week_start)
        );
        CREATE TABLE IF NOT EXISTS weekly_winners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_id INTEGER NOT NULL REFERENCES tools(id),
            week_start TEXT NOT NULL UNIQUE,
            votes_at_time INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS winner_seen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            winner_id INTEGER NOT NULL REFERENCES weekly_winners(id),
            seen_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, winner_id)
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            username TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL,
            is_bot INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS best_practices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            author_name TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS activity_feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            user_id INTEGER REFERENCES users(id),
            tool_id INTEGER REFERENCES tools(id),
            message TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS board_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            author_name TEXT NOT NULL,
            message TEXT DEFAULT '',
            color TEXT DEFAULT '#FFEF5E',
            emoji TEXT DEFAULT '',
            drawing_data TEXT DEFAULT '',
            pen_color TEXT DEFAULT '#000000',
            x_pos REAL DEFAULT 100.0,
            y_pos REAL DEFAULT 100.0,
            rotation REAL DEFAULT 0.0,
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT DEFAULT (datetime('now', '+7 days'))
        );
    """)
    conn.commit()
    conn.close()

def seed_demo_data():
    conn = get_db()
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
        conn.close()
        return

    users = [
        ("sarah_j",  "Sarah Johnson",  "sarah.j@block.xyz",
         "Senior Data Analyst who loves building AI workflows to automate repetitive reporting tasks.",
         "AI-powered anomaly detection for transaction data"),
        ("marcus_t", "Marcus Thompson","marcus.t@block.xyz",
         "Operations specialist passionate about using Claude to streamline GSO processes.",
         "Claude-based SOC analysis assistant"),
        ("priya_k",  "Priya Kapoor",   "priya.k@block.xyz",
         "Product ops manager using AI to connect the dots across teams and tools.",
         "AI meeting summarizer + action item tracker"),
        ("devon_r",  "Devon Rivera",   "devon.r@block.xyz",
         "Data engineer building pipelines and AI tools to make data more accessible.",
         "Natural language SQL query builder"),
        ("alex_c",   "Alex Chen",      "alex.c@block.xyz",
         "Strategy analyst using AI to distill complex market signals into clear insights.",
         "Competitive intelligence dashboard"),
        ("morgan_l", "Morgan Lee",     "morgan.l@block.xyz",
         "Trust & Safety analyst leveraging AI to move faster on investigations.",
         "Risk signal pattern recognition tool"),
    ]
    for u in users:
        conn.execute(
            "INSERT INTO users (username,display_name,email,bio,favorite_ai_project) VALUES (?,?,?,?,?)", u)
    conn.commit()

    uid = {r["username"]: r["id"] for r in
           rows(conn.execute("SELECT id,username FROM users").fetchall())}

    tools = [
        (uid["sarah_j"],  "Transaction Anomaly Detector",
         "https://databricks.com",
         "Detects unusual transaction patterns using ML",
         "An AI-powered tool that monitors real-time transaction data and flags anomalies using a fine-tuned isolation forest model. Integrates with Databricks and sends Slack alerts when suspicious patterns are detected. Reduces manual review time by 70%.",
         '["databricks","ml","transactions"]', 42, 18, 1),
        (uid["marcus_t"], "Claude SOC Assistant",
         "https://claude.ai",
         "AI copilot for Security Operations Center analysts",
         "A Claude-powered assistant embedded in the SOC workflow that helps analysts triage alerts, look up historical context, draft incident reports, and suggest remediation steps. Connected to internal runbooks and Jira.",
         '["claude","security","productivity"]', 38, 15, 1),
        (uid["priya_k"],  "Meeting Intelligence Hub",
         "https://databricks.com",
         "AI meeting summarizer with action item extraction",
         "Transcribes Zoom/Meet recordings, generates structured summaries, extracts action items with owners, and posts them to Confluence and Slack. Uses Whisper for transcription and Claude for summarization.",
         '["meetings","productivity","claude"]', 31, 12, 0),
        (uid["devon_r"],  "NL-SQL Builder",
         "https://databricks.com",
         "Ask questions about your data in plain English",
         "A natural language interface to your Databricks SQL warehouse. Type questions like 'top 10 markets by GMV last quarter' and get instant SQL plus a visualization. Supports schema exploration and query explanation.",
         '["databricks","sql","nlp"]', 27, 11, 1),
        (uid["alex_c"],   "Competitive Intel Dashboard",
         "https://databricks.com",
         "Real-time competitive landscape tracker powered by AI",
         "Scrapes public sources to track competitor moves, summarizes findings with Claude, and presents them in an interactive dashboard. Weekly digest emails delivered to stakeholders automatically.",
         '["research","competitive-intel","automation"]', 19, 8, 0),
        (uid["morgan_l"], "Risk Signal Analyzer",
         "https://databricks.com",
         "Pattern recognition for Trust & Safety teams",
         "Aggregates risk signals across multiple data sources and uses ML to surface high-confidence patterns. Helps T&S analysts prioritize their work queue and see the full picture on any entity.",
         '["trust-safety","ml","analytics"]', 24, 10, 0),
        (uid["sarah_j"],  "Report Autopilot",
         "https://databricks.com",
         "Automated weekly business report generator",
         "Pulls data from Snowflake, generates narrative insights with Claude, and delivers formatted reports to stakeholders every Monday morning. Supports custom templates and follow-up questions.",
         '["reporting","automation","claude"]', 15, 6, 0),
        (uid["devon_r"],  "Data Dictionary Builder",
         "https://databricks.com",
         "Auto-generates documentation for your Delta tables",
         "Scans your Unity Catalog, samples table data, and uses Claude to generate human-readable descriptions for tables and columns. Keeps your data dictionary up to date automatically.",
         '["databricks","documentation","unity-catalog"]', 12, 5, 0),
    ]
    for t in tools:
        conn.execute(
            "INSERT INTO tools "
            "(owner_id,name,url,description,summary,tags,click_count,vote_count,is_featured) "
            "VALUES (?,?,?,?,?,?,?,?,?)", t)
    conn.commit()

    tids = [r["id"] for r in rows(conn.execute("SELECT id FROM tools").fetchall())]
    for i, user_id in enumerate(uid.values()):
        for tid in tids[:3 + (i % 4)]:
            conn.execute("INSERT OR IGNORE INTO votes (user_id,tool_id) VALUES (?,?)", (user_id, tid))
    conn.commit()

    today = date.today()
    last_mon = today - timedelta(days=today.weekday() + 7)
    conn.execute(
        "INSERT INTO weekly_winners (tool_id,week_start,votes_at_time) VALUES (?,?,?)",
        (tids[0], last_mon.isoformat(), 42))
    conn.commit()

    chat_seed = [
        (uid["sarah_j"],  "sarah_j",  "Sarah Johnson",
         "Hey everyone! Just deployed Transaction Anomaly Detector v2.0 — now with 95% accuracy!", 0),
        (uid["marcus_t"], "marcus_t", "Marcus Thompson",
         "Incredible Sarah! The new Claude model improvements in my SOC Assistant are massive too.", 0),
        (uid["priya_k"],  "priya_k",  "Priya Kapoor",
         "Has anyone tried connecting Claude to Confluence? Trying to make the Meeting Hub smarter.", 0),
        (None, "AOL_AI", "AOL AI",
         "Welcome to GSO Ops AI Tools! I'm AOL AI, your resident assistant. Ask me anything about AI tools, prompting, Databricks, or how to get started building. IM me anytime!", 1),
        (uid["devon_r"],  "devon_r",  "Devon Rivera",
         "NL-SQL Builder now supports Delta Live Tables queries! Huge for streaming use cases.", 0),
        (uid["alex_c"],   "alex_c",   "Alex Chen",
         "Can the leaderboard surface tools by team/domain? That would be super useful for discovery.", 0),
    ]
    for m in chat_seed:
        conn.execute(
            "INSERT INTO chat_messages (user_id,username,display_name,message,is_bot) VALUES (?,?,?,?,?)", m)
    conn.commit()

    activities = [
        ("tool_added", uid["devon_r"],  tids[7], "Devon Rivera added Data Dictionary Builder"),
        ("tool_added", uid["sarah_j"],  tids[6], "Sarah Johnson added Report Autopilot"),
        ("vote",       uid["morgan_l"], tids[0], "Morgan Lee voted for Transaction Anomaly Detector"),
        ("tool_added", uid["morgan_l"], tids[5], "Morgan Lee added Risk Signal Analyzer"),
        ("vote",       uid["alex_c"],   tids[1], "Alex Chen voted for Claude SOC Assistant"),
        ("tool_added", uid["alex_c"],   tids[4], "Alex Chen added Competitive Intel Dashboard"),
        ("tool_added", uid["priya_k"],  tids[2], "Priya Kapoor added Meeting Intelligence Hub"),
        ("vote",       uid["devon_r"],  tids[2], "Devon Rivera voted for Meeting Intelligence Hub"),
    ]
    for a in activities:
        conn.execute(
            "INSERT INTO activity_feed (event_type,user_id,tool_id,message) VALUES (?,?,?,?)", a)
    conn.commit()

    practices = [
        (uid["sarah_j"],  "Sarah Johnson",
         "Always validate AI output before production",
         "Before deploying any AI tool to production, run a validation suite. For Claude integrations, test edge cases with adversarial inputs. For ML models, maintain a holdout test set you never train on. Document your validation methodology — future-you will thank you."),
        (uid["marcus_t"], "Marcus Thompson",
         "Use structured outputs for reliability",
         "When using Claude or other LLMs, always request structured JSON output rather than free-form text when you need to parse results. This dramatically reduces parsing errors. Pydantic + Claude's structured output feature is a winning combo that makes your tools production-ready."),
        (uid["devon_r"],  "Devon Rivera",
         "Version control your prompts",
         "Treat your LLM prompts like code. Version them in Git, write tests for them, and review changes carefully. A small change to a system prompt can have huge downstream effects. Use a prompt registry pattern and track performance metrics per version."),
    ]
    for p in practices:
        conn.execute(
            "INSERT INTO best_practices (user_id,author_name,title,content) VALUES (?,?,?,?)", p)
    conn.commit()

    # Seed board notes
    import random
    board_notes = [
        (uid["sarah_j"],  "Sarah Johnson",
         "New anomaly model is live! 🚀 95% precision on the holdout set.",
         "#FFEF5E", "🚀", "", 80, 60, -2.5),
        (uid["marcus_t"], "Marcus Thompson",
         "Reminder: Claude SOC demo is Thursday 2pm PT. Come see it live!",
         "#B8E0FF", "📅", "", 280, 140, 1.8),
        (uid["priya_k"],  "Priya Kapoor",
         "Meeting Hub just got Confluence integration! Link in Slack.",
         "#A8F5A0", "✅", "", 520, 80, -1.2),
        (uid["devon_r"],  "Devon Rivera",
         "NL-SQL now supports DLT! Ask it anything about your streaming tables.",
         "#FFBF69", "💡", "", 160, 300, 3.0),
        (uid["alex_c"],   "Alex Chen",
         "Great quarter everyone. Competitive dashboard showed 3 new signals this week.",
         "#E8B4F8", "📊", "", 440, 260, -3.5),
        (uid["morgan_l"], "Morgan Lee",
         "T&S risk model update — false positive rate down 40%. Big win!",
         "#FF8DA1", "🎉", "", 680, 120, 2.1),
    ]
    for n in board_notes:
        conn.execute("""
            INSERT INTO board_notes
            (user_id,author_name,message,color,emoji,drawing_data,x_pos,y_pos,rotation)
            VALUES (?,?,?,?,?,?,?,?,?)""", n)
    conn.commit()
    conn.close()

# ── Auth ─────────────────────────────────────────────────────────────────────
def encode_token(user_id: int) -> str:
    payload = f"{user_id}:{SECRET}"
    return base64.b64encode(payload.encode()).decode()

def decode_token(token: str) -> Optional[int]:
    try:
        decoded = base64.b64decode(token.encode()).decode()
        uid_s, secret = decoded.rsplit(":", 1)
        if secret == SECRET:
            return int(uid_s)
    except Exception:
        pass
    return None

async def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    uid = decode_token(authorization.split(" ", 1)[1])
    if not uid:
        return None
    conn = get_db()
    u = row(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    conn.close()
    return u

async def require_user(authorization: Optional[str] = Header(None)):
    u = await get_current_user(authorization)
    if not u:
        raise HTTPException(401, "Not authenticated")
    return u

# ── AI Service ───────────────────────────────────────────────────────────────
GSO_SYSTEM = """You are AOL AI — the friendly AI assistant inside GSO Ops AI Tools, Block's internal platform where the GSO team shares AI tools they've built.

You help users with:
- Questions about the tools on this platform
- AI/ML best practices (prompting, Claude usage, Databricks workflows)  
- How to build their own AI tools
- General technical guidance

Keep responses concise (2-3 paragraphs), friendly, and bring some of that old-school AOL IM energy!"""

async def summarize_url(url: str) -> dict:
    if not _ai_client or not os.getenv("ANTHROPIC_API_KEY"):
        return {
            "name": "My AI Tool",
            "summary": f"An AI-powered tool accessible at {url}. Add your ANTHROPIC_API_KEY to enable auto-summarization.",
            "description": "Describe what your tool does.",
            "tags": ["ai", "tool"],
        }
    try:
        page_text = ""
        try:
            resp = http_requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                page_text = re.sub(r"<[^>]+>", " ", resp.text)[:3000]
        except Exception:
            pass

        msg = _ai_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=400,
            messages=[{"role": "user", "content": (
                f"Analyze this AI tool URL and generate directory metadata.\n\n"
                f"URL: {url}\nPage excerpt: {page_text[:1500] if page_text else 'unavailable'}\n\n"
                f"Return ONLY valid JSON with: name (3-5 words), summary (2-3 sentences), "
                f"description (one sentence), tags (2-4 items from: databricks,claude,ml,"
                f"automation,productivity,analytics,sql,reporting,nlp,research,security)"
            )}],
        )
        text = msg.content[0].text.strip()
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except Exception as e:
        return {
            "name": "AI Tool",
            "summary": f"A tool at {url}. Edit this description to explain what it does.",
            "description": "Add a description for your tool.",
            "tags": ["ai"],
        }

async def get_ai_response(message: str, history: List[dict]) -> str:
    if not _ai_client or not os.getenv("ANTHROPIC_API_KEY"):
        return (
            "AOL AI is currently offline (add ANTHROPIC_API_KEY to .env to enable). "
            "But hey — the GSO community is right here! Ask your question in chat and someone will answer. :)"
        )
    try:
        msgs = []
        for h in history[-8:]:
            if h.get("role") in ("user", "assistant"):
                msgs.append({"role": h["role"], "content": h["content"]})
        msgs.append({"role": "user", "content": message})
        resp = _ai_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            system=GSO_SYSTEM,
            messages=msgs,
        )
        return resp.content[0].text
    except Exception as e:
        return f"AOL AI hit a snag: {str(e)[:120]}. Try again in a moment!"

# ── Pydantic models ───────────────────────────────────────────────────────────
class LoginReq(BaseModel):
    username: str
    display_name: str
    email: Optional[str] = None

class ProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    favorite_ai_project: Optional[str] = None
    avatar_url: Optional[str] = None

class ToolCreate(BaseModel):
    name: str
    url: str
    description: str = ""
    summary: str = ""
    tags: List[str] = []

class ChatMsg(BaseModel):
    message: str

class AskReq(BaseModel):
    message: str
    history: Optional[List[dict]] = []

class BestPractice(BaseModel):
    title: str
    content: str

class SummarizeReq(BaseModel):
    url: str

class BoardNoteCreate(BaseModel):
    message: str = ""
    color: str = "#FFEF5E"
    emoji: str = ""
    drawing_data: str = ""
    pen_color: str = "#000000"
    x_pos: float = 100.0
    y_pos: float = 100.0
    rotation: float = 0.0

class BoardNotePosition(BaseModel):
    x_pos: float
    y_pos: float

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="GSO Ops AI Tools", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

@app.on_event("startup")
async def on_startup():
    init_db()
    seed_demo_data()

@app.get("/", include_in_schema=False)
async def serve_spa():
    return FileResponse(str(WEB_DIR / "index.html"))

# ── Auth routes ───────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
async def login(req: LoginReq):
    username = re.sub(r"[^a-z0-9_]", "_", req.username.lower().strip())
    conn = get_db()
    u = row(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())
    if not u:
        conn.execute(
            "INSERT INTO users (username,display_name,email) VALUES (?,?,?)",
            (username, req.display_name.strip(), req.email or ""))
        conn.commit()
        u = row(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())
    conn.close()
    return {"token": encode_token(u["id"]), "user": u}

@app.get("/api/auth/me")
async def get_me(current_user=Depends(get_current_user)):
    if not current_user:
        raise HTTPException(401, "Not authenticated")
    return current_user

@app.put("/api/auth/me")
async def update_me(update: ProfileUpdate, current_user=Depends(require_user)):
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if fields:
        conn = get_db()
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE users SET {sets} WHERE id=?", (*fields.values(), current_user["id"]))
        conn.commit()
        u = row(conn.execute("SELECT * FROM users WHERE id=?", (current_user["id"],)).fetchone())
        conn.close()
        return u
    return current_user

# ── Tool routes ───────────────────────────────────────────────────────────────
def tool_query_base():
    return """
        SELECT t.*, u.username, u.display_name as owner_name, u.avatar_url as owner_avatar
        FROM tools t JOIN users u ON t.owner_id = u.id
    """

@app.get("/api/tools")
async def list_tools(
    sort: str = "newest",
    owner: Optional[str] = None,
    search: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    conn = get_db()
    q = tool_query_base() + " WHERE 1=1"
    params = []
    if owner:
        q += " AND u.username=?"; params.append(owner)
    if search:
        q += " AND (t.name LIKE ? OR t.summary LIKE ? OR t.description LIKE ?)"
        params.extend([f"%{search}%"] * 3)
    order = {"newest": "t.created_at DESC", "most_used": "t.click_count DESC",
             "most_voted": "t.vote_count DESC"}.get(sort, "t.created_at DESC")
    q += f" ORDER BY {order}"
    ts = rows(conn.execute(q, params).fetchall())
    if current_user:
        voted = {r["tool_id"] for r in
                 rows(conn.execute("SELECT tool_id FROM votes WHERE user_id=?",
                                   (current_user["id"],)).fetchall())}
        for t in ts:
            t["user_voted"] = t["id"] in voted
    conn.close()
    return ts

@app.get("/api/tools/featured")
async def featured_tools():
    conn = get_db()
    ts = rows(conn.execute(
        tool_query_base() + " WHERE 1=1 ORDER BY t.vote_count DESC, t.click_count DESC LIMIT 6"
    ).fetchall())
    conn.close()
    return ts

@app.get("/api/tools/{tool_id}")
async def get_tool(tool_id: int, current_user=Depends(get_current_user)):
    conn = get_db()
    t = row(conn.execute(
        tool_query_base() + " WHERE t.id=?", (tool_id,)
    ).fetchone())
    if not t:
        raise HTTPException(404, "Tool not found")
    if current_user:
        voted = conn.execute(
            "SELECT 1 FROM votes WHERE user_id=? AND tool_id=?",
            (current_user["id"], tool_id)).fetchone()
        t["user_voted"] = bool(voted)
    conn.close()
    return t

@app.post("/api/tools")
async def create_tool(tool: ToolCreate, current_user=Depends(require_user)):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO tools (owner_id,name,url,description,summary,tags) VALUES (?,?,?,?,?,?)",
        (current_user["id"], tool.name, tool.url, tool.description,
         tool.summary, json.dumps(tool.tags)))
    tid = cur.lastrowid
    conn.execute(
        "INSERT INTO activity_feed (event_type,user_id,tool_id,message) VALUES (?,?,?,?)",
        ("tool_added", current_user["id"], tid,
         f"{current_user['display_name']} added a new tool: {tool.name}"))
    conn.commit()
    t = row(conn.execute(tool_query_base() + " WHERE t.id=?", (tid,)).fetchone())
    conn.close()
    return t

@app.delete("/api/tools/{tool_id}")
async def delete_tool(tool_id: int, current_user=Depends(require_user)):
    conn = get_db()
    t = conn.execute(
        "SELECT * FROM tools WHERE id=? AND owner_id=?",
        (tool_id, current_user["id"])).fetchone()
    if not t:
        raise HTTPException(404, "Tool not found or not yours")
    conn.execute("DELETE FROM votes WHERE tool_id=?", (tool_id,))
    conn.execute("DELETE FROM weekly_votes WHERE tool_id=?", (tool_id,))
    conn.execute("DELETE FROM activity_feed WHERE tool_id=?", (tool_id,))
    conn.execute("DELETE FROM tools WHERE id=?", (tool_id,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/tools/{tool_id}/click")
async def track_click(tool_id: int):
    conn = get_db()
    conn.execute("UPDATE tools SET click_count=click_count+1 WHERE id=?", (tool_id,))
    conn.commit()
    cnt = conn.execute("SELECT click_count FROM tools WHERE id=?", (tool_id,)).fetchone()
    conn.close()
    return {"click_count": cnt[0] if cnt else 0}

@app.post("/api/tools/{tool_id}/vote")
async def toggle_vote(tool_id: int, current_user=Depends(require_user)):
    conn = get_db()
    existing = conn.execute(
        "SELECT 1 FROM votes WHERE user_id=? AND tool_id=?",
        (current_user["id"], tool_id)).fetchone()
    if existing:
        conn.execute("DELETE FROM votes WHERE user_id=? AND tool_id=?",
                     (current_user["id"], tool_id))
        conn.execute("UPDATE tools SET vote_count=MAX(0,vote_count-1) WHERE id=?", (tool_id,))
        voted = False
    else:
        conn.execute("INSERT INTO votes (user_id,tool_id) VALUES (?,?)",
                     (current_user["id"], tool_id))
        conn.execute("UPDATE tools SET vote_count=vote_count+1 WHERE id=?", (tool_id,))
        conn.execute(
            "INSERT INTO activity_feed (event_type,user_id,tool_id,message) VALUES (?,?,?,?)",
            ("vote", current_user["id"], tool_id,
             f"{current_user['display_name']} voted for a tool"))
        voted = True
    conn.commit()
    cnt = conn.execute("SELECT vote_count FROM tools WHERE id=?", (tool_id,)).fetchone()
    conn.close()
    return {"voted": voted, "vote_count": cnt[0] if cnt else 0}

# ── Leaderboard ───────────────────────────────────────────────────────────────
@app.get("/api/leaderboard")
async def leaderboard():
    conn = get_db()
    base = """
        SELECT t.id, t.name, t.url, t.click_count, t.vote_count,
               u.display_name as owner_name, u.username
        FROM tools t JOIN users u ON t.owner_id=u.id
    """
    most_used  = rows(conn.execute(base + " ORDER BY t.click_count DESC LIMIT 10").fetchall())
    most_voted = rows(conn.execute(base + " ORDER BY t.vote_count  DESC LIMIT 10").fetchall())
    weekly_history = rows(conn.execute("""
        SELECT ww.week_start, ww.votes_at_time, t.name as tool_name, t.id as tool_id,
               u.display_name as owner_name
        FROM weekly_winners ww
        JOIN tools t ON ww.tool_id=t.id
        JOIN users u ON t.owner_id=u.id
        ORDER BY ww.week_start DESC LIMIT 10
    """).fetchall())
    conn.close()
    return {"most_used": most_used, "most_voted": most_voted, "weekly_history": weekly_history}

@app.post("/api/weekly-vote/{tool_id}")
async def cast_weekly_vote(tool_id: int, current_user=Depends(require_user)):
    week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO weekly_votes (user_id,tool_id,week_start) VALUES (?,?,?)",
            (current_user["id"], tool_id, week_start))
        conn.commit()
        conn.close()
        return {"success": True}
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(400, "Already voted this week")

# ── Winner ────────────────────────────────────────────────────────────────────
@app.get("/api/winner")
async def get_winner(current_user=Depends(get_current_user)):
    conn = get_db()
    w = row(conn.execute("""
        SELECT ww.*, t.name as tool_name, t.url as tool_url, t.summary as tool_summary,
               u.display_name as owner_name, u.username as owner_username
        FROM weekly_winners ww
        JOIN tools t ON ww.tool_id=t.id
        JOIN users u ON t.owner_id=u.id
        ORDER BY ww.created_at DESC LIMIT 1
    """).fetchone())
    if w and current_user:
        seen = conn.execute(
            "SELECT 1 FROM winner_seen WHERE user_id=? AND winner_id=?",
            (current_user["id"], w["id"])).fetchone()
        w["user_has_seen"] = bool(seen)
    elif w:
        w["user_has_seen"] = True
    conn.close()
    return w

@app.post("/api/winner/{winner_id}/seen")
async def mark_winner_seen(winner_id: int, current_user=Depends(require_user)):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO winner_seen (user_id,winner_id) VALUES (?,?)",
                 (current_user["id"], winner_id))
    conn.commit()
    conn.close()
    return {"success": True}

# ── Users ─────────────────────────────────────────────────────────────────────
@app.get("/api/users")
async def list_users():
    conn = get_db()
    users = rows(conn.execute(
        "SELECT id,username,display_name,bio,favorite_ai_project,avatar_url,created_at FROM users ORDER BY display_name"
    ).fetchall())
    conn.close()
    return users

@app.get("/api/users/{username}")
async def get_user(username: str, current_user=Depends(get_current_user)):
    conn = get_db()
    u = row(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())
    if not u:
        raise HTTPException(404, "User not found")
    ts = rows(conn.execute(
        "SELECT t.*, u.display_name as owner_name, u.username FROM tools t "
        "JOIN users u ON t.owner_id=u.id WHERE t.owner_id=? ORDER BY t.created_at DESC",
        (u["id"],)).fetchall())
    if current_user:
        voted = {r["tool_id"] for r in
                 rows(conn.execute("SELECT tool_id FROM votes WHERE user_id=?",
                                   (current_user["id"],)).fetchall())}
        for t in ts:
            t["user_voted"] = t["id"] in voted
    conn.close()
    return {**u, "tools": ts}

# ── Chat ──────────────────────────────────────────────────────────────────────
@app.get("/api/chat")
async def get_chat(since: Optional[int] = None):
    conn = get_db()
    if since:
        ms = rows(conn.execute(
            "SELECT * FROM chat_messages WHERE id>? ORDER BY id ASC LIMIT 50",
            (since,)).fetchall())
    else:
        ms = rows(conn.execute(
            "SELECT * FROM chat_messages ORDER BY id DESC LIMIT 50"
        ).fetchall())
        ms.reverse()
    conn.close()
    return ms

@app.post("/api/chat")
async def post_chat(msg: ChatMsg, current_user=Depends(require_user)):
    conn = get_db()
    conn.execute(
        "INSERT INTO chat_messages (user_id,username,display_name,message) VALUES (?,?,?,?)",
        (current_user["id"], current_user["username"],
         current_user["display_name"], msg.message))
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {"success": True, "id": new_id}

@app.post("/api/ask")
async def ask_ai(req: AskReq, current_user=Depends(get_current_user)):
    response = await get_ai_response(req.message, req.history or [])
    conn = get_db()
    conn.execute(
        "INSERT INTO chat_messages (user_id,username,display_name,message,is_bot) VALUES (?,?,?,?,?)",
        (None, "AOL_AI", "AOL AI", response, 1))
    conn.commit()
    conn.close()
    return {"response": response}

# ── Feed ──────────────────────────────────────────────────────────────────────
@app.get("/api/feed")
async def get_feed(limit: int = 20):
    conn = get_db()
    items = rows(conn.execute("""
        SELECT af.*, u.display_name as user_name, t.name as tool_name
        FROM activity_feed af
        LEFT JOIN users u ON af.user_id=u.id
        LEFT JOIN tools t ON af.tool_id=t.id
        ORDER BY af.created_at DESC LIMIT ?
    """, (limit,)).fetchall())
    conn.close()
    return items

# ── Best Practices ────────────────────────────────────────────────────────────
@app.get("/api/best-practices")
async def get_practices():
    conn = get_db()
    ps = rows(conn.execute(
        "SELECT * FROM best_practices ORDER BY created_at DESC").fetchall())
    conn.close()
    return ps

@app.post("/api/best-practices")
async def post_practice(practice: BestPractice, current_user=Depends(require_user)):
    conn = get_db()
    conn.execute(
        "INSERT INTO best_practices (user_id,author_name,title,content) VALUES (?,?,?,?)",
        (current_user["id"], current_user["display_name"],
         practice.title, practice.content))
    conn.commit()
    conn.close()
    return {"success": True}

# ── AI endpoints ──────────────────────────────────────────────────────────────
@app.post("/api/summarize")
async def summarize(req: SummarizeReq):
    result = await summarize_url(req.url)
    return result

# ── Board ─────────────────────────────────────────────────────────────────────
@app.get("/api/board")
async def get_board_notes(current_user=Depends(get_current_user)):
    conn = get_db()
    notes = rows(conn.execute("""
        SELECT * FROM board_notes
        WHERE expires_at > datetime('now')
        ORDER BY created_at ASC
    """).fetchall())
    conn.close()
    for n in notes:
        n["is_mine"] = bool(current_user and n["user_id"] == current_user["id"])
    return notes

@app.post("/api/board")
async def create_board_note(note: BoardNoteCreate, current_user=Depends(require_user)):
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO board_notes
        (user_id, author_name, message, color, emoji, drawing_data, pen_color, x_pos, y_pos, rotation)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (current_user["id"], current_user["display_name"],
          note.message, note.color, note.emoji, note.drawing_data,
          note.pen_color, note.x_pos, note.y_pos, note.rotation))
    nid = cur.lastrowid
    conn.commit()
    n = row(conn.execute("SELECT * FROM board_notes WHERE id=?", (nid,)).fetchone())
    conn.close()
    n["is_mine"] = True
    return n

@app.put("/api/board/{note_id}/position")
async def update_note_position(note_id: int, pos: BoardNotePosition,
                               current_user=Depends(require_user)):
    conn = get_db()
    conn.execute("UPDATE board_notes SET x_pos=?, y_pos=? WHERE id=?",
                 (pos.x_pos, pos.y_pos, note_id))
    conn.commit()
    conn.close()
    return {"success": True}

@app.delete("/api/board/{note_id}")
async def delete_board_note(note_id: int, current_user=Depends(require_user)):
    conn = get_db()
    n = conn.execute("SELECT user_id FROM board_notes WHERE id=?", (note_id,)).fetchone()
    if not n:
        raise HTTPException(404, "Note not found")
    if n["user_id"] != current_user["id"]:
        raise HTTPException(403, "Not your note")
    conn.execute("DELETE FROM board_notes WHERE id=?", (note_id,))
    conn.commit()
    conn.close()
    return {"success": True}

# ── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def get_stats():
    conn = get_db()
    total_tools  = conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    total_users  = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_votes  = conn.execute("SELECT COALESCE(SUM(vote_count),0) FROM tools").fetchone()[0]
    total_clicks = conn.execute("SELECT COALESCE(SUM(click_count),0) FROM tools").fetchone()[0]
    conn.close()
    return {
        "total_tools": total_tools,
        "total_users": total_users,
        "total_votes": total_votes,
        "total_clicks": total_clicks,
    }
