"""
JSON 解析与修复：应对中转站不返回规范 JSON 的情况。

为什么需要这个？
    我们的 AI 分析要输出结构化评分，必须能解析成 JSON。
    但中转站经常：
      - 不支持 response_format={"type":"json_object"}，返回带解释的文字
      - 用 ```json 代码块包裹
      - 输出中文引号、多余逗号、单引号

三级降级策略：
    1. 直接解析
    2. 剥掉代码块/前后文字，取出最外层 {...}
    3. 正则+替换修复常见语法错误后重试
"""

from __future__ import annotations

import json
import re


def parse_json_loose(text: str) -> tuple[dict | list | None, str]:
    """
    宽松解析。返回 (对象, 使用的方法)。

    方法名：direct / fence / brace / repair / failed
    """
    if not text or not text.strip():
        return None, "failed"

    raw = text.strip()

    # ---- 第1级：直接解析
    try:
        return json.loads(raw), "direct"
    except json.JSONDecodeError:
        pass

    # ---- 第2级：剥掉 ```json 代码块
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        body = fence.group(1).strip()
        try:
            return json.loads(body), "fence"
        except json.JSONDecodeError:
            raw = body

    # ---- 第3级：截取最外层大括号/中括号
    extracted = _extract_outer(raw)
    if extracted:
        try:
            return json.loads(extracted), "brace"
        except json.JSONDecodeError:
            fixed = _repair(extracted)
            try:
                return json.loads(fixed), "repair"
            except json.JSONDecodeError:
                pass

    # 最后一搏：对整段文本修复
    fixed = _repair(raw)
    try:
        return json.loads(fixed), "repair"
    except json.JSONDecodeError:
        return None, "failed"


def _extract_outer(text: str) -> str | None:
    """取出第一个 { 到最后一个 } 之间的内容（或 [ ... ]）。"""
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end > start:
            return text[start:end + 1]
    return None


def _repair(text: str) -> str:
    """修复常见的 JSON 语法错误。"""
    s = text

    # 中文引号 -> 英文
    s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")

    # Python 字面量 -> JSON
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\bNone\b", "null", s)

    # 去掉结尾多余逗号：,} 或 ,]
    s = re.sub(r",(\s*[}\]])", r"\1", s)

    # 去掉 // 行注释和 /* */ 块注释
    s = re.sub(r"//[^\n\r]*", "", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)

    # 单引号键名 -> 双引号（只处理简单情况）
    s = re.sub(r"'([A-Za-z_][A-Za-z0-9_]*)'\s*:", r'"\1":', s)

    return s.strip()


def safe_number(v, default=None):
    """把 "12.5%" / "12.5" / 12.5 统一转成 float。"""
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "").replace(",", "").replace("，", "")
    s = s.replace("约", "").replace("≈", "").strip()
    try:
        return float(s)
    except ValueError:
        return default
