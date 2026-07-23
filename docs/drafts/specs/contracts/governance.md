# Contract Governance

> 状态：draft  
> 冻结后目标位置：`docs/01 governance/contract-governance.md`

## 1. 路径决策

仓库已经存在分层文档树，因此选定：

```text
docs/drafts/specs/                 # 草案、评审、inventory、实施门禁
docs/01 governance/               # 冻结流程与跨 contract 版本政策
docs/02 specs/contracts/          # frozen machine contracts + 短人读不变量
docs/03 designs/                  # 实现设计，不具有 contract 优先级
```

不再创建根级 `spec/contracts/`。CI 必须正确引用带空格的固定路径，禁止再维护第二份镜像。

## 2. 生命周期

```text
planned -> draft -> frozen -> deprecated -> retired
```

- planned：只有范围/owner；
- draft：可以修改，不承诺兼容；
- frozen：机器 schema/vectors/tests 齐全，变更受版本政策约束；
- deprecated：仍兼容读取，不用于新 writer；
- retired：默认 reader 不再支持，必须有明确迁移窗口。

只有 owner 审核、冻结门禁全过并完成 changelog 后，才能从 drafts 移入 frozen root。

## 3. 每个 contract 的骨架

```yaml
id: storage/models-v1
status: frozen
owners: [repository-maintainers]
last_reviewed: "2026-07-22"
applies_to: [sub2api, newapi-legacy]
invariants: []
schema: storage/models-latest-v1.schema.json
vectors: []
non_goals: []
breaking_change_rules: []
```

owner、status、last_reviewed 禁止只存在于 prose。

## 4. 证据优先级

```text
frozen machine contract
  > 对应版本 contract tests/vectors
  > 当前实现
  > 主 requirements/architecture
  > inventory/review/历史 skill reference
```

Inventory 只回答“现在是什么”，contract 回答“允许是什么”。当前实现与 frozen contract 冲突时，先判定实现 regression 或显式 contract migration，禁止静默修改 schema 迎合实现。

## 5. 版本规则

以下必须升对应 contract version：

- 删除/重命名字段；
- 字段 type/null 语义变化；
- canonicalization/hash 变化；
- identity 规范化/managed name 变化；
- event diff/dedup 语义变化；
- CLI mode/退出码意义变化；
- 默认远端写权限扩大；
- provider 成功/失败判定变化；
- reader 从接受变拒绝某个已 frozen 输入。

以下通常可以同版本演进，但仍需 schema/changelog：

- 新增真正可选字段且旧 reader 已允许；
- 增加新 provider deployment profile；
- 增加不改变已有结果的 vectors/tests；
- 修正文档说明，不改变 machine rule。

日志措辞、内部函数和 dynamic inventory 不属于 contract。

## 6. Reader/writer 政策

```text
known version + expected site/backend -> read
explicit recognized legacy            -> compatibility adapter
unknown version                        -> reject and preserve
mismatched site/backend                -> reject; never diff/write
corrupt business latest                -> reject; never reset empty
corrupt disposable auth cache          -> discard and re-authenticate
```

- writer 永远输出当前完整 profile；
- read-only reader 禁止 write-on-read；
- migration 必须显式、幂等、可回滚；
- 表示迁移禁止伪造业务 change event；
- JSONL 不原地重写时必须有 multi-version reader 或分文件策略。

## 7. CI 门禁

冻结 contract 必须检查：

1. schema 可解析；
2. positive fixtures 全通过；negative fixtures 全拒绝；
3. golden hash 与所有 writer/reader 一致；
4. transition/state vectors 一致；
5. CLI mode 副作用矩阵；
6. remote mutation fail-closed；
7. secret scan；
8. systemd static invariants；
9. env example 与 config manifest；
10. unknown version/site/backend rejection；
11. legacy migration 幂等和可恢复；
12. contract diff 配套 changelog/version。

## 8. Changelog

`docs/02 specs/contracts/CHANGELOG.md` 必须记录：

- contract id/version；
- change type：compatible/breaking/migration/provider-refresh；
- effective date；
- affected reader/writer/deployment；
- migration/rollback；
- fixtures 和探活证据。

任何 golden vector 变化都必须解释是 bug fix、表示迁移还是 breaking change。

## 9. 文档收敛

冻结后：

- README、design、skills references 只链接 contract；
- 禁止复制完整字段表和算法；
- 旧 reference 标 deprecated 并指向 frozen path；
- inventory 可以更新/归档，不影响 contract；
- 实施 checklist 不移入 frozen bundle。

## 10. 聚合消费者

`groups_all`、未来 `models_all` 必须：

- 通过 contract reader；
- 使用显式 backend/schema；
- 对 unknown/mismatch fail closed；
- 不成为采集 writer 的输入；
- 聚合格式如需稳定，另建 consumer contract，不塞入 storage contract。
