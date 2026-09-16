# -*- coding: utf-8 -*-
"""可定位的适龄中文错误。

所有领域错误都抛出 ``DomainError``，由 API 层统一渲染成 JSON：

    {
      "error": {
        "code": "DANGLING_REFERENCE",
        "message": "先修里提到了概念卡里没有的概念「X」",
        "loc": ["concepts", 2, "prerequisite_schemes", 0, 1],
        "hint": "先补上这张概念卡，或把引用改对。"
      },
      "errors": [...]   # 整单拒绝时给出全部问题
    }
"""

from __future__ import annotations

from typing import Any, Iterable

# 让 8-11 岁小朋友和家长也能看懂的固定提示
_HINTS: dict[str, str] = {
    "DUPLICATE_ID": "每一张卡、每一段材料都要有只属于自己的名字（标识）。",
    "DANGLING_REFERENCE": "先补上这张概念卡，或把引用改成已经存在的概念。",
    "ILLEGAL_PREREQUISITE": "先修只能写成“方案列表”：每个方案是一个概念标识的小清单。",
    "EMPTY_SCHEME_MIXED": "一个方案若表示“无需先修”，就只能单独存在，不能和别的方案并列。",
    "DUPLICATE_IN_SCHEME": "同一个方案里同一张先修卡写一次就够了。",
    "UNKNOWN_MASTERY_STATUS": "掌握状态只支持 mastered（会了）、not_mastered（还不会）、uncertain（不确定）。",
    "MISSING_GOAL": "目标机关要使用概念卡里已有的概念标识。",
    "GOAL_NOT_COVERED": "目标机关本身没有对应材料页，又不能请外部补教“目标”本身；请补充讲它的材料。",
    "SEARCH_TOO_LARGE": "概念数量太多，请拆成更小的一包再找路线。",
}


class DomainError(Exception):
    """单个可定位的领域错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        loc: list[str | int] | None = None,
        hint: str | None = None,
        status_code: int = 422,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.loc = list(loc or [])
        self.hint = hint if hint is not None else _HINTS.get(code, "")
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "loc": self.loc,
        }
        if self.hint:
            item["hint"] = self.hint
        return item


class ValidationBundle(Exception):
    """整单拒绝：一次给出全部定位错误。"""

    def __init__(self, errors: Iterable[DomainError]) -> None:
        self.errors: list[DomainError] = list(errors)
        super().__init__("; ".join(e.message for e in self.errors))

    def to_dict(self) -> dict[str, Any]:
        items = [e.to_dict() for e in self.errors]
        first = items[0]
        return {
            "error": first,
            "errors": items,
        }
