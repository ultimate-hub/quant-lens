"""
用户数据：设置 + 候选池。

设计要点：
    1. 全部新增表，不动现有表 —— 升级不影响已有数据和 AI 配置
    2. 设置用 KV 表存 JSON，避免频繁改表结构
    3. 候选池支持"内置池"（不可删）和用户自建池
    4. 标的冗余存名字，列表页不用每次查接口

批量解析器（parse_symbols）是这块的核心难点：
    用户会粘各种格式：纯代码、带后缀、中文名、逗号空格混排。
    解析不了的要列出来让用户确认，不能静默丢弃。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from app.core.db import db_session

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS watchlists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    note       TEXT DEFAULT '',
    is_builtin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    name         TEXT DEFAULT '',
    industry     TEXT DEFAULT '',
    note         TEXT DEFAULT '',
    added_at     TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (watchlist_id, symbol)
);
CREATE INDEX IF NOT EXISTS idx_wl_items_list ON watchlist_items(watchlist_id);
"""

# 默认设置
DEFAULTS = {
    "default_profile": "balanced",
    "default_watchlist": "",          # 空 = 用内置全部板块
    "enabled_markets": ["SH", "SZ", "HK", "US"],
    "benchmarks": {                   # 各市场默认基准
        "SH": "510300.SH", "SZ": "510300.SH", "BJ": "510300.SH",
        "HK": "2800.HK", "US": "SPY.US",
    },
    "cache_hours": {"quotes": 6, "news": 6},
    "screen_concurrency": 8,
    "ai_timeout": 120,
    "library_lang": "zh",             # zh / en / both
    "theme": "light",
}


def init() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA)


# ==================================================================
# 设置
# ==================================================================
def get_settings() -> dict:
    """返回全部设置（合并默认值）。"""
    init()
    with db_session() as conn:
        rows = conn.execute("SELECT key, value FROM user_settings").fetchall()

    out = dict(DEFAULTS)
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            pass
    return out


def set_settings(patch: dict) -> dict:
    """局部更新设置，返回更新后的全量。"""
    init()
    with db_session() as conn:
        for k, v in (patch or {}).items():
            conn.execute(
                "INSERT OR REPLACE INTO user_settings(key, value, updated_at)"
                " VALUES (?,?,datetime('now','localtime'))",
                (k, json.dumps(v, ensure_ascii=False)))
    return get_settings()


# ==================================================================
# 候选池
# ==================================================================
@dataclass
class Watchlist:
    id: int
    name: str
    note: str = ""
    is_builtin: bool = False
    count: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "note": self.note,
                "is_builtin": self.is_builtin, "count": self.count}


def list_watchlists() -> list[dict]:
    init()
    with db_session() as conn:
        rows = conn.execute("""
            SELECT w.id, w.name, w.note, w.is_builtin,
                   (SELECT COUNT(1) FROM watchlist_items i WHERE i.watchlist_id = w.id) AS cnt
            FROM watchlists w
            ORDER BY w.is_builtin DESC, w.id
        """).fetchall()
    return [{"id": r["id"], "name": r["name"], "note": r["note"] or "",
             "is_builtin": bool(r["is_builtin"]), "count": r["cnt"]} for r in rows]


def create_watchlist(name: str, note: str = "") -> int:
    init()
    name = (name or "").strip()
    if not name:
        raise ValueError("候选池名称不能为空")
    with db_session() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO watchlists(name, note) VALUES (?,?)", (name, note))
            return int(cur.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"候选池「{name}」已存在") from exc


def rename_watchlist(wid: int, name: str) -> None:
    with db_session() as conn:
        conn.execute("UPDATE watchlists SET name=? WHERE id=? AND is_builtin=0",
                     ((name or "").strip(), wid))


def delete_watchlist(wid: int) -> None:
    with db_session() as conn:
        row = conn.execute("SELECT is_builtin FROM watchlists WHERE id=?", (wid,)).fetchone()
        if row and row["is_builtin"]:
            raise ValueError("内置候选池不能删除")
        conn.execute("DELETE FROM watchlist_items WHERE watchlist_id=?", (wid,))
        conn.execute("DELETE FROM watchlists WHERE id=?", (wid,))


def get_watchlist_items(wid: int) -> list[dict]:
    init()
    with db_session() as conn:
        rows = conn.execute(
            "SELECT symbol, name, industry, note FROM watchlist_items"
            " WHERE watchlist_id=? ORDER BY industry, symbol", (wid,)).fetchall()
    return [dict(r) for r in rows]


def add_items(wid: int, items: list[dict]) -> int:
    """批量添加。items: [{symbol, name, industry}]，重复的会更新名字。"""
    init()
    if not items:
        return 0
    with db_session() as conn:
        for it in items:
            sym = (it.get("symbol") or "").strip().upper()
            if not sym:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO watchlist_items"
                "(watchlist_id, symbol, name, industry, note) VALUES (?,?,?,?,?)",
                (wid, sym, it.get("name", ""), it.get("industry", ""), it.get("note", "")))
    return len(items)


def remove_items(wid: int, symbols: list[str]) -> int:
    init()
    if not symbols:
        return 0
    with db_session() as conn:
        conn.executemany(
            "DELETE FROM watchlist_items WHERE watchlist_id=? AND symbol=?",
            [(wid, s.upper()) for s in symbols])
    return len(symbols)


# ==================================================================
# 批量解析（核心：容错）
# ==================================================================
# 标准代码：600519.SH / 00700.HK / AAPL.US / BTC.CRYPTO
_RE_STD = re.compile(r"^(\d{5,6}|[A-Z][A-Z0-9.\-]{0,9})\.(SH|SZ|BJ|HK|US|CRYPTO)$", re.I)
# 纯A股代码
_RE_CN6 = re.compile(r"^\d{6}$")
# 港5位
_RE_HK5 = re.compile(r"^\d{5}$")
# 美股字母
_RE_US = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
# 分隔符：逗号/顿号/空格/换行/分号
_SPLIT = re.compile(r"[,，、;；\s\n\r\t|]+")


def parse_symbols(text: str) -> dict:
    """
    把用户粘贴的一坨文本解析成标的列表。

    支持：
        600519 / 600519.SH / SH600519 / 茅台 / 贵州茅台(600519)
        AAPL / AAPL.US / 00700.HK
        逗号、空格、换行、顿号混排

    返回：
        {
          "ok": [{symbol, code, kind, raw}],      识别成功的
          "unknown": [{raw, reason}],             识别不了的（要提示用户）
          "count": 2
        }
    """
    text = (text or "").strip()
    if not text:
        return {"ok": [], "unknown": [], "count": 0}

    # 先把 "name(code)" 形式拆开，保留 code
    text = re.sub(r"[（(]\s*(\d{5,6}|[A-Za-z][\w.\-]*)\s*[）)]", r" \1 ", text)

    tokens = [t for t in _SPLIT.split(text) if t]

    ok: list[dict] = []
    unknown: list[dict] = []
    seen: set[str] = set()

    for raw in tokens:
        t = raw.strip().upper()
        if not t:
            continue

        sym = None
        kind = None

        m = _RE_STD.match(t)
        if m:
            sym = f"{m.group(1).upper()}.{m.group(2).upper()}"
            kind = "code"
        elif _RE_CN6.match(t):
            # 6位纯数字：6开头沪市，0/3深市，4/8北交所
            if t[0] == "6":
                sym, kind = f"{t}.SH", "code"
            elif t[0] in "03":
                sym, kind = f"{t}.SZ", "code"
            elif t[0] in "48":
                sym, kind = f"{t}.BJ", "code"
            else:
                unknown.append({"raw": raw, "reason": "6位数字但无法判断市场"})
                continue
        elif _RE_HK5.match(t):
            sym, kind = f"{t}.HK", "code"
        elif t.startswith(("SH", "SZ", "BJ", "HK", "US")) and len(t) > 2:
            prefix, code = t[:2], t[2:]
            sym, kind = f"{code}.{prefix}", "code"
        elif _RE_US.match(t) and any(c.isalpha() for c in t):
            sym, kind = f"{t}.US", "code"
        elif re.search(r"[\u4e00-\u9fa5]", raw):
            # 中文名字：留给调用方去查代码
            unknown.append({"raw": raw, "reason": "name", "name": raw})
            continue
        else:
            unknown.append({"raw": raw, "reason": "格式无法识别"})
            continue

        if sym in seen:
            continue
        seen.add(sym)
        ok.append({"symbol": sym, "code": sym.split(".")[0], "kind": kind, "raw": raw})

    return {"ok": ok, "unknown": unknown, "count": len(ok)}


# ==================================================================
# 内置池同步（把 sector_map 的 91 只灌进来）
# ==================================================================
def ensure_builtin_watchlist() -> int:
    """
    确保存在「预置·全部板块」这个内置池，并从 sector_map 同步内容。
    返回池 id。
    """
    from app.news import sector_map

    init()
    with db_session() as conn:
        row = conn.execute(
            "SELECT id FROM watchlists WHERE is_builtin=1 LIMIT 1").fetchone()
        if row:
            wid = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO watchlists(name, note, is_builtin) VALUES (?,?,1)",
                ("预置·全部板块", "系统内置，覆盖 30 个板块的代表个股", ))
            wid = int(cur.lastrowid)

        # 同步内容
        conn.execute("DELETE FROM watchlist_items WHERE watchlist_id=?", (wid,))
        for sector, info in sector_map.SECTORS.items():
            for code, name in info.get("stocks", []):
                conn.execute(
                    "INSERT OR REPLACE INTO watchlist_items"
                    "(watchlist_id, symbol, name, industry) VALUES (?,?,?,?)",
                    (wid, code, name, sector))
    return wid
