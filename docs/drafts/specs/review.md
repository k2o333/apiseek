# 契约冻结方案评审

**评审对象：** [README.md](./README.md)（Zhongzhuan 契约冻结方案）  
**评审日期：** 2026-07-22  
**对照范围：** 当前工作区实现（`sub2api_monitor.py` / `newapi_monitor.py` / `*_models.py` / `monitor_storage.py` / systemd unit / tests）、`docs/drafts/get-models/`、`docs/drafts/newapi/`、现网 `data/*` 快照。  
**结论：** 方向正确，P0 边界选择大体合理，但**文档同时承担了「现状盘点 / 目标契约 / CI 门禁 / 实施路线」四类职责**，体量过大且部分事实已过期。建议先收敛成「短决策 brief + 按契约拆分的权威稿」，再谈冻结。

严重度：

| 级别 | 含义 |
|------|------|
| **P0** | 冻结前必须处理；否则会把错误语义写进 contract，或门禁建立在过期事实上 |
| **P1** | 首次发布前处理；否则实现/下游仍会各读各的 |
| **P2** | 结构精简与可维护性；不阻塞技术正确性 |

---

## 1. 值得保留

以下不是过度设计，应进入最终 contract：

1. **六类边界优先级（P0 持久化/CLI/远端写/provider，P1 配置/systemd）**  
   与真实兼容成本一致：文件和 CLI 比 dataclass 更难改。

2. **不同名文件 ≠ 同一 schema**  
   Sub2API groups legacy 与 New-API groups v1 的事件语义差异写得很清楚；禁止按文件名猜格式是对的。

3. **models 尚未完整发布 → 直接统一真正的 v1**  
   比继承两套半成品并事后兼容更低熵。

4. **schema 不以当前 Python 输出为权威**  
   防止错误实现被“合法化”；contract → tests → implementation 的方向正确。

5. **远端写 fail-closed**  
   分页不完整禁止 create、unknown POST 不二次盲发、managed identity、永不自动删除用户资源——这些应原样进入机器可读契约。

6. **明确不冻结清单（§17）**  
   防止契约膨胀到日志措辞和内部函数签名。

7. **冲突处理优先级（§19）**  
   machine contract > contract tests > 当前实现 > draft，这条规则本身就应写进 governance。

---

## 2. 总体问题：一份文档做了四件事

当前 README 约 **1070 行 / 19 节**，混合了：

| 层 | 内容 | 读者 | 刷新频率 |
|----|------|------|----------|
| A. 决策 brief | 为什么冻结、P0/P1、不做的事 | 评审/owner | 低 |
| B. as-is 盘点 | 冲突表、测试基线、unit 差异 | 实现者 | **很高** |
| C. to-be 契约 | schema、CLI 矩阵、安全不变量 | CI/实现 | 中，变更需升版本 |
| D. 工程计划 | Phase、CI 门禁、checklist | 项目排期 | 高 |

结果是：

- 契约语义被“现状描述”和“路线图”稀释；
- as-is 一过期，整份文件可信度一起下降（见 §3）；
- 评审时很难只签核“冻结什么”，而不得不连实现顺序一起辩论。

这不是内容错误，而是**文档形态错误**。

---

## 3. 事实过期与表述偏差（P0 / P1）

对照 2026-07-22 工作区实测：

### P0-1：§4.3 / §15.1 / §16 Phase 3 / §18 仍写 New-API models CLI 未接入

**文档写：** CLI 入口未接线；`tests/test_newapi_models_cli.py` 导致 **154 passed, 18 failed**。

**现状：**

- `newapi_monitor.py` 已有 `argparse` mutually-exclusive 的  
  `--models-preflight` / `--models-bootstrap` / `--models-refresh`，并在 `main()` 分发。
- 全量测试结果为：

```text
172 passed, 7 subtests passed
```

**影响：** 验收清单和 Phase 顺序建立在旧基线上，冻结评审会误判“还差一整段 CLI 工程”。

**建议：** 立刻把 §4.3 改成“已接线，待 contract 冻结与生产 enable 门禁”；§18 换成当日可复现命令与结果；§15.1 去掉“flags 接入”类已完成项，改成“副作用矩阵与 schema 断言是否齐”。

### P0-2：models “同名 v1” 的差异表大体正确，但成功列表语义应更精确

文档 §4.2 写 Sub2API 模型规范化“转字符串并排序 hash；成功列表可保留原顺序/重复/空白”。

**实现更接近：**

| 维度 | Sub2API | New-API |
|------|---------|---------|
| 解析落盘 | strip 后保留**顺序**；**不去重**；空串丢弃 | strip + **去重** + **排序** 后落盘 |
| hash | `sorted(str(m) for m in models)`（不 strip、不去重） | 先 `normalize_model_ids` 再 hash |
| `last_full_result` | `{target,ok,failed}`；deadline skip **并入 failed** 写入（`failed + skipped_deadline`） | 四字段，`target == ok+failed+skipped` |
| `backend` 顶层 | 无 | 无（与文档一致） |

文档把 Sub2API deadline 写成“合并计入 failed”是对的；但“可保留空白”不准确（解析阶段已 strip 掉空串）。冻结 models v1 时应**只写目标语义**，as-is 差异放到附录，避免读者把过渡实现当目标。

### P1-1：systemd hardening 对照不完整

§4.4 暗示 `newapi-monitor-once` 与 `sub2api-models-daily` 已“较完整”，仅 `newapi-models-daily` 缺项。

**实测矩阵：**

| unit | UMask | Nice | NoNewPrivileges / PrivateTmp / ProtectSystem / ReadWritePaths |
|------|-------|------|----------------------------------------------------------------|
| `newapi-monitor-once@.service` | ✅ | ✅ | ✅ |
| `sub2api-monitor-once@.service` | ❌ | ✅ | ✅ |
| `sub2api-models-daily@.service` | ❌ | ✅ | ✅ |
| `newapi-models-daily@.service` | ❌ | ❌ | ❌（仅 ExecStart + Timeout） |
| `sub2api-monitor@.service`（legacy simple） | ❌ | ❌ | 部分（无 Nice/UMask/Timeout） |

因此：

- “完整参考实现”其实只有 **newapi-monitor-once**（含 UMask）；
- Sub2API once/daily 也缺 UMask；
- 冻结 hardening 时应写**目标最小集合**，再列每个 unit 的 gap，而不是二值“完整/不完整”。

### P1-2：现网 models 快照已在生产路径上，却无 `backend`

`data/*/models_latest.json`（pinaic 等）已是 `schema_version: 1`，`last_full_result` 仅三字段，且**无 `backend`**。  
文档主张“models 尚在接入、先统一再首次发布”——对 New-API 仍 partially true，对 Sub2API **已经有站点落盘**。

**影响：** 若直接把目标 v1（四字段 + backend 硬校验）写进 writer，会与已有文件冲突；需要显式：

- 兼容读（缺 `backend` 时按路径/配置推断？还是 hard fail？），或  
- 一次性 migration / 重 bootstrap 策略。

“尚未发布所以随便改”已经不成立。

### P1-3：`spec/contracts/` 与仓库文档树未对齐

仓库已有 `docs/02 specs/`（空）、`docs/01 governance/`、`docs/03 designs/`，草案却放在 `docs/drafts/specs/`，目标路径又是根级 `spec/contracts/`。

未解释：

- 为何不用 `docs/02 specs/contracts/` 或仓库根 `contracts/`；
- draft → frozen 的路径与 review 回写约定；
- 与 skills `references/` 的废止关系（§15.2 提了，但没有单一入口）。

路径不定，后面所有 “CI 对 `spec/contracts` 做门禁” 都落不了地。

---

## 4. 契约内容的错漏与张力

### 4.1 Groups：Sub2API legacy hash 未定义到可复现（P1）

§6.4 详细冻结了 New-API canonical 字段与 golden cases，但 Sub2API groups hash 只出现在向量文件名 `groups-sub2api-legacy-hash.json`，**未写 canonicalize 规则**（全 object？哪些字段？id 类型 number/string？）。

现实现是 `canonicalize_groups` + 全量 group object 排序序列化。若不写进 contract，legacy 只“名义冻结”，golden 无法独立复现。

### 4.2 Groups v2 与 events 算法捆绑（P1，建议写死）

§6.2 正确指出：升到 groups v2 时 events 必须一并改成尾事件语义。  
但 §15.1 把决策写成“继续 legacy 或迁移 v2”的开放题，**没有默认推荐**。

评审建议（低熵默认）：

```text
短期：sub2api-groups-legacy 只读兼容 + newapi-groups-v1 冻结
中期：不主动迁 groups v2，除非出现跨 backend 统一消费强需求
若迁 v2：latest envelope + events 算法 + diff 形状同一版本号一次做完
```

开放决策可以保留，但应标 **Recommended default**，避免每次评审重开。

### 4.3 Models：非字符串 model id 的失败策略未拍板（P0）

§6.7：“必须明确拒绝或忽略……推荐整包失败……最终选择需在 schema 与 provider parser 中保持一致。”

这是**语义分叉点**，不能进 frozen 时仍 open：

- 忽略 → 上游漂移会变成“模型消失”；
- 整包失败 → 单坏行导致整组无成功更新。

建议冻结为：**parser 层 contract fail（该 group 记失败，不更新成功快照）**；不要静默 drop。并在 provider fixture 里固定一条 malformed id 样本。

### 4.4 Models events 与 groups events 的崩溃语义不一致未升级策略（P1）

- groups（目标/New-API）：尾事件去重，崩溃窗口可 repair latest。  
- models（§6.8）：允许 at-least-once；若改尾去重则升版本。

可以接受，但应在 manifest 里显式写：

```yaml
events_dedup:
  groups: tail_after_hash
  models: at_least_once  # v1
```

否则 CI/实现者容易“顺手统一”造成 models 消费者行为变化。

### 4.5 CLI 退出码表过粗（P1）

§7.1 用 0/1/2 三类是合理的，但矩阵里若干边界未钉死：

| 场景 | 文档 | 建议钉死 |
|------|------|----------|
| Sub2API 默认循环 + T-new 部分失败 | “正常停止 0” vs 批次失败 1 | 明确：循环模式 0=进程被正常停止；单次回合失败是否记 journal only |
| New-API 默认 groups 成功、T-new 部分失败仍 0 | 已写 | 与 models flag 路径“无 failed/skipped 才 0”对比加一句 rationale，避免读者以为矛盾 |
| preflight 部分检查失败 | “检查全过 0” | 缺省 1 还是 2？建议：检查未过 → 1；配置/锁 → 2 |
| lock 冲突 | 归入 2 | 与 daily 有界等待失败区分：等待超时是 1 还是 2 |

### 4.6 配置契约缺“总开关”语义（P1）

§10 列了 base URL / site id 等，但 models 相关开关（如 Sub2API 的 enable / T-new、bootstrap 门禁、daily enable）没有进入 manifest 示例。  
这些恰恰是**远端写边界**的配置面，应与 §8 交叉引用，否则 contract 只冻了“怎么写 token 名”，没冻“什么时候允许写”。

### 4.7 下游 `groups_all` / 未来 `models_all`（P2）

§15.2 提到 `models_all` reader，但现网已有 `docs/websites/table/groups_all.*`。  
建议在边界地图（§3）加一条下游只读聚合，标明：**聚合产物不是 contract 输入，必须经 per-site reader**。

### 4.8 Provider profile 与 BotCF（P2）

§9.1 对 BotCF 的处理正确（先独立探针）。可在范围头更醒目写：

```text
当前冻结对象：
  - sub2api-v1（已验证部署列表 + 日期）
  - torchai-rc21-v1（groups；models 写路径另附探针清单）
非冻结 / 未宣称：
  - BotCF models
  - “任意 New-API”
```

与 §1 范围段落合并，避免后文 profile 命名才第一次收紧。

### 4.9 Auth cache 与公共数据 API 边界（保留，可更短）

§6.9 合理。建议压成 5 条不变量 + “字段级 schema 不进 public bundle”，细节放到 provider/internal 文档，避免 public contract 膨胀。

---

## 5. 更简化、更优雅的形态

### 5.1 推荐拆分（核心建议）

不要继续维护“单文件百科”。评审通过后改为：

```text
docs/drafts/specs/                    # 仅草案期
  README.md                           # ≤150 行决策 brief（见下）
  review.md                           # 本文
  inventory-as-is.md                  # 可删的现状冲突与测试基线

# 冻结后（路径二选一，先定再写 CI）
spec/contracts/          或  docs/02-specs/contracts/
  manifest.yaml
  CHANGELOG.md
  storage/...
  cli/...
  ...
```

**Brief（未来 README）只保留：**

1. 目的与不包含  
2. 六类契约优先级表  
3. 三条硬规则：schema 权威、fail closed、不兼容必升版本  
4. 开放决策（legacy groups / models 非字符串 id / hardening UMask）及 **Recommended default**  
5. 指向 machine contract 与验收清单的链接  
6. 证据优先级

其余 JSON 示例、golden 规则、CLI 全矩阵、systemd 全量字段 → **只存在于 contract 文件**，brief 不复制。

### 5.2 每份契约文件的统一骨架

```yaml
# 例：storage/models-latest-v1.schema.json 旁附 models-v1.md 可选
id: models-storage-v1
status: draft | frozen
owners: [...]
applies_to: [sub2api, newapi]
invariants: [...]          # 人读，短
schema: ...                # 机读
vectors: [...]
non_goals: [...]
breaking_change_rules: [...]
```

人读文档只解释**为什么**和**不变量**；字段表尽量由 schema 生成，避免第三份手工表。

### 5.3 删减与合并对照

| 当前章节 | 建议 |
|----------|------|
| §1–2 目的/结论 | 保留为 brief 主体 |
| §3 边界地图 | 保留一张 ASCII；细节不展开 |
| §4 冲突 | 挪到 `inventory-as-is.md`，冻结后归档或删除 |
| §5 bundle 树 | 保留目录树 + manifest 最小字段 |
| §6 持久化 | 拆成 4 个 storage contract（groups-legacy / groups-newapi / models / auth-behavior） |
| §7 CLI | 两个 yaml 矩阵，brief 只留退出码表 |
| §8 远端写 | 独立 `safety/remote-mutation-v1.yaml`，两 backend 共享不变量 + profile 扩展 |
| §9 provider | 每 profile 目录；总则 ≤1 页 |
| §10 配置 | manifest 驱动；删除与 env.example 重复的散文 |
| §11–12 部署/安全 | 合并为 `deployment/systemd-v1.yaml` + `security-invariants.md` |
| §13 版本策略 | 升为 governance 一页，所有 contract 引用 |
| §14 CI | 独立 `contract-ci.md` 或直接写进 CI workflow 注释 + 测试入口 |
| §15–16 清单/Phase | 工程 backlog，**不进 frozen bundle** |
| §17 不冻结 | 保留在 brief |
| §18 基线 | 只放 inventory；注明日期；禁止当契约 |
| §19 证据 | 保留链接列表，缩短 |

目标体量：

- brief：**~100–150 行**  
- 每个 storage/cli contract：**schema + ≤80 行人读不变量**  
- 总“人读契约面”远小于当前 1070 行单文件，且可独立 diff。

### 5.4 表达层的小优化

1. **As-is / To-be 分栏**  
   冲突表保留 as-is；目标用单独 “Target” 列或单独文件，避免同一段落既描述现状又立法。

2. **示例 JSON 换成 vector 引用**  
   正文只写字段不变量；完整 JSON 放 `vectors/`，CI 直接跑。

3. **“建议 / 必须 / 禁止”用统一用词**  
   全文混用“建议统一”“应冻结”“必须”。冻结稿应用 RFC 语：  
   `MUST` / `MUST NOT` / `SHOULD` / `MAY`，中文可用「必须 / 禁止 / 应当 / 可以」。

4. **一张“冻结门禁”表取代 §15 长 checklist**  
   按契约 ID 勾选，而不是按叙事章节勾选。

5. **Owner 与 status 进 manifest**  
   现在只有技术字段；没有 owner/status/last_reviewed，长期一定漂。

### 5.5 不要过度简化的部分

以下若为了“优雅”而删掉，会回退到已踩过的坑：

- 远端写 unknown outcome 状态机  
- 分页 incomplete → 禁止 create  
- models 失败保留 last success  
- groups `A→B→A` 与半行 JSONL  
- managed identity 长度/碰撞向量  
- 未知 schema/site/backend hard fail  

简化的是**文档结构**，不是这些不变量。

---

## 6. 建议的默认决策（供评审勾选）

| ID | 议题 | 推荐默认 | 理由 |
|----|------|----------|------|
| D1 | Sub2API groups | **保持 legacy 只读兼容**，不主动 groups-v2 | 无跨 backend 统一消费硬需求时，迁移成本高于收益 |
| D2 | Models v1 | **立即统一**四字段 `last_full_result` + 顶层 `backend` | 已有落盘，越晚统一成本越高 |
| D3 | 缺 `backend` 的旧 models 文件 | **读路径：若缺省则用运行时 config.backend 填充并 SHOULD 回写；写路径始终带 backend** | 比 hard fail 更可运维；比永久缺字段更可校验 |
| D4 | 非字符串 model id | **该 group contract fail，不更新成功快照** | 避免静默当成模型删除 |
| D5 | Models 规范化 | **New-API 规则**（strip/去重/排序后落盘与 hash） | 文档已倾向；Sub2API 应对齐 writer |
| D6 | Events dedup | groups=tail；models=at-least-once（v1） | 保持现状语义，避免假统一 |
| D7 | systemd hardening | **以 newapi-monitor-once 为模板**（含 UMask）；所有 credential-bearing unit 对齐；legacy simple 标 deprecated | 单一目标，gap 可测 |
| D8 | Contract 路径 | **选定一个并写进 governance**：建议仓库根 `spec/contracts/`（短、适合 CI）或 `docs/02-specs/contracts/`（与现文档树一致） | 先定路径再写文件 |
| D9 | Brief vs inventory | **as-is 基线不进 frozen**；过期只改 inventory | 防止 §18 类漂移污染契约 |

---

## 7. 对原文档的具体修订清单（若暂不拆分）

若短期仍维持单文件，至少做这些最小修补：

1. **更新 §4.3 / §15 / §16 / §18** 至当前 CLI 已接线、`172 passed` 基线。  
2. **§4.4** 改成 unit×hardening 矩阵，不要二值“完整”。  
3. **§4.2 / §6.6** 标明 Sub2API 已有站点 `models_latest`，统一 v1 需要迁移策略（接 D3）。  
4. **§6.4** 补 Sub2API legacy canonicalize 规则，或明确“legacy hash 以代码+vector 为准、不声称跨语言可复现”。  
5. **§6.7** 关掉 open question，写入 D4/D5。  
6. **§5** 增加路径决策与 `docs/02 specs/` 关系一句。  
7. **§1 范围** 与 §9 profile 命名对齐，前置 “非通用 New-API”。  
8. **全文** 区分 MUST/SHOULD；目标语义与 as-is 用小标题分开。  
9. **§16 Phase 3** 删除已完成的 “接入 New-API CLI modes”，改为 “contract tests 覆盖副作用矩阵”。  
10. **增加决策表（本文 §6）** 到文首或文末，方便一次评审勾选。

---

## 8. 评审结论

| 维度 | 判断 |
|------|------|
| 技术方向 | **通过**（边界选择、fail closed、schema 权威、models 先统一） |
| 作为冻结权威稿 | **不通过**（过长、as-is/to-be 混写、关键基线过期、若干语义未拍板） |
| 作为评审输入 | **可用**，但应先按 §7 修订或按 §5 拆分后再二次签核 |
| 下一步 | ① 勾选 §6 默认决策 ② 更新过期事实 ③ 产出 brief + `manifest.yaml` 骨架 ④ 再写 JSON Schema，而不是继续加长本 README |

**一句话：** 把“立法”从“勘察笔记”里拆出来；冻结不变量，归档现状；用机器可读 contract 做唯一事实来源，而不是再写第二份 1000 行散文。
