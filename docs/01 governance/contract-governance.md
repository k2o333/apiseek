# 契约治理规则

> 状态：active
> 适用范围：`docs/02 specs/contracts/` 中的全部契约
> 最近复核：2026-07-23

## 1. 目标

本规则解决三类问题：

1. 稳定边界不能只存在于实现、README 或 draft；
2. “正式发布”和“已经冻结”必须分开，避免过早承诺兼容；
3. schema、hash、CLI、远端写和部署变化必须能被 review、测试、迁移和回滚约束。

规范词：**必须/禁止**等价于 MUST/MUST NOT；**应当**等价于 SHOULD，偏离时必须记录原因；**可以**等价于 MAY。

## 2. 文档放置

```text
docs/01 governance/               # 跨契约治理
docs/02 specs/contracts/          # 正式、受控的契约工作区
docs/03 designs/                  # 当前实现设计
docs/drafts/specs/                # 原始 brief/review/inventory/checklist
docs/drafts/<topic>/              # 历史需求、探针和替代方案
```

`docs/02 specs/contracts/` 不是 frozen-only 目录。`planned`、`draft`、`frozen` 可以共存，但每项状态必须由 `manifest.yaml` 明示。只有 `status: frozen` 的 machine contract 才产生稳定兼容承诺。

禁止维护第二份“同步镜像”。正式文件与历史 draft 内容冲突时，按证据优先级处理；历史文件不回写覆盖正式文件。

## 3. 生命周期

```text
planned -> draft -> frozen -> deprecated -> retired
```

| 状态 | 含义 | 允许的变化 |
|---|---|---|
| `planned` | 已确认范围，machine artifacts 尚不完整 | 可调整，不承诺兼容 |
| `draft` | 人读不变量已发布，正在补 schema/vectors/tests | 可调整；必须同步 manifest 和评审记录 |
| `frozen` | 门禁完成，已形成版本化兼容承诺 | breaking change 必须升版本 |
| `deprecated` | reader 仍兼容，writer 不再产生 | 只接受兼容修复和迁移工作 |
| `retired` | 默认 reader 不再支持 | 必须已过迁移窗口并写明恢复方式 |

状态变更必须同时修改 manifest、changelog 和对应 artifacts。禁止仅修改标题中的状态。

## 4. Contract 元数据

每项 contract 必须在 manifest 中声明：

```yaml
- id: storage/models-v1
  status: draft
  owners: [repository-maintainers]
  last_reviewed: "2026-07-23"
  applies_to: [sub2api, newapi-legacy]
  spec: storage.md
  required_artifacts: [schema, hash_vectors, migration_vectors]
```

`owners` 在冻结前必须替换成真实责任人或稳定团队名。`last_reviewed` 是语义复核日期，不是文件修改日期。

## 5. 冻结门禁

契约只有同时满足以下条件才可改为 `frozen`：

1. 人读不变量已定稿，范围与 non-goals 明确；
2. machine schema/config manifest 可解析；
3. positive/negative fixtures 通过 schema；
4. canonical hash 有跨 writer/reader 的 golden vectors；
5. 状态转换、崩溃窗口和 legacy migration 有 vectors；
6. CLI mode 的远端读取、mutation、本地写、锁和退出码有测试；
7. remote mutation 的 incomplete/unknown outcome 路径 fail closed；
8. provider profile 有脱敏 fixtures、最后探活日期和 capability 表；
9. systemd 与 installer 静态 contract tests 通过；
10. env examples 与 config manifest 一致；
11. secret scan 覆盖 stdout、stderr、日志、latest 和 events；
12. changelog、effective date、迁移和回滚已完成评审。

“现有单测全绿”只是必要证据，不能替代上述 artifacts。

## 6. 版本规则

以下变化必须升对应 contract version：

- 删除、重命名字段或改变字段类型/null 语义；
- 改变 canonicalization、hash、identity 或 managed name；
- 改变 event diff、顺序或 dedup 语义；
- 改变 CLI mode、副作用或退出码含义；
- 扩大默认远端写权限；
- 改变 provider 成功/失败判定；
- 从接受变为拒绝任一已 frozen 输入。

新增真正可选字段、增加 deployment profile 或补充不改变已有结果的 vectors，通常可以同版本演进，但仍必须更新 schema、测试和 changelog。

日志措辞、内部函数/class、动态站点数量和网站 UI 不属于 contract。

## 7. Reader、Writer 与迁移

```text
known version + expected site/backend -> read
explicit recognized legacy            -> compatibility adapter
unknown version                        -> reject and preserve
mismatched site/backend                -> reject; never diff/write
corrupt business latest                -> reject; preserve last recoverable state
corrupt disposable auth cache          -> discard and re-authenticate
```

- writer 必须输出当前完整 profile；
- read-only reader 禁止 write-on-read；
- migration 必须显式、幂等、可回滚；
- 表示迁移禁止伪造业务 change event；
- 混合版本 JSONL 必须逐行识别，或使用明确的分文件策略；
- 聚合消费者必须通过 contract reader，禁止从文件名或 id 类型猜 backend/schema。

## 8. 证据与冲突处理

证据优先级：

```text
frozen schema/manifest
  > matching contract tests/vectors
  > current implementation
  > docs/03 designs
  > primary draft requirements
  > inventory/review/historical notes
```

发现冲突时：

1. 先确认 contract 状态；
2. frozen contract 与实现不一致时，判定实现 regression 或提出版本化 migration；
3. draft contract 与实现不一致时，在 design 中列为 gap，不得伪称实现满足；
4. draft 之间冲突时，由正式 contract/design 记录明确决策；
5. 不通过修改 schema 来无说明地迎合偶然实现行为。

## 9. Changelog

`docs/02 specs/contracts/CHANGELOG.md` 在首次冻结前创建，至少记录：

- contract id/version；
- change type：compatible、breaking、migration、provider-refresh；
- effective date；
- 受影响 reader/writer/deployment；
- migration/rollback；
- fixtures、探针和测试证据。

任一 golden vector 变化必须说明它是 bug fix、表示迁移还是 breaking change。

## 10. Draft 的保留方式

评审、inventory、探针和 checklist 保留在 `docs/drafts/`，不删除历史证据。正式文档可以链接它们解释来源，但正式入口不得要求读者先从多个 draft 推导当前决策。

当 draft 内容被提升后：

- 正式文件重新表述稳定结论，不机械继承“未来时”或已过期实施状态；
- draft README 标记正式替代入口；
- 历史原文保持不变，除非需要加 superseded 提示；
- 后续语义修改只在正式文件和对应变更记录中进行。
