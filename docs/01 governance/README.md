# 文档治理

> 状态：active
> 最近复核：2026-07-23

本目录定义仓库正式文档的职责、生命周期和冲突处理。这里的“正式”表示文档进入受控目录、拥有明确状态和变更规则；**不表示所有契约都已经 frozen**。

## 目录职责

| 目录 | 职责 | 是否可作为正式入口 |
|---|---|---|
| `docs/01 governance/` | 生命周期、版本、证据优先级、冻结门禁 | 是 |
| `docs/02 specs/` | 受控契约及 machine contract 清单 | 是；以每项 `status` 为准 |
| `docs/03 designs/` | 当前实现结构、数据流和运维设计 | 是；不能覆盖 frozen contract |
| `docs/drafts/` | 评审原文、探针、清单、替代方案、历史推演 | 否；仅作证据或工作材料 |

## 权威顺序

```text
frozen machine contract
  > 对应版本 contract tests / vectors
  > 当前实现与测试
  > docs/03 designs 中的当前实现设计
  > docs/drafts 中的 requirements / review / inventory
```

当 `docs/02 specs` 中的条目仍为 `draft` 或 `planned` 时，它表达目标边界，不得反过来宣称当前实现已经满足。当前行为以代码、测试和 `docs/03 designs` 的差异说明为准。

## 入口

- [契约治理规则](./contract-governance.md)
- [正式契约索引](../02%20specs/README.md)
- [当前实现设计索引](../03%20designs/README.md)
- [草案归档](../drafts/)

## 导航规则

仓库根 `AGENTS.md` 和 `README.md` 必须优先链接正式目录。只有以下主题可以直接链接 `docs/drafts/`：

- 原始评审与采纳记录；
- 尚未完成的 provider 探针；
- 实施 checklist、inventory 和迁移演练记录；
- 被否决或历史替代方案。

若一份 draft 已成为日常开发、运维或兼容判断的必要入口，应先提炼到对应正式目录，再从正式文件反向链接其历史证据。
