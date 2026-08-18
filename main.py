# -*- coding: utf-8 -*-
"""main.py —— 程序入口（后端）

职责分两块：
    1. 定义 API 接口（前后端靠这些接口对话，见下方路由）
    2. 托管前端静态文件（HTML/CSS/JS 也是这个服务发出去的）

运行方式（在项目目录下）：
    .venv\\Scripts\\python -m uvicorn main:app --port 8000
然后浏览器打开 http://localhost:8000 就是整个应用。

数据操作的具体实现都在 database.py，这里只做"接请求 -> 调函数 -> 回结果"。
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

import database


# ==================== 启动钩子 ====================
# lifespan 是 FastAPI 官方的"生命周期"机制：
# yield 之前的部分 = 服务启动时自动执行一次（保证数据库表存在，用户不用手动初始化）；
# yield 之后的部分 = 服务关闭时执行（本项目暂时没有要清理的，留空）。
@asynccontextmanager
async def lifespan(_app: FastAPI):
    database.init_db()  # 启动时建表（表已存在则跳过）
    yield


# 创建 FastAPI 应用实例：整个后端的"总服务台"
app = FastAPI(title="客户跟进管理", description="自由职业者的客户台账 API", lifespan=lifespan)


# ==================== 统一错误格式 ====================
# 默认情况下，请求体类型不对（比如报价传了 "abc"、漏传 name）会被
# Pydantic 拒绝并返回 422 + 英文错误 + detail 是数组——这和本项目的
# 约定（400 + 中文原因 + detail 是字符串）不一致，前端也没法直接展示。
# 这里注册一个全局处理器，把这类"格式错误"翻译成统一的中文格式。
@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, exc: RequestValidationError):
    # exc.errors() 是错误列表，取第一条的 msg（如 "Input should be a
    # valid number"），其余细节忽略——对小白用户来说一条就够用了。
    first = exc.errors()[0] if exc.errors() else {}
    field = first.get("loc", ["?"])[-1]  # loc 是出错字段的路径，取最后一段
    reason = first.get("msg", "数据格式不正确")
    detail = f"「{field}」字段格式不正确（{reason}）"
    return JSONResponse(status_code=400, content={"detail": detail})


# ==================== 请求体模型 ====================
# Pydantic 模型：声明"这个接口要收什么样的数据"。
# FastAPI 会自动校验请求体：缺字段、类型不对都会直接返回 422 并附原因，
# 不用自己写一堆 if 判断。

class ClientIn(BaseModel):
    """新增/修改客户时，前端提交的字段。

    只有 name 是必填的（没有姓名记录毫无意义）；其余字段缺省为空。
    """
    name: str                      # 姓名（必填）
    source: str = ""               # 来源
    project_desc: str = ""         # 项目描述
    quote: float | None = None     # 报价（元），允许不填
    status: str = "待跟进"          # 状态
    notes: str = ""                # 备注
    next_followup: str = ""        # 下次跟进日期 YYYY-MM-DD，允许不填

    @field_validator("quote", mode="before")
    @classmethod
    def empty_quote_is_none(cls, value):
        """宽容处理：前端/调用方传空字符串时当作"没填"。

        不写这个校验的话，空字符串会被 422 拒绝——
        对调 API 的人来说太苛刻了，能看懂的输入就该收下。
        """
        if value == "" or value is None:
            return None
        return value


# ==================== API 接口 ====================

@app.get("/api/clients")
def api_list_clients(
    status: str = Query("", description="按状态筛选，如 待跟进"),
    source: str = Query("", description="按来源筛选"),
    q: str = Query("", description="关键词，搜姓名/项目描述/备注"),
):
    """GET /api/clients?status=待跟进&q=张三 —— 客户列表（支持组合筛选）。

    筛选参数都放在网址问号后面（查询字符串），前端用 fetch 拼上去。
    """
    return database.list_clients(status=status, source=source, q=q)


@app.post("/api/clients", status_code=201)
def api_create_client(body: ClientIn):
    """POST /api/clients —— 新建客户。成功返回 201（创建成功）+ 完整记录。"""
    try:
        return database.create_client(body.model_dump())
    except ValueError as exc:
        # 数据不合法（如姓名为空）：返回 400，把中文原因带给前端展示
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/clients/{client_id}")
def api_get_client(client_id: int):
    """GET /api/clients/{id} —— 查单个客户详情。不存在返回 404。"""
    client = database.get_client(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="客户不存在")
    return client


@app.put("/api/clients/{client_id}")
def api_update_client(client_id: int, body: ClientIn):
    """PUT /api/clients/{id} —— 修改客户（全字段覆盖式更新）。"""
    try:
        return database.update_client(client_id, body.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/clients/{client_id}")
def api_delete_client(client_id: int):
    """DELETE /api/clients/{id} —— 删除客户。成功返回 {ok: true}。"""
    if not database.delete_client(client_id):
        raise HTTPException(status_code=404, detail="客户不存在")
    return {"ok": True}


@app.get("/api/stats")
def api_stats():
    """GET /api/stats —— 首页统计：各状态数量 + 今日要跟进列表。"""
    return database.get_stats()


# ==================== 前端静态文件 ====================
# 把 static/ 目录整个挂到网站根路径：
#   static/index.html  ->  http://localhost:8000/
#   static/app.js      ->  http://localhost:8000/app.js
# 注意两点：
#   1) 路径用 Path(__file__) 拼绝对路径——不管从哪个目录启动服务都能找到前端；
#   2) html=True 只对"目录请求"回落到 index.html（比如访问 /docs/），
#      访问不存在的文件路径仍是 404。本应用用 # 锚点路由，不受影响。
#   3) 挂载必须放在所有 API 路由之后，否则静态托管会"吃掉" /api/... 请求。

app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
