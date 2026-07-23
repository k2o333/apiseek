# 契约冻结评审采纳记录

> 对应评审：[review.md](./review.md)  
> 日期：2026-07-22  
> 结论：采纳结构拆分和全部技术纠偏；D3 采用更保守的“显式迁移或下次成功写入”，不做无条件 write-on-read。

## 1. 总体建议

| 评审建议 | 结论 | 落点 |
|---|---|---|
| 单文件百科拆成 brief + inventory + contracts | 采纳 | README 收敛；新增 `inventory-as-is.md` 与 `contracts/` |
| as-is 不进入 frozen contract | 采纳 | 测试基线、生产快照、unit gap 全部移入 inventory |
| schema 为权威，不由实现生成 | 采纳 | README 硬规则；governance |
| 使用统一 MUST/SHOULD/MAY 用词 | 采纳 | README 定义；各 contract 使用“必须/禁止/应当/可以” |
| owner/status/last_reviewed 进入 manifest | 采纳 | `contracts/manifest.yaml` |
| JSON 示例移入 vectors | 采纳为目标 | 当前草案只保留算法伪代码；冻结时生成 vectors |

## 2. 事实纠偏

| ID | 评审意见 | 结论 | 修订 |
|---|---|---|---|
| P0-1 | New-API models CLI 已接入，旧 154/18 基线过期 | 采纳 | inventory 更新为 `172 passed, 7 subtests passed`；删除“待接线”任务 |
| P0-2 | Sub2API models parse/list/hash 描述不精确 | 采纳 | inventory 分开记录 parser、落盘和 hash 行为；target 独立写入 storage |
| P1-1 | systemd hardening 不是单一 unit 缺项 | 采纳 | inventory 使用 unit x hardening 矩阵；target 使用统一最小集合 |
| P1-2 | Sub2API models 已有生产快照 | 采纳 | models v1 增加 legacy 兼容、迁移和无伪 change event 规则 |
| P1-3 | contract 最终路径未决定 | 采纳 | 选定 `docs/02 specs/contracts/`，governance 记录 draft -> frozen 流程 |

## 3. 契约内容意见

| 评审意见 | 结论 | 修订 |
|---|---|---|
| 补齐 Sub2API legacy groups canonical hash | 采纳 | storage 写出完整 sort/key/JSON 参数和类型语义 |
| 默认不主动迁 groups v2 | 采纳 | D1；出现跨 backend 统一消费硬需求才开新 ADR |
| 非字符串 model id 必须拍板 | 采纳 | D4：整 group contract fail，失败保旧 |
| models 统一 New-API 规范化 | 采纳 | D5：trim/去重/排序同时用于落盘与 hash |
| 显式记录两类 event dedup | 采纳 | manifest `events_dedup`；storage 分别描述 |
| CLI 退出码需要细化 | 采纳 | cli 固定 preflight fail=1、配置/参数/锁=2、运行失败=1 |
| 配置缺远端写开关语义 | 采纳 | config 增加 CLI、incremental env、daily enable、bootstrap 四层门禁 |
| 聚合产物必须经 contract reader | 采纳 | README 边界地图与 governance |
| BotCF models 范围需前置 | 采纳 | README 范围和 provider profiles |
| Auth cache 公共契约应缩短 | 采纳 | storage 仅保留五类行为；字段级 schema 不进 public bundle |

## 4. 默认决策 D1-D9

| ID | 结论 | 说明 |
|---|---|---|
| D1 | 采纳 | legacy groups 保持可复现和兼容，不主动迁 v2 |
| D2 | 采纳 | models 统一当前 v1 目标，但承认已存在 legacy v1 |
| D3 | 折中采纳 | 缺 backend 仅在 expected backend=sub2api、site 匹配、shape 合法时兼容；不 write-on-read，显式 migration 或下次成功 checkpoint 回写 |
| D4 | 采纳 | malformed/non-string model id 导致 group contract fail |
| D5 | 采纳 | models 统一 trim/去重/排序 |
| D6 | 采纳 | groups tail dedup，models v1 at-least-once |
| D7 | 采纳 | 所有 credential-bearing unit 对齐 hardening；legacy simple deprecated |
| D8 | 采纳并选定 | 使用已有 `docs/02 specs/contracts/`，避免再造根级目录 |
| D9 | 采纳 | current-state inventory 永不进入 frozen bundle |

## 5. 未采纳项

没有直接拒绝的技术建议。仅对 D3 作安全性收紧：读取文件不应隐式产生磁盘写入，因为只读聚合、validate 和诊断工具不应修改生产状态。兼容 reader 在内存补齐 backend；显式 migration 或下一次本来就要发生的成功 checkpoint 才持久化当前格式。
