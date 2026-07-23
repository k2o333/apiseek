# 分组倍率规范化（getmulti）

> 状态：**implemented**（2026-07-23；drafts 保留需求原文；权威入口见 `docs/03 designs/sub2api-monitor.md` / `newapi-monitor.md` §倍率）  
> 日期：2026-07-23  
> 挂靠：`docs/drafts/get-models/`（与 models 并列的分组侧增强，不依赖 models bootstrap）

## 目标

在现有 **约每 5 分钟** 的分组采集轮次中，对每个分组的 **计费倍率** 做站级可配置换算，并与分组元数据 **一一对应落盘**。

| 维度 | 现状 | 本方案 |
|------|------|--------|
| 远端字段 | Sub2API / New-API 已回 `rate_multiplier` | **保留原始值**，不改写远端语义 |
| 业务倍率 | 直接展示原始数（pinaic `16` 易误解为 16×） | 按站配置 `÷ divisor`（pinaic/hubway 默认 **10** → `1.6`） |
| 配置 | 无 | 每站 `sites/<id>.env` 可配；缺省 `1`（不换算） |
| 落盘 | `groups_latest` 内嵌在 group 对象 | 同对象增加有效倍率字段；事件可感知倍率变化 |
| 调度 | 已有 groups timer | **不新增 timer**；在成功 poll 路径内联处理 |

## 动机（事实）

- 部分站（已确认：**pinaic、hubway**）后台展示/计费习惯为「原始倍率 ÷ 10」：
  - 例：pinaic 分组 `CCMAX` 远端 `rate_multiplier=16` → 业务倍率 **1.6**
- 其余站多数已是「显示即计费」（如 botcf `0.12`、aiapibank `0.15`），**不能**全局 ÷10。
- 运维与下游报表需要：既能对照远端，又能直接读业务倍率，且 **group_id ↔ rate** 同快照绑定。

## 文档索引

| 文档 | 内容 |
|------|------|
| [design.md](./design.md) | 主方案：数据流、配置、hash/event、边界 |
| [data-model.md](./data-model.md) | 落盘字段与兼容 |
| [implementation-checklist.md](./implementation-checklist.md) | 分阶段实现与验收 |

## 范围

| 纳入 | 不纳入（首版） |
|------|----------------|
| Sub2API 全活跃站（含 pinaic / hubway） | 模型列表 / API Key（见 get-models 主方案） |
| New-API（botcf / torchai）同一套 env 开关 | 按模型再拆倍率、peak/image/video 独立业务换算 |
| 站级统一 divisor | 按 group 名的特例表（可用后续扩展点） |
| 快照 + 事件中的 raw / effective | 独立 Prometheus 指标、SQLite |

## 一句话结论

> **每轮成功 groups poll 后，按站配置的 `MONITOR_RATE_DIVISOR` 计算 `rate_multiplier_effective = raw / divisor`，与分组同对象写入 `groups_latest`；raw 永不丢弃；默认 divisor=1；pinaic/hubway 配 10。**
