"""
AI 渠道管理：把「中转站配置」持久化到数据库。

一个「渠道」= 一个中转站 + 一个模型。你可以配置多个：
    渠道A  DeepSeek-中转1   openai     https://xxx/v1   deepseek-chat
    渠道B  Claude-中转      anthropic  https://yyy      claude-sonnet-4
    渠道C  OpenAI-中转      openai     https://zzz/v1   gpt-4o

支持：
    - 按任务分配不同渠道（分析用贵的、摘要用便宜的）
    - 启用/禁用
    - 记录每次探测的能力结果（流式/JSON/延迟）
    - 记录用量统计（请求数、失败数、token数）
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

from app.ai import secret_store
from app.core.db import db_session

# 协议类型
PROTOCOL_OPENAI = "openai"        # /v1/chat/completions
PROTOCOL_ANTHROPIC = "anthropic"  # /v1/messages

PROTOCOL_LABELS = {
    PROTOCOL_OPENAI: "OpenAI 兼容",
    PROTOCOL_ANTHROPIC: "Anthropic 原生",
}

# 常见中转站预设（用户选一个再填 key 即可）
PRESETS = [
    {"key": "deepseek", "label": "DeepSeek", "protocol": PROTOCOL_OPENAI,
     "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    {"key": "moonshot", "label": "月之暗面 Kimi", "protocol": PROTOCOL_OPENAI,
     "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-32k"},
    {"key": "dashscope", "label": "阿里通义千问", "protocol": PROTOCOL_OPENAI,
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"key": "zhipu", "label": "智谱 GLM", "protocol": PROTOCOL_OPENAI,
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus"},
    {"key": "openai", "label": "OpenAI 官方", "protocol": PROTOCOL_OPENAI,
     "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"key": "anthropic", "label": "Claude 官方", "protocol": PROTOCOL_ANTHROPIC,
     "base_url": "https://api.anthropic.com", "model": "claude-3-5-sonnet-20241022"},
    {"key": "openrouter", "label": "OpenRouter", "protocol": PROTOCOL_OPENAI,
     "base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini"},
    {"key": "ollama", "label": "本地 Ollama", "protocol": PROTOCOL_OPENAI,
     "base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b"},
    {"key": "custom", "label": "自定义中转站", "protocol": PROTOCOL_OPENAI,
     "base_url": "", "model": ""},
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_channels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,              -- 显示名，如 "DS-中转1"
    protocol      TEXT NOT NULL DEFAULT 'openai',
    base_url      TEXT NOT NULL,
    model         TEXT NOT NULL,
    key_cipher    TEXT,                       -- DPAPI 加密后的密钥
    enabled       INTEGER DEFAULT 1,
    temperature   REAL DEFAULT 0.3,
    max_tokens    INTEGER DEFAULT 2048,
    extra_headers TEXT,                       -- JSON，自定义请求头
    capabilities  TEXT,                       -- JSON，探测结果
    is_default    INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  INTEGER,
    task        TEXT,                          -- 任务类型: analysis/summary/probe
    ok          INTEGER,
    latency_ms  INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    error       TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
"""


@dataclass
class Channel:
    """一个 AI 渠道的配置（内存对象，key 已解密）。"""

    id: int | None = None
    name: str = ""
    protocol: str = PROTOCOL_OPENAI
    base_url: str = ""
    model: str = ""
    api_key: str = ""          # 明文，只在内存中短暂存在
    enabled: bool = True
    temperature: float = 0.3
    max_tokens: int = 2048
    extra_headers: dict = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    is_default: bool = False

    @property
    def key_masked(self) -> str:
        return secret_store.mask(self.api_key)

    @property
    def label(self) -> str:
        return f"{self.name}({self.model})"


def init_ai_tables() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA)


# ==================================================================
# 增删改查
# ==================================================================
def add_channel(name: str, base_url: str, model: str, api_key: str,
                protocol: str = PROTOCOL_OPENAI, temperature: float = 0.3,
                max_tokens: int = 2048, extra_headers: dict | None = None,
                is_default: bool = False) -> int:
    """新增渠道。密钥会先加密再入库。"""
    init_ai_tables()
    cipher = secret_store.encrypt(api_key)
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO ai_channels"
            "(name, protocol, base_url, model, key_cipher, temperature,"
            " max_tokens, extra_headers, is_default)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (name, protocol, base_url.rstrip("/"), model, cipher,
             temperature, max_tokens,
             json.dumps(extra_headers or {}, ensure_ascii=False),
             1 if is_default else 0))
        cid = cur.lastrowid
        if is_default:
            conn.execute("UPDATE ai_channels SET is_default=0 WHERE id<>?", (cid,))
    return int(cid)


def update_channel(cid: int, **fields) -> None:
    """更新渠道字段。api_key 传明文，会自动加密。"""
    allowed = {"name", "protocol", "base_url", "model", "enabled",
               "temperature", "max_tokens", "is_default"}
    sets, params = [], []
    for k, v in fields.items():
        if k == "api_key":
            sets.append("key_cipher=?")
            params.append(secret_store.encrypt(v))
        elif k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
        elif k == "extra_headers":
            sets.append("extra_headers=?")
            params.append(json.dumps(v, ensure_ascii=False))
    if not sets:
        return
    params.append(cid)
    with db_session() as conn:
        conn.execute(f"UPDATE ai_channels SET {','.join(sets)} WHERE id=?", params)
        if fields.get("is_default"):
            conn.execute("UPDATE ai_channels SET is_default=0 WHERE id<>?", (cid,))


def delete_channel(cid: int) -> None:
    with db_session() as conn:
        conn.execute("DELETE FROM ai_channels WHERE id=?", (cid,))


def list_channels(only_enabled: bool = False) -> list[Channel]:
    """列出所有渠道（密钥已解密，供运行时使用）。"""
    init_ai_tables()
    sql = "SELECT * FROM ai_channels"
    if only_enabled:
        sql += " WHERE enabled=1"
    sql += " ORDER BY is_default DESC, id"

    with db_session() as conn:
        rows = conn.execute(sql).fetchall()

    out = []
    for r in rows:
        try:
            key = secret_store.decrypt(r["key_cipher"] or "")
            key_err = ""
        except secret_store.SecretError as exc:
            key, key_err = "", str(exc)

        ch = Channel(
            id=r["id"], name=r["name"], protocol=r["protocol"],
            base_url=r["base_url"], model=r["model"], api_key=key,
            enabled=bool(r["enabled"]), temperature=r["temperature"],
            max_tokens=r["max_tokens"],
            extra_headers=_loads(r["extra_headers"]),
            capabilities=_loads(r["capabilities"]),
            is_default=bool(r["is_default"]),
        )
        if key_err:
            ch.capabilities["key_error"] = key_err
        out.append(ch)
    return out


def get_channel(cid: int) -> Channel | None:
    return next((c for c in list_channels() if c.id == cid), None)


def get_default_channel(task: str | None = None) -> Channel | None:
    """
    取默认渠道。
    task 参数预留给"按任务分配渠道"（分析用A、摘要用B）。
    """
    chans = list_channels(only_enabled=True)
    if not chans:
        return None
    return next((c for c in chans if c.is_default), chans[0])


def save_capabilities(cid: int, caps: dict) -> None:
    with db_session() as conn:
        conn.execute("UPDATE ai_channels SET capabilities=? WHERE id=?",
                     (json.dumps(caps, ensure_ascii=False), cid))


# ==================================================================
# 用量统计
# ==================================================================
def log_usage(channel_id: int | None, task: str, ok: bool, latency_ms: int,
              prompt_tokens: int = 0, completion_tokens: int = 0,
              error: str = "") -> None:
    init_ai_tables()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO ai_usage(channel_id, task, ok, latency_ms,"
            " prompt_tokens, completion_tokens, error) VALUES (?,?,?,?,?,?,?)",
            (channel_id, task, 1 if ok else 0, latency_ms,
             prompt_tokens, completion_tokens, error[:300]))


def usage_summary(days: int = 7) -> list[dict]:
    init_ai_tables()
    with db_session() as conn:
        rows = conn.execute(
            "SELECT channel_id, COUNT(*) AS calls, SUM(ok) AS ok_calls,"
            " AVG(latency_ms) AS avg_ms, SUM(prompt_tokens) AS pt,"
            " SUM(completion_tokens) AS ct"
            " FROM ai_usage WHERE created_at >= datetime('now','localtime', ?)"
            " GROUP BY channel_id", (f"-{days} days",)).fetchall()
    return [dict(r) for r in rows]


def _loads(txt) -> dict:
    if not txt:
        return {}
    try:
        return json.loads(txt)
    except (json.JSONDecodeError, TypeError):
        return {}
