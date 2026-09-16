# -*- coding: utf-8 -*-
"""领域规划器：校验、最短学习路径搜索、缺页/循环补教集合。

裁决优先级（题目要求）：
  1. 新增概念数最少；
  2. 使用的不同段落数最少；
  3. 学习概念标识序列字典序最小；
  4. 对应段落标识序列字典序最小。
"""

from __future__ import annotations

from collections import deque
from itertools import combinations
from typing import Iterable

from .errors import DomainError, ValidationBundle
from .models import (
    ConceptCard,
    MaterialSegment,
    Optimality,
    PlanRequest,
    PlanResult,
    PlanStep,
    PrerequisiteEvidence,
    RemediationConcept,
    UnblockedConcept,
)

# 搜索安全上限，超过后给出可定位的中文错误而不是卡住
MAX_EXPANSIONS = 120_000
MAX_COMBINATIONS = 200_000


# ---------------------------------------------------------------------------
# 整单校验
# ---------------------------------------------------------------------------

def validate_request(req: PlanRequest) -> None:
    """语义校验；发现问题时整单拒绝（一次返回全部定位错误）。"""

    errors: list[DomainError] = []

    concept_ids: set[str] = set()
    for i, c in enumerate(req.concepts):
        if c.id in concept_ids:
            errors.append(
                DomainError(
                    "DUPLICATE_ID",
                    f"概念标识「{c.id}」重复了，第 {i + 1} 张卡和前面的卡撞名了。",
                    ["concepts", i, "id"],
                )
            )
        concept_ids.add(c.id)

    segment_ids: set[str] = set()
    for i, m in enumerate(req.materials):
        if m.id in segment_ids:
            errors.append(
                DomainError(
                    "DUPLICATE_ID",
                    f"材料分段标识「{m.id}」重复了，第 {i + 1} 段和前面的段撞名了。",
                    ["materials", i, "id"],
                )
            )
        segment_ids.add(m.id)

    # 先修结构与悬空引用
    for i, c in enumerate(req.concepts):
        schemes = c.prerequisite_schemes
        if len(schemes) > 12:
            errors.append(
                DomainError(
                    "ILLEGAL_PREREQUISITE",
                    f"概念「{c.id}」的替代先修方案最多 12 个，现在有 {len(schemes)} 个。",
                    ["concepts", i, "prerequisite_schemes"],
                )
            )
        has_empty = any(len(s) == 0 for s in schemes)
        if has_empty and len(schemes) > 1:
            errors.append(
                DomainError(
                    "EMPTY_SCHEME_MIXED",
                    f"概念「{c.id}」里“无需先修”的空方案和别的方案并列了；"
                    "无需先修时应只保留一个空方案。",
                    ["concepts", i, "prerequisite_schemes"],
                )
            )
        for s, scheme in enumerate(schemes):
            if len(scheme) > 20:
                errors.append(
                    DomainError(
                        "ILLEGAL_PREREQUISITE",
                        f"概念「{c.id}」的第 {s + 1} 个先修方案太长（{len(scheme)} 个），最多 20 个。",
                        ["concepts", i, "prerequisite_schemes", s],
                    )
                )
            if len(set(scheme)) != len(scheme):
                dup = next(x for x in scheme if scheme.count(x) > 1)
                errors.append(
                    DomainError(
                        "DUPLICATE_IN_SCHEME",
                        f"概念「{c.id}」的第 {s + 1} 个先修方案里，「{dup}」写了不止一次。",
                        ["concepts", i, "prerequisite_schemes", s],
                    )
                )
            for k, ref in enumerate(scheme):
                if ref not in concept_ids:
                    errors.append(
                        DomainError(
                            "DANGLING_REFERENCE",
                            f"概念「{c.id}」的第 {s + 1} 个先修方案里，"
                            f"提到了概念卡里没有的概念「{ref}」。",
                            ["concepts", i, "prerequisite_schemes", s, k],
                        )
                    )

    # 材料讲授引用
    for i, m in enumerate(req.materials):
        if len(m.teaches) > 30:
            errors.append(
                DomainError(
                    "ILLEGAL_PREREQUISITE",
                    f"材料分段「{m.id}」一次讲太多概念（{len(m.teaches)} 个），最多 30 个。",
                    ["materials", i, "teaches"],
                )
            )
        for k, ref in enumerate(m.teaches):
            if ref not in concept_ids:
                errors.append(
                    DomainError(
                        "DANGLING_REFERENCE",
                        f"材料分段「{m.id}」声明讲解概念「{ref}」，但概念卡里没有这张卡。",
                        ["materials", i, "teaches", k],
                    )
                )

    # 掌握状态引用
    for key in req.mastery:
        if key not in concept_ids:
            errors.append(
                DomainError(
                    "DANGLING_REFERENCE",
                    f"掌握状态里提到了概念卡里没有的概念「{key}」。",
                    ["mastery", key],
                )
            )

    # 目标引用
    if req.goal not in concept_ids:
        errors.append(
            DomainError(
                "MISSING_GOAL",
                f"目标机关「{req.goal}」不在概念卡里。",
                ["goal"],
                hint="先把目标机关加进概念卡，或把目标改成已有概念的标识。",
            )
        )

    if errors:
        raise ValidationBundle(errors)


# ---------------------------------------------------------------------------
# 规划器
# ---------------------------------------------------------------------------

class Planner:
    def __init__(self, req: PlanRequest, *, skip_validation: bool = False) -> None:
        if not skip_validation:
            validate_request(req)
        self.req = req
        self.concepts: dict[str, ConceptCard] = {c.id: c for c in req.concepts}
        self.segments: dict[str, MaterialSegment] = {m.id: m for m in req.materials}

        # 概念 -> 讲授它的段落（按段落标识排序，保证字典序裁决确定）
        self.coverage: dict[str, list[str]] = {c.id: [] for c in req.concepts}
        for m in sorted(req.materials, key=lambda x: x.id):
            for cid in dict.fromkeys(m.teaches):  # 去重保序
                self.coverage[cid].append(m.id)

        # “不确定”视为未掌握：只有 mastered 进集合
        self.mastered: set[str] = {
            cid for cid, st in req.mastery.items() if st == "mastered"
        }
        self.goal = req.goal

    # ---------------- 基础判断 ----------------

    def _satisfied_schemes(self, cid: str, known: set[str]) -> list[int]:
        """返回在 known 集合下已满足的方案序号（按提交顺序）。"""
        out: list[int] = []
        for idx, scheme in enumerate(self.concepts[cid].prerequisite_schemes):
            if all(p in known for p in scheme):
                out.append(idx)
        return out

    def _ready(self, cid: str, known: set[str], covered_only: bool) -> bool:
        """概念是否可学：方案为空或任一方案满足；材料搜索时还须被覆盖。"""
        if covered_only and not self.coverage.get(cid):
            return False
        schemes = self.concepts[cid].prerequisite_schemes
        return len(schemes) == 0 or any(
            all(p in known for p in scheme) for scheme in schemes
        )

    def _saturate(
        self, seeds: set[str], covered_only: bool, stop_at: str | None = None
    ) -> set[str]:
        """从 seeds 出发反复吸收所有“可学”概念，直到不动点。"""
        known = set(seeds)
        changed = True
        while changed:
            if stop_at is not None and stop_at in known:
                return known
            changed = False
            for cid in self.concepts:
                if cid not in known and self._ready(cid, known, covered_only):
                    known.add(cid)
                    changed = True
        return known

    # ---------------- 入口 ----------------

    def plan(self) -> PlanResult:
        goal = self.goal

        # 目标已掌握：空路径
        if goal in self.mastered:
            return PlanResult(
                status="path",
                goal_id=goal,
                goal_already_mastered=True,
                steps=[],
                concept_sequence=[],
                segment_sequence=[],
                optimality=Optimality(new_concept_count=0, distinct_segment_count=0),
                summary=f"「{self.concepts[goal].name}」已经掌握啦，可以直接做目标机关！",
            )

        # 目标没有材料页：它不能进材料路径，也不允许外部补教“目标本身”
        if not self.coverage.get(goal):
            raise DomainError(
                "GOAL_NOT_COVERED",
                f"目标机关「{goal}」没有任何材料分段讲解，没法在材料里学会它；"
                "外部补教也不能代替目标机关本身。",
                loc=["goal"],
            )

        candidates = self._search_path()
        if candidates:
            scored = []
            for seq in candidates:
                seg_seq = self._assign_segments(seq)
                scored.append(
                    (len(set(seg_seq)), tuple(seq), tuple(seg_seq), seq, seg_seq)
                )
            _, _, _, seq, seg_seq = min(scored, key=lambda x: (x[0], x[1], x[2]))
            return self._build_path_result(seq, seg_seq)
        return self._build_remediation_result()

    # ---------------- 材料内完整路径 BFS ----------------

    def _search_path(self) -> list[list[str]]:
        """按概念数最少优先 BFS，返回最浅层全部到达目标的候选概念序列。

        同一“已掌握集合”只保留字典序最小的序列；不同集合的候选
        交给段落分配阶段，按不同段落数、字典序继续裁决。
        """
        start = frozenset(self.mastered)
        # 状态：已掌握概念集合 -> 该集合对应的字典序最小概念序列
        frontier: dict[frozenset[str], tuple[str, ...]] = {start: ()}
        seen: set[frozenset[str]] = {start}
        expansions = 0

        while frontier:
            # 本层先检查目标是否可达（目标在本层被学会）
            goal_hits = [seq for known_set, seq in frontier.items() if self.goal in known_set]
            if goal_hits:
                return [list(s) for s in goal_hits]

            next_frontier: dict[frozenset[str], tuple[str, ...]] = {}
            for known_set, seq in frontier.items():
                known = set(known_set)
                candidates = [
                    cid
                    for cid in self.coverage  # dict 按提交顺序，最后统一用序列字典序
                    if cid not in known and self._ready(cid, known, covered_only=True)
                ]
                expansions += 1
                if expansions > MAX_EXPANSIONS:
                    raise DomainError(
                        "SEARCH_TOO_LARGE",
                        "概念之间的先修关系展开后路线太多，暂时算不过来；请把一包概念拆小一些。",
                        loc=["concepts"],
                    )
                for cid in sorted(candidates):
                    new_set = frozenset(known | {cid})
                    if new_set in seen:
                        continue
                    new_seq = seq + (cid,)
                    old = next_frontier.get(new_set)
                    if old is None or new_seq < old:
                        next_frontier[new_set] = new_seq
            seen.update(next_frontier.keys())
            frontier = next_frontier

        return []

    # ---------------- 段落分配：不同段落最少，字典序兜底 ----------------

    def _assign_segments(self, seq: list[str]) -> list[str]:
        # dp: 已用段落集合 -> （段落序列，不同段落数）
        dp: dict[frozenset[str], tuple[str, ...]] = {frozenset(): ()}
        for cid in seq:
            nxt: dict[frozenset[str], tuple[str, ...]] = {}
            options = self.coverage[cid]  # 已按标识排序
            for used, chosen in dp.items():
                for seg_id in options:
                    new_used = used | {seg_id}
                    new_chosen = chosen + (seg_id,)
                    old = nxt.get(new_used)
                    if old is None or new_chosen < old:
                        nxt[new_used] = new_chosen
            dp = nxt

        best_set, best_tuple = min(
            dp.items(), key=lambda kv: (len(kv[0]), kv[1])
        )
        return list(best_tuple)

    # ---------------- 构造路径结果 ----------------

    def _build_path_result(self, seq: list[str], seg_seq: list[str]) -> PlanResult:
        # 每个概念最终命中的方案
        chosen_scheme: dict[str, int] = {}
        known_before: set[str] = set(self.mastered)
        for cid in seq:
            satisfied = self._satisfied_schemes(cid, known_before)
            if satisfied:
                chosen_scheme[cid] = satisfied[0]
            known_before.add(cid)

        step_index = {cid: i + 1 for i, cid in enumerate(seq)}
        steps: list[PlanStep] = []
        for i, cid in enumerate(seq):
            known = set(self.mastered) | set(seq[:i])
            basis: list[PrerequisiteEvidence] = []
            scheme_idx: int | None = chosen_scheme.get(cid)
            if scheme_idx is not None:
                for pre in self.concepts[cid].prerequisite_schemes[scheme_idx]:
                    if pre in self.mastered:
                        basis.append(
                            PrerequisiteEvidence(
                                prerequisite_id=pre,
                                prerequisite_name=self.concepts[pre].name,
                                scheme_index=scheme_idx,
                                satisfied_by="mastered",
                                satisfied_at_step=None,
                            )
                        )
                    else:
                        basis.append(
                            PrerequisiteEvidence(
                                prerequisite_id=pre,
                                prerequisite_name=self.concepts[pre].name,
                                scheme_index=scheme_idx,
                                satisfied_by="learned",
                                satisfied_at_step=step_index[pre],
                            )
                        )

            # 解除的阻塞：后续路径中命中方案依赖本概念的概念
            unblocks: list[UnblockedConcept] = []
            for later in seq[i + 1:]:
                sidx = chosen_scheme.get(later)
                deps = (
                    set(self.concepts[later].prerequisite_schemes[sidx])
                    if sidx is not None
                    else set()
                )
                if cid in deps:
                    unblocks.append(
                        UnblockedConcept(
                            concept_id=later,
                            concept_name=self.concepts[later].name,
                            is_goal=(later == self.goal),
                        )
                    )

            seg_id = seg_seq[i]
            steps.append(
                PlanStep(
                    step=i + 1,
                    concept_id=cid,
                    concept_name=self.concepts[cid].name,
                    is_goal=(cid == self.goal),
                    satisfaction_basis=basis,
                    used_scheme_index=scheme_idx,
                    read_segment_id=seg_id,
                    read_segment_title=self.segments[seg_id].title,
                    unblocks=unblocks,
                )
            )

        distinct = len(set(seg_seq))
        goal_name = self.concepts[self.goal].name
        summary = (
            f"跟着 {len(seq)} 步走：先学前面的小概念，最后就能完成「{goal_name}」；"
            f"全程新学 {len(seq)} 个概念，共读 {distinct} 段材料。"
        )
        return PlanResult(
            status="path",
            goal_id=self.goal,
            goal_already_mastered=False,
            steps=steps,
            concept_sequence=seq,
            segment_sequence=seg_seq,
            optimality=Optimality(
                new_concept_count=len(seq), distinct_segment_count=distinct
            ),
            summary=summary,
        )

    # ---------------- 缺页 / 循环：最小外部补教集合 ----------------

    def _dependency_cone(self) -> list[str]:
        """目标（传递）依赖到的全部概念，排除已掌握与目标本身，按标识排序。"""
        cone: set[str] = set()
        queue: deque[str] = deque([self.goal])
        while queue:
            cid = queue.popleft()
            for scheme in self.concepts[cid].prerequisite_schemes:
                for pre in scheme:
                    if pre not in cone and pre not in self.mastered and pre != self.goal:
                        cone.add(pre)
                        queue.append(pre)
        return sorted(cone)

    def _minimal_remediation(self, cone: list[str]) -> set[str] | None:
        """枚举：先基数最小，再升序标识序列字典序最小。"""
        base = set(self.mastered)
        tried = 0
        for k in range(0, len(cone) + 1):
            for combo in combinations(cone, k):
                tried += 1
                if tried > MAX_COMBINATIONS:
                    raise DomainError(
                        "SEARCH_TOO_LARGE",
                        "缺页和循环的补教组合太多，暂时算不过来；请把一包概念拆小一些。",
                        loc=["concepts"],
                    )
                reachable = self._saturate(
                    base | set(combo), covered_only=True, stop_at=self.goal
                )
                if self.goal in reachable:
                    return set(combo)
        return None

    def _build_remediation_result(self) -> PlanResult:
        cone = self._dependency_cone()
        remediation = self._minimal_remediation(cone)
        if remediation is None:  # 理论上不会发生：cone 全体补教后必然可达
            raise DomainError(
                "GOAL_NOT_COVERED",
                f"即使补教所有相关概念，目标机关「{self.goal}」仍然无法完成，请检查概念卡。",
                loc=["goal"],
            )

        items: list[RemediationConcept] = []
        for cid in sorted(remediation):
            reason = "missing_page" if not self.coverage.get(cid) else "cycle"
            items.append(
                RemediationConcept(
                    concept_id=cid,
                    concept_name=self.concepts[cid].name,
                    reason=reason,  # type: ignore[arg-type]
                )
            )

        reasons = {it.reason for it in items}
        if reasons == {"missing_page"}:
            top_reason = "missing_page"
            reason_text = "材料里没有讲到它们（缺页）"
        elif reasons == {"cycle"}:
            top_reason = "cycle"
            reason_text = "它们和别的概念互相卡住，需要从外面先带进门（无外部入口的循环）"
        else:
            top_reason = "both"
            reason_text = "有的材料缺页，有的互相循环卡住"

        goal_name = self.concepts[self.goal].name
        summary = (
            f"材料里暂时走不通：{reason_text}。请在材料之外先补教这 "
            f"{len(items)} 个概念，之后孩子就能顺着材料完成「{goal_name}」。"
        )
        return PlanResult(
            status="remediation",
            goal_id=self.goal,
            remediation=items,
            unreachable_reason=top_reason,  # type: ignore[arg-type]
            summary=summary,
        )


def plan(req: PlanRequest) -> PlanResult:
    return Planner(req).plan()
