"""
GSO Ops AI Tools — FastAPI backend
All-in-one: database, auth (SSO + local), AI service, and API routes.
"""
import os, json, re, base64, sqlite3, asyncio, secrets as _secrets
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Header, Depends, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests as http_requests

try:
    import anthropic as _anthropic
    _ai_client = _anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
except Exception:
    _ai_client = None

# ── Config ───────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
WEB_DIR  = BASE_DIR / "web"

# DB_PATH: override via env var to point at a Databricks Unity Catalog Volume
# e.g.  DB_PATH=/Volumes/main/gso_ops/app_data/gso_tools.db
_db_path_env = os.getenv("DB_PATH", "")
if _db_path_env:
    DB_PATH = Path(_db_path_env)
else:
    DB_PATH = BASE_DIR / "data" / "gso_tools.db"

# Ensure parent directory exists (local dev creates data/; on Databricks the
# Volume directory already exists so mkdir is a no-op / gracefully ignored)
try:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# SECRET_KEY: must be set to a real random value in production.
# Generate one with:  python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET = os.getenv("SECRET_KEY", "")
if not SECRET:
    SECRET = _secrets.token_hex(32)   # ephemeral fallback for local dev

# Databricks Apps inject the authenticated user's email via this header.
# When this env var is set (or DATABRICKS_RUNTIME_VERSION is present) we know
# we are running inside a Databricks App and SSO is available.
IS_DATABRICKS = bool(
    os.getenv("DATABRICKS_APP_NAME") or
    os.getenv("DATABRICKS_RUNTIME_VERSION") or
    os.getenv("DATABRICKS_HOST")
)

# ── DB backend selection ──────────────────────────────────────────────────────
# On Databricks: try Delta Lake first; fall back to SQLite+DBFS if SDK fails.
# Local dev: always SQLite.
import app.db_delta as _delta

USE_DELTA = IS_DATABRICKS  # flip to False to force SQLite locally

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
    conn = get_db_and_backup()
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

# seed_demo_data removed — app starts fresh for production use.

# ── DBFS persistence layer ────────────────────────────────────────────────────
# On Databricks Apps, /tmp is ephemeral.  We back the SQLite file up to DBFS
# FileStore so data survives redeploys and container restarts.
DBFS_BACKUP = "dbfs:/FileStore/gso-ops-ai-tools/gso_tools.db"
_dbfs_lock = asyncio.Lock()

def _dbfs_creds():
    """Return (headers, host) for DBFS REST calls, or (None, None) if unavailable."""
    token = os.getenv("DATABRICKS_TOKEN", "")
    host  = os.getenv("DATABRICKS_HOST", "").rstrip("/")
    if token and host:
        return {"Authorization": f"Bearer {token}"}, host
    return None, None

def restore_db_from_dbfs():
    """Download the SQLite DB from DBFS on cold-start."""
    if not IS_DATABRICKS:
        return
    if DB_PATH.exists() and DB_PATH.stat().st_size > 4096:
        return  # already have a live local copy
    headers, host = _dbfs_creds()
    if not headers:
        return
    try:
        # Check if backup exists
        st = http_requests.get(
            f"{host}/api/2.0/dbfs/get-status",
            headers=headers,
            params={"path": DBFS_BACKUP},
            timeout=8,
        ).json()
        size = st.get("file_size", 0)
        if not size:
            return  # no backup yet — first ever deploy

        # Read the file in 512 KB chunks
        offset, chunks = 0, []
        chunk_size = 512 * 1024
        while True:
            resp = http_requests.get(
                f"{host}/api/2.0/dbfs/read",
                headers=headers,
                params={"path": DBFS_BACKUP, "offset": offset, "length": chunk_size},
                timeout=20,
            ).json()
            data = resp.get("data", "")
            if not data:
                break
            chunks.append(base64.b64decode(data))
            offset += len(chunks[-1])
            if len(chunks[-1]) < chunk_size:
                break  # last chunk

        if chunks:
            DB_PATH.write_bytes(b"".join(chunks))
            print(f"[DB] Restored {DB_PATH.stat().st_size:,} bytes from DBFS")
    except Exception as ex:
        print(f"[DB] Could not restore from DBFS: {ex}")

def backup_db_to_dbfs():
    """Upload the current SQLite DB to DBFS (called after each write)."""
    if not IS_DATABRICKS or not DB_PATH.exists():
        return
    headers, host = _dbfs_creds()
    if not headers:
        return
    try:
        db_bytes = DB_PATH.read_bytes()
        resp = http_requests.post(
            f"{host}/api/2.0/dbfs/put",
            headers=headers,
            json={"path": DBFS_BACKUP,
                  "contents": base64.b64encode(db_bytes).decode(),
                  "overwrite": True},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"[DB] DBFS backup failed: {resp.text[:120]}")
    except Exception as ex:
        print(f"[DB] Could not backup to DBFS: {ex}")

def get_db_and_backup():
    """Return a DB connection whose commit() also triggers a DBFS backup.
    On Databricks with Delta Lake available, returns a DeltaConn instead."""
    if USE_DELTA and _delta.is_available():
        return _delta.get_conn()

    class _BackupConn:
        """Thin wrapper: proxies everything to the real connection; backup on commit."""
        __slots__ = ("_c",)
        def __init__(self, c):
            object.__setattr__(self, "_c", c)
        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_c"), name)
        def execute(self, *a, **kw):
            return object.__getattribute__(self, "_c").execute(*a, **kw)
        def executemany(self, *a, **kw):
            return object.__getattribute__(self, "_c").executemany(*a, **kw)
        def commit(self):
            c = object.__getattribute__(self, "_c")
            c.commit()
            if IS_DATABRICKS:
                import threading
                threading.Thread(target=backup_db_to_dbfs, daemon=True).start()
        def close(self):
            object.__getattribute__(self, "_c").close()
    return _BackupConn(get_db())

def seed_bot_welcome():
    """Insert AOL AI's welcome message once so the chat room has context."""
    conn = get_db_and_backup()
    existing = conn.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE is_bot=1"
    ).fetchone()
    count = existing[0] if existing else 0
    if count == 0:
        conn.execute(
            "INSERT INTO chat_messages (user_id,username,display_name,message,is_bot,created_at) "
            "VALUES (?,?,?,?,?,datetime('now'))",
            (None, "AOL_AI", "AOL AI",
             "Welcome to GSO Ops AI Tools! I'm AOL AI — your resident AI assistant. "
             "Type @AOL_AI in any message to ask me anything about AI tools, prompting, "
             "Databricks, or how to get started building. IM me anytime! 👋", 1)
        )
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

async def get_current_user(
    authorization: Optional[str] = Header(None),
    x_app_token:   Optional[str] = Header(None),
):
    # X-App-Token is our custom header (Authorization is stripped by Databricks Apps proxy)
    token_str = x_app_token or (
        authorization.split(" ", 1)[1]
        if authorization and authorization.startswith("Bearer ")
        else None
    )
    if not token_str:
        print("[AUTH] no token — X-App-Token and Authorization both missing/bad")
        return None
    uid = decode_token(token_str)
    if not uid:
        print(f"[AUTH] decode_token failed — token prefix={token_str[:12]}... SECRET_OK={bool(SECRET)}")
        return None
    using_delta = USE_DELTA and _delta.is_available()
    print(f"[AUTH] uid={uid} backend={'delta' if using_delta else 'sqlite'}")
    conn = get_db_and_backup()
    result = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    u = row(result)
    conn.close()
    if u:
        print(f"[AUTH] found user id={u.get('id')} username={u.get('username')}")
    else:
        print(f"[AUTH] NO user found for id={uid} — result was {result}")
    return u

async def require_user(
    authorization: Optional[str] = Header(None),
    x_app_token:   Optional[str] = Header(None),
):
    u = await get_current_user(authorization, x_app_token)
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

# On Databricks Apps, requests are same-origin so CORS is irrelevant, but we
# allow * for local dev convenience.  Tighten to your workspace URL in prod if
# you want an extra layer: allow_origins=["https://<workspace>.azuredatabricks.net"]
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
    if USE_DELTA and _delta.is_available():
        print("[startup] Delta Lake mode — initialising tables")
        _delta.init_tables()
    else:
        print("[startup] SQLite mode — restoring from DBFS and initialising")
        restore_db_from_dbfs()
        init_db()
    seed_bot_welcome()

@app.get("/", include_in_schema=False)
async def serve_spa():
    return FileResponse(str(WEB_DIR / "index.html"))

# ── Auth routes ───────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
async def login(req: LoginReq):
    username = re.sub(r"[^a-z0-9_]", "_", req.username.lower().strip())
    conn = get_db_and_backup()
    u = row(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())
    if not u:
        conn.execute(
            "INSERT INTO users (username,display_name,email,created_at) VALUES (?,?,?,datetime('now'))",
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

# ── Databricks SSO ────────────────────────────────────────────────────────────
def _resolve_databricks_identity(request: Request) -> dict:
    """
    Databricks Apps inject several headers. X-Forwarded-User may be a numeric
    user ID or an email depending on the workspace config. We use the injected
    OAuth access token to call the SCIM /Me endpoint which always returns the
    full user profile (displayName, emails, userName).
    Falls back gracefully through header → SCIM → derived values.
    """
    host = os.getenv("DATABRICKS_HOST", "")
    access_token = (
        request.headers.get("X-Forwarded-Access-Token") or
        request.headers.get("x-forwarded-access-token") or
        ""
    ).strip()

    # Prefer the SCIM /Me endpoint — most reliable source of truth
    if host and access_token:
        try:
            resp = http_requests.get(
                f"{host.rstrip('/')}/api/2.0/preview/scim/v2/Me",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=5,
            )
            if resp.status_code == 200:
                me = resp.json()
                # emails list: prefer "work" type, fall back to first
                emails = me.get("emails", [])
                email = next(
                    (e["value"] for e in emails if e.get("primary")),
                    emails[0]["value"] if emails else ""
                )
                display_name = me.get("displayName") or me.get("name", {}).get("formatted", "")
                user_name    = me.get("userName", "")
                # userName is often the email in Databricks
                if not email and "@" in user_name:
                    email = user_name
                return {"email": email, "display_name": display_name, "user_name": user_name}
        except Exception:
            pass

    # Fallback: read X-Forwarded-User / X-Forwarded-Email headers
    forwarded_user = (
        request.headers.get("X-Forwarded-User") or
        request.headers.get("x-forwarded-user") or ""
    ).strip()
    forwarded_email = (
        request.headers.get("X-Forwarded-Email") or
        request.headers.get("x-forwarded-email") or ""
    ).strip()

    email = forwarded_email or (forwarded_user if "@" in forwarded_user else "")
    return {"email": email, "display_name": "", "user_name": forwarded_user}


@app.post("/api/auth/sso")
async def sso_login(request: Request):
    """
    Called by the frontend on every page load.
    Uses the injected Databricks OAuth token to look up the real user profile
    via SCIM /Me, so display names and emails are always correct.
    Returns 403 outside Databricks (local dev) so the frontend falls back to
    the manual login form.
    """
    identity = _resolve_databricks_identity(request)
    email        = identity["email"]
    display_name = identity["display_name"]
    user_name    = identity["user_name"]

    # Need at least one identifier
    if not email and not user_name:
        raise HTTPException(
            status_code=403,
            detail="No SSO identity — running in local/dev mode"
        )

    # Stable username: prefer email local part, fall back to user_name
    identifier = email or user_name
    local_part  = identifier.split("@")[0]
    username    = re.sub(r"[^a-z0-9_]", "_", local_part.lower())

    # Display name: prefer SCIM displayName, derive from email/username otherwise
    if not display_name:
        display_name = " ".join(p.capitalize() for p in re.split(r"[._-]+", local_part))

    # Lookup key: prefer email (stable), fall back to username
    lookup_field = "email" if email else "username"
    lookup_value = email    if email else username

    conn = get_db_and_backup()
    u = row(conn.execute(f"SELECT * FROM users WHERE {lookup_field}=?", (lookup_value,)).fetchone())
    if not u:
        try:
            conn.execute(
                "INSERT INTO users (username, display_name, email, created_at) VALUES (?,?,?,datetime('now'))",
                (username, display_name, email or "")
            )
            conn.commit()
            u = row(conn.execute(f"SELECT * FROM users WHERE {lookup_field}=?", (lookup_value,)).fetchone())
        except Exception:
            username = f"{username}_{_secrets.token_hex(3)}"
            conn.execute(
                "INSERT INTO users (username, display_name, email, created_at) VALUES (?,?,?,datetime('now'))",
                (username, display_name, email or "")
            )
            conn.commit()
            u = row(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())
    else:
        # Update display name if SCIM gave us a better one and we didn't have it
        if display_name and (not u.get("display_name") or u["display_name"] == u["username"]):
            conn.execute("UPDATE users SET display_name=? WHERE id=?", (display_name, u["id"]))
            conn.commit()
            u["display_name"] = display_name
    conn.close()

    token = encode_token(u["id"])
    print(f"[SSO] issuing token for user id={u['id']} username={u.get('username')} email={u.get('email')}")
    return {"token": token, "user": u, "sso": True}


@app.get("/api/config")
async def get_config():
    """Tells the frontend what mode the server is running in."""
    return {
        "sso_mode": IS_DATABRICKS,
        "ai_enabled": bool(_ai_client and os.getenv("ANTHROPIC_API_KEY")),
    }

@app.get("/api/health")
async def health_check():
    """Liveness probe for Databricks Apps and load balancers."""
    conn = get_db_and_backup()
    user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    tool_count = conn.execute("SELECT COUNT(*) FROM tools").fetchone()[0]
    conn.close()
    return {
        "status": "ok",
        "db_path": str(DB_PATH),
        "users": user_count,
        "tools": tool_count,
        "sso_mode": IS_DATABRICKS,
    }

@app.put("/api/auth/me")
async def update_me(update: ProfileUpdate, current_user=Depends(require_user)):
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    if fields:
        conn = get_db_and_backup()
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
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
    ts = rows(conn.execute(
        tool_query_base() + " WHERE 1=1 ORDER BY t.vote_count DESC, t.click_count DESC LIMIT 6"
    ).fetchall())
    conn.close()
    return ts

@app.get("/api/tools/{tool_id}")
async def get_tool(tool_id: int, current_user=Depends(get_current_user)):
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
    cur = conn.execute(
        "INSERT INTO tools (owner_id,name,url,description,summary,tags,created_at,updated_at) VALUES (?,?,?,?,?,?,datetime('now'),datetime('now'))",
        (current_user["id"], tool.name, tool.url, tool.description,
         tool.summary, json.dumps(tool.tags)))
    tid = cur.lastrowid
    conn.execute(
        "INSERT INTO activity_feed (event_type,user_id,tool_id,message,created_at) VALUES (?,?,?,?,datetime('now'))",
        ("tool_added", current_user["id"], tid,
         f"{current_user['display_name']} added a new tool: {tool.name}"))
    conn.commit()
    t = row(conn.execute(tool_query_base() + " WHERE t.id=?", (tid,)).fetchone())
    conn.close()
    return t

@app.delete("/api/tools/{tool_id}")
async def delete_tool(tool_id: int, current_user=Depends(require_user)):
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
    conn.execute("UPDATE tools SET click_count=click_count+1 WHERE id=?", (tool_id,))
    conn.commit()
    cnt = conn.execute("SELECT click_count FROM tools WHERE id=?", (tool_id,)).fetchone()
    conn.close()
    return {"click_count": cnt[0] if cnt else 0}

@app.post("/api/tools/{tool_id}/vote")
async def toggle_vote(tool_id: int, current_user=Depends(require_user)):
    conn = get_db_and_backup()
    existing = conn.execute(
        "SELECT 1 FROM votes WHERE user_id=? AND tool_id=?",
        (current_user["id"], tool_id)).fetchone()
    if existing:
        conn.execute("DELETE FROM votes WHERE user_id=? AND tool_id=?",
                     (current_user["id"], tool_id))
        conn.execute("UPDATE tools SET vote_count=MAX(0,vote_count-1) WHERE id=?", (tool_id,))
        voted = False
    else:
        conn.execute("INSERT INTO votes (user_id,tool_id,created_at) VALUES (?,?,datetime('now'))",
                     (current_user["id"], tool_id))
        conn.execute("UPDATE tools SET vote_count=vote_count+1 WHERE id=?", (tool_id,))
        conn.execute(
            "INSERT INTO activity_feed (event_type,user_id,tool_id,message,created_at) VALUES (?,?,?,?,datetime('now'))",
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
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
    try:
        conn.execute(
            "INSERT INTO weekly_votes (user_id,tool_id,week_start,created_at) VALUES (?,?,?,datetime('now'))",
            (current_user["id"], tool_id, week_start))
        conn.commit()
        conn.close()
        return {"success": True}
    except Exception:
        conn.close()
        raise HTTPException(400, "Already voted this week")

# ── Winner ────────────────────────────────────────────────────────────────────
@app.get("/api/winner")
async def get_winner(current_user=Depends(get_current_user)):
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
    conn.execute("INSERT OR IGNORE INTO winner_seen (user_id,winner_id) VALUES (?,?)",
                 (current_user["id"], winner_id))
    conn.commit()
    conn.close()
    return {"success": True}

# ── Users ─────────────────────────────────────────────────────────────────────
@app.get("/api/users")
async def list_users():
    conn = get_db_and_backup()
    users = rows(conn.execute(
        "SELECT id,username,display_name,bio,favorite_ai_project,avatar_url,created_at FROM users ORDER BY display_name"
    ).fetchall())
    conn.close()
    return users

@app.get("/api/users/{username}")
async def get_user(username: str, current_user=Depends(get_current_user)):
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
    conn.execute(
        "INSERT INTO chat_messages (user_id,username,display_name,message,created_at) VALUES (?,?,?,?,datetime('now'))",
        (current_user["id"], current_user["username"],
         current_user["display_name"], msg.message))
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {"success": True, "id": new_id}

@app.post("/api/ask")
async def ask_ai(req: AskReq, current_user=Depends(get_current_user)):
    response = await get_ai_response(req.message, req.history or [])
    conn = get_db_and_backup()
    conn.execute(
        "INSERT INTO chat_messages (user_id,username,display_name,message,is_bot,created_at) VALUES (?,?,?,?,?,datetime('now'))",
        (None, "AOL_AI", "AOL AI", response, 1))
    conn.commit()
    conn.close()
    return {"response": response}

# ── Feed ──────────────────────────────────────────────────────────────────────
@app.get("/api/feed")
async def get_feed(limit: int = 20):
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
    ps = rows(conn.execute(
        "SELECT * FROM best_practices ORDER BY created_at DESC").fetchall())
    conn.close()
    return ps

@app.post("/api/best-practices")
async def post_practice(practice: BestPractice, current_user=Depends(require_user)):
    conn = get_db_and_backup()
    conn.execute(
        "INSERT INTO best_practices (user_id,author_name,title,content,created_at) VALUES (?,?,?,?,datetime('now'))",
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
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
    cur = conn.execute("""
        INSERT INTO board_notes
        (user_id, author_name, message, color, emoji, drawing_data, pen_color, x_pos, y_pos, rotation, created_at, expires_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now','+7 days'))
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
    conn = get_db_and_backup()
    conn.execute("UPDATE board_notes SET x_pos=?, y_pos=? WHERE id=?",
                 (pos.x_pos, pos.y_pos, note_id))
    conn.commit()
    conn.close()
    return {"success": True}

@app.delete("/api/board/{note_id}")
async def delete_board_note(note_id: int, current_user=Depends(require_user)):
    conn = get_db_and_backup()
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
    conn = get_db_and_backup()
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
