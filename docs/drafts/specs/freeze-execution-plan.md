# 契约冻结实施方案（建议稿）

> 类型：工程实施方案，不进入 frozen bundle  
> 状态：proposal  
> 日期：2026-07-23  
> 状态权威：[`docs/02 specs/contracts/manifest.yaml`](../../02%20specs/contracts/manifest.yaml)  
> 治理权威：[`docs/01 governance/contract-governance.md`](../../01%20governance/contract-governance.md)  
> 正式契约入口：[`docs/02 specs/README.md`](../../02%20specs/README.md)

本方案定义如何把当前 formal-but-draft 契约逐项推进为可验证、可迁移、可回滚的 frozen contract。它不复制当前状态，也不替代 governance、manifest 或正式 spec；任何状态判断都必须直接读取 manifest。

本方案首先修复治理和证据模型，再冻结业务契约。**Phase 0 完成前，禁止把任何 contract 改为 `frozen`。**

---

## 1. 目标、原则与非目标

### 1.1 目标

冻结完成后，每个 contract 都必须满足：

1. 人读不变量、machine artifact 和实现行为一致；
2. 所有必需证据都有稳定路径，可由仓库内命令重放；
3. positive case 被接受，negative case 被明确拒绝；
4. writer、reader、迁移和失败路径由同一组 vectors 约束；
5. 远端 mutation 在分页、鉴权、hydration 或结果不确定时 fail closed；
6. secret 不进入普通输出、日志和业务快照；
7. breaking change 有新 contract version、迁移和回滚；
8. 单项冻结和 bundle 发布都有唯一、可审计的判定过程。

### 1.2 实施原则

- **manifest 单一状态源**：状态、owner、依赖和 artifact 入口只在正式 manifest 维护。
- **spec 先于实现收敛**：draft contract 可以根据评审修改；一旦 frozen，不得用偶然实现行为反向放宽 schema。
- **证据可寻址**：`required_artifacts` 不能只是标签，必须能解析到实际文件和测试。
- **门禁可判定**：每项 gate 必须是 `required` 或经治理允许的 `not_applicable`，禁止 PR 中临时口头跳过。
- **契约边界单一**：storage、CLI、provider、config、mutation、deployment 分别版本化，不互相夹带未归属语义。
- **离线 CI，在线探活留证**：CI 回放脱敏 fixtures，不在常规测试中依赖第三方网络；人工探活产物脱敏后进入 provider evidence。
- **失败保旧**：groups、models、invite 的 provider/auth/contract 失败不得覆盖最后成功业务快照。
- **先兼容读，再升级写**：涉及现网文件时，先发布并验证 compatibility reader，再改变 writer 或执行显式 migration。

### 1.3 非目标

- 不把 `docs/drafts/` 变成第二套正式契约；
- 不承诺任意 New-API 部署或未探活版本；
- 不启用 BotCF models mutation；
- 不改变 timer + oneshot 的生产运行模型；
- 不冻结具体站点的实时 group/model 内容；
- 不把在线 curl 成功本身当成可重复 contract evidence；
- 不在本方案中承诺工期；排期应由 owner 根据 probe 和 migration 风险单独制定。

---

## 2. 先修正治理模型

当前 governance 把 12 项门禁写成所有 contract 必须同时满足，但 storage contract 不应拥有 systemd 字段，deployment contract 也没有 canonical hash。为避免用草稿擅自引入豁免，Phase 0 必须先正式修订 governance。

### 2.1 单项冻结与 bundle 发布

采用以下语义：

- contract 可以独立从 `draft` 变为 `frozen`；
- frozen contract 的兼容承诺不因 bundle 仍为 draft 而失效；
- bundle 只有在其 `release_contracts` 全部 frozen、依赖闭合、发布检查通过后才可 frozen；
- planned contract 可以不进入某次 bundle release，但必须从 `release_contracts` 明确排除，不能靠读者猜测；
- bundle version 代表一组精确 contract id/version，不替代单项版本。

manifest 应增加类似字段：

```yaml
bundle:
  id: zhongzhuan-contracts
  version: 1
  status: draft
  release_contracts:
    - storage/sub2api-groups-legacy
    - storage/newapi-groups-v1
    - storage/models-v1
    # 其余进入本次 release 的精确 ID
```

bundle 进入 frozen 时必须记录 effective date、release commit、迁移入口和整体 rollback 说明。

### 2.2 Gate 适用性

governance 应把门禁定义为“所有 contract 都必须声明处理结果”，而不是“所有 contract 都必须产出同一种 artifact”。建议正式采用以下适用性：

| Gate | 内容 | 必须适用的 contract | 允许 N/A 的典型情况 |
|---|---|---|---|
| G1 | 人读不变量、scope、non-goals | 全部 | 不允许 |
| G2 | 可解析的 machine contract | 全部 | 不允许；auth 可用 behavior manifest，不要求公共字段 schema |
| G3 | positive/negative validation | 有输入 envelope、config、provider fixture 的 contract | 无结构化输入时 |
| G4 | canonical hash golden vectors | 定义 hash 的 storage contract | CLI、provider、deployment 等不定义 hash |
| G5 | transition/crash/migration vectors | 有持久状态或状态机的 contract | 纯静态 config schema |
| G6 | mode、副作用、锁、退出码 | CLI contract | 非 CLI contract |
| G7 | incomplete/unknown mutation fail closed | safety contract 和实际 mutation adapter | 不允许远端 mutation 的 contract |
| G8 | fixtures、探活日期、capability | provider contract | 非 provider contract；消费者改用 `depends_on` |
| G9 | systemd/installer 静态验证 | deployment contract | 非 deployment contract |
| G10 | env example 与 config manifest | config contract | 非 config contract |
| G11 | secret boundary 与 canary scan | 处理 credential、provider 数据、输出或落盘的 runtime contract | 纯文档索引类 contract |
| G12 | changelog、effective date、迁移/回滚评审 | 全部 | 不允许；无迁移时写明 `migration: none` |

`not_applicable` 必须满足：

1. 由 governance 的适用性规则允许；
2. 在 manifest 中记录稳定原因；
3. 引用正式评审记录；
4. contract 类型或范围变化后重新评估；
5. G1、G2、G12 永远不能 N/A。

示例：

```yaml
gates:
  G4:
    state: not_applicable
    reason: "This CLI contract does not define content hashing."
    reviewed_in: "docs/02 specs/contracts/CHANGELOG.md#cli-sub2api-v1"
```

禁止仅在 PR 描述中写 `N/A`；PR 关闭后，判定依据必须仍在仓库中。

### 2.3 跨契约依赖

一个 contract 不应复制另一个 contract 的门禁。依赖应在 manifest 显式声明：

```yaml
depends_on:
  - id: storage/models-v1
    minimum_status: frozen
  - id: provider/torchai-rc21-models-v1
    minimum_status: frozen
    required_capabilities: [models_read]
```

规则：

- `minimum_status: frozen` 的依赖未满足时，消费者不得 frozen；
- provider capability 可以是 `supported`、`blocked` 或 `unsupported`，禁止 `pending` 进入依赖它的 frozen contract；
- `blocked/unsupported` 是可冻结结论，但生产路径必须保持关闭；
- 扩大 capability 不得静默生效，必须更新 profile、fixtures、测试和 changelog；
- 循环依赖必须通过拆 contract 或收窄 scope 消除，不能靠评审口头放行。

### 2.4 Owner 与日期语义

- `owners`：对兼容承诺和 breaking change 有批准责任的个人或稳定团队；
- `last_reviewed`：contract 语义最后复核日期；
- `verified_deployments[].verified_at`：某 provider deployment 最后成功探活日期；
- 不再新增语义重叠的 `last_probed`；
- 文件格式化、链接修复或文字勘误不得自动刷新 `last_reviewed`。

---

## 3. Manifest 与 artifact 设计

### 3.1 主 manifest

主 manifest 继续是 contract 状态和入口的唯一权威。每项至少包含：

```yaml
- id: storage/newapi-groups-v1
  status: draft
  owners: [data-contract-maintainers]
  last_reviewed: "2026-07-23"
  applies_to: [newapi-legacy]
  spec: storage.md
  artifact_manifest: artifacts/storage/newapi-groups-v1/artifact.yaml
  required_artifacts:
    - latest_schema
    - event_schema
    - positive_fixtures
    - negative_fixtures
    - hash_vectors
    - transition_vectors
  depends_on: []
  gates:
    G1: {state: required, evidence: [human_spec]}
    G2: {state: required, evidence: [latest_schema, event_schema]}
    G3: {state: required, evidence: [positive_fixtures, negative_fixtures]}
    G4: {state: required, evidence: [hash_vectors]}
    G5: {state: required, evidence: [transition_vectors]}
    G6:
      state: not_applicable
      reason: "Storage contract does not define CLI modes."
      reviewed_in: "docs/02 specs/contracts/CHANGELOG.md#storage-newapi-groups-v1"
    G11: {state: required, evidence: [secret_scan_cases]}
    G12: {state: required, evidence: [changelog_entry]}
```

约束：

- `required_artifacts` 中每个 id 必须在 `artifact_manifest` 中存在；
- artifact manifest 不得声明另一个 contract id；
- 所有路径必须是仓库根相对路径，解析后仍位于仓库内；
- 禁止 glob 指向 `data/`、真实 `sites/*.env` 或其他未入库 secret；
- frozen contract 的 artifact manifest 不得引用 `docs/drafts/`；
- manifest 中没有 `required_artifacts` 的 contract 不允许 frozen。

### 3.2 Artifact manifest

每个 contract 使用一个可解析的 `artifact.yaml`：

```yaml
contract_id: storage/newapi-groups-v1
schema_dialect: "https://json-schema.org/draft/2020-12/schema"
artifacts:
  human_spec:
    kind: human_spec
    path: "docs/02 specs/contracts/storage.md"
    section: "storage/newapi-groups-v1"
  latest_schema:
    kind: json_schema
    path: "docs/02 specs/contracts/artifacts/storage/newapi-groups-v1/schemas/latest.schema.json"
  event_schema:
    kind: json_schema
    path: "docs/02 specs/contracts/artifacts/storage/newapi-groups-v1/schemas/event.schema.json"
  positive_fixtures:
    kind: fixture_set
    path: "docs/02 specs/contracts/artifacts/storage/newapi-groups-v1/fixtures/positive"
    expected: accept
  negative_fixtures:
    kind: fixture_set
    path: "docs/02 specs/contracts/artifacts/storage/newapi-groups-v1/fixtures/negative"
    expected: reject
  hash_vectors:
    kind: vector_set
    path: "docs/02 specs/contracts/artifacts/storage/newapi-groups-v1/vectors/hash"
  transition_vectors:
    kind: vector_set
    path: "docs/02 specs/contracts/artifacts/storage/newapi-groups-v1/vectors/transition"
  secret_scan_cases:
    kind: test_cases
    path: "docs/02 specs/contracts/artifacts/common/secret-scan"
  changelog_entry:
    kind: changelog_anchor
    path: "docs/02 specs/contracts/CHANGELOG.md"
    anchor: "storage-newapi-groups-v1"
validators:
  - "tests.contract.test_manifest_integrity"
  - "tests.contract.test_schema_fixtures"
  - "tests.contract.test_hash_vectors"
  - "tests.contract.test_group_transitions"
```

`kind` 必须来自工具内固定枚举；新增 kind 需先修改 checker 和测试，禁止把任意文件伪装成证据。

### 3.3 推荐目录

```text
docs/02 specs/contracts/
  manifest.yaml
  CHANGELOG.md
  storage.md
  cli.md
  config.md
  provider-profiles.md
  remote-mutation.md
  deployment-security.md
  artifacts/
    common/
      secret-scan/
    storage/
      sub2api-groups-legacy/
        artifact.yaml
        schemas/
        fixtures/positive/
        fixtures/negative/
        vectors/hash/
        vectors/transition/
      newapi-groups-v1/
      models-v1/
      auth-behavior-v1/
      invite-link-v1/
    cli/
      sub2api-v1/
      newapi-legacy-v1/
      invite-links-v1/
    config/
      sub2api-v1/
      newapi-legacy-v1/
    provider/
      sub2api-v1/
      newapi-legacy-groups-v1/
      newapi-legacy-aff-v1/
      torchai-rc21-models-v1/
    safety/
      remote-mutation-v1/
    deployment/
      systemd-v1/

tests/contract/
  __init__.py
  test_manifest_integrity.py
  test_schema_fixtures.py
  test_hash_vectors.py
  test_storage_transitions.py
  test_storage_migrations.py
  test_cli_matrices.py
  test_provider_fixtures.py
  test_remote_mutation_vectors.py
  test_config_manifests.py
  test_systemd_units.py
  test_secret_boundaries.py

tools/
  contract_check.py
```

空目录不提交；只有形成 README、schema、fixture 或 vector 时才创建。

---

## 4. 验证工具与 CI

### 4.1 固定工具链

在仓库 `.venv` 中增加并锁定：

- `jsonschema>=4.23,<5`：JSON Schema Draft 2020-12；
- `PyYAML>=6,<7`：使用 `yaml.safe_load` 读取 manifest；
- 继续使用标准库 `unittest` 作为测试入口。

每个 JSON Schema 必须包含：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:zhongzhuan:storage:newapi-groups-v1:latest"
}
```

仅靠 `format: date-time` 不足以表达仓库的 UTC `Z`、秒精度规则；时间字段还必须使用明确 pattern，测试覆盖 offset、无时区和小数秒拒绝。

### 4.2 `tools/contract_check.py`

工具至少提供：

```bash
.venv/bin/python tools/contract_check.py validate
.venv/bin/python tools/contract_check.py list
.venv/bin/python tools/contract_check.py list --status draft
.venv/bin/python tools/contract_check.py check storage/models-v1
```

`validate` 必须检查：

1. manifest YAML 可解析、ID 唯一、状态合法；
2. owner 非占位，日期格式合法；
3. spec 和 artifact manifest 路径存在且不越出仓库；
4. `required_artifacts` 与 artifact manifest 一一对应；
5. gate state 只能是 `required` 或合法的 `not_applicable`；
6. required gate 的 evidence 均存在；
7. dependency 存在、无循环、状态/capability 满足；
8. JSON Schema dialect 和 `$id` 正确且 schema 自身可校验；
9. positive fixtures 全部被接受，negative fixtures 全部被拒绝；
10. vector case id 唯一，输入和 expected 文件成对；
11. changelog anchor、effective date、migration、rollback 存在；
12. frozen contract 不引用 drafts、真实 data 或 secret env；
13. bundle release set 精确、无重复且依赖闭合。

`list` 的输出用于查看现状；不得把输出再次手抄进 playbook。

### 4.3 CI 命令

提交冻结 PR 前必须依次运行：

```bash
.venv/bin/python tools/contract_check.py validate
.venv/bin/python -m unittest discover -s tests/contract -t . -v
.venv/bin/python -m unittest discover -s tests -t . -v
```

deployment 相关变更还必须运行：

```bash
systemd-analyze verify ./*.service ./*.timer
```

CI job 必须实际执行上述命令，不能仅保证“本地可跑”。provider 在线探活不进入普通 CI；CI 只重放已脱敏 fixtures。

### 4.4 Fixture 与 vector 约定

Schema fixture：

- `fixtures/positive/*.json` 必须 validate success；
- `fixtures/negative/*.json` 必须 validate failure；
- negative case 通过文件名或 sidecar 声明稳定 `error_kind`，不绑定第三方库完整报错文本；
- 每个 schema 至少覆盖缺字段、错类型、额外字段、未知 version、错 site/backend、边界值和 secret 字段注入。

行为 vector 使用单 case 目录：

```text
vectors/transition/a-b-a/
  case.yaml
  initial.json
  inputs.json
  expected-latest.json
  expected-events.jsonl
```

`case.yaml` 至少包含 contract id、case id、runner、expected outcome。测试通过 runner registry 调用正式 writer/reader；禁止在测试里重写一份生产算法后只验证自己。

### 4.5 Secret scan

secret test 采用“canary 值 + 结构规则”组合：

- 在 fake env/provider response 中注入唯一 canary password、JWT、session、API Key 和 proxy userinfo；
- 执行 CLI、writer、error path 和日志捕获；
- 扫描 stdout、stderr、日志、latest 和 events，确保 canary 不出现；
- 同时拒绝 `Authorization`、`Set-Cookie`、完整 response body 等高风险结构；
- 允许 `key_id`、`error_kind` 等非 secret 字段，禁止用包含单词 `key` 的粗糙正则造成误报；
- provider fixtures 自身只能使用明显无效且不可还原身份的占位值。

---

## 5. 契约边界调整

### 5.1 Invite 拆分

现有 `storage/invite-link-v1` 同时描述落盘、CLI 和第三方 endpoint。冻结前应拆成：

1. `storage/invite-link-v1`：只负责 envelope、reader/writer、TTL 判断、base URL 失效、atomic write、文件 mode 和失败保旧；
2. `cli/invite-links-v1`：负责 `--validate`、默认 collect、`--force`、副作用和退出码 0/1/2；
3. `provider/sub2api-v1` 增加明确 `aff_read` capability 和 fixtures；
4. 新增 `provider/newapi-legacy-aff-v1`，冻结 `/api/user/self` 与 `/api/user/aff` 的鉴权、fallback、envelope 和漂移策略。

`cli/invite-links-v1` 依赖 storage 和相应 provider profile。storage contract 不依赖在线 provider，可以先冻结本地格式，但不得据此宣称采集链路整体 frozen。

### 5.2 Auth behavior

`storage/auth-behavior-v1` 不发布 auth cache 公共字段 schema，但仍必须有 machine-readable behavior cases：

- permission 0600；
- 同目录 atomic replace；
- domain/site/backend mismatch 拒绝复用；
- corrupt/unknown cache 丢弃并重新认证；
- secret 不进入普通业务文件和输出。

manifest 使用 `public_field_schema: false`，同时要求 `behavior_vectors`，避免“没有 schema”等于“没有 machine evidence”。

### 5.3 Provider fixtures 按 capability 适用

provider 正式 spec 中的 fixture 集合应按声明 capability 选择，不能要求 groups-only profile 提供 create/update success：

- 所有 profile：auth、business failure、401/403、429、HTML、malformed/empty envelope；
- pagination capability：multi-page、total 不足、repeat/no-progress；
- models read capability：empty、malformed id、Key auth domain；
- mutation capability：create/update known success、known failure、unknown outcome、re-list claim；
- capability 标为 `unsupported` 时：必须有防误启用测试，不要求伪造成功 fixture。

### 5.4 Config example 范围

config manifest 应声明由它管理的 example 文件。校验是双向的：

- manifest 的 required key 必须出现在至少一个对应 profile example；
- example 中的受控 key 必须存在于 manifest；
- site-specific 非 secret override 必须显式允许；
- 不要求为每个生产 `sites/<id>.env` 复制一份 example；
- 禁止读取真实 `.env` 来生成或校验公开 example。

---

## 6. 分阶段实施

### Phase 0：治理闭合

**工作：**

1. 修改正式 governance，加入 §2 的 gate 适用性、N/A、dependency 和 bundle release 规则；
2. 统一 provider 日期字段为 `verified_at` 与 `last_reviewed`；
3. 修改正式 storage/CLI/provider specs，完成 invite 边界拆分；
4. 将 provider fixture 要求改为 capability-specific；
5. 扩展 manifest 结构，加入 `artifact_manifest`、gates、dependencies 和 bundle release set；
6. 为新增 `cli/invite-links-v1`、`provider/newapi-legacy-aff-v1` 登记 planned 条目；
7. 确认真实 owner 或稳定团队标识。

**出口条件：**

- governance 对每种 gate 的适用性只有一种解释；
- manifest schema 通过评审；
- invite 的 storage、CLI、provider 版本归属明确；
- 所有 contract 仍保持原状态，不在同一 PR 偷跑 frozen。

### Phase 1：证据基础设施

**工作：**

1. 增加 `jsonschema`、`PyYAML` 依赖；
2. 实现 `tools/contract_check.py`；
3. 创建 `tests/contract/` 和通用 loader/runner；
4. 建立 artifact 目录及 `artifact.yaml` schema；
5. 建立 common secret canary cases；
6. 创建 `CHANGELOG.md` 模板；
7. 接入 CI，并验证故意删除 artifact、错写 schema、泄漏 canary 时会失败。

**出口条件：**

- checker 能在当前 draft manifest 上运行；
- 至少一个示例 contract 能完整演示 accept/reject/vector/changelog 检查；
- CI 对门禁失败返回非零；
- 正常全量 unittest 仍通过。

### Phase 2：Storage 基础契约

#### 2A `storage/sub2api-groups-legacy`

必须完成：

- latest/event schema 与严格 legacy recognizer；
- number/string id、Unicode、额外字段、默认/紧凑 separators 的 hash vectors；
- `none->A`、`A->A`、`A->B`、`A->B->A` historical scan vectors；
- event-before-latest 崩溃窗口和失败保旧；
- 正式 writer 与 compatibility reader 共用 vectors；
- secret canary scan；
- changelog、effective date、`migration: none`、rollback。

#### 2B `storage/newapi-groups-v1`

必须完成：

- latest/event Draft 2020-12 schemas；
- ratio 的 bool/null/negative/non-finite reject，description null normalize；
- name trim/duplicate、稳定排序与 hash vectors；
- `none->A`、`A->A`、`A->B`、`B->A` 三事件语义；
- JSONL 半行、event-before-latest、tail dedup vectors；
- `SnapshotStore` writer/reader 与 vectors 绑定；
- secret canary scan和 changelog。

#### 2C `storage/auth-behavior-v1`

必须完成：

- behavior manifest 与 vectors；
- `TokenStore`、New-API auth state 的 0600/atomic/mismatch/corrupt tests；
- exception、日志和业务文件 secret scan；
- auth schema 演进导致 cache discard 的兼容说明；
- changelog 和 rollback。

以上三项可分别 frozen，不要求同一 PR。每次冻结前独立运行 contract checker 和全量测试。

### Phase 3：Config 与只读 Provider

#### 3A Config

- 从两个 `load_config` 盘点变量，但由正式评审决定目标键表；
- 发布 common/sub2api/newapi machine config manifests；
- 固定 type、default、required、secret、validation、override、mutation 标记；
- 对 path、HTTPS origin、site id、timeout、boolean、permission 写 negative cases；
- 与声明的 `.env.example` 双向校验；
- 固化 process environment override 和“不修改 `os.environ`”；
- 固化 `--validate` 的允许/禁止副作用；
- legacy fallback 标 deprecated 并记录删除版本。

#### 3B Provider read profiles

- 每个 deployment 记录 host、observable upstream version、`verified_at`；
- 固化 login/refresh/groups/models/aff 中实际声明的 endpoint 和 envelope；
- 所有 fixture 脱敏并可离线 replay；
- capability 逐项写 `supported|blocked|unsupported`，禁止 pending profile 被消费者冻结依赖；
- New-API groups 与 aff 分 profile，避免 endpoint 能力互相推导；
- BotCF models 明确 unsupported，并有防误启用测试。

**出口条件：** config manifest 成为 example 和 validate test 的事实源；provider profile 的每个已声明 capability 都有匹配 fixture 或明确 blocked/unsupported 证据。

### Phase 4：Invite 链路

#### 4A Storage

- schema 覆盖 `additionalProperties: false`；
- UTC `Z` 秒精度、正整数且拒绝 bool 的 TTL；
- site/backend/base URL mismatch；
- `invite_link` 精确拼装与空白 aff code reject；
- TTL hit、TTL expired、base URL changed、force decision vectors；
- 远端失败保留旧文件，合法更新 atomic 且 mode 0644。

#### 4B Provider

- Sub2API aff auth/recovery/envelope fixtures；
- New-API self 优先、aff fallback、`new-api-user` 和 business failure fixtures；
- 真实探活只保存脱敏结果和 `verified_at`；
- secret scan 确认 token/session 不进入 fixture 和 invite latest。

#### 4C CLI

- machine mode matrix：validate/default/force；
- provider call、auth write、invite write、groups/models write、mutation、exit code逐格断言；
- validate=0/config=2/provider-auth-contract=1；
- TTL hit=0 且零 provider call；
- 参数冲突在任何 provider/业务写前返回 2。

storage 可以先独立 frozen；“invite 链路 frozen”只有在 storage、CLI 和所需 provider dependencies 都 frozen 后才能声明。

### Phase 5：Models storage 收敛与迁移

按以下顺序实施，禁止先改 writer 再补 reader：

1. 建 legacy production-like fixtures，不使用真实现网 secret/data；
2. 实现严格 compatibility reader：expected Sub2API、version/site/shape/result 校验；
3. reader 仅内存补 backend、skipped、source，并标 `needs_migration`；
4. 增加新目标 schema和严格 model id parser；
5. 两 backend 统一 trim、去重、排序、hash；
6. writer 输出 backend、四字段 result 和合法 source；
7. 实现显式、幂等、可回滚 migration，默认 dry-run，迁移前备份；
8. representation migration 不写业务 change event，历史 JSONL 不原地改写；
9. 在现网文件副本演练，不直接以生产目录作为测试输入；
10. writer、reader、migration 共用 schema/hash/migration vectors。

必须覆盖：

- null 与 empty models 的区别；
- malformed/non-string/trim-empty id 整组失败；
- failure 保留最后成功 models/hash/key_id/success_at；
- full result 算术约束；
- bootstrap timestamp 只在完整成功时设置；
- at-least-once event 崩溃窗口；
- migration 幂等、备份恢复、无伪 event。

### Phase 6：Remote mutation safety

必须对 Sub2API Key 和 New-API Token 分别提供 vectors，不能只验证 TorchAI happy path：

- managed identity：trim、空值、ASCII、中文、UTF-8 byte 边界、碰撞；
- pagination：多页、短页但 total 未满足、repeat/no-progress、total 漂移、max pages；
- hydration：timeout、masked、空 secret、部分成功均为 unknown；
- resource policy：用户资源永不 update/delete，managed resource 仅在 capability 支持时 repair；
- create known success/failure/unknown outcome；
- unknown outcome re-list claim，禁止同轮第二次 POST；
- mutation 后 re-list/re-hydrate 验证；
- API Key 401 只换 Key，不触发 management re-login；
- cold/default/preflight create=0；
- BotCF models adapter 不可启用。

provider capability 未闭合时可以将能力冻结为 blocked，但 production mutation 必须保持关闭。任何从 blocked 到 supported 的变化都必须带 probe、fixtures、tests 和 changelog。

### Phase 7：Monitor CLI

分别为 `cli/sub2api-v1` 和 `cli/newapi-legacy-v1` 发布 machine-readable mode matrix。每行至少包含：

```text
mode, provider_reads, remote_mutations, auth_writes, groups_writes,
models_writes, lock_policy, success_exit, local_error_exit, runtime_error_exit
```

必须覆盖：

- Sub2API validate/default-loop/once/preflight/bootstrap/refresh；
- New-API validate/default-single-run/preflight/bootstrap/refresh；
- parser 互斥与 validate 组合；
- cold refresh 在 list/create/bind/models 前返回 1；
- preflight fail=1、lock=2；
- default cold 零 mutation、零 models request；
- T-new default partial=0、full partial=1；
- loop/signal 与 once 退出语义；
- stdout 明确为 human-only，或另建 `cli-output-v1` schema，二者必须选一。

CLI contract frozen 前，其 storage/config/safety/provider dependencies 必须满足 manifest 声明。

### Phase 8：Deployment/systemd

必须完成：

- 修齐所有 credential-bearing once/daily unit 的 `UMask=0077` 和 hardening；
- 静态验证 timer schedule、jitter、persistent、Unit 指向；
- 验证 service 无 `[Install]`、timer 无错误 `Requires=`；
- ExecStart 使用仓库固定绝对路径、`.venv/bin/python` 和正确 CLI mode；
- `ReadWritePaths` 只开放 data 路径；
- models timer 默认 disabled；
- installer 验证 venv/env/unit、执行 validate 和 `systemd-analyze verify`；
- installer 拒绝 dual run，任一站失败整体非零；
- enable models 前检查 bootstrap 和 provider capability；若只能 warning，按正式 SHOULD 偏离流程记录；
- 安装/停止/回滚命令可在测试 sandbox 中验证；
- stdout/stderr/journal sample 通过 canary secret scan。

deployment frozen 只说明已登记 path profile。以后支持其他项目路径，需要新的受控 profile、renderer/drop-in 和测试，不能手改 unit 后沿用原承诺。

### Phase 9：Bundle 发布

只有以下条件全部满足才将 bundle 改为 frozen：

1. `release_contracts` 每项均 frozen；
2. dependency closure 全部满足，无 pending capability；
3. checker、contract tests、全量 tests、systemd verify 全绿；
4. 所有 owner 完成语义复核；
5. migration 已在生产文件副本演练；
6. bundle changelog 包含 effective date、受影响部署和 rollback；
7. README、skills、design 只链接正式 contract，不复制 machine 字段表；
8. 发布 commit 固定，生成可重放的 contract inventory；
9. manifest 的 bundle status 在最后一个发布 PR 中改为 frozen。

bundle 发布失败不回退已经正确 frozen 的单项 contract；修正 release set 或发布证据后重新评审 bundle。

---

## 7. 每类 contract 的最小证据包

下表中的“最小包”始终还包括：G1、G2、G12、owner、`last_reviewed`、合法 N/A、依赖闭合和适用的 secret scan。

| Contract | 专属必需证据 |
|---|---|
| `storage/sub2api-groups-legacy` | latest/event schema、legacy recognizer、hash vectors、historical dedup transitions、crash/失败保旧 |
| `storage/newapi-groups-v1` | latest/event schema、normalize fixtures、hash vectors、tail transitions、半行/crash recovery |
| `storage/models-v1` | latest/event schema、strict id fixtures、hash vectors、legacy reader/migration/rollback、失败保旧 |
| `storage/auth-behavior-v1` | behavior manifest、0600/atomic/mismatch/corrupt vectors、secret boundary |
| `storage/invite-link-v1` | envelope schema、TTL/base URL/force decisions、atomic/mode、失败保旧 |
| `cli/sub2api-v1` | mode matrix、side-effect/lock/exit tests、cold gate、stdout policy |
| `cli/newapi-legacy-v1` | mode matrix、side-effect/lock/exit tests、T-new semantics、stdout policy |
| `cli/invite-links-v1` | validate/default/force matrix、TTL zero-call、0/1/2 exits |
| `safety/remote-mutation-v1` | identity/pagination/state vectors、unknown outcome、post-mutation validation、两 backend tests |
| `provider/sub2api-v1` | deployment metadata、capability-specific sanitized fixtures、probe evidence、drift tests |
| `provider/newapi-legacy-groups-v1` | auth/groups fixtures、header/redirect/ratio semantics、probe evidence |
| `provider/newapi-legacy-aff-v1` | self/aff fallback fixtures、header/auth semantics、probe evidence |
| `provider/torchai-rc21-models-v1` | pagination/secret/models/mutation capability fixtures、UTF-8 constraints、unknown outcome probe |
| `config/sub2api-v1` | config manifest、declared examples、negative cases、legacy deprecation、mutation gates |
| `config/newapi-legacy-v1` | config manifest、declared examples、negative cases、fixed-path rules、mutation gates |
| `deployment/systemd-v1` | unit/timer/installer static tests、hardening、default-disabled、dual-run/rollback、verify output |

任何表内未列出的适用 gate 仍然必须满足；本表不能作为跳过 governance 的依据。

---

## 8. 单项冻结 PR 流程

### 8.1 PR 前置条件

- contract 当前为 `draft`；
- owner 已确认且非占位；
- 正式 spec 的 scope/non-goals 已定稿；
- dependency 已 frozen 或按 governance 明确允许；
- implementation gap 已清零，或 compatibility reader/migration 已完成；
- artifact manifest 和 required artifact 已齐备。

### 8.2 PR 内容

每个冻结 PR 必须包含：

1. 正式 spec 语义 diff；
2. artifact manifest；
3. schemas/config manifests/profiles/matrices；
4. positive/negative fixtures 和 vectors；
5. contract tests 与必要实现改动；
6. migration、dry-run、backup、rollback 工具或 `migration: none`；
7. changelog entry 和 effective date；
8. manifest 的 owner、review date、gate evidence、dependency 和 status 更新；
9. 三条标准验证命令的结果；
10. mutation/provider/deployment 变更的安全评审记录。

### 8.3 状态修改顺序

同一 PR 中：

1. 先提交 spec/artifacts/tests/implementation；
2. checker 在 `status: draft` 下验证证据完整；
3. 最后一个逻辑 commit 将该项改为 `frozen`；
4. CI 按 frozen 规则重新验证；
5. 合并后不再手工更新 draft 状态表。

不要求使用多个物理 commit，但 PR diff 和 CI 必须能证明状态变化不是先于证据发生。

### 8.4 Review 职责

- contract owner：语义、兼容性、版本判断；
- implementation reviewer：writer/reader/CLI 与 artifact 一致；
- security reviewer：secret、auth、mutation、deployment 相关变更；
- operations reviewer：systemd、installer、migration 和 rollback；
- 同一人可以承担多个角色，但职责结论必须在评审记录中明确。

---

## 9. Frozen 后的变更规则

### 9.1 必须升版本

- 删除、重命名字段或改变类型/null 语义；
- 改 canonicalization、hash、identity、managed name；
- 改 event diff、顺序、dedup 或 crash recovery 语义；
- 改 CLI mode、副作用、锁或退出码；
- 扩大默认远端写；
- 改 provider 成功/失败判断或支持 deployment 范围；
- 从接受变成拒绝任一 frozen positive fixture；
- 改 migration 后产生的目标表示或 rollback 能力。

新版本必须使用新 contract id、schema `$id` 和 artifact 目录；禁止覆盖旧 vectors 让历史 CI 失去可重放性。

### 9.2 同版本兼容变化

通常允许：

- 新增不改变既有结果的 negative vector；
- 增加真正 optional 且旧 reader 明确允许的字段；
- 补充 probe fixture，但不改变已声明 capability 和成功判定；
- 修复测试工具本身，不改变 contract expected output。

仍然必须更新 changelog，并由 checker 同时跑旧、新 evidence。golden output 变化不能仅标“测试修复”，必须判断是 regression、表示迁移还是 breaking change。

### 9.3 Provider 漂移

发现线上漂移时：

1. 当前站点 contract fail，保留最后成功 snapshot；
2. 立即关闭受影响 mutation capability；
3. 做只读、脱敏 probe；
4. 新增 fixture 重放问题；
5. 判断是兼容 refresh 还是新 profile version；
6. profile、tests、`verified_at`、`last_reviewed` 和 changelog 同步更新；
7. 全部门禁通过后才重新启用 capability。

---

## 10. 状态查看与维护

状态查看只使用：

```bash
.venv/bin/python tools/contract_check.py list
.venv/bin/python tools/contract_check.py list --status planned
.venv/bin/python tools/contract_check.py list --status draft
.venv/bin/python tools/contract_check.py list --status frozen
```

在 checker 尚未实现前，直接打开正式 manifest。禁止在本方案、README、acceptance 或 issue 中维护需要长期同步的完整状态表。

本方案只在以下情况更新：

- 治理流程本身变化；
- artifact 目录或 checker 接口变化；
- phase 依赖关系变化；
- 发现会影响冻结正确性的通用风险。

单项 contract 的状态、owner、artifact 和 probe 日期变化不更新本方案。

---

## 11. 最终验收清单

### 11.1 单项 contract

- [ ] manifest 是唯一状态源，contract id/version 唯一；
- [ ] owner 非占位，`last_reviewed` 是本次语义复核日；
- [ ] scope/non-goals 定稿；
- [ ] artifact manifest 可解析，所有 required artifact 可寻址；
- [ ] 每项 gate 为 required 或治理允许的 N/A；
- [ ] positive fixtures 被接受，negative fixtures 被拒绝；
- [ ] vectors 由正式 writer/reader/adapter 重放；
- [ ] dependencies 状态和 capabilities 满足；
- [ ] implementation gap 清零或迁移/兼容 reader 完成；
- [ ] failure-preserve/fail-closed/secret cases 通过；
- [ ] changelog、effective date、migration、rollback 完整；
- [ ] checker、contract tests、全量 tests 全绿；
- [ ] status 最后改为 frozen。

### 11.2 Bundle

- [ ] release set 精确且全部 frozen；
- [ ] dependency closure 无 planned/draft/pending；
- [ ] provider capability 与生产 enable 状态一致；
- [ ] migration 在生产副本演练并可回滚；
- [ ] systemd/installer 验证通过；
- [ ] README/skills/design 指向正式 contract；
- [ ] bundle changelog 和 effective date 完整；
- [ ] bundle owner 签核；
- [ ] bundle status 最后改为 frozen。

---

## 12. 推荐执行顺序

```text
Phase 0  governance + manifest schema + invite boundary
   |
Phase 1  checker + artifact loader + contract CI
   |
   +--> Phase 2  groups/auth storage
   |
   +--> Phase 3  config + read provider profiles
              |
              +--> Phase 4  invite storage/provider/CLI
              |
              +--> Phase 5  models reader/writer/migration
                           |
                           +--> Phase 6  remote mutation safety
                                      |
                                      +--> Phase 7  monitor CLI
                                                 |
                                                 +--> Phase 8  deployment
                                                            |
                                                            +--> Phase 9  bundle release
```

可以并行编写互不依赖的 fixtures 和 schemas，但 status 变化必须遵守 manifest dependency。首个试点建议选 `storage/newapi-groups-v1`：它有清晰 schema、hash、transition 和 crash vectors，足以验证整套基础设施，又不需要先启用远端 mutation。auth 可作为第二个试点，用于验证“无公共字段 schema，但仍有 machine behavior evidence”的路径。

本方案的完成标准不是“所有框都写了”，而是任何评审者在同一 commit 上运行同一命令，都能得到相同的冻结结论。
