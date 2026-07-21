# Sub2API 分组模型采集方案（get-models）

## 目标

在现有 **Sub2API 分组监控** 之上，为每个站点、每个可用分组维护 **可用 API Key 覆盖**，并持久记录 **该分组模型列表**。

| 维度 | 现状 | 本方案 |
|------|------|--------|
| 分组列表 | 约每 5 分钟拉取落盘 | 周期保持；与 daily 抢锁时允许偶发跳过一轮 |
| API Key | 人工/一次性脚本 | 确定性名 + **可重入 reconcile**；只增不删；**默认不自动写** |
| 模型列表 | 无 | `models_latest` + events；**真新组一次增量** / **每日全量** / **显式 bootstrap** |
| 离线可读 | `groups_latest.json` | 另增 models 文件；join 查询 |

## 文档索引

| 文档 | 内容 |
|------|------|
| [design.md](./design.md) | 主方案（含 P0/P1 定稿） |
| [data-model.md](./data-model.md) | 最小落盘 schema |
| [timer-units.example.md](./timer-units.example.md) | systemd 与锁语义 |
| [implementation-checklist.md](./implementation-checklist.md) | 分阶段验收 |
| [review.md](./review.md) | 评审原文 + 采纳状态 |

## 适用范围

**Sub2API：** aiapibank, aresaicode, hubway, iaiguo, klinkw, littleapi, pinaic, yybb；aijws 须 preflight。  
**排除 New-API：** botcf, torchai。

**站点能力硬前提：** `GET /api/v1/keys` **可回读完整 secret**；分页可证明取全。不满足则 **不启用、不 create**。

## 一句话结论

> **分组仍高频只读监控；模型低频。自动写 Key 默关，须 preflight + 显式 bootstrap。Key reconcile 可重入；models 失败保旧、逐组 checkpoint；日更上海 00:00 抖动并有界等锁。**

## 评审采纳

评审（`review.md`，2026-07-21）判断：**总体架构正确，异常路径未闭合前不可直接编码上线。**

| 编号 | 结论 | 是否采纳 | 落入 |
|------|------|----------|------|
| P0-1 | create→bind 非事务，需确定性名 + reconcile | **是** | design §4.3 §5.3 |
| P0-2 | keys 必须分页取全，否则禁 create | **是** | design §5.2 |
| P0-3 | ensure 后必须用 keys_after | **是** | design §5.3–5.5 |
| P0-4 | secret 可回读为硬前提，不做假降级 | **是** | design §3 |
| P0-5 | 默认关；禁隐式冷启动；开关语义清晰 | **是** | design §4.5 §6.3 |
| P0-6 | JWT 与 API Key 分域；usable coverage | **是** | design §4.2 §5.7 |
| P1-1 | daily 有界等锁 + 有限重试；诚实 stale | **是** | design §7.4, timer |
| P1-2 | 成功/尝试正交；null vs [] | **是** | data-model §4 |
| P1-3 | full attempt/success/result；部分失败 exit≠0 | **是** | design §7.2, data-model |
| P1-4 | 新组有界重试，非每 5 分钟死循环 | **是** | design §4.5 |
| P1-5 | 逐组 checkpoint | **是** | design §5.6 |
| P1-6 | usable_keys 纯函数 | **是** | design §4.4 |
| 熵 | 删 keys_index；models 不复制 group 元数据 | **是** | data-model 全文 |
| 熵 | 模块 `sub2api_models.py` | **是** | design §5.1 |
| 调度 | daily 强制 re-GET；OnCalendar 上海时区 | **是** | design §9, timer |
| 调度 | 删 Python DAILY_TZ 双源 | **是** | design §6.3 |

**未采纳/弱化：**

- 同日 00:30 backup timer：评审作可选加固；定稿 **第一版不做**，优先锁等待+进程内重试，避免双跑复杂度。  
- 从首版就写 `keys_secrets.json`：评审作备选；定稿选 **「可回读 secret」硬前提**（评审推荐的低熵选项）。

探针参考：`scripts/probe_groups_models.py`（生产以 monitor CLI 为准）。

状态：**定稿草案（drafts）** — 可进入 Phase 1 编码，实现须跟 checklist，不得回退到「默认 ENABLE + 缺快照并入每轮」。
