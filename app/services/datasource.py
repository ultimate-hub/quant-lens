"""
数据源模块：从东方财富的公开接口抓取行情数据。

为什么不用 akshare？
  akshare 是个优秀的开源库，但它体积大、依赖多、且经常因为数据源改版而失效。
  它底层抓的其实就是东方财富这些公开接口。
  我们直接对接接口，依赖更少、更可控、出问题也容易定位。

三个接口（都是东方财富对公众开放的网页数据接口）：
  1. 基金历史净值  api.fund.eastmoney.com/f10/lsjz
  2. 基金搜索      fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx
  3. 股票K线       push2his.eastmoney.com/api/qt/stock/kline/get

重要细节：trust_env = False
  你机器上配置了代理环境变量（127.0.0.1:7897），但代理程序没运行，
  这会导致所有请求失败。trust_env=False 告诉 requests "别读系统代理设置，直连"。
  这是本次开发中真实踩过的坑，写进文档了。
"""

import json
import re

import pandas as pd
import requests

from app.core.config import HTTP_TIMEOUT

# 创建一个全局会话（Session）：
#   - 复用 TCP 连接，比每次新建快很多
#   - 统一设置请求头，模拟浏览器，避免被服务器拒绝
_session = requests.Session()
_session.trust_env = False          # 忽略系统代理（见文件顶部说明）
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
})


class DataSourceError(Exception):
    """数据源异常：网络失败、接口改版、返回格式不对等情况统一抛这个。"""


def _get_json(url: str, referer: str | None = None, params: dict | None = None):
    """发起 GET 请求并解析 JSON，失败时抛出 DataSourceError。"""
    headers = {"Referer": referer} if referer else {}
    try:
        resp = _session.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        text = resp.text.strip()
        # 有些接口返回 JSONP 格式（形如 callback({...})），需要剥掉外层函数名
        if text and not text.startswith(("{", "[")):
            m = re.search(r"\((.*)\)\s*;?\s*$", text, re.S)
            if m:
                text = m.group(1)
        return json.loads(text)
    except requests.RequestException as exc:
        raise DataSourceError(f"网络请求失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DataSourceError(f"返回内容不是合法JSON（接口可能已改版）: {text[:120]}") from exc


# ==================================================================
# 基金
# ==================================================================
def fetch_fund_info(code: str) -> dict:
    """查询基金基本信息（名称、类型）。"""
    url = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
    data = _get_json(url, params={"m": "1", "key": code})

    for item in data.get("Datas") or []:
        if str(item.get("CODE")) == str(code):
            extra = item.get("FundBaseInfo") or {}
            return {
                "code": str(code),
                "name": item.get("NAME") or "",
                "fund_type": extra.get("FTYPE") or item.get("CATEGORYDESC") or "",
            }
    raise DataSourceError(f"未找到基金 {code}，请确认代码是否正确")


def fetch_fund_nav(code: str) -> pd.DataFrame:
    """
    抓取基金全部历史净值。

    返回 DataFrame，列为：trade_date, unit_nav, acc_nav, pct_change

    策略：主通道 + 备用通道
      主通道 pingzhongdata：一次请求返回该基金从成立到今天的全部净值（几百KB），
                            0.2 秒搞定，是效率最高的方式。
      备用通道 lsjz 分页：官方F10接口，但每页最多给 20 条，
                          一只老基金要翻近 200 页（几十秒），只在主通道失效时用。

    为什么要设计备用通道？
      数据源随时可能改版。主通道挂了软件还能用，这叫"容错"。
    """
    try:
        return _fetch_nav_from_pingzhong(code)
    except DataSourceError:
        return _fetch_nav_from_lsjz(code)


def _fetch_nav_from_pingzhong(code: str) -> pd.DataFrame:
    """
    从 pingzhongdata 接口抓净值。

    这个接口返回的其实是一段 JavaScript 代码，里面定义了若干变量：
        var Data_netWorthTrend = [{"x":时间戳毫秒,"y":单位净值,"equityReturn":涨跌幅}, ...];
        var Data_ACWorthTrend = [[时间戳, 累计净值], ...];
    我们用正则表达式把数组"抠"出来，再当 JSON 解析。
    """
    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    try:
        resp = _session.get(url, headers={"Referer": "https://fund.eastmoney.com/"},
                            timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        text = resp.content.decode("utf-8", "ignore")
    except requests.RequestException as exc:
        raise DataSourceError(f"网络请求失败: {exc}") from exc

    m = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\])\s*;", text, re.S)
    if not m:
        raise DataSourceError(f"基金 {code} 的净值数组未找到（接口可能已改版）")

    try:
        nav_list = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise DataSourceError("净值数组解析失败") from exc

    if not nav_list:
        raise DataSourceError(f"基金 {code} 净值数组为空")

    # 累计净值（可能不存在，比如货币基金）
    acc_map: dict[str, float] = {}
    m2 = re.search(r"Data_ACWorthTrend\s*=\s*(\[.*?\])\s*;", text, re.S)
    if m2:
        try:
            for ts, acc in json.loads(m2.group(1)):
                acc_map[_ts_to_date(ts)] = _to_float(acc)
        except (json.JSONDecodeError, TypeError, ValueError):
            acc_map = {}

    rows = []
    for item in nav_list:
        d = _ts_to_date(item.get("x"))
        if not d:
            continue
        rows.append({
            "trade_date": d,
            "unit_nav": _to_float(item.get("y")),
            "acc_nav": acc_map.get(d),
            "pct_change": _to_float(item.get("equityReturn")),
        })

    if not rows:
        raise DataSourceError(f"基金 {code} 没有有效净值记录")

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["trade_date", "unit_nav"]).drop_duplicates("trade_date")
    return df.sort_values("trade_date").reset_index(drop=True)


def _fetch_nav_from_lsjz(code: str, max_pages: int = 250, page_size: int = 20) -> pd.DataFrame:
    """备用通道：F10 历史净值接口，分页抓取。"""
    url = "https://api.fund.eastmoney.com/f10/lsjz"
    referer = "https://fundf10.eastmoney.com/"
    rows: list[dict] = []

    for page in range(1, max_pages + 1):
        payload = _get_json(
            url, referer=referer,
            params={"fundCode": code, "pageIndex": page, "pageSize": page_size},
        )
        items = ((payload.get("Data") or {}).get("LSJZList")) or []
        if not items:
            break

        for it in items:
            rows.append({
                "trade_date": it.get("FSRQ"),
                "unit_nav": _to_float(it.get("DWJZ")),
                "acc_nav": _to_float(it.get("LJJZ")),
                "pct_change": _to_float(it.get("JZZZL")),
            })

        total = int(payload.get("TotalCount") or 0)
        if len(rows) >= total:
            break

    if not rows:
        raise DataSourceError(f"基金 {code} 没有取到净值数据")

    df = pd.DataFrame(rows)
    df = df.dropna(subset=["trade_date"]).drop_duplicates("trade_date")
    return df.sort_values("trade_date").reset_index(drop=True)


def _ts_to_date(ts) -> str | None:
    """
    毫秒时间戳 -> 'YYYY-MM-DD'。
    这些时间戳都是 UTC 零点，转成 UTC 日期即可对应 A 股的交易日。
    """
    if ts is None:
        return None
    try:
        return pd.to_datetime(int(ts), unit="ms", utc=True).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError):
        return None


# ==================================================================
# 股票
# ==================================================================
def to_secid(code: str) -> str:
    """
    把股票代码转成东方财富的 secid 格式。
    规则：沪市（6开头、9开头）前面加 1.，深市（0、3开头）加 0.
    例：600519 -> 1.600519   000001 -> 0.000001
    """
    code = str(code).strip()
    if code.startswith(("6", "9", "5")):
        return f"1.{code}"
    return f"0.{code}"


def fetch_stock_kline(code: str, beg: str = "20180101", end: str | None = None,
                      klt: int = 101) -> pd.DataFrame:
    """
    抓取股票K线（前复权）。

    参数：
      beg: 起始日期，格式 YYYYMMDD
      klt: 101=日线 102=周线 103=月线

    同样采用"主通道 + 备用通道"：
      主通道 腾讯财经：稳定、支持前复权、单次最多 800 条（我们用日期区间分段补齐）
      备用通道 东方财富：单次可拿全，但近期出现限流，作为备份
    """
    try:
        return _fetch_kline_tencent(code, beg, end, klt)
    except DataSourceError:
        return _fetch_kline_eastmoney(code, beg, end, klt)


def to_tencent_symbol(code: str) -> str:
    """
    股票代码 -> 腾讯行情代码。
    规则：沪市(sh) 6/9/5 开头，深市(sz) 0/3 开头，北交所(bj) 4/8 开头
    """
    code = str(code).strip()
    if code.startswith(("6", "9", "5")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


_KLT_TO_TENCENT = {101: "day", 102: "week", 103: "month"}


def _fetch_kline_tencent(code: str, beg: str, end: str | None, klt: int) -> pd.DataFrame:
    """
    腾讯接口分段抓取。

    难点：单次最多返回 800 条（约3年日线）。
    解法：从最新日期往回一段一段要，直到覆盖到 beg 为止（"倒序分页"）。
    """
    symbol = to_tencent_symbol(code)
    period = _KLT_TO_TENCENT.get(klt, "day")

    beg_dt = pd.to_datetime(beg, format="%Y%m%d")
    end_dt = pd.to_datetime(end, format="%Y%m%d") if end else pd.Timestamp.today().normalize()

    collected: dict[str, list] = {}
    cursor = end_dt

    for _ in range(30):                       # 最多翻30段，足够覆盖30年日线
        if cursor < beg_dt:
            break
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        # 腾讯接口的 param 格式：代码,周期,起始日,结束日,条数,复权方式
        # 每次从 cursor 往回取约 3 年（640 个交易日上限），不足就继续往前翻
        window_start = max(beg_dt, cursor - pd.Timedelta(days=1100))
        params = {
            "param": (f"{symbol},{period},"
                      f"{window_start.strftime('%Y-%m-%d')},"
                      f"{cursor.strftime('%Y-%m-%d')},640,qfq"),
        }

        payload = _get_json(url, referer="https://gu.qq.com/", params=params)
        data = payload.get("data")
        if not isinstance(data, dict) or symbol not in data:
            raise DataSourceError(f"腾讯接口返回异常（{payload.get('msg')}），股票 {code}")

        node = data[symbol]
        rows = node.get(f"qfq{period}") or node.get(period) or []
        if not rows:
            break

        for row in rows:
            if len(row) >= 6:
                collected[row[0]] = row

        earliest = pd.to_datetime(rows[0][0])
        if earliest <= beg_dt or len(rows) < 10:
            break
        cursor = earliest - pd.Timedelta(days=1)

    if not collected:
        raise DataSourceError(f"股票 {code} 没有取到K线数据")

    # 腾讯行格式：[日期, 开盘, 收盘, 最高, 最低, 成交量]
    records = []
    for d in sorted(collected):
        row = collected[d]
        records.append({
            "trade_date": d,
            "open": _to_float(row[1]),
            "close": _to_float(row[2]),
            "high": _to_float(row[3]),
            "low": _to_float(row[4]),
            "volume": _to_float(row[5]),
            "amount": None,          # 腾讯该接口不提供成交额
            "pct_change": None,
        })

    df = pd.DataFrame(records)
    df = df.dropna(subset=["trade_date", "close"]).drop_duplicates("trade_date")
    df = df.sort_values("trade_date").reset_index(drop=True)

    # 涨跌幅用收盘价自己算（比接口给的更可靠）
    df["pct_change"] = (df["close"].pct_change(fill_method=None) * 100).round(2)

    # 取股票名称
    try:
        qt = (data[symbol].get("qt") or {}).get(symbol)
        df.attrs["name"] = qt[1] if isinstance(qt, list) and len(qt) > 1 else ""
    except Exception:  # noqa: BLE001
        df.attrs["name"] = ""
    return df


def _fetch_kline_eastmoney(code: str, beg: str, end: str | None, klt: int) -> pd.DataFrame:
    """备用通道：东方财富K线接口，一次可拿全历史。"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": to_secid(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": klt, "fqt": 1,
        "beg": beg, "end": end or "20500101",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    payload = _get_json(url, referer="https://quote.eastmoney.com/", params=params)

    data = payload.get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        raise DataSourceError(f"股票 {code} 没有取到K线数据（东财接口）")

    rows = []
    for line in klines:
        # 每行格式：日期,开盘,收盘,最高,最低,成交量,成交额,振幅
        parts = line.split(",")
        if len(parts) < 8:
            continue
        rows.append({
            "trade_date": parts[0],
            "open": _to_float(parts[1]),
            "close": _to_float(parts[2]),
            "high": _to_float(parts[3]),
            "low": _to_float(parts[4]),
            "volume": _to_float(parts[5]),
            "amount": _to_float(parts[6]),
            "pct_change": _to_float(parts[7]),
        })

    df = pd.DataFrame(rows).sort_values("trade_date").reset_index(drop=True)
    df.attrs["name"] = data.get("name") or ""
    return df


def _to_float(v) -> float | None:
    """把接口返回的字符串转成浮点数；空字符串/None 统一转成 None（SQL里就是 NULL）。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "")
    if s in ("", "-", "--", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None
