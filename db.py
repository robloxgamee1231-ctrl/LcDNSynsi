"""
db.py — SQLite persistence for the Discord bot.

Tables:
  users  — credits, roles (JSON array), titles (JSON array), ban/timeout state
  logs   — audit log of all actions
  guilds — server whitelist / ban list
"""

import json
import sqlite3
import time
from pathlib import Path

_DB_PATH = Path(__file__).parent / "bot_data.db"

# ── Role constants ─────────────────────────────────────────────────────────────

ROLE_USER  = "user"
ROLE_BETA  = "beta"
ROLE_ADMIN = "admin"
ROLE_OWNER = "owner"

# Priority order (higher index = higher rank)
ROLE_PRIORITY = [ROLE_USER, ROLE_BETA, ROLE_ADMIN, ROLE_OWNER]

# Credits granted when a role is first added
ROLE_CREDITS: dict[str, int | None] = {
    ROLE_USER:  1_500,
    ROLE_BETA:  90_000,
    ROLE_ADMIN: 150_000,
    ROLE_OWNER: None,   # None = infinite
}

COST_IMAGE = 500
COST_VIDEO = 1_000

PRESET_TITLES: dict[str, str] = {
    "coder":        "🖥️ The Coder",
    "best_admin":   "👑 The Best Admin",
    "most_online":  "🌐 Most Online",
    "beta_pioneer": "⚡ Beta Pioneer",
    "legend":       "🌟 Legend",
    "creative":     "🎨 Creative Genius",
    "top_creator":  "🔥 Top Creator",
    "diamond":      "💎 Diamond Member",
    "guardian":     "🛡️ Guardian",
    "champion":     "🏆 Champion",
    "ai_whisperer": "🤖 AI Whisperer",
    "trendsetter":  "🌈 Trendsetter",
}


# ── DB connection ──────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


# ── Schema / migration ─────────────────────────────────────────────────────────

def init_db() -> None:
    with _conn() as c:
        # Create tables with new schema
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id        INTEGER PRIMARY KEY,
                username       TEXT    DEFAULT '',
                credits        INTEGER DEFAULT 1500,
                role           TEXT    DEFAULT 'user',
                title          TEXT    DEFAULT NULL,
                is_banned      INTEGER DEFAULT 0,
                ban_reason     TEXT    DEFAULT NULL,
                timeout_until  REAL    DEFAULT 0,
                created_at     REAL    DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                actor_id   INTEGER DEFAULT NULL,
                action     TEXT    NOT NULL,
                details    TEXT    DEFAULT '',
                timestamp  REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id    INTEGER PRIMARY KEY,
                guild_name  TEXT    DEFAULT '',
                is_allowed  INTEGER DEFAULT 0,
                is_banned   INTEGER DEFAULT 0,
                ban_reason  TEXT    DEFAULT NULL,
                added_at    REAL    DEFAULT 0
            );
        """)
        # Add new multi-value columns if they don't exist yet (migration)
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        if "roles" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN roles TEXT DEFAULT NULL")
            # Migrate existing single `role` → JSON array
            c.execute("""
                UPDATE users SET roles = json_array(role)
                WHERE roles IS NULL AND role IS NOT NULL
            """)
            c.execute("""
                UPDATE users SET roles = '["user"]'
                WHERE roles IS NULL
            """)
        if "titles" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN titles TEXT DEFAULT '[]'")
            # Migrate existing single `title` → JSON array
            c.execute("""
                UPDATE users
                SET titles = json_array(title)
                WHERE title IS NOT NULL AND titles = '[]'
            """)
        if "bypass_prefix" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN bypass_prefix INTEGER DEFAULT 0")


# ── JSON helpers ───────────────────────────────────────────────────────────────

def _load_roles(raw: str | None) -> list[str]:
    if not raw:
        return [ROLE_USER]
    try:
        return json.loads(raw) or [ROLE_USER]
    except Exception:
        return [ROLE_USER]


def _load_titles(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        return json.loads(raw) or []
    except Exception:
        return []


def _top_role(roles: list[str]) -> str:
    """Return the highest-priority role in the list."""
    best = ROLE_USER
    for r in roles:
        if ROLE_PRIORITY.index(r) > ROLE_PRIORITY.index(best):
            best = r
    return best


# ── User getters / ensure ──────────────────────────────────────────────────────

def ensure_user(user_id: int, username: str = "") -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            default_roles = json.dumps([ROLE_USER])
            c.execute(
                "INSERT INTO users "
                "(user_id, username, credits, role, roles, titles, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, username, ROLE_CREDITS[ROLE_USER],
                 ROLE_USER, default_roles, "[]", time.time()),
            )
            row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        elif username and row["username"] != username:
            c.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
        return dict(row)


def get_user(user_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_roles(user_id: int) -> list[str]:
    u = get_user(user_id)
    if u is None:
        return [ROLE_USER]
    return _load_roles(u.get("roles"))


def get_user_titles(user_id: int) -> list[str]:
    u = get_user(user_id)
    if u is None:
        return []
    return _load_titles(u.get("titles"))


# ── Credits ────────────────────────────────────────────────────────────────────

def is_infinite(user_id: int) -> bool:
    return ROLE_OWNER in get_user_roles(user_id)


def get_credits(user_id: int) -> int | None:
    if is_infinite(user_id):
        return None
    u = get_user(user_id)
    return u["credits"] if u else 0


def has_credits(user_id: int, cost: int) -> bool:
    if is_infinite(user_id):
        return True
    u = get_user(user_id)
    return u is not None and u["credits"] >= cost


def deduct_credits(user_id: int, cost: int) -> bool:
    if is_infinite(user_id):
        return True
    with _conn() as c:
        row = c.execute("SELECT credits FROM users WHERE user_id=?", (user_id,)).fetchone()
        if row is None or row["credits"] < cost:
            return False
        c.execute("UPDATE users SET credits=credits-? WHERE user_id=?", (cost, user_id))
        return True


def refund_credits(user_id: int, cost: int) -> None:
    if is_infinite(user_id):
        return
    with _conn() as c:
        c.execute("UPDATE users SET credits=credits+? WHERE user_id=?", (cost, user_id))


def add_credits(user_id: int, amount: int) -> int | None:
    if is_infinite(user_id):
        return None
    with _conn() as c:
        c.execute("UPDATE users SET credits=credits+? WHERE user_id=?", (amount, user_id))
        row = c.execute("SELECT credits FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row["credits"] if row else None


def set_credits(user_id: int, amount: int) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET credits=? WHERE user_id=?", (amount, user_id))


def remove_credits(user_id: int, amount: int) -> int:
    with _conn() as c:
        row = c.execute("SELECT credits FROM users WHERE user_id=?", (user_id,)).fetchone()
        new_bal = max(0, (row["credits"] if row else 0) - amount)
        c.execute("UPDATE users SET credits=? WHERE user_id=?", (new_bal, user_id))
        return new_bal


# ── Roles (multi) ──────────────────────────────────────────────────────────────

def get_role(user_id: int) -> str:
    """Return the single highest role (for permission checks)."""
    return _top_role(get_user_roles(user_id))


def add_role(user_id: int, role: str) -> list[str]:
    """Add a role to the user's role list. Returns updated list."""
    roles = get_user_roles(user_id)
    if role not in roles:
        roles.append(role)
    top = _top_role(roles)
    with _conn() as c:
        c.execute(
            "UPDATE users SET roles=?, role=? WHERE user_id=?",
            (json.dumps(roles), top, user_id),
        )
    # Grant credits if this role gives more than the user currently has
    grant = ROLE_CREDITS.get(role)
    if grant is not None:
        u = get_user(user_id)
        if u and u["credits"] < grant:
            set_credits(user_id, grant)
    return roles


def remove_role(user_id: int, role: str) -> list[str]:
    """Remove a role. Always keeps at least 'user'. Returns updated list."""
    roles = get_user_roles(user_id)
    roles = [r for r in roles if r != role]
    if not roles:
        roles = [ROLE_USER]
    top = _top_role(roles)
    with _conn() as c:
        c.execute(
            "UPDATE users SET roles=?, role=? WHERE user_id=?",
            (json.dumps(roles), top, user_id),
        )
    return roles


def set_role(user_id: int, role: str) -> None:
    """Replace ALL roles with just this one (legacy compat)."""
    grant = ROLE_CREDITS.get(role)
    credits = grant if grant is not None else 999_999_999
    with _conn() as c:
        c.execute(
            "UPDATE users SET roles=?, role=?, credits=? WHERE user_id=?",
            (json.dumps([role]), role, credits, user_id),
        )


# ── Titles (multi) ─────────────────────────────────────────────────────────────

def add_title(user_id: int, title: str) -> list[str]:
    """Add a title to the user's titles list. Returns updated list."""
    titles = get_user_titles(user_id)
    if title not in titles:
        titles.append(title)
    with _conn() as c:
        c.execute(
            "UPDATE users SET titles=?, title=? WHERE user_id=?",
            (json.dumps(titles), titles[0] if titles else None, user_id),
        )
    return titles


def remove_title(user_id: int, title: str) -> list[str]:
    """Remove a specific title. Returns updated list."""
    titles = [t for t in get_user_titles(user_id) if t != title]
    with _conn() as c:
        c.execute(
            "UPDATE users SET titles=?, title=? WHERE user_id=?",
            (json.dumps(titles), titles[0] if titles else None, user_id),
        )
    return titles


# Legacy single-title helpers (kept for compat)
def set_title(user_id: int, title: str | None) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET title=? WHERE user_id=?", (title, user_id))


def get_title(user_id: int) -> str | None:
    u = get_user(user_id)
    return u["title"] if u else None


# ── Ban / Timeout ──────────────────────────────────────────────────────────────

def ban_user(user_id: int, reason: str = "") -> None:
    with _conn() as c:
        c.execute(
            "UPDATE users SET is_banned=1, ban_reason=? WHERE user_id=?",
            (reason, user_id),
        )


def unban_user(user_id: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE users SET is_banned=0, ban_reason=NULL WHERE user_id=?",
            (user_id,),
        )


def timeout_user(user_id: int, minutes: int) -> float:
    until = time.time() + minutes * 60
    with _conn() as c:
        c.execute(
            "UPDATE users SET timeout_until=? WHERE user_id=?",
            (until, user_id),
        )
    return until


def is_blocked(user_id: int) -> tuple[bool, str]:
    u = get_user(user_id)
    if u is None:
        return False, ""
    if u["is_banned"]:
        reason = u["ban_reason"] or "No reason given."
        return True, f"🚫 You are **banned** from this bot.\n> {reason}"
    to = u["timeout_until"] or 0
    if to > time.time():
        remaining = int(to - time.time())
        m, s = divmod(remaining, 60)
        return True, f"⏳ You are **timed out** from this bot for `{m}m {s:02d}s`."
    return False, ""


# ── Guild whitelist / ban ─────────────────────────────────────────────────────

def ensure_guild(guild_id: int, guild_name: str = "") -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM guilds WHERE guild_id=?", (guild_id,)).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO guilds (guild_id, guild_name, added_at) VALUES (?,?,?)",
                (guild_id, guild_name, time.time()),
            )
            row = c.execute("SELECT * FROM guilds WHERE guild_id=?", (guild_id,)).fetchone()
        elif guild_name and row["guild_name"] != guild_name:
            c.execute("UPDATE guilds SET guild_name=? WHERE guild_id=?", (guild_name, guild_id))
        return dict(row)


def get_guild(guild_id: int) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM guilds WHERE guild_id=?", (guild_id,)).fetchone()
        return dict(row) if row else None


def allow_guild(guild_id: int, guild_name: str = "") -> None:
    ensure_guild(guild_id, guild_name)
    with _conn() as c:
        c.execute(
            "UPDATE guilds SET is_allowed=1, is_banned=0, ban_reason=NULL WHERE guild_id=?",
            (guild_id,),
        )


def ban_guild_db(guild_id: int, guild_name: str = "", reason: str = "") -> None:
    ensure_guild(guild_id, guild_name)
    with _conn() as c:
        c.execute(
            "UPDATE guilds SET is_banned=1, is_allowed=0, ban_reason=? WHERE guild_id=?",
            (reason, guild_id),
        )


def unban_guild_db(guild_id: int) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE guilds SET is_banned=0, ban_reason=NULL WHERE guild_id=?",
            (guild_id,),
        )


def is_guild_allowed(guild_id: int) -> bool:
    """Return True if this guild has been explicitly allowed."""
    g = get_guild(guild_id)
    return bool(g and g["is_allowed"])


def is_guild_banned(guild_id: int) -> bool:
    """Return True if this guild is banned."""
    g = get_guild(guild_id)
    return bool(g and g["is_banned"])


def any_guild_allowed() -> bool:
    """Return True if at least one guild has been allowed (whitelist is active)."""
    with _conn() as c:
        row = c.execute("SELECT COUNT(*) FROM guilds WHERE is_allowed=1").fetchone()
        return row[0] > 0


def get_all_guilds() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM guilds ORDER BY added_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_all_users(limit: int = 50, offset: int = 0) -> list[dict]:
    """Return all users ordered by credits descending (for admin listing)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM users ORDER BY credits DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Bypass Prefix ─────────────────────────────────────────────────────────────

def get_bypass_prefix(user_id: int) -> bool:
    """Return True if the user has the bypass name prefix enabled."""
    u = get_user(user_id)
    return bool(u and u.get("bypass_prefix"))


def set_bypass_prefix(user_id: int, enabled: bool) -> None:
    """Enable or disable the bypass name prefix for a user."""
    with _conn() as c:
        c.execute(
            "UPDATE users SET bypass_prefix=? WHERE user_id=?",
            (1 if enabled else 0, user_id),
        )


# ── Logs ───────────────────────────────────────────────────────────────────────

def log_action(
    user_id: int, action: str, details: str = "", actor_id: int | None = None
) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO logs (user_id, actor_id, action, details, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, actor_id, action, details, time.time()),
        )


def get_logs(
    user_id: int | None = None,
    limit: int = 20,
    action: str | None = None,
) -> list[dict]:
    with _conn() as c:
        parts, params = [], []
        if user_id:
            parts.append("user_id=?"); params.append(user_id)
        if action:
            parts.append("action=?"); params.append(action)
        where = ("WHERE " + " AND ".join(parts)) if parts else ""
        params.append(limit)
        rows = c.execute(
            f"SELECT * FROM logs {where} ORDER BY timestamp DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


# ── Config (allowed Discord server roles) ─────────────────────────────────────

def _ensure_config_table(c: sqlite3.Connection) -> None:
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        )
    """)


def get_allowed_discord_roles() -> list[int]:
    """Return the list of Discord server role IDs allowed to use the bot."""
    with _conn() as c:
        _ensure_config_table(c)
        row = c.execute(
            "SELECT value FROM config WHERE key='allowed_discord_roles'"
        ).fetchone()
        if row is None:
            return []
        try:
            return json.loads(row["value"]) or []
        except Exception:
            return []


def add_allowed_discord_role(role_id: int) -> list[int]:
    roles = get_allowed_discord_roles()
    if role_id not in roles:
        roles.append(role_id)
    with _conn() as c:
        _ensure_config_table(c)
        c.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('allowed_discord_roles', ?)",
            (json.dumps(roles),),
        )
    return roles


def remove_allowed_discord_role(role_id: int) -> list[int]:
    roles = [r for r in get_allowed_discord_roles() if r != role_id]
    with _conn() as c:
        _ensure_config_table(c)
        c.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('allowed_discord_roles', ?)",
            (json.dumps(roles),),
        )
    return roles


def get_stats() -> dict:
    import calendar, datetime as _dt
    with _conn() as c:
        total    = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        banned   = c.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
        betas    = c.execute("SELECT COUNT(*) FROM users WHERE roles LIKE '%beta%'").fetchone()[0]
        admins   = c.execute("SELECT COUNT(*) FROM users WHERE roles LIKE '%admin%'").fetchone()[0]
        img_uses = c.execute("SELECT COUNT(*) FROM logs WHERE action='image'").fetchone()[0]
        vid_uses = c.execute("SELECT COUNT(*) FROM logs WHERE action='video'").fetchone()[0]

        # Today's window (UTC midnight)
        now_utc   = _dt.datetime.utcnow()
        day_start = _dt.datetime(now_utc.year, now_utc.month, now_utc.day).timestamp()
        img_today = c.execute(
            "SELECT COUNT(*) FROM logs WHERE action='image' AND timestamp>=?", (day_start,)
        ).fetchone()[0]
        vid_today = c.execute(
            "SELECT COUNT(*) FROM logs WHERE action='video' AND timestamp>=?", (day_start,)
        ).fetchone()[0]
        fails_today = c.execute(
            "SELECT COUNT(*) FROM logs WHERE action='gen_fail' AND timestamp>=?", (day_start,)
        ).fetchone()[0]

        # Total credits held across non-owner users
        total_credits = c.execute(
            "SELECT COALESCE(SUM(credits), 0) FROM users WHERE role != 'owner'"
        ).fetchone()[0]

        # Users active (any log) in last 7 days
        week_ago = time.time() - 7 * 86400
        active_7d = c.execute(
            "SELECT COUNT(DISTINCT user_id) FROM logs WHERE timestamp>=? AND user_id!=0",
            (week_ago,),
        ).fetchone()[0]

    return {
        "total": total, "banned": banned,
        "betas": betas, "admins": admins,
        "images": img_uses, "videos": vid_uses,
        "img_today": img_today, "vid_today": vid_today,
        "fails_today": fails_today,
        "total_credits": total_credits,
        "active_7d": active_7d,
    }
