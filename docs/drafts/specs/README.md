# Zhongzhuan 契约冻结决策

> **正式替代入口：** [docs/02 specs](../../02%20specs/README.md) 与 [契约治理规则](../../01%20governance/contract-governance.md)。本目录保留 2026-07-22 的评审、inventory 和实施门禁，不再作为契约主入口。
> 状态：draft brief，尚未冻结。  
> 评审日期：2026-07-22。  
> 冻结目标路径：`docs/02 specs/contracts/`。  
> 本目录只承载历史草案、现状盘点和实施门禁；逐项正式状态以 `docs/02 specs/contracts/manifest.yaml` 为准，冻结后以 machine contract 为权威。

## 1. 范围

本方案冻结仓库中已有真实兼容成本的六类边界：

| 优先级 | 契约 | 冻结对象 |
|---|---|---|
| P0 | Storage | latest/events schema、canonical hash、失败保旧、兼容读取 |
| P0 | CLI | mode、退出码、锁、远端/本地副作用 |
| P0 | Remote mutation | managed identity、fail closed、unknown outcome |
| P0 | Provider profile | 已验证 HTTP 形状、失败分类、脱敏 fixtures |
| P1 | Config | env 名称、类型、默认值、远端写开关 |
| P1 | Deployment/security | timer、opt-in、timeout、hardening、secret 边界 |

当前冻结对象严格限定为：

- `sub2api-v1`：仅列入 profile 的已验证部署和探活日期；
- `newapi-legacy-groups-v1`：BotCF/TorchAI 已探活的 session groups 协议；
- `torchai-rc21-models-v1`：models 写路径必须先完成 provider 探针门禁。

当前不宣称支持 BotCF models、任意 New-API 部署或未来上游版本。

## 2. 三条硬规则

1. **Schema 是权威。** contract -> vectors/tests -> implementation；禁止从当前 Python 输出反向生成权威 schema。
2. **远端写必须 fail closed。** 分页、hydration、provider 语义或写结果不确定时禁止继续 create/update。
3. **不兼容变化必须升版本。** 字段/null/hash/identity/退出码/默认写权限变化必须有 changelog 和迁移策略。

规范用词：

- **必须 / 禁止**：MUST / MUST NOT；
- **应当**：SHOULD，偏离时必须记录理由；
- **可以**：MAY。

## 3. 已采纳的默认决策

| ID | 决策 |
|---|---|
| D1 | Sub2API groups 保持 legacy 兼容；没有跨 backend 强需求时不主动迁 v2 |
| D2 | models 当前目标 v1 统一 `backend`、四字段 full result、相同规范化 |
| D3 | 旧 Sub2API models 缺 backend 时仅在严格 legacy 条件下兼容；显式迁移或下次成功 checkpoint 输出当前格式，禁止无条件写时回写 |
| D4 | 任一 model row 的 id 非字符串、缺失或 trim 后为空：该 group contract fail，保留最后成功快照 |
| D5 | models 统一使用 trim、去重、排序后的列表落盘和 hash |
| D6 | groups events 使用 tail dedup；models v1 明确保持 at-least-once |
| D7 | credential-bearing systemd unit 以 `newapi-monitor-once@` hardening 为目标；legacy simple 标记 deprecated |
| D8 | 冻结路径使用现有文档树 `docs/02 specs/contracts/`；governance 使用 `docs/01 governance/` |
| D9 | 测试基线、现网文件和 unit gap 只放 inventory，不进入 frozen contract |

完整采纳说明见 [review-adoption.md](./review-adoption.md)。

## 4. 边界地图

```text
sites/<id>.env -> CLI -> provider HTTP
                     -> data/<id>/{auth,groups,models,lock}
systemd timer ------> CLI exit code / journal

per-site contract reader -> groups_all / future models_all
                         -> docs/websites/table/*
```

聚合产物不是采集器的 contract 输入。聚合程序必须经 per-site contract reader 读取，不得从 id 类型、文件名或目录名猜 backend/schema。

## 5. 草案结构

| 文件 | 职责 |
|---|---|
| [freeze-playbook.md](./freeze-playbook.md) | **怎么冻结**：现状清单、门禁、分阶段步骤、产物路径、PR 模板（2026-07-23） |
| [freeze-execution-plan.md](./freeze-execution-plan.md) | **建议实施方案**：治理修正、可寻址 artifacts、契约拆分、CI 与逐阶段出口条件（2026-07-23） |
| [goal-prompt.txt](./goal-prompt.txt) | **`/goal` 实现提示词**：默认交付 Phase 0 + Phase 1 + 试点证据（不偷跑 frozen） |
| [inventory-as-is.md](./inventory-as-is.md) | 会过期的实现、测试、现网数据和 unit gap |
| [contracts/storage.md](./contracts/storage.md) | groups/models/auth 持久化目标与 legacy 迁移 |
| [contracts/cli.md](./contracts/cli.md) | CLI mode、退出码、锁与副作用矩阵 |
| [contracts/remote-mutation.md](./contracts/remote-mutation.md) | 远端 create/update 安全不变量 |
| [contracts/provider-profiles.md](./contracts/provider-profiles.md) | provider 支持范围和 fixtures |
| [contracts/config.md](./contracts/config.md) | env schema 与远端写开关 |
| [contracts/deployment-security.md](./contracts/deployment-security.md) | systemd、安装和安全 |
| [contracts/governance.md](./contracts/governance.md) | 路径、版本、owner、CI 和证据优先级 |
| [contracts/manifest.yaml](./contracts/manifest.yaml) | machine contract 清单骨架 |
| [acceptance.md](./acceptance.md) | 草案期工程门禁；不进入 frozen bundle |
| [review.md](./review.md) | 原始评审意见 |

冻结后完整 JSON 示例移入 `vectors/`，字段约束移入 JSON Schema；人读文档只解释不变量和兼容策略，不复制第三份字段表。

## 6. 冻结门禁

| Contract ID | 当前状态 | 冻结前剩余条件 |
|---|---|---|
| storage/sub2api-groups-legacy | draft | canonical hash vectors、legacy reader tests |
| storage/newapi-groups-v1 | draft | JSON Schema、A->B->A/半行 vectors |
| storage/models-v1 | draft | legacy migration、统一 writer、strict model id |
| cli/sub2api-v1 | draft | 副作用矩阵断言、refresh bootstrap gate 决策落地 |
| cli/newapi-legacy-v1 | draft | 副作用矩阵/schema summary 断言 |
| safety/remote-mutation-v1 | draft | provider 探针闭合、unknown outcome vectors |
| config/* | planned | manifest 与所有 env example 对齐 |
| deployment/systemd-v1 | draft | 所有 credential-bearing unit hardening 对齐 |

当前实现测试基线及日期见 inventory；“测试全绿”是必要条件，不等于 contract 已冻结。

## 7. 不冻结

- 当前站点 group/model 数量和具体内容；
- 精确日志措辞与字段顺序；
- Python 模块、class、dataclass、helper 签名；
- 网站 UI/DOM selector；
- fake server 内部实现；
- 可重建 auth cache 的全部字段级公共 schema；
- 未探活的 provider 或“通用 New-API”承诺。

## 8. 证据优先级

```text
frozen machine contract
  > 对应版本 contract tests/vectors
  > 当前实现行为
  > 主 requirements/architecture
  > inventory、review、历史 skill reference
```

发现冲突时必须按此顺序处理；不得让过期 inventory 或 draft 覆盖 frozen contract。
