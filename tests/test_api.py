# -*- coding: utf-8 -*-
"""API 测试：版本创建、版本隔离、错误 JSON 契约。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "test.db"
    app = create_app(db_path=str(db))
    with TestClient(app) as c:
        yield c


def _base_payload() -> dict:
    return {
        "concepts": [
            {"id": "fold", "name": "山折", "prerequisite_schemes": []},
            {"id": "flap", "name": "V 形弹片",
             "prerequisite_schemes": [["fold"]]},
            {"id": "mouth", "name": "会张嘴的大嘴",
             "prerequisite_schemes": [["flap"]]},
        ],
        "materials": [
            {"id": "M1", "title": "认识山折", "teaches": ["fold"]},
            {"id": "M2", "title": "折 V 形弹片", "teaches": ["flap"]},
            {"id": "M3", "title": "贴出大嘴", "teaches": ["mouth"]},
        ],
        "goal": "mouth",
        "mastery": {},
    }


class TestPlanEndpoint:
    def test_health(self, client):
        assert client.get("/health").json() == {"status": "ok"}

    def test_create_version_returns_steps(self, client):
        r = client.post("/api/versions", json=_base_payload())
        assert r.status_code == 200
        body = r.json()
        assert body["version"]["newly_created"] is True
        result = body["result"]
        assert result["status"] == "path"
        assert result["concept_sequence"] == ["fold", "flap", "mouth"]
        assert [s["read_segment_id"] for s in result["steps"]] == ["M1", "M2", "M3"]
        assert body["version"]["id"]

    def test_same_input_reuses_version(self, client):
        r1 = client.post("/api/versions", json=_base_payload())
        r2 = client.post("/api/versions", json=_base_payload())
        assert r1.json()["version"]["id"] == r2.json()["version"]["id"]
        assert r2.json()["version"]["newly_created"] is False

    def test_field_order_does_not_change_version(self, client):
        p1 = _base_payload()
        p2 = _base_payload()
        # 概念/材料顺序倒换、显式写出全部 not_mastered 掌握状态，内容等价
        p2["concepts"] = list(reversed(p2["concepts"]))
        p2["materials"] = list(reversed(p2["materials"]))
        p2["mastery"] = {"flap": "not_mastered", "fold": "not_mastered",
                         "mouth": "not_mastered"}
        r1 = client.post("/api/versions", json=p1)
        r2 = client.post("/api/versions", json=p2)
        assert r1.json()["version"]["id"] == r2.json()["version"]["id"]

    def test_remediation_endpoint_shape(self, client):
        payload = _base_payload()
        payload["materials"] = [m for m in payload["materials"] if m["id"] != "M2"]
        r = client.post("/api/versions", json=payload)
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["status"] == "remediation"
        assert [x["concept_id"] for x in result["remediation"]] == ["flap"]
        assert result["unreachable_reason"] == "missing_page"


class TestVersionIsolation:
    def test_old_version_input_and_result_do_not_drift(self, client):
        payload = _base_payload()
        v1 = client.post("/api/versions", json=payload).json()
        v1_id = v1["version"]["id"]
        assert v1["result"]["concept_sequence"] == ["fold", "flap", "mouth"]

        # 掌握状态改变：重算出新版本
        changed = _base_payload()
        changed["mastery"] = {"fold": "mastered", "flap": "mastered"}
        v2 = client.post("/api/versions", json=changed).json()
        assert v2["version"]["id"] != v1_id
        assert v2["result"]["concept_sequence"] == ["mouth"]

        # 材料改变：再出一个新版本
        changed2 = _base_payload()
        changed2["materials"].append(
            {"id": "M9", "title": "一页全讲", "teaches": ["fold", "flap"]})
        v3 = client.post("/api/versions", json=changed2).json()
        assert v3["version"]["id"] not in (v1_id, v2["version"]["id"])

        # 旧版本输入与结果原样可取
        old = client.get(f"/api/versions/{v1_id}").json()
        assert old["result"]["concept_sequence"] == ["fold", "flap", "mouth"]
        assert old["request"]["mastery"] == {
            "flap": "not_mastered", "fold": "not_mastered", "mouth": "not_mastered"
        }

        listing = client.get("/api/versions").json()["versions"]
        assert {v["id"] for v in listing} == {
            v1_id, v2["version"]["id"], v3["version"]["id"]
        }

    def test_get_unknown_version_is_chinese_404(self, client):
        r = client.get("/api/versions/deadbeefdeadbeef")
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "VERSION_NOT_FOUND"
        assert body["error"]["loc"] == ["version_id"]
        assert "找不到版本" in body["error"]["message"]


class TestChineseErrors:
    def test_dangling_reference_localized(self, client):
        payload = _base_payload()
        payload["concepts"][1]["prerequisite_schemes"] = [["ghost"]]
        r = client.post("/api/versions", json=payload)
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "DANGLING_REFERENCE"
        loc = body["error"]["loc"]
        assert loc[:1] == ["concepts"]
        assert "ghost" in body["error"]["message"]
        assert body["error"]["hint"]

    def test_error_loc_points_to_submitted_order_not_sorted(self, client):
        """错误定位用用户提交顺序：把出错卡放在最后一张，loc 索引应仍是最后。"""
        payload = _base_payload()
        bad = payload["concepts"].pop(1)  # flap
        bad["prerequisite_schemes"] = [["ghost"]]
        payload["concepts"].append(bad)  # 放到最后（规范化排序也会改变位置）
        r = client.post("/api/versions", json=payload)
        assert r.status_code == 422
        loc = r.json()["error"]["loc"]
        assert loc[0] == "concepts"
        assert loc[1] == len(payload["concepts"]) - 1

    def test_duplicate_id_localized(self, client):
        payload = _base_payload()
        payload["concepts"].append(payload["concepts"][0])
        r = client.post("/api/versions", json=payload)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "DUPLICATE_ID"

    def test_unknown_mastery_status_localized(self, client):
        payload = _base_payload()
        payload["mastery"] = {"fold": "maybe"}
        r = client.post("/api/versions", json=payload)
        assert r.status_code == 422
        codes = {e["code"] for e in r.json()["errors"]}
        assert "UNKNOWN_MASTERY_STATUS" in codes

    def test_illegal_structure_and_extra_field(self, client):
        payload = _base_payload()
        payload["concepts"][0]["prerequisite_schemes"] = "fold"
        r = client.post("/api/versions", json=payload)
        assert r.status_code == 422
        assert r.json()["errors"]

        payload = _base_payload()
        payload["nope"] = 1
        r = client.post("/api/versions", json=payload)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "UNKNOWN_FIELD"

    def test_goal_not_covered_localized(self, client):
        payload = _base_payload()
        payload["materials"] = [m for m in payload["materials"] if m["id"] != "M3"]
        r = client.post("/api/versions", json=payload)
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "GOAL_NOT_COVERED"
