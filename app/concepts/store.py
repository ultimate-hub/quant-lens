"""
概念库存储：内置概念 + 用户自定义概念。

设计要点：
    1. 内置概念来自 seed.py，首次运行时灌进数据库，之后可以改股票但不能删概念
        —— 改了也不会被下次启动覆盖（用 is_builtin 标记 + 只在表为空时灌种）
    2. 用户概念完全自由：新建、改名、加/减股票、加别名
    3. 别名参与关键词匹配：新闻里说「算力租赁」，能命中「AI算力」这个概念
    4. 对外提供和 sector_map 同形状的接口，让新闻模块无缝切换
"""

from __future__ import annotations

from app.core.db import db_session

SCHEMA = """
CREATE TABLE IF NOT EXISTS concepts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL DEFAULT 'concept',
    note       TEXT DEFAULT '',
    is_builtin INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 999,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS concept_items (
    concept_id INTEGER NOT NULL,
    symbol     TEXT NOT NULL,
    name       TEXT DEFAULT '',
    PRIMARY KEY (concept_id, symbol)
);

CREATE TABLE IF NOT EXISTS concept_aliases (
    concept_id INTEGER NOT NULL,
    alias      TEXT NOT NULL,
    PRIMARY KEY (concept_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_concept_items ON concept_items(concept_id);
CREATE INDEX IF NOT EXISTS idx_concept_alias ON concept_aliases(concept_id);
"""

KIND_LABELS = {"industry": "行业", "concept": "概念"}


def init() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA)


def ensure_seeded() -> None:
    """
    把内置概念同步进数据库。

    这是**增量同步**，不是「只在空库时灌一次」：
        1. 新加的内置概念 → 插入
        2. 已存在的内置概念 → 补上新增的成分股和别名（不删用户自己加的）
        3. 修正历史遗留的分类错误（见下面的迁移）

    早期版本用的是「表里没数据才灌」，导致后来在 seed.py 里加概念，
    老用户永远看不到。改成同步后，升级就能拿到新内容。
    """
    from app.concepts import seed

    init()
    with db_session() as conn:
        rows = conn.execute("SELECT id, name, kind, is_builtin FROM concepts").fetchall()
        by_name = {r["name"]: r for r in rows}

        for idx, item in enumerate(seed.all_seed()):
            row = by_name.get(item["name"])

            if row is None:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO concepts"
                    "(name, kind, note, is_builtin, sort_order) VALUES (?,?,?,1,?)",
                    (item["name"], item["kind"], "", idx))
                cid = cur.lastrowid
                if not cid:
                    continue
            else:
                if not row["is_builtin"]:
                    # 用户自己建了同名概念，不覆盖
                    continue
                cid = row["id"]
                # 迁移：早期把「港股科技」归成了概念，现在应该是行业
                if row["kind"] != item["kind"]:
                    conn.execute("UPDATE concepts SET kind=? WHERE id=?",
                                 (item["kind"], cid))

            for code, name in item["stocks"]:
                conn.execute(
                    "INSERT OR IGNORE INTO concept_items(concept_id, symbol, name)"
                    " VALUES (?,?,?)", (cid, code, name))
            for alias in item["aliases"]:
                conn.execute(
                    "INSERT OR IGNORE INTO concept_aliases(concept_id, alias) VALUES (?,?)",
                    (cid, alias))


# ==================================================================
# 查询
# ==================================================================
def list_concepts(kind: str | None = None, q: str | None = None) -> list[dict]:
    ensure_seeded()
    sql = """
        SELECT c.id, c.name, c.kind, c.note, c.is_builtin, c.sort_order,
               (SELECT COUNT(1) FROM concept_items i WHERE i.concept_id = c.id) AS cnt
        FROM concepts c
    """
    params: list = []
    where: list[str] = []

    if kind:
        where.append("c.kind = ?")
        params.append(kind)
    if q:
        where.append("(c.name LIKE ? OR c.note LIKE ? OR EXISTS("
                     "SELECT 1 FROM concept_aliases a WHERE a.concept_id = c.id"
                     " AND a.alias LIKE ?))")
        like = f"%{q}%"
        params.extend([like, like, like])

    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.is_builtin DESC, c.sort_order, c.id"

    with db_session() as conn:
        rows = conn.execute(sql, params).fetchall()

    return [{"id": r["id"], "name": r["name"], "kind": r["kind"],
             "kind_label": KIND_LABELS.get(r["kind"], r["kind"]),
             "note": r["note"] or "", "is_builtin": bool(r["is_builtin"]),
             "count": r["cnt"]} for r in rows]


def get_concept(cid: int) -> dict | None:
    init()
    with db_session() as conn:
        row = conn.execute(
            "SELECT id, name, kind, note, is_builtin, sort_order FROM concepts WHERE id=?",
            (cid,)).fetchone()
        if not row:
            return None
        items = conn.execute(
            "SELECT symbol, name FROM concept_items WHERE concept_id=? ORDER BY symbol",
            (cid,)).fetchall()
        aliases = conn.execute(
            "SELECT alias FROM concept_aliases WHERE concept_id=? ORDER BY alias",
            (cid,)).fetchall()

    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"],
        "kind_label": KIND_LABELS.get(row["kind"], row["kind"]),
        "note": row["note"] or "", "is_builtin": bool(row["is_builtin"]),
        "items": [dict(i) for i in items],
        "aliases": [a["alias"] for a in aliases],
        "count": len(items),
    }


# ==================================================================
# 用户自定义：增删改
# ==================================================================
def create_concept(name: str, kind: str = "concept", note: str = "",
                   aliases: list[str] | None = None,
                   symbols: list[str] | None = None) -> int:
    init()
    name = (name or "").strip()
    if not name:
        raise ValueError("概念名称不能为空")
    if kind not in ("industry", "concept"):
        kind = "concept"

    with db_session() as conn:
        exists = conn.execute("SELECT id FROM concepts WHERE name=?", (name,)).fetchone()
        if exists:
            raise ValueError(f"「{name}」已存在")
        # 用户概念排在后面
        cur = conn.execute(
            "INSERT INTO concepts(name, kind, note, is_builtin, sort_order)"
            " VALUES (?,?,?,0,900)", (name, kind, note))
        cid = int(cur.lastrowid)

        for code in (symbols or []):
            code = (code or "").strip().upper()
            if code:
                conn.execute(
                    "INSERT OR IGNORE INTO concept_items(concept_id, symbol, name)"
                    " VALUES (?,?,'')", (cid, code))
        for alias in (aliases or []):
            alias = (alias or "").strip()
            if alias:
                conn.execute(
                    "INSERT OR IGNORE INTO concept_aliases(concept_id, alias) VALUES (?,?)",
                    (cid, alias))
    return cid


def update_concept(cid: int, name: str | None = None,
                   note: str | None = None, kind: str | None = None) -> None:
    init()
    sets, params = [], []
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("名称不能为空")
        sets.append("name=?")
        params.append(name)
    if note is not None:
        sets.append("note=?")
        params.append(note)
    if kind in ("industry", "concept"):
        sets.append("kind=?")
        params.append(kind)
    if not sets:
        return
    params.append(cid)
    with db_session() as conn:
        conn.execute(f"UPDATE concepts SET {','.join(sets)} WHERE id=?", params)


def delete_concept(cid: int) -> None:
    init()
    with db_session() as conn:
        row = conn.execute("SELECT is_builtin FROM concepts WHERE id=?", (cid,)).fetchone()
        if not row:
            raise ValueError("概念不存在")
        if row["is_builtin"]:
            raise ValueError("内置概念不能删除，但可以编辑其中的股票")
        conn.execute("DELETE FROM concept_items WHERE concept_id=?", (cid,))
        conn.execute("DELETE FROM concept_aliases WHERE concept_id=?", (cid,))
        conn.execute("DELETE FROM concepts WHERE id=?", (cid,))


# ==================================================================
# 成分股
# ==================================================================
def add_items(cid: int, symbols: list[str]) -> int:
    init()
    added = 0
    with db_session() as conn:
        for code in symbols or []:
            code = (code or "").strip().upper()
            if not code:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO concept_items(concept_id, symbol, name)"
                " VALUES (?,?,'')", (cid, code))
            added += 1
    return added


def remove_items(cid: int, symbols: list[str]) -> int:
    init()
    with db_session() as conn:
        conn.executemany(
            "DELETE FROM concept_items WHERE concept_id=? AND symbol=?",
            [(cid, (s or "").strip().upper()) for s in (symbols or []) if s])
    return len(symbols or [])


def add_aliases(cid: int, aliases: list[str]) -> int:
    init()
    with db_session() as conn:
        for a in aliases or []:
            a = (a or "").strip()
            if a:
                conn.execute(
                    "INSERT OR IGNORE INTO concept_aliases(concept_id, alias) VALUES (?,?)",
                    (cid, a))
    return len(aliases or [])


def remove_alias(cid: int, alias: str) -> None:
    init()
    with db_session() as conn:
        conn.execute(
            "DELETE FROM concept_aliases WHERE concept_id=? AND alias=?",
            (cid, (alias or "").strip()))


# ==================================================================
# 关键词匹配（给新闻和搜索用，形状与 sector_map 一致）
# ==================================================================
def map_text(text: str, max_items: int = 5) -> list[dict]:
    """
    在一段文本里找命中的概念。

    返回（和 sector_map.map_text 同形状）：
        [{"sector": 概念名, "matched_terms": [...], "stocks": [[code, name], ...]}]
    """
    ensure_seeded()
    text = text or ""
    if not text:
        return []

    with db_session() as conn:
        rows = conn.execute("""
            SELECT c.id, c.name, a.alias
            FROM concepts c LEFT JOIN concept_aliases a ON a.concept_id = c.id
        """).fetchall()
        items = conn.execute(
            "SELECT concept_id, symbol, name FROM concept_items").fetchall()

    by_cid: dict[int, list[list[str]]] = {}
    for it in items:
        by_cid.setdefault(it["concept_id"], []).append([it["symbol"], it["name"] or ""])

    hits: dict[int, dict] = {}
    for r in rows:
        cid, name, alias = r["id"], r["name"], r["alias"]
        terms = [name] + ([alias] if alias else [])
        matched = [t for t in terms if t and t in text]
        if matched:
            hits.setdefault(cid, {"sector": name, "matched_terms": [],
                                  "stocks": by_cid.get(cid, [])})
            for m in matched:
                if m not in hits[cid]["matched_terms"]:
                    hits[cid]["matched_terms"].append(m)

    out = list(hits.values())
    out.sort(key=lambda x: len(x["matched_terms"]), reverse=True)
    return out[:max_items]


def all_names(kind: str | None = None) -> list[str]:
    return [c["name"] for c in list_concepts(kind=kind)]


def dump_all() -> dict[str, dict]:
    """
    一次性导出全部概念（名称 → {aliases, stocks, kind}）。

    给 sector_map 做合并用 —— 避免每查一个概念就发一次 SQL。
    """
    ensure_seeded()
    with db_session() as conn:
        concepts = conn.execute(
            "SELECT id, name, kind FROM concepts").fetchall()
        items = conn.execute(
            "SELECT concept_id, symbol, name FROM concept_items").fetchall()
        aliases = conn.execute(
            "SELECT concept_id, alias FROM concept_aliases").fetchall()

    out: dict[str, dict] = {}
    by_id: dict[int, dict] = {}
    for c in concepts:
        entry = {"aliases": [], "stocks": [], "kind": c["kind"]}
        out[c["name"]] = entry
        by_id[c["id"]] = entry

    for it in items:
        e = by_id.get(it["concept_id"])
        if e is not None:
            e["stocks"].append((it["symbol"], it["name"] or ""))
    for a in aliases:
        e = by_id.get(a["concept_id"])
        if e is not None:
            e["aliases"].append(a["alias"])

    return out


def sector_stocks(name: str) -> list[tuple[str, str]]:
    """按概念名取成分股（兼容 sector_map 接口）。"""
    ensure_seeded()
    with db_session() as conn:
        row = conn.execute("SELECT id FROM concepts WHERE name=?", (name,)).fetchone()
        if not row:
            return []
        rows = conn.execute(
            "SELECT symbol, name FROM concept_items WHERE concept_id=? ORDER BY symbol",
            (row["id"],)).fetchall()
    return [(r["symbol"], r["name"] or "") for r in rows]


def stats() -> dict:
    ensure_seeded()
    with db_session() as conn:
        total = conn.execute("SELECT COUNT(1) c FROM concepts").fetchone()["c"]
        ind = conn.execute(
            "SELECT COUNT(1) c FROM concepts WHERE kind='industry'").fetchone()["c"]
        con = conn.execute(
            "SELECT COUNT(1) c FROM concepts WHERE kind='concept'").fetchone()["c"]
        custom = conn.execute(
            "SELECT COUNT(1) c FROM concepts WHERE is_builtin=0").fetchone()["c"]
        stocks = conn.execute(
            "SELECT COUNT(DISTINCT symbol) c FROM concept_items").fetchone()["c"]
    return {"total": total, "industries": ind, "concepts": con,
            "custom": custom, "stocks": stocks}