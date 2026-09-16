#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify 服务入口：

1. 运行领域与 API 测试（pytest）；
2. 对常驻的“生产接口”（API_URL，默认 http://api:8000）做冒烟：
   - 健康检查；
   - 提交一单可学会目标机关的完整步骤链；
   - 提交一单缺页场景，确认返回外部补教集合；
   - 提交一单悬空引用，确认整单拒绝并返回可定位中文错误；
   - 版本隔离：改掌握状态后新版本出现、旧版本结果不漂移。

任何一步失败即以非零码退出。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
API_URL = os.environ.get("API_URL", "http://api:8000")
TIMEOUT_STEPS = 60


def post(path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API_URL + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def get(path: str) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(API_URL + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def wait_for_api() -> None:
    for _ in range(TIMEOUT_STEPS):
        try:
            with urllib.request.urlopen(API_URL + "/health", timeout=5) as resp:
                if resp.status == 200:
                    print("✓ 生产接口健康检查通过")
                    return
        except Exception:
            pass
        time.sleep(1)
    print("✗ 等待生产接口超时", file=sys.stderr)
    sys.exit(1)


def full_path_payload() -> dict[str, Any]:
    return {
        "concepts": [
            {"id": "fold", "name": "山折", "prerequisite_schemes": []},
            {"id": "axis", "name": "对称轴", "prerequisite_schemes": []},
            {"id": "tab", "name": "连接片",
             "prerequisite_schemes": [["fold", "axis"]]},
            {"id": "mouth", "name": "会张嘴的大嘴",
             "prerequisite_schemes": [["tab"]]},
        ],
        "materials": [
            {"id": "M1", "title": "认识山折", "teaches": ["fold"]},
            {"id": "M2", "title": "找对称轴", "teaches": ["axis"]},
            {"id": "M3", "title": "剪连接片", "teaches": ["tab"]},
            {"id": "M4", "title": "贴出大嘴", "teaches": ["mouth"]},
        ],
        "goal": "mouth",
        "mastery": {},
    }


def check(cond: bool, ok_msg: str, fail_msg: str) -> None:
    if not cond:
        print(f"✗ {fail_msg}", file=sys.stderr)
        sys.exit(1)
    print(f"✓ {ok_msg}")


def smoke() -> None:
    wait_for_api()

    # 1) 完整步骤链
    code, body = post("/api/versions", full_path_payload())
    check(code == 200, "完整路径请求返回 200", f"完整路径请求返回 {code}：{body}")
    result = body["result"]
    check(
        result["concept_sequence"] == ["axis", "fold", "tab", "mouth"],
        "步骤链为 axis → fold → tab → mouth，确实能完成目标机关",
        f"步骤链不正确：{result.get('concept_sequence')}",
    )
    check(
        all(s["read_segment_id"] for s in result["steps"])
        and all(s["satisfaction_basis"] is not None for s in result["steps"]),
        "每步都带应读段落与满足依据",
        "步骤缺少段落或依据",
    )
    v1 = body["version"]["id"]

    # 2) 缺页 → 补教集合
    missing = full_path_payload()
    missing["materials"] = [m for m in missing["materials"] if m["id"] != "M3"]
    code, body = post("/api/versions", missing)
    check(code == 200, "缺页请求返回 200", f"缺页请求返回 {code}：{body}")
    r2 = body["result"]
    check(
        r2["status"] == "remediation"
        and [x["concept_id"] for x in r2["remediation"]] == ["tab"],
        "缺页时不返回半条路径，而是给出最小补教集合 {tab}",
        f"缺页结果不正确：{r2}",
    )

    # 3) 悬空引用 → 整单拒绝、可定位中文错误
    bad = full_path_payload()
    bad["concepts"][0]["prerequisite_schemes"] = [["不存在的折法"]]
    code, body = post("/api/versions", bad)
    check(code == 422, "悬空引用返回 422", f"悬空引用返回 {code}")
    err = body["error"]
    check(
        err["code"] == "DANGLING_REFERENCE" and err["loc"] and err["message"],
        "错误可定位（code/loc/message 均为中文可读）",
        f"错误结构不正确：{err}",
    )

    # 4) 版本隔离：掌握状态改变产生新版本，旧版本不漂移
    changed = full_path_payload()
    changed["mastery"] = {"fold": "mastered", "axis": "mastered",
                          "tab": "mastered"}
    code, body = post("/api/versions", changed)
    check(code == 200, "掌握状态变更请求返回 200", f"返回 {code}：{body}")
    v2 = body["version"]["id"]
    check(v1 != v2, "掌握状态改变产生新版本", "版本未随掌握状态改变")
    check(
        body["result"]["concept_sequence"] == ["mouth"],
        "新版本只学目标一步",
        f"新版本结果不正确：{body['result'].get('concept_sequence')}",
    )
    code, old = get(f"/api/versions/{v1}")
    check(
        code == 200
        and old["result"]["concept_sequence"] == ["axis", "fold", "tab", "mouth"],
        "旧版本输入与结果未漂移",
        f"旧版本结果漂移：{old}",
    )


def main() -> None:
    print("== 1/2 运行测试套件 ==")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        print("✗ 测试套件失败", file=sys.stderr)
        sys.exit(proc.returncode)

    print("== 2/2 对生产接口冒烟 ==")
    smoke()
    print("\n全部验证通过：孩子可以拿到确实能学会并完成目标纸机关的步骤链。")


if __name__ == "__main__":
    main()
