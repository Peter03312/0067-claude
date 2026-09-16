# -*- coding: utf-8 -*-
"""测试夹具：一套贴近纸故事卡场景的基础数据。"""

from __future__ import annotations

import pytest

# 概念（prerequisite_schemes 省略表示无需先修）：
# fold 山折 / valley 谷折 / axis 对称轴 / tab 连接片 /
# flap V 形弹片 / mouth 张嘴机关 / popup 弹起机关
BASE_CONCEPTS: list[dict] = [
    {"id": "fold", "name": "山折", "prerequisite_schemes": []},
    {"id": "valley", "name": "谷折", "prerequisite_schemes": []},
    {"id": "axis", "name": "对称轴", "prerequisite_schemes": []},
    {
        "id": "tab",
        "name": "连接片",
        # 连接片：能分清山折或谷折其一、再认出对称轴即可（替代方案）
        "prerequisite_schemes": [["fold", "axis"], ["valley", "axis"]],
    },
    {
        "id": "flap",
        "name": "V 形弹片",
        "prerequisite_schemes": [["fold"], ["valley"]],
    },
    {
        "id": "mouth",
        "name": "会张嘴的大嘴",
        # 两条替代路线：连接片路线 或 弹片路线
        "prerequisite_schemes": [["tab"], ["flap"]],
    },
    {
        "id": "popup",
        "name": "会弹起的小人",
        "prerequisite_schemes": [["tab", "axis"]],
    },
]

BASE_MATERIALS: list[dict] = [
    {"id": "M1", "title": "第1页：认识山折", "teaches": ["fold"]},
    {"id": "M2", "title": "第2页：认识谷折", "teaches": ["valley"]},
    {"id": "M3", "title": "第3页：找对称轴", "teaches": ["axis"]},
    {"id": "M4", "title": "第4页：剪一个连接片", "teaches": ["tab"]},
    {"id": "M5", "title": "第5页：折 V 形弹片", "teaches": ["flap", "fold"]},
    {"id": "M6", "title": "第6页：贴出会张嘴的大嘴", "teaches": ["mouth"]},
    {"id": "M7", "title": "第7页：固定会弹起的小人", "teaches": ["popup"]},
]


@pytest.fixture
def concepts() -> list[dict]:
    return [dict(c, prerequisite_schemes=[list(s) for s in c.get("prerequisite_schemes", [])]) for c in BASE_CONCEPTS]


@pytest.fixture
def materials() -> list[dict]:
    return [dict(m, teaches=list(m["teaches"])) for m in BASE_MATERIALS]


@pytest.fixture
def payload(concepts, materials) -> dict:
    return {
        "concepts": concepts,
        "materials": materials,
        "goal": "mouth",
        "mastery": {},
    }
