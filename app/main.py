# -*- coding: utf-8 -*-
"""纸故事卡学习路径 API。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .errors import DomainError, ValidationBundle
from .models import PlanRequest, PlanResult
from .planner import Planner
from .storage import VersionStore, canonicalize_request


def _pydantic_code(err: dict[str, Any]) -> str:
    etype = err.get("type", "")
    if etype == "extra_forbidden":
        return "UNKNOWN_FIELD"
    if etype.startswith("enum") or etype == "literal_error":
        return "UNKNOWN_MASTERY_STATUS"
    if etype in {"missing", "value_error"}:
        return "INVALID_REQUEST"
    if "string" in etype or "literal" in etype:
        return "INVALID_VALUE"
    return "INVALID_REQUEST"


def _translate_pydantic(err: dict[str, Any]) -> dict[str, Any]:
    loc = [x for x in err.get("loc", ()) if x != "body"]
    etype = err.get("type", "")
    msg = err.get("msg", "请求内容不正确。")
    code = _pydantic_code(err)

    if etype.startswith("enum") or etype == "literal_error":
        message = (
            "掌握状态只支持 mastered（会了）、not_mastered（还不会）、"
            f"uncertain（不确定）；“不确定”会按未掌握处理。"
        )
        hint = "把状态改成这三个英文词之一，小朋友也能看懂返回里的中文解释。"
    elif etype == "extra_forbidden":
        field = loc[-1] if loc else ""
        message = f"出现了契约里没有的字段「{field}」，请删掉它。"
        hint = "接口只接受 concepts、materials、goal、mastery 这几个字段。"
    elif etype == "missing":
        field = loc[-1] if loc else ""
        message = f"缺少必填字段「{field}」。"
        hint = "请按 README 里的例子补齐这个字段。"
    elif "string_too_short" in etype:
        field = loc[-1] if loc else ""
        message = f"字段「{field}」不能是空白，要写清楚。"
        hint = "给小朋友看的名字、标题都要认认真真填上。"
    elif "value_error" in etype:
        message = msg.removeprefix("Value error, ")
        hint = "请对照 README 里的 JSON 例子检查标识和字段。"
    elif "too_long" in etype:
        field = loc[-1] if loc else ""
        message = f"字段「{field}」超出长度限制了。"
        hint = "请把名字、标题或先修清单缩短一些。"
    else:
        message = f"输入格式有问题：{msg}"
        hint = "请对照 README 里的 JSON 例子检查。"

    item: dict[str, Any] = {"code": code, "message": message, "loc": loc}
    if hint:
        item["hint"] = hint
    return item


def create_app(db_path: str | None = None) -> FastAPI:
    store: dict[str, VersionStore] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        store["db"] = VersionStore(db_path or os.environ.get("PAPERCARDS_DB"))
        yield
        store["db"].conn.close()

    app = FastAPI(
        title="纸故事卡机关学习路径 API",
        version="1.0.0",
        description=(
            "为 8-11 岁小朋友和家长规划“会张嘴/弹起立体纸故事卡”的学习步骤链。"
            "只在材料覆盖内找完整路径；缺页或无外部入口的循环则给出最小外部补教集合。"
        ),
        lifespan=lifespan,
    )

    # ---- 错误渲染 ----

    @app.exception_handler(ValidationBundle)
    async def _bundle_handler(request: Request, exc: ValidationBundle) -> JSONResponse:
        body = exc.to_dict()
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(DomainError)
    async def _domain_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.to_dict(), "errors": [exc.to_dict()]},
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        items = [_translate_pydantic(e) for e in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={"error": items[0], "errors": items},
        )

    # ---- 路由 ----

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/versions")
    async def create_version(payload: PlanRequest) -> dict[str, Any]:
        """提交一单规划；内容相同则复用同一不可变版本。"""
        # 先按用户提交的原始顺序校验，保证 loc 下标可定位
        Planner(payload)
        canonical, normalized = canonicalize_request(payload)
        result: PlanResult = Planner(normalized, skip_validation=True).plan()
        vid, created_at, created_now = store["db"].create_or_get(
            canonical, normalized, result
        )
        return {
            "version": {
                "id": vid,
                "created_at": created_at,
                "newly_created": created_now,
            },
            "result": result.model_dump(mode="json"),
        }

    @app.get("/api/versions")
    async def list_versions() -> dict[str, Any]:
        return {"versions": store["db"].list_versions()}

    @app.get("/api/versions/{vid}")
    async def get_version(vid: str) -> dict[str, Any]:
        data = store["db"].get(vid)
        if data is None:
            raise DomainError(
                "VERSION_NOT_FOUND",
                f"找不到版本「{vid}」。版本标识很长，复制时请保持完整。",
                loc=["version_id"],
                hint="可以先调用 GET /api/versions 查看已有版本。",
                status_code=404,
            )
        return data

    return app


app = create_app()
