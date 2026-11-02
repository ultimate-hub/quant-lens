"""
API 路由模块：把 Python 函数暴露成网页可以访问的接口。

什么是 API？
  你可以把 API 理解为"点餐窗口"：网页（顾客）说出需求，比如
  "给我基金 110022 的指标"，后端（后厨）算好后端出一盘 JSON 数据。

什么是 JSON？
  一种纯文本的数据格式，形如 {"name": "易方达消费", "sharpe": 0.85}
  人和程序都能读，是互联网通用的数据交换格式。

自动生成的接口文档：
  启动后访问 http://127.0.0.1:8000/docs 可以看到所有接口，并且能在线点"试一试"。
  这是 FastAPI 自带的福利，不用手写文档。
"""

from fastapi import APIRouter, HTTPException, Query

from app.services import indicators, pipeline

router = APIRouter(prefix="/api", tags=["量化分析"])


@router.get("/health")
def health():
    """
    健康检查：用来确认服务是否活着。

    同时充当心跳：前端每10秒调一次，桌面版靠它判断窗口是否还开着。
    """
    from app.core import heartbeat
    heartbeat.touch()
    return {"status": "ok", "service": "量化分析平台"}


@router.get("/funds/search")
def search_fund(key: str = Query(..., description="基金代码或名称关键词")):
    """搜索基金。目前直接查东方财富的搜索接口。"""
    from app.services import datasource
    try:
        return datasource.fetch_fund_info(key)
    except datasource.DataSourceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/funds/{code}/sync")
def sync_fund(code: str):
    """
    同步基金数据（网络 -> 本地数据库）。

    为什么用 POST 而不是 GET？
      POST 表示"这是一个会产生副作用的操作"（会写数据库）。
      GET 应该只读。这是 HTTP 语义规范。
    """
    try:
        return pipeline.sync_fund(code)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"同步失败: {exc}") from exc


@router.get("/funds/{code}")
def fund_detail(
    code: str,
    start: str | None = Query(None, description="起始日期 YYYY-MM-DD"),
    end: str | None = Query(None, description="结束日期 YYYY-MM-DD"),
    use_acc_nav: bool = Query(False, description="是否用累计净值（含分红）"),
    auto_sync: bool = Query(True, description="本地无数据时自动同步"),
):
    """
    基金详情：基础信息 + 净值曲线 + 全部指标 + 综合评分。

    这是前端"基金诊断页"唯一需要调用的接口 —— 一次拿全。
    """
    # 1. 本地没有数据就先同步（首次查询会有几秒等待）
    if not pipeline.has_fund_data(code):
        if not auto_sync:
            raise HTTPException(status_code=404, detail="本地无该基金数据，请先同步")
        try:
            pipeline.sync_fund(code)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"数据同步失败: {exc}") from exc

    info = pipeline.load_fund_info(code) or {"code": code, "name": "", "fund_type": ""}

    # 2. 读取净值序列
    nav = pipeline.load_fund_nav(code, start, end, use_acc_nav=use_acc_nav)
    if len(nav) < 2:
        raise HTTPException(status_code=404, detail="该区间内净值数据不足")

    # 3. 计算指标与评分
    metrics = indicators.compute_all_metrics(nav)
    score = indicators.score_fund(metrics)

    # 4. 回撤序列（前端画"水下曲线"用）
    dd = indicators.drawdown_series(nav)

    return {
        "info": info,
        "metrics": metrics,
        "score": score,
        "nav_series": [
            {"date": d.strftime("%Y-%m-%d"), "nav": round(float(v), 4)}
            for d, v in nav.items()
        ],
        "drawdown_series": [
            {"date": d.strftime("%Y-%m-%d"), "dd": round(float(v), 4)}
            for d, v in dd.items()
        ],
    }


@router.get("/funds/{code}/nav")
def fund_nav(code: str, start: str | None = None, end: str | None = None):
    """只取净值数据（轻量接口，画图够用）。"""
    nav = pipeline.load_fund_nav(code, start, end)
    if nav.empty:
        raise HTTPException(status_code=404, detail="本地无数据，请先调用 sync 接口")
    return {
        "code": code,
        "series": [
            {"date": d.strftime("%Y-%m-%d"), "nav": round(float(v), 4)}
            for d, v in nav.items()
        ],
    }


@router.get("/compare")
def compare_funds(codes: str = Query(..., description="多个基金代码，逗号分隔"),
                  start: str | None = None):
    """
    多基金对比：把所有净值归一化到 1（即"假设都从 1 块钱开始"），
    这样不同净值的基金才能放在同一张图上比较。
    """
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        raise HTTPException(status_code=400, detail="请至少提供一个基金代码")

    result = []
    for code in code_list:
        if not pipeline.has_fund_data(code):
            try:
                pipeline.sync_fund(code)
            except Exception:  # noqa: BLE001
                continue
        nav = pipeline.load_fund_nav(code, start)
        if len(nav) < 2:
            continue
        base = nav.iloc[0]
        info = pipeline.load_fund_info(code) or {}
        result.append({
            "code": code,
            "name": info.get("name", ""),
            "series": [
                {"date": d.strftime("%Y-%m-%d"), "value": round(float(v / base), 4)}
                for d, v in nav.items()
            ],
        })

    if not result:
        raise HTTPException(status_code=404, detail="没有可对比的数据")
    return {"funds": result}


@router.post("/stocks/{code}/sync")
def sync_stock(code: str, beg: str = "20200101"):
    """同步股票日线数据。"""
    try:
        return pipeline.sync_stock(code, beg=beg)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"同步失败: {exc}") from exc


@router.get("/stocks/{code}")
def stock_detail(code: str, start: str | None = None):
    """股票详情：K线数据 + 指标。"""
    close = pipeline.load_stock_close(code, start)
    if len(close) < 2:
        raise HTTPException(status_code=404, detail="本地无该股票数据，请先同步")
    metrics = indicators.compute_all_metrics(close)
    return {
        "code": code,
        "metrics": metrics,
        "series": [
            {"date": d.strftime("%Y-%m-%d"), "close": round(float(v), 2)}
            for d, v in close.items()
        ],
    }
