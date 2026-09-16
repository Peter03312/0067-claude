# -*- coding: utf-8 -*-
"""Pydantic 契约：概念卡、材料分段、掌握状态与规划请求。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ID_PATTERN = re.compile(r"^[\w一-鿿][\w一-鿿・.\- ]{0,63}$", re.UNICODE)

MasteryStatus = Literal["mastered", "not_mastered", "uncertain"]

MAX_CONCEPTS = 60
MAX_SEGMENTS = 60
MAX_SCHEMES = 12
MAX_SCHEME_SIZE = 20
MAX_TEACHES = 30


class ConceptCard(BaseModel):
    """一张概念卡，例如“山折”“对称轴”“V 形弹片”。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="概念唯一标识")
    name: str = Field(..., min_length=1, max_length=80, description="给小朋友看的名字")
    # 可替代先修方案：任一方案内概念全部满足即可学习本概念；
    # 空列表表示无需先修。
    prerequisite_schemes: list[list[str]] = Field(
        default_factory=list, description="可替代先修方案；每个方案内概念须全部满足"
    )

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        v = v.strip()
        if not ID_PATTERN.match(v):
            raise ValueError("标识需为 1-64 个字符的中英文、数字、下划线或连字符")
        return v

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("名字不能是空白")
        return v.strip()


class MaterialSegment(BaseModel):
    """带唯一标识的材料分段；读到一段可以学会它讲的若干概念。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="材料分段唯一标识")
    title: str = Field(..., min_length=1, max_length=120, description="段落标题")
    teaches: list[str] = Field(..., min_length=1, description="本段讲解的概念标识")

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        v = v.strip()
        if not ID_PATTERN.match(v):
            raise ValueError("标识需为 1-64 个字符的中英文、数字、下划线或连字符")
        return v

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("标题不能是空白")
        return v.strip()


class PlanRequest(BaseModel):
    """规划一单：概念卡 + 材料分段 + 目标机关 + 掌握状态。"""

    model_config = ConfigDict(extra="forbid")

    concepts: list[ConceptCard] = Field(..., min_length=1, max_length=MAX_CONCEPTS)
    materials: list[MaterialSegment] = Field(..., min_length=1, max_length=MAX_SEGMENTS)
    goal: str = Field(..., min_length=1, description="目标机关对应的概念标识")
    mastery: dict[str, MasteryStatus] = Field(
        default_factory=dict, description="概念标识 -> 掌握状态；不确定视为未掌握"
    )


class PrerequisiteEvidence(BaseModel):
    """一步满足先修的依据。"""

    prerequisite_id: str
    prerequisite_name: str
    scheme_index: int = Field(..., description="使用的是第几个可替代先修方案（从 0 开始）")
    satisfied_by: Literal["mastered", "learned"]
    satisfied_at_step: int | None = Field(
        None, description="若在路径中学会，记录是第几步（从 1 开始）"
    )


class UnblockedConcept(BaseModel):
    """学会本步概念后解除阻塞的后续概念。"""

    concept_id: str
    concept_name: str
    is_goal: bool


class PlanStep(BaseModel):
    """学习路径上的一步。"""

    step: int = Field(..., ge=1)
    concept_id: str
    concept_name: str
    is_goal: bool
    satisfaction_basis: list[PrerequisiteEvidence] = Field(
        ..., description="满足依据：命中的先修方案及每个先修的来源"
    )
    used_scheme_index: int | None = Field(
        None, description="本概念命中的先修方案序号；无需先修时为 null"
    )
    read_segment_id: str = Field(..., description="应读段落标识")
    read_segment_title: str
    unblocks: list[UnblockedConcept]


class Optimality(BaseModel):
    """裁决依据，便于家长核对。"""

    new_concept_count: int
    distinct_segment_count: int


class RemediationConcept(BaseModel):
    """需要外部补教的概念。"""

    concept_id: str
    concept_name: str
    reason: Literal["missing_page", "cycle"]


class PlanResult(BaseModel):
    """规划结果：要么是完整路径，要么是外部补教集合。"""

    status: Literal["path", "remediation"]
    goal_id: str
    goal_already_mastered: bool = False
    steps: list[PlanStep] = Field(default_factory=list)
    concept_sequence: list[str] = Field(default_factory=list)
    segment_sequence: list[str] = Field(default_factory=list)
    optimality: Optimality | None = None
    remediation: list[RemediationConcept] = Field(default_factory=list)
    unreachable_reason: Literal["missing_page", "cycle", "both"] | None = None
    summary: str
