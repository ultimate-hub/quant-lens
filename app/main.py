"""
应用入口：创建 FastAPI 应用、挂载静态文件、注册路由。

FastAPI 的启动逻辑：
  1. 导入本文件 -> 创建 app 对象
  2. 执行 @app.on_event("startup") 注册的初始化（这里是建表）
  3. uvicorn 开始监听端口，等待请求
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.routes_v2 import router as router_v2
from app.core.config import STATIC_DIR
from app.core.db import init_db

app = FastAPI(
    title="量化分析平台 API",
    description="A股 + 国内基金的量化分析服务（MVP）",
    version="0.1.0",
)

# CORS（跨域资源共享）：
#   浏览器有安全策略，默认禁止网页访问"不同源"的接口。
#   开发阶段前后端可能在不同端口，所以先放开。生产环境应改成具体域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(router_v2)


@app.on_event("startup")
def on_startup():
    """服务启动时执行一次：建表。"""
    init_db()


# 挂载静态文件目录（前端页面、ECharts 图表库）
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    """访问根路径时返回前端页面。"""
    v2 = STATIC_DIR / "v2.html"
    if v2.exists():
        return FileResponse(v2)
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "前端页面尚未创建，请访问 /docs 查看接口文档"}


@app.get("/v1")
def index_v1():
    """旧版基金诊断页（保留）。"""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "v1 页面不存在"}
