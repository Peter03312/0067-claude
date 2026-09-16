# -*- coding: utf-8 -*-
"""领域测试：替代方案、并列最优、缺页、循环、非法引用。"""

from __future__ import annotations

import pytest

from app.errors import ValidationBundle
from app.models import PlanRequest
from app.planner import Planner, plan


def req(payload: dict) -> PlanRequest:
    return PlanRequest.model_validate(payload)


# ---------------------------------------------------------------------------
# 基础路径与替代方案
# ---------------------------------------------------------------------------

class TestAlternativeSchemes:
    def test_picks_route_with_fewest_new_concepts(self, payload):
        """弹片路线只需 flap（含 fold 的材料覆盖），连接片路线要 tab+axis+fold。

        flap 只需 fold 一个先修；tab 需 fold+axis 两个，
        所以最少新增概念是 fold, flap, mouth 三个，而不是 tab 路线四个。
        """
        result = plan(req(payload))
        assert result.status == "path"
        assert result.concept_sequence == ["fold", "flap", "mouth"]
        assert result.optimality.new_concept_count == 3

    def test_mastered_prerequisite_changes_best_route(self, payload):
        """已掌握 fold/axis 后，连接片路线只需 tab、mouth（2 个新概念）。

        为避免弹片路线同样只有 2 个新概念（flap 只需 fold），
        本用例让 flap 必须先学 valley（未掌握），于是 tab 路线唯一最优。
        """
        payload["mastery"] = {"fold": "mastered", "axis": "mastered"}
        for c in payload["concepts"]:
            if c["id"] == "flap":
                c["prerequisite_schemes"] = [["valley"]]
        result = plan(req(payload))
        assert result.status == "path"
        assert result.concept_sequence == ["tab", "mouth"]
        assert result.optimality.new_concept_count == 2
        # tab 一步的依据全部标 mastered
        tab_step = result.steps[0]
        assert tab_step.concept_id == "tab"
        assert tab_step.used_scheme_index == 0
        assert {b.prerequisite_id: b.satisfied_by for b in tab_step.satisfaction_basis} == {
            "fold": "mastered",
            "axis": "mastered",
        }

    def test_uncertain_counts_as_not_mastered(self, payload):
        """“不确定”视为未掌握。"""
        payload["mastery"] = {"fold": "uncertain", "flap": "uncertain"}
        result = plan(req(payload))
        assert result.concept_sequence == ["fold", "flap", "mouth"]

    def test_step_carries_basis_segment_and_unblocks(self, payload):
        result = plan(req(payload))
        fold_step, flap_step, mouth_step = result.steps
        # M5 同时讲 flap 与 fold，fold 步读 M5 可让全程只用到 2 个不同段落
        assert fold_step.read_segment_id == "M5"
        assert flap_step.read_segment_id == "M5"
        assert fold_step.read_segment_title
        assert fold_step.used_scheme_index is None
        assert {u.concept_id for u in fold_step.unblocks} == {"flap"}
        # flap 通过方案 0（fold），解除 mouth 的阻塞
        assert flap_step.used_scheme_index == 0
        assert [(b.prerequisite_id, b.satisfied_by, b.satisfied_at_step)
                for b in flap_step.satisfaction_basis] == [("fold", "learned", 1)]
        assert {u.concept_id for u in flap_step.unblocks} == {"mouth"}
        assert mouth_step.is_goal is True
        assert mouth_step.read_segment_id == "M6"
        assert mouth_step.unblocks == []

    def test_goal_already_mastered_returns_empty_path(self, payload):
        payload["mastery"] = {"mouth": "mastered"}
        result = plan(req(payload))
        assert result.status == "path"
        assert result.goal_already_mastered is True
        assert result.steps == []
        assert result.optimality.new_concept_count == 0


# ---------------------------------------------------------------------------
# 并列最优：段落数与字典序
# ---------------------------------------------------------------------------

class TestTieBreaking:
    def test_fewer_distinct_segments_wins_over_lexicographic(self, materials):
        """两条 2 概念路径；一条能用同一段落讲两个概念，应胜出。"""
        concepts = [
            {"id": "goal", "name": "G", "prerequisite_schemes": [["x"], ["y"]]},
            {"id": "x", "name": "X", "prerequisite_schemes": []},
            {"id": "y", "name": "Y", "prerequisite_schemes": []},
        ]
        materials.extend([
            {"id": "M10", "title": "一段同时讲 goal 和 x", "teaches": ["goal", "x"]},
            {"id": "M11", "title": "只讲 y", "teaches": ["y"]},
        ])
        # 原始 M1..M7 不含 goal，去掉无关基础数据
        payload = {
            "concepts": concepts,
            "materials": materials[-2:] + [
                {"id": "M12", "title": "只讲 goal", "teaches": ["goal"]}
            ],
            "goal": "goal",
            "mastery": {},
        }
        result = plan(req(payload))
        assert result.concept_sequence == ["x", "goal"]
        assert result.segment_sequence == ["M10", "M10"]
        assert result.optimality.distinct_segment_count == 1

    def test_same_segment_count_concept_sequence_lexicographic(self):
        concepts = [
            {"id": "goal", "name": "G", "prerequisite_schemes": [["x"], ["y"]]},
            {"id": "x", "name": "X", "prerequisite_schemes": []},
            {"id": "y", "name": "Y", "prerequisite_schemes": []},
        ]
        materials = [
            {"id": "S1", "title": "goal", "teaches": ["goal"]},
            {"id": "S2", "title": "x", "teaches": ["x"]},
            {"id": "S3", "title": "y", "teaches": ["y"]},
        ]
        result = plan(req({"concepts": concepts, "materials": materials,
                           "goal": "goal", "mastery": {}}))
        # x 与 y 同为 2 概念、2 段落；概念序列字典序 x,goal 更小
        assert result.concept_sequence == ["x", "goal"]

    def test_segment_sequence_lexicographic_final_tiebreak(self):
        concepts = [
            {"id": "goal", "name": "G", "prerequisite_schemes": [["x"]]},
            {"id": "x", "name": "X", "prerequisite_schemes": []},
        ]
        materials = [
            {"id": "SA", "title": "goal A", "teaches": ["goal"]},
            {"id": "SB", "title": "goal B", "teaches": ["goal"]},
            {"id": "SC", "title": "x", "teaches": ["x"]},
        ]
        result = plan(req({"concepts": concepts, "materials": materials,
                           "goal": "goal", "mastery": {}}))
        assert result.concept_sequence == ["x", "goal"]
        # 概念序列相同、不同段落数相同（2）：goal 取字典序最小段落 SA
        assert result.segment_sequence == ["SC", "SA"]


# ---------------------------------------------------------------------------
# 缺页与无外部入口循环
# ---------------------------------------------------------------------------

class TestRemediation:
    def test_missing_page_returns_minimal_external_set(self, payload):
        """删掉讲授连接片与弹片的页：mouth 无路；补 flap 或 tab 二选一。"""
        payload["materials"] = [m for m in payload["materials"]
                                if m["id"] not in ("M4", "M5")]
        # fold 仍有 M1；但 mouth 的两个先修 tab/flap 都缺页
        result = plan(req(payload))
        assert result.status == "remediation"
        assert result.unreachable_reason == "missing_page"
        ids = [r.concept_id for r in result.remediation]
        # 基数最小为 1；flap < tab 字典序
        assert ids == ["flap"]
        assert result.steps == []

    def test_missing_intermediate_page_seed_covers_its_own_prereqs(self):
        """缺页的概念自身有先修：直接补教该概念即可，不连带先修。"""
        concepts = [
            {"id": "goal", "name": "G", "prerequisite_schemes": [["tab"]]},
            {"id": "tab", "name": "T", "prerequisite_schemes": [["fold"]]},
            {"id": "fold", "name": "F", "prerequisite_schemes": []},
        ]
        materials = [
            {"id": "S1", "title": "goal", "teaches": ["goal"]},
            {"id": "S2", "title": "fold", "teaches": ["fold"]},
        ]
        result = plan(req({"concepts": concepts, "materials": materials,
                           "goal": "goal", "mastery": {}}))
        assert result.status == "remediation"
        assert [r.concept_id for r in result.remediation] == ["tab"]
        assert result.remediation[0].reason == "missing_page"

    def test_cycle_without_external_entry(self, payload):
        """tab <-> flap 互相依赖：补其一即可从外部打破循环。"""
        payload["concepts"] = [
            c for c in payload["concepts"] if c["id"] not in ("tab", "flap")
        ] + [
            {"id": "tab", "name": "连接片", "prerequisite_schemes": [["flap"]]},
            {"id": "flap", "name": "V 形弹片", "prerequisite_schemes": [["tab"]]},
        ]
        result = plan(req(payload))
        assert result.status == "remediation"
        assert result.unreachable_reason == "cycle"
        assert len(result.remediation) == 1
        item = result.remediation[0]
        assert item.concept_id == "flap"  # flap < tab
        assert item.reason == "cycle"

    def test_self_dependency_is_cycle(self):
        """a 把自己当先修：无外部入口的循环，补教 a 即可。"""
        concepts = [
            {"id": "goal", "name": "G", "prerequisite_schemes": [["a"]]},
            {"id": "a", "name": "A", "prerequisite_schemes": [["a"]]},
        ]
        materials = [
            {"id": "S1", "title": "goal", "teaches": ["goal"]},
            {"id": "S2", "title": "a", "teaches": ["a"]},
        ]
        result = plan(req({"concepts": concepts, "materials": materials,
                           "goal": "goal", "mastery": {}}))
        assert result.status == "remediation"
        assert result.unreachable_reason == "cycle"
        assert [(r.concept_id, r.reason) for r in result.remediation] == [("a", "cycle")]

    def test_missing_page_and_cycle_reason_together(self):
        """补教集合同时含循环概念与缺页概念时，总原因为 both。

        goal 同时需要 a 和 b：a 与 c 互为先修（都有页，循环）；
        b 没有任何材料页（缺页）。单独补 a 或 c 解不开缺页，
        单独补 b 解不开循环，最小集合必须是 {a, b}。
        """
        concepts = [
            {"id": "goal", "name": "G", "prerequisite_schemes": [["a", "b"]]},
            {"id": "a", "name": "A", "prerequisite_schemes": [["c"]]},
            {"id": "c", "name": "C", "prerequisite_schemes": [["a"]]},
            {"id": "b", "name": "B", "prerequisite_schemes": []},
        ]
        materials = [
            {"id": "S1", "title": "goal", "teaches": ["goal"]},
            {"id": "S2", "title": "a", "teaches": ["a"]},
            {"id": "S3", "title": "c", "teaches": ["c"]},
        ]
        result = plan(req({"concepts": concepts, "materials": materials,
                           "goal": "goal", "mastery": {}}))
        assert result.status == "remediation"
        assert result.unreachable_reason == "both"
        assert {r.concept_id: r.reason for r in result.remediation} == {
            "a": "cycle", "b": "missing_page"
        }

    def test_remediation_excludes_goal_and_mastered(self):
        """补教集合不含目标本身；已掌握概念无需补教。"""
        concepts = [
            {"id": "goal", "name": "G", "prerequisite_schemes": [["a"]]},
            {"id": "a", "name": "A", "prerequisite_schemes": [["b"]]},
            {"id": "b", "name": "B", "prerequisite_schemes": []},
        ]
        materials = [{"id": "S1", "title": "goal", "teaches": ["goal"]}]
        result = plan(req({"concepts": concepts, "materials": materials,
                           "goal": "goal", "mastery": {"b": "mastered"}}))
        assert result.status == "remediation"
        ids = [r.concept_id for r in result.remediation]
        assert "goal" not in ids and "b" not in ids
        assert ids == ["a"]

    def test_lexicographic_remediation_among_equal_cardinality(self):
        concepts = [
            {"id": "goal", "name": "G", "prerequisite_schemes": [["a", "b"]]},
            {"id": "a", "name": "A", "prerequisite_schemes": [["c"]]},
            {"id": "b", "name": "B", "prerequisite_schemes": [["d"]]},
            {"id": "c", "name": "C", "prerequisite_schemes": []},
            {"id": "d", "name": "D", "prerequisite_schemes": []},
        ]
        materials = [
            {"id": "S1", "title": "goal", "teaches": ["goal"]},
        ]
        result = plan(req({"concepts": concepts, "materials": materials,
                           "goal": "goal", "mastery": {}}))
        # 补 c,d 两个后 a,b 仍缺页；直接补 a,b 基数为 2，补 c,d 也要再补 a,b。
        # 最小是 {a,b}
        assert [r.concept_id for r in result.remediation] == ["a", "b"]


# ---------------------------------------------------------------------------
# 整单拒绝：悬空引用 / 重复标识 / 非法先修结构
# ---------------------------------------------------------------------------

class TestRejection:
    def _codes(self, payload: dict) -> list[str]:
        with pytest.raises(ValidationBundle) as exc:
            Planner(req(payload))
        return sorted(e.code for e in exc.value.errors)

    def test_dangling_prerequisite_reference_rejected(self, payload):
        payload["concepts"][0]["prerequisite_schemes"] = [["ghost"]]
        with pytest.raises(ValidationBundle) as exc:
            Planner(req(payload))
        err = next(e for e in exc.value.errors if e.code == "DANGLING_REFERENCE")
        assert "ghost" in err.message
        assert err.loc[0] == "concepts"
        assert err.loc[-2:] == [0, 0]

    def test_dangling_material_reference_rejected(self, payload):
        payload["materials"][0]["teaches"] = ["ghost"]
        codes = self._codes(payload)
        assert "DANGLING_REFERENCE" in codes

    def test_dangling_mastery_reference_rejected(self, payload):
        payload["mastery"] = {"ghost": "mastered"}
        codes = self._codes(payload)
        assert "DANGLING_REFERENCE" in codes

    def test_duplicate_concept_id_rejected(self, payload):
        payload["concepts"].append(dict(payload["concepts"][0]))
        assert "DUPLICATE_ID" in self._codes(payload)

    def test_duplicate_segment_id_rejected(self, payload):
        payload["materials"].append(dict(payload["materials"][0]))
        assert "DUPLICATE_ID" in self._codes(payload)

    def test_empty_scheme_mixed_with_others_rejected(self, payload):
        payload["concepts"][0]["prerequisite_schemes"] = [[], ["axis"]]
        assert "EMPTY_SCHEME_MIXED" in self._codes(payload)

    def test_duplicate_within_scheme_rejected(self, payload):
        payload["concepts"][0]["prerequisite_schemes"] = [["fold", "fold"]]
        assert "DUPLICATE_IN_SCHEME" in self._codes(payload)

    def test_missing_goal_rejected(self, payload):
        payload["goal"] = "ghost"
        assert "MISSING_GOAL" in self._codes(payload)

    def test_goal_without_coverage_rejected(self, payload):
        """目标本身无材料页且未掌握：不返回路径也不返回补教，直接中文报错。"""
        from app.errors import DomainError
        payload["materials"] = [m for m in payload["materials"] if m["id"] != "M6"]
        with pytest.raises(DomainError) as exc:
            plan(req(payload))
        assert exc.value.code == "GOAL_NOT_COVERED"
        assert exc.value.loc == ["goal"]

    def test_multiple_errors_returned_together(self, payload):
        """整单拒绝时一次给出全部可定位错误。"""
        payload["concepts"][0]["prerequisite_schemes"] = [["ghost1", "ghost1"]]
        payload["materials"][0]["teaches"] = ["ghost2"]
        payload["goal"] = "ghost3"
        with pytest.raises(ValidationBundle) as exc:
            Planner(req(payload))
        codes = {e.code for e in exc.value.errors}
        assert {"DANGLING_REFERENCE", "DUPLICATE_IN_SCHEME", "MISSING_GOAL"} <= codes
        assert all(e.loc for e in exc.value.errors)
        assert all(e.hint for e in exc.value.errors)
