# 纸故事卡机关学习路径 API

为 **8–11 岁小朋友和家长**服务的纯后端 API：当你们要做一张
**会张嘴或会弹起的立体纸故事卡**时，它会帮孩子排出一条确实能学会的步骤链——
先分清「山折 / 谷折 / 对称轴 / 连接片」等概念，再一步步走到目标机关。

- Python 3.12 + FastAPI + Pydantic 契约
- SQLite 保存**不可变版本**：掌握状态或材料一变就出新版本，旧版本永远不漂移
- 只在材料覆盖内搜索**完整路径**；材料缺页或遇到“没有外部入口的循环”时，
  不返回半条路径，而是给出最小的**外部补教概念集合**
- 所有错误都是**可定位的适龄中文 JSON**（告诉家长在第几张卡/第几个字段改）

## 一、快速开始

```bash
# 常驻 API（宿主端口可用 API_PORT 覆盖，默认 8000）
docker compose up -d --build
API_PORT=9000 docker compose up -d        # 换宿主端口示例

# 验证服务：跑全部测试 + 对生产接口冒烟，完成后退出
docker compose --profile verify up --build verify
```

不用 Docker 时（需要 Python 3.12；3.11 也可运行测试）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload          # 起服务
.venv/bin/python -m pytest tests -q              # 跑测试

# 本地冒烟（先在本机起好 API，再让脚本指向本地地址）
API_URL=http://127.0.0.1:8000 .venv/bin/python scripts/verify.py
```

## 二、请求契约

`POST /api/versions`

```json
{
  "concepts": [
    {"id": "fold",  "name": "山折",   "prerequisite_schemes": []},
    {"id": "axis",  "name": "对称轴", "prerequisite_schemes": []},
    {"id": "valley","name": "谷折",   "prerequisite_schemes": []},
    {"id": "tab",   "name": "连接片",
      "prerequisite_schemes": [["fold", "axis"], ["valley", "axis"]]},
    {"id": "mouth", "name": "会张嘴的大嘴",
      "prerequisite_schemes": [["tab"]]}
  ],
  "materials": [
    {"id": "M1", "title": "第1页 认识山折", "teaches": ["fold"]},
    {"id": "M2", "title": "第2页 找对称轴", "teaches": ["axis"]},
    {"id": "M3", "title": "第3页 剪连接片", "teaches": ["tab"]},
    {"id": "M4", "title": "第4页 贴出大嘴", "teaches": ["mouth"]}
  ],
  "goal": "mouth",
  "mastery": {"fold": "uncertain"}
}
```

| 字段 | 说明 |
| --- | --- |
| `concepts[].id` / `materials[].id` | 唯一标识，1–64 个中英文/数字/下划线/连字符 |
| `prerequisite_schemes` | **可替代先修方案**：列表的每个元素是一个方案；任一方案里的概念全部满足就能学。`[]` 表示无需先修；空方案与其它方案并列会被整单拒绝 |
| `materials[].teaches` | 这一段材料讲到的概念；一段可以讲多个概念（读一段就能解锁多个） |
| `mastery` | `mastered`（会了）/ `not_mastered`（还不会）/ `uncertain`（不确定）。**不确定按未掌握处理**；缺省也按未掌握 |

## 三、搜索与裁决规则

只在**材料覆盖**内找从已掌握集合出发、最终学会目标的完整路径。找到完整路径时，按下面顺序选最优：

1. **新增概念数最少**；
2. 使用的**不同材料段落数最少**；
3. 学习**概念标识序列字典序**最小；
4. 对应**段落标识序列字典序**最小。

每一步都会返回：

- `satisfaction_basis`：满足依据——命中了第几个替代方案、每个先修是“已经会了”还是“在第几步刚学会”；
- `read_segment_id` / `read_segment_title`：这一步应读哪一段；
- `unblocks`：学会这一步后，路径上哪些后续概念解除阻塞（含是否为目标）。

### 缺页 / 无外部入口的循环

当材料覆盖内走不通时，**不返回半条路径**，而是计算“**视为在材料外部补教后，可使目标可达、且不含目标本身**”的概念集合，
先取**基数最小**，基数相同再取**升序标识序列字典序最小**。响应 `status` 为 `remediation`：

- `reason = missing_page`：该概念没有任何材料页讲到；
- `reason = cycle`：它和别的概念互相依赖、没有外部入口（例如 A 要 B、B 要 A），需要家长/老师先从外部带进门。

> 特例：如果**目标机关本身**没有任何材料页，外部补教不能代替目标本身，
> 接口返回 422 `GOAL_NOT_COVERED`，请补一段讲目标的材料。

## 四、版本与不漂移

- 版本按**规范化输入内容寻址**（SHA-256 前缀）：同一份内容重复提交返回同一 `version.id`；
  概念/材料书写顺序不影响版本，掌握状态缺省与显式 `not_mastered` 等价。
- 掌握状态或材料一改变，就会产生新版本并重新计算。
- `GET /api/versions/{id}` 永远返回该版本**当时的输入与结果**（直接读存档，不重算）；
  `GET /api/versions` 列出历史版本。
- SQLite 文件路径由环境变量 `PAPERCARDS_DB` 控制（默认 `data/papercards.db`）。

## 五、错误长什么样

悬空引用、重复标识、非法先修结构一律**整单拒绝**，一次给出全部问题：

```json
{
  "error": {
    "code": "DANGLING_REFERENCE",
    "message": "概念「tab」的第 1 个先修方案里，提到了概念卡里没有的概念「fold」。",
    "loc": ["concepts", 2, "prerequisite_schemes", 0, 0],
    "hint": "先补上这张概念卡，或把引用改成已经存在的概念。"
  },
  "errors": [ /* 本单的全部可定位错误 */ ]
}
```

常见错误码：`DUPLICATE_ID`、`DANGLING_REFERENCE`、`ILLEGAL_PREREQUISITE`、
`EMPTY_SCHEME_MIXED`、`DUPLICATE_IN_SCHEME`、`UNKNOWN_MASTERY_STATUS`、
`MISSING_GOAL`、`GOAL_NOT_COVERED`、`VERSION_NOT_FOUND`。

## 六、接口一览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/api/versions` | 提交一单规划，创建/复用不可变版本 |
| GET | `/api/versions` | 版本列表 |
| GET | `/api/versions/{id}` | 取某版本的原始输入与结果（不重算） |

## 七、目录结构与测试

```
app/
  models.py    Pydantic 契约
  errors.py    可定位适龄中文错误
  planner.py   校验 + 路径搜索（BFS）+ 最小补教集合 + 段落裁决
  storage.py   SQLite 不可变版本（内容寻址）
  main.py      FastAPI 路由与错误渲染
tests/         领域测试 + API 测试
scripts/verify.py   verify 服务：pytest + 生产接口冒烟
```

测试覆盖：替代方案、并列最优（段落数与两层字典序）、版本隔离、缺页、
无外部入口循环、非法/悬空/重复引用整单拒绝、不确定按未掌握、目标缺页等。
