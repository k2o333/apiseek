# 契约冻结操作手册（怎么做）

> 类型：工程 playbook / 实施门禁，**不**进入 frozen bundle。  
> 状态：draft  
> 日期：2026-07-23  
> 权威状态：[`docs/02 specs/contracts/manifest.yaml`](../../02%20specs/contracts/manifest.yaml)  
> 治理规则：[`docs/01 governance/contract-governance.md`](../../01%20governance/contract-governance.md)  
> 正式入口：[`docs/02 specs/README.md`](../../02%20specs/README.md)  
> 相关历史：[`acceptance.md`](./acceptance.md)、[`inventory-as-is.md`](./inventory-as-is.md)、[`review-adoption.md`](./review-adoption.md)

本文件回答两件事：

1. **现在有哪些契约没冻结**（以 manifest 为准）；  
2. **冻结前具体要做什么、按什么顺序做、产物放哪、怎样算过门**。

规范用词与治理一致：**必须/禁止** = MUST/MUST NOT；**应当** = SHOULD；**可以** = MAY。

---

## 0. 现状快照（2026-07-23）

| 层级 | ID / 范围 | status |
|------|-----------|--------|
| Bundle | `zhongzhuan-contracts` | **draft** |
| 全部 machine contract | 见下表 14 项 | **0 frozen** |

### 0.1 逐项清单

| # | Contract ID | status | 人读 spec | 声明的 required_artifacts |
|---|-------------|--------|-----------|---------------------------|
| 1 | `storage/sub2api-groups-legacy` | draft | storage.md | schema, hash_vectors, transition_vectors |
| 2 | `storage/newapi-groups-v1` | draft | storage.md | schema, hash_vectors, transition_vectors |
| 3 | `storage/models-v1` | draft | storage.md | schema, hash_vectors, legacy_migration_vectors |
| 4 | `storage/auth-behavior-v1` | draft | storage.md | （无字段 schema；行为 fixtures） |
| 5 | `storage/invite-link-v1` | draft | storage.md | （manifest 未列；冻结前应补齐） |
| 6 | `cli/sub2api-v1` | draft | cli.md | mode_matrix, side_effect_tests |
| 7 | `cli/newapi-legacy-v1` | draft | cli.md | mode_matrix, side_effect_tests |
| 8 | `safety/remote-mutation-v1` | draft | remote-mutation.md | state_vectors, pagination_vectors, identity_vectors |
| 9 | `deployment/systemd-v1` | draft | deployment-security.md | （应补 unit/installer tests） |
| 10 | `provider/sub2api-v1` | **planned** | provider-profiles.md | 探活 + fixtures |
| 11 | `provider/newapi-legacy-groups-v1` | **planned** | provider-profiles.md | 同上 |
| 12 | `provider/torchai-rc21-models-v1` | **planned** | provider-profiles.md | 同上 + mutation capability |
| 13 | `config/sub2api-v1` | **planned** | config.md | config manifest + env examples |
| 14 | `config/newapi-legacy-v1` | **planned** | config.md | 同上 |

**结论：现在没有任何契约处于 frozen；也不能对外做兼容承诺。**

### 0.2 已有实现 vs 冻结证据

| 已有（不足以冻结） | 仍缺（冻结证据） |
|--------------------|------------------|
| 人读 contract 在 `docs/02 specs/contracts/` | JSON Schema / config manifest |
| 大量 unittest（行为近似） | 与 schema 绑定的 golden vectors / fixtures 目录 |
| 设计文档 `docs/03 designs/` | provider 脱敏 fixtures + last_probed 登记 |
| invite CLI + 20 单测 | invite schema + CLI 退出码矩阵 + 正式 vectors |
| systemd unit 模板 | installer/unit 静态 contract tests、hardening 对齐 |

单测全绿只是必要证据，**不能**替代治理 §5 的 12 条门禁。

---

## 1. 冻结的定义与硬规则

### 1.1 何时算「冻结」

仅当 **同时** 满足：

1. `manifest.yaml` 中该项 `status: frozen`；  
2. `required_artifacts` 全部存在且 CI/本地可跑；  
3. 对应 changelog 有 **effective date** 与迁移/回滚说明；  
4. `owners` 为真实责任人（禁止继续用占位 `repository-maintainers`）。

禁止：只改 prose 标题里的「已冻结」、或只改实现不改 manifest。

### 1.2 生命周期

```text
planned → draft → frozen → deprecated → retired
```

- **planned → draft**：人读不变量定稿，范围/non-goals 清晰，manifest 更新。  
- **draft → frozen**：§2 门禁 checklist 全勾。  
- **breaking change**（字段删改、hash、退出码、默认远端写等）：必须升 contract 版本号，并写迁移。

### 1.3 三条硬规则（全仓库）

1. **Schema / 人读不变量是权威**；禁止用「当前 Python 碰巧输出什么」反推正式 schema 后不再 revisit 边界。  
2. **远端写 fail closed**；分页/hydration/语义不确定禁止继续 create/update。  
3. **失败不覆盖最后成功业务快照**（groups/models/invite latest）。

### 1.4 证据优先级（冲突时）

```text
frozen machine contract
  > matching contract tests / vectors
  > current implementation
  > docs/03 designs
  > docs/drafts/*（含本文件）
```

本 playbook 永远低于正式 manifest 与 frozen artifacts。

---

## 2. 通用冻结门禁（每项都过）

复制治理 §5，做成可打勾清单。**N/A 必须在评审记录里写明原因**，禁止默默跳过。

| # | 门禁 | 做法 | 产物建议路径 |
|---|------|------|--------------|
| G1 | 人读不变量定稿 | 改正式 `docs/02 specs/contracts/*.md`；范围与 non-goals 明确 | 正式 spec 段落 |
| G2 | machine schema 可解析 | 为 envelope 写 JSON Schema（或等价） | `docs/02 specs/contracts/schemas/` 或 `artifacts/<id>/` |
| G3 | pos/neg fixtures 过 schema | 合法/非法 JSON 样本 | `.../fixtures/positive|negative/` |
| G4 | hash golden vectors | 输入 → 期望 content_hash | `.../vectors/hash/` |
| G5 | 状态/崩溃/迁移 vectors | A→B→A、半行、tmp 残留、legacy migrate | `.../vectors/transition|migration/` |
| G6 | CLI mode / 退出码 | mode × 副作用 × exit 0/1/2 | `tests/contract/` + mode_matrix.md |
| G7 | remote mutation fail closed | incomplete/unknown 不二次盲发 | vectors + tests |
| G8 | provider profile | 脱敏 fixtures、last_probed、capability 表 | provider-profiles + fixtures |
| G9 | systemd/installer 静态测 | unit 字段、UMask、路径 | `tests/contract/test_systemd_*.py` |
| G10 | env examples 一致 | `.env.example` ↔ config manifest | sites/*.example + config.md |
| G11 | secret scan | stdout/日志/latest/events 无 JWT/session/password/key | scan 脚本或 CI job |
| G12 | changelog / 生效日 / 迁移 | 单契约或 bundle CHANGELOG | `docs/02 specs/contracts/CHANGELOG.md` |

**横切（所有项）：**

- [ ] 替换 `owners`  
- [ ] `last_reviewed` 更新为评审日  
- [ ] 实现差异表：frozen 前必须「实现达标」或「有版本化迁移 + 兼容 reader」  
- [ ] 评审纪要链接（PR 或 `docs/drafts/specs/review-*.md`）

---

## 3. 推荐目录布局（建议新建）

正式 artifacts 建议落在契约根下，避免与 `docs/drafts` 混淆：

```text
docs/02 specs/contracts/
  manifest.yaml
  CHANGELOG.md                 # 冻结时创建
  storage.md / cli.md / ...
  schemas/
    storage-sub2api-groups-legacy.schema.json
    storage-newapi-groups-v1.schema.json
    storage-models-v1.schema.json
    storage-invite-link-v1.schema.json
    ...
  fixtures/
    storage-invite-link-v1/
      positive/ok-min.json
      negative/bad-link.json
    ...
  vectors/
    storage-sub2api-groups-legacy/
      hash/
      transition/
    storage-newapi-groups-v1/
      ...
    safety-remote-mutation-v1/
      ...
  matrices/
    cli-sub2api-v1.md
    cli-newapi-legacy-v1.md
  providers/
    sub2api-v1.md              # 或并入 provider-profiles.md 章节
    fixtures/                  # 脱敏 HTTP 录制
```

测试建议：

```text
tests/contract/
  test_schema_fixtures.py      # 加载 schema 校验 fixtures
  test_hash_vectors.py
  test_cli_mode_matrix.py
  test_systemd_units.py
  test_secret_scan_samples.py
```

现有 `tests/test_*.py` 可继续作实现回归；**contract 测试必须能指回 manifest 的 artifact 名**。

---

## 4. 分阶段怎么做（推荐顺序）

依赖关系：

```text
Phase A  基础：owners、changelog 骨架、secret scan、artifact 目录
Phase B  planned → draft：config + provider 人读定稿 + 探活登记
Phase C  storage 字段契约：groups → models（含迁移）→ auth → invite
Phase D  safety/remote-mutation（依赖 provider identity）
Phase E  CLI mode 矩阵（依赖 storage 副作用语义）
Phase F  deployment/systemd
Phase G  可选：invite 可与 C 后半并行提前冻结
```

下面每阶段写：**目标 → 具体步骤 → 完成标准 → 主要风险**。

---

### Phase A — 横切基建（约 0.5–1 天）

**目标：** 后续每一项冻结都有地方放证据、有人签字、有扫描。

| 步骤 | 动作 |
|------|------|
| A1 | 确认 owners 名单（个人或稳定团队），写进 governance 或 README，再批量改 manifest |
| A2 | 创建 `docs/02 specs/contracts/CHANGELOG.md` 骨架（Unreleased / 模板） |
| A3 | 建 `schemas/` `fixtures/` `vectors/` `matrices/` 空目录 + README（说明命名） |
| A4 | 定 secret scan 范围：禁止出现在 stdout/日志/groups|models|invite latest/events 的模式；auth 文件路径 allowlist |
| A5 | 约定 contract 测试命令：`.venv/bin/python -m unittest discover -s tests/contract -v` |

**完成标准：**

- [ ] owners 非占位  
- [ ] 空目录与命名约定合并  
- [ ] secret scan 能对样例 positive/negative 跑通  

**风险：** owners 不确认则任何 `frozen` 都不合规。

---

### Phase B — Config + Provider（planned → draft，再择机 frozen）

#### B1 `config/sub2api-v1` / `config/newapi-legacy-v1`

| 步骤 | 动作 |
|------|------|
| B1.1 | 从 `sub2api_monitor.load_config` / `newapi_monitor.load_config` **导出键表**（名称、类型、默认、是否 secret、是否影响远端写） |
| B1.2 | 写入正式 `config.md` 完整键表；与 `sites/*.env.example` 对齐；缺 example 的补齐 |
| B1.3 | machine 形态：YAML/JSON config manifest（键 → type/default/required） |
| B1.4 | 测试：缺密码、非 HTTPS、site_id 非法 → exit 2；example 键集合 ⊇ required |
| B1.5 | manifest：`planned` → `draft`；artifacts 齐后 → `frozen` |

**完成标准：** 改一个 required env 名必须改 example + manifest + 测试三者之一失败。

#### B2 Provider profiles

| 步骤 | 动作 |
|------|------|
| B2.1 | `provider/sub2api-v1`：列已支持站点共性 endpoint（login/refresh/groups/keys/models/aff）、envelope、`User-Agent` 绑定说明 |
| B2.2 | `provider/newapi-legacy-groups-v1`：BotCF/TorchAI groups 路径、session、`new-api-user` 规则 |
| B2.3 | `provider/torchai-rc21-models-v1`：models/token API、**create/repair capability 门禁**；探针未过禁止 frozen 宣称可自动写 |
| B2.4 | 每个 profile：脱敏 request/response fixtures（无真实 token/email/session）+ `last_probed: YYYY-MM-DD` |
| B2.5 | 漂移策略写死：envelope 不符 → contract fail，不静默兼容未知字段当成功 |
| B2.6 | planned → draft →（fixtures+探活+scan）→ frozen |

**完成标准：** 新站点接入必须能对照 profile 勾选 capability，而不是只 copy env。

**风险：** 把「某一天 curl 通了」当成 frozen provider；必须有可回放 fixtures。

---

### Phase C — Storage

#### C1 `storage/sub2api-groups-legacy`（draft → frozen）

| 步骤 | 动作 |
|------|------|
| C1.1 | JSON Schema：latest 字段 `site_id, fetched_at, count, content_hash, groups`（无 schema_version 的 legacy 形态） |
| C1.2 | 实现 **识别器**：何时当 legacy 读、何时 reject（对照 storage.md §2.4） |
| C1.3 | **hash vectors**：固定 groups 列表 → 期望 `sha256:...`；覆盖 int/string id、Unicode、额外字段进 hash |
| C1.4 | **transition vectors**：initial 空 diff；A→B→A 第二次 A 可不发事件（historical_hash_scan） |
| C1.5 | 测试绑定 writer（`sub2api_monitor`）与 reader；失败保旧：poll 失败不改 latest |
| C1.6 | 实现差异清零或文档化「仅 reader 兼容」 |

**完成标准：** 独立脚本只喂 vectors 目录即可复算 hash，与实现一致。

#### C2 `storage/newapi-groups-v1`

| 步骤 | 动作 |
|------|------|
| C2.1 | Schema：`schema_version=1, site_id, backend=newapi, ...` + event 形状 |
| C2.2 | 规范化 vectors：ratio/null/负数/bool → reject；合法 ratio → 稳定 id 排序 |
| C2.3 | **tail dedup** vectors：A→B→A 行为与 Sub2API legacy **不同**，必须分开测 |
| C2.4 | 半行 JSONL / event 已写 latest 未换 → 崩溃窗口 vectors |
| C2.5 | 对齐 `monitor_storage.SnapshotStore` |

#### C3 `storage/models-v1`（最重）

| 步骤 | 动作 |
|------|------|
| C3.1 | **先做实现收敛**（否则冻结等于冻结错误行为）：两 backend writer 输出 `backend`、四字段 full result、`source=bootstrap\|refresh\|incremental` |
| C3.2 | model id：非字符串/空/trim 空 → **整组 contract fail**，保旧 latest |
| C3.3 | trim、去重、排序 + hash vectors |
| C3.4 | Schema latest/events |
| C3.5 | **legacy_migration_vectors**：缺 backend 文件 → 内存兼容标记 `needs_migration`；显式 migration 幂等、无伪业务 event |
| C3.6 | 禁止 write-on-read |

**完成标准：** `docs/02 specs/README.md`「尚未冻结」列表中 models 相关条目全部关掉。

#### C4 `storage/auth-behavior-v1`

| 步骤 | 动作 |
|------|------|
| C4.1 | 不强制 public JSON Schema；写 **行为 fixtures 表**（mode 0600、atomic、domain mismatch、corrupt discard） |
| C4.2 | 测试：`TokenStore` / `save_auth_state`；secret 不进 groups/models |
| C4.3 | manifest 可补 `required_artifacts: [behavior_vectors]` |

#### C5 `storage/invite-link-v1`（可与 C 并行，依赖弱）

| 步骤 | 动作 |
|------|------|
| C5.1 | Envelope schema（见 storage.md §6） |
| C5.2 | fixtures：合法；错 `invite_link`；错 version；空白 aff_code |
| C5.3 | vectors：`ttl_ok` 不请求；`base_url_changed` 必请求；远端失败保留旧文件 |
| C5.4 | CLI 矩阵：`--validate`→0；配置错→2；远端错→1；`--force` 忽略 TTL |
| C5.5 | 在 manifest 补 `required_artifacts` |
| C5.6 | 可选：登记 aff endpoint 到 provider 附录 |

**完成标准：** `invite_links.py` 行为与 schema/vectors 一一对应。

---

### Phase D — `safety/remote-mutation-v1`

| 步骤 | 动作 |
|------|------|
| D1 | 依赖 B2 TorchAI models 探针闭合（create/repair 真实语义） |
| D2 | **identity_vectors**：managed 名算法、UTF-8 字节上限、碰撞 |
| D3 | **pagination_vectors**：中途失败 fail closed，不半页当成功 |
| D4 | **state_vectors**：hydration unknown、create=0、unknown POST 不二次盲发 |
| D5 | 测例：user 资源永不 update/delete；models Key 401 不 management re-login |
| D6 | BotCF models 保持 unsupported，测试防止误启用 |
| D7 | 与 CLI 默认「冷路径零 mutation」一致 |

**完成标准：** 任一 uncertain outcome 路径都有 negative vector + 测试名可检索。

---

### Phase E — CLI

#### E1 `cli/sub2api-v1`

| 步骤 | 动作 |
|------|------|
| E1.1 | 写 `matrices/cli-sub2api-v1.md`：行=mode（validate, default loop, once, models-preflight, bootstrap, refresh…），列=调 provider? 写 auth? 写 groups? 写 models? mutation? exit |
| E1.2 | 实现/测试对齐：锁不可用→2；配置→2；provider/auth/contract→1；成功→0 |
| E1.3 | parser 互斥或「文档化的手工互斥」+ contract 测试 |
| E1.4 | 决定 stdout：human-only **或** 发布 `cli-output-v1` schema（cli.md 已提示） |

#### E2 `cli/newapi-legacy-v1`

同 E1，但反映 **无 `--once`**、默认单轮 collect、models flags 互斥等差异。

**完成标准：** 矩阵中每一格有测试或显式 N/A 理由。

---

### Phase F — `deployment/systemd-v1`

| 步骤 | 动作 |
|------|------|
| F1 | 静态解析 `*.service` / `*.timer`：User、WorkingDirectory、ExecStart、ReadWritePaths、无 Environment 塞密码 |
| F2 | credential-bearing unit：`UMask=0077`（或文档等价）对齐；修 `sub2api-monitor-once@` / models-daily gaps（acceptance 已列） |
| F3 | installer 脚本：bootstrap/provider capability 校验；与 deployment-security.md 一致 |
| F4 | timer + oneshot only；禁止应用内多线程 supervisor 回归测试（文档级 + 无反向 unit） |
| F5 | 与 Phase E CLI 入口路径一致（`.venv/bin/python … --env-file sites/%i.env`） |

**完成标准：** `tests/contract/test_systemd_*.py` 改坏 UMask 或 ExecStart 即红。

---

## 5. 单项「冻结 PR」模板

每个 contract 单独或小批量 PR，标题示例：`freeze: storage/invite-link-v1`。

**PR 必须包含：**

1. 正式 spec 最终 diff（若有）  
2. schemas / fixtures / vectors / matrices  
3. `tests/contract/…`  
4. 实现 diff（若有）  
5. `manifest.yaml`：`status: frozen`，`last_reviewed`，`owners`  
6. `CHANGELOG.md` 条目：effective date、兼容范围、迁移  
7. secret scan 通过说明  
8. N/A 门禁列表（若有）

**合并后检查：**

```bash
# 示例
.venv/bin/python -m unittest discover -s tests/contract -v
.venv/bin/python -m unittest discover -s tests -v
# 再人工打开 manifest，确认 status: frozen
```

---

## 6. 按契约的「最小可冻结包」速查

| Contract | 最小包（缺一不可） |
|----------|-------------------|
| sub2api-groups-legacy | schema + hash_vectors + transition_vectors + 失败保旧测 + changelog |
| newapi-groups-v1 | schema + hash_vectors + transition（tail）+ 规范化 reject 测 + changelog |
| models-v1 | **实现对齐** + schema + hash_vectors + legacy_migration_vectors + 严格 id 测 + changelog |
| auth-behavior-v1 | 行为 vectors/测（0600/atomic/mismatch）+ secret 边界 + changelog |
| invite-link-v1 | schema + fixtures + TTL/base_url/保旧 vectors + CLI 0/1/2 + changelog |
| cli/* | mode_matrix 文档 + side_effect 测 + 退出码测 + changelog |
| remote-mutation | 三类 vectors + fail closed 测 + provider 探针证据 + changelog |
| provider/* | capability 表 + 脱敏 fixtures + last_probed + scan + changelog |
| config/* | 键表 manifest + env.example 一致 + 非法配置测 + changelog |
| deployment/systemd-v1 | unit/installer 静态测 + hardening 对齐 + changelog |

---

## 7. 与实现差距清单（冻结前必须处理）

摘自正式 README「尚未冻结」与 inventory，转化为动作：

| 差距 | 阻塞谁 | 动作 |
|------|--------|------|
| Sub2API models 缺 backend / 三字段 result / source=daily | models-v1 | 改 writer + migration |
| 两 backend model-id 严格性不一致 | models-v1 | 统一 reject 规则 |
| TorchAI mutation 探针未闭合 | remote-mutation, torchai-rc21-models | 脱敏探针 + capability 门禁 |
| BotCF models 勿误开 | remote-mutation, provider | 测试钉死 unsupported |
| unit UMask / hardening 不齐 | deployment/systemd-v1 | 改 unit + 静态测 |
| installer 未强制 bootstrap/capability | deployment | 改 install_*.sh + 测 |
| owners 占位 | **全部** | Phase A1 |
| 无 JSON Schema / 完整 vectors | storage/cli/safety | Phase C–E |
| invite 部分站探活超时 | invite 探活附录 | 重试或记录 last_probed 失败站 |

---

## 8. 建议工时量级（仅供排期，非承诺）

| 阶段 | 粗量级 | 说明 |
|------|--------|------|
| A 基建 | 0.5–1 d | owners 等待另计 |
| B config+provider draft | 2–4 d | 探活依赖外网 |
| C1–C2 groups | 2–3 d | hash 细节易踩坑 |
| C3 models | 3–5 d | 含实现迁移 |
| C4–C5 auth+invite | 1–2 d | invite 可先冻 |
| D mutation | 2–4 d | 依赖 TorchAI 探针 |
| E CLI | 1–2 d | |
| F systemd | 1–2 d | |
| **合计** | **约 2–4 周** 专职 | 可并行 invite / groups |

可优先 **invite-link-v1 + auth-behavior-v1 + newapi-groups-v1** 练兵冻结流程，再啃 models。

---

## 9. 明确不在本轮冻结范围（non-goals）

除非另开 contract 版本，否则不要塞进 v1 冻结：

- 跨站聚合表（`docs/websites/table/*`）作为采集器输入契约  
- BotCF models 自动写  
- 「任意 New-API 部署」通用兼容  
- 应用内多线程 / 常驻 Supervisor  
- invite events JSONL、invite 的 systemd timer（可后置）  
- 从草稿目录 `docs/drafts/specs/contracts/*` 双写权威（禁止第二镜像）

---

## 10. 检查清单：改 status 前最后 10 问

1. manifest 里 artifacts 路径是否都存在？  
2. fixtures 是否全部过 schema？  
3. vectors 是否与实现 hash/行为逐字节或逐字段一致？  
4. 是否有失败保旧 / fail closed 的 negative 测？  
5. secret scan 是否覆盖 CLI 输出样例？  
6. owners / last_reviewed / changelog effective date 是否写了？  
7. 实现是否仍依赖「未文档化的」字段？  
8. N/A 门禁是否写进 PR 描述？  
9. 兼容 reader / 迁移是否可回滚？  
10. 是否误把 `docs/drafts` 当权威改完却忘了改 `docs/02 specs`？

全部「是」才允许 `status: frozen`。

---

## 11. 维护本 playbook

| 事件 | 动作 |
|------|------|
| 某 contract 已 frozen | 在 §0 表更新状态；acceptance.md 对应勾选 |
| 新增 contract | 在 §0 与 §6 补行；manifest 同步 |
| 冻结流程变更 | 改本文件 + 指向 governance 的 diff |
| 与正式 spec 冲突 | **以正式 spec + manifest 为准**，回写本 playbook |

---

## 12. 相关文件索引

| 用途 | 路径 |
|------|------|
| 状态权威 | `docs/02 specs/contracts/manifest.yaml` |
| 治理门禁 | `docs/01 governance/contract-governance.md` |
| 正式 storage/cli/… | `docs/02 specs/contracts/*.md` |
| 旧 checklist | `docs/drafts/specs/acceptance.md` |
| 实现差距 inventory | `docs/drafts/specs/inventory-as-is.md` |
| 决策采纳 | `docs/drafts/specs/review-adoption.md` |
| 实现设计 | `docs/03 designs/` |
| Invite 实现 | `invite_links.py`、`docs/03 designs/invite-links.md` |

---

**一句话：** 现在 14 项全没冻结；冻结不是改标题，而是按 Phase A→F 补 **schema/fixtures/vectors/CLI 矩阵/探活/changelog/owners**，并用 `tests/contract` 钉死。invite 可较早单独练兵；models + remote-mutation 是关键活。
