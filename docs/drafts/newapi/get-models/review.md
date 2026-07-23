# New-API get-models 需求评审

评审对象：本目录的 `README.md`、`requirements.md`、`touch_ai.md`，并对照已运行的 `newapi_monitor.py`、`monitor_storage.py`、Sub2API 的 `sub2api_models.py`、CLI 编排、systemd 单元与专项测试。

评审日期：2026-07-22。

## 0. 采纳状态（2026-07-22 回写）

| 项 | 状态 |
|----|------|
| P0-1 … P0-5 | **全部采纳**，已写入收敛后的 `requirements.md` |
| P1-1 … P1-7 | **全部采纳** |
| 文档收敛为唯一权威 requirements（不另写厚 design） | **采纳** |
| 零 Token 自动首创 | **不采纳**（第一版：人工 seed Token） |
| 编码 | **P0 探针契约勾选前禁止 create 合并** |

下文为评审原文，保留备查。

---

## 1. 结论

总体方向正确，但当前 `requirements.md` 还不适合直接进入编码。

已经梳理得比较好的部分包括：管理面 Session 与模型面 API Key 分域、默认不远端写、显式 preflight/bootstrap、分页不完整时禁止 create、未知 POST 结果先 re-list、失败保留旧模型、逐组 checkpoint、全量与增量分频、同站共锁，以及不删除用户 Token。这些都是 Sub2API 已实现并由测试覆盖的有效经验。

主要问题不在总体架构，而在 New-API 特有契约尚未闭合。`touch_ai.md` 证明了 torchai 的单站正常路径，但没有证明分页、Token 状态/过期语义、受限 Token、零 Token 冷启动、managed Token 被禁用或改绑等异常路径。当前需求直接套用了 Sub2API 的 `usable_keys` 和 reconcile 语义，会在 New-API 的“列表只返回脱敏 Key”这一差异上产生重复 Token，且不能保证采集到“分组完整模型列表”。

建议结论：**保留现有调度和最小状态模型，先修完 P0 项；不需要再写一份同等厚度的 design/data-model 才能开工。**

验证基线：`tests.test_sub2api_models`、`tests.test_models_cli`、`tests.test_newapi_monitor` 共 64 个测试通过。这只能证明已有 Sub2API 机制和 New-API groups 基线可用，不能替代下列 New-API Token 协议测试。

## 2. 阻断问题

### P0-1：在计算 Missing 前没有把脱敏 Token 补成可判定状态

`requirements.md:118-133` 定义 usable 必须能取得明文 secret，但 `requirements.md:251-255` 又让 `ensure_coverage` 在 list 后直接计算 Missing；直到 `requirements.md:301` 的模型刷新阶段才按 Token 获取 secret。`touch_ai.md:338-364` 已明确列表中的 `key` 是掩码，不能当 secret。

照此实现时，原有业务 Token 都会因“没有 secret”被视为不 usable，随后为已覆盖分组创建重复 managed Token。

定稿要求：

1. `list_tokens_all` 成功且分页完整后，先对与当前分组相关、状态可能可用的 Token 执行 `get_token_secret(id)`，形成仅存在于本轮内存的 hydrated token 列表，再计算 coverage/Missing。
2. 某个已有 Token 的 secret 获取出现 timeout、5xx、分页漂移或契约不明时，该组应记为 `coverage_unknown` 并 **fail closed，不 create**；不能把“暂时无法证明可用”解释成“不存在”。
3. create 后必须 re-list、重新取该 Token 的 secret，再把 hydrated `tokens_after` 交给 models 流程。
4. 增加测试：已有脱敏业务 Token 可回读时不 create；secret 临时失败时不 create；create 后 secret 可立即用于 models。

### P0-2：当前 usable 定义不能保证“分组完整模型列表”

`requirements.md:126-133` 只过滤分组、secret、disabled 和 expired；`requirements.md:155-157` 对第一个成功 Token 就停止；`requirements.md:381` 还规定业务名 Token 已覆盖时不创建 managed Token。但 New-API Token 本身有 `model_limits_enabled/model_limits`、`allow_ips` 等限制字段，创建载荷也显式设置了这些字段（`requirements.md:230-240`）。

因此，一个开启模型白名单的业务 Token 即使 `/v1/models` 返回 200，也只会得到子集。当前算法会把这个子集错误标成该分组的完整模型列表。同组多个受限 Token 时，“取第一个成功”也不等于完整，更不能无依据地求并集。

定稿要求：把目标从模糊的 usable Token 改成 **inventory-suitable Token**，至少要求已绑定目标组、secret 可回读、启用、未过期、`model_limits_enabled=false`，并明确 IP/其他访问限制的处理。字段缺失时不得默认代表“不受限”，应由站点 preflight 确认契约。只有 inventory-suitable 业务 Token 才能阻止 managed Token 创建。

### P0-3：managed Token 的异常状态没有收敛路径

`requirements.md:227-245` 只处理 managed Token 未绑定或已绑定本组。以下状态没有可执行结果：

- 同名 managed Token 已绑定本组，但被禁用、已过期、开启模型限制或 secret 无法取得；步骤 4 禁止再 create，却也不修复。
- 同名 managed Token 被人工改绑到另一非空分组；它既不属于步骤 3，也不属于步骤 4，步骤 5 再 POST 同名可能冲突或重复。
- 分组名较长时，`newapi-monitor:g:<group>` 是否超过 Token name 长度上限未验证。

这与 `requirements.md:377-381` 宣称的“禁用/过期不算 coverage”“改绑后可补”“全量 refresh 恢复覆盖”相冲突。

定稿要求：只对精确识别的 managed Token 定义一种修复策略，并通过 torchai 探针确认 PUT 载荷。最低闭环是允许重绑、重新启用、恢复无限期和关闭模型限制；如果上游不允许修复，则改用带短 hash/代次的确定性名称，并承认不能保证同名 managed `<=1`。用户 Token 仍保持永不修改。

### P0-4：零 Token 站点会被 preflight 永久阻塞，refresh 也缺少首次启用门禁

`requirements.md:92-93` 要求 preflight 使用至少一个既有 Token 验证 secret 和 models；`requirements.md:176` 又要求 bootstrap 必须先通过 preflight。`touch_ai.md` 恰好已有 `KV` Token，所以没有暴露问题。一个全新账号若 Token 数为 0，将既不能通过 preflight，也不允许 bootstrap 创建第一个 Token。

此外，`requirements.md:287` 只对 bootstrap 强制 preflight，`requirements.md:542-547` 允许 daily refresh 直接进入 ensure。手工执行 `--models-refresh` 可能绕过首次启用门禁并写远端。

第一版建议选择最简单、最诚实的契约：**运维必须先人工准备一个未受限的 seed Token**，preflight 才能通过；不实现“零 Token 自动首创”。同时 `--models-refresh` 必须要求已有 `bootstrap_completed_at`，否则退出非 0 且零 create/零 models 写盘。若希望支持零 Token 自动化，就必须单独承认 preflight 无法在零写条件下验证 `/key` 能力，并设计 create 后失败的处置，不能两种语义并存。

### P0-5：探针缺少 create 安全所需的原始 Token 契约

`touch_ai.md:154-174` 只把 `data.items` 映射成少数字段，没有保存脱敏后的完整 envelope。需求因此在 `requirements.md:209-218` 假定 `total/page/size/has_more`，在 `requirements.md:130` 又把 status 规则留给不存在的“探针与实现表”。New-API 常见的数值 `status`、`expired_time=-1`、分页参数名和业务失败 envelope 都没有定稿。

在任何自动 create 前，应补一次只读、脱敏探针并写成明确契约：

- token list 的完整 envelope、实际分页参数和页码起点；
- `status` 每个值的含义，`expired_time` 的单位和 `-1` 语义；
- `model_limits_enabled`、`allow_ips` 等 inventory-suitable 判定字段；
- GET/POST/PUT 在 HTTP 200 + `success=false` 时的错误语义；
- PUT 能否修复 managed Token，以及 Token name 长度/唯一性约束。

分页循环还应继承现有 Sub2API 实现中的 `max_pages` 上限，并增加重复页/无进展保护；无法证明取全时继续保持零 create。

## 3. 高优先级问题

### P1-1：不要改变已上线的默认 CLI 语义

`requirements.md:431-441` 新增 `--once`，但当前 `newapi_monitor.py` 的默认行为本来就是一次有界采集，生产 unit 也只传 `--env-file`。为对齐 Sub2API 而增加一个无价值的 `--once` 会扩大兼容面。

建议保留当前默认命令；只新增三个互斥的 models flags。无 models flag时继续执行 groups 单轮采集。相应地，文档中的 T-new 应描述为“默认 groups 轮次”，而不是绑定 `--once` 名称。

### P1-2：空 groups 契约自相矛盾

`requirements.md:89` 同时写“非空对象”和“允许空对象”。当前 `newapi_monitor.py` 与 `monitor_storage.normalize_groups_dict` 都明确把空对象判为 contract failure，现有测试也锁定这一行为。

建议第一版保持现网契约：空 groups 失败且不覆盖快照。除非实站证明空组是合法业务状态，否则不要为 models 功能顺带改变 groups 基线。

### P1-3：管理面认证恢复不能只看 HTTP 401

`requirements.md:315` 只写 401 重登一次，而现有 New-API groups 客户端还会识别 HTTP 200/其他状态下的 `success=false`、`未提供 New-Api-User`、`请先登录` 等业务认证失败。Token CRUD/get-secret 必须复用同一个已认证 `requests.Session`、`user_id` 和一次性恢复预算，不能在 `newapi_models.py` 另建一套登录状态机。

建议让 `newapi_monitor.py` 持有认证与通用 management request，`newapi_models.py` 只接收该客户端或注入的 list/create/update/get-secret 函数。`/v1/models` 仍严格禁止触发 Session 重登。

### P1-4：deadline 中未开始的组必须计入全量失败

`requirements.md:309` 规定临近 deadline 停止开新组，但 `requirements.md:403-408` 只按 `failed_count > 0` 决定退出码，未说明 skipped 计数。全量任务可能处理前几组后因 deadline 停止，却被记成成功。

要求 `target = ok + failed + skipped`，`skipped > 0` 时不更新 `last_full_success_at`，退出非 0；可以在 `last_full_result` 增加 `skipped`，或把 skipped 合并进 failed，但只能选一种固定 schema。

### P1-5：模型 envelope 与规范化仍过于宽松

`requirements.md:326` 的“`data: [{id}]` 等 unwrap”不足以区分合法空列表和错误 envelope，也没有定义重复 id、空 id、非字符串 id和顺序抖动。

建议只接受已探针确认的 envelope，加一组明确兼容形状；模型 id 做 `str.strip`、去空、去重并稳定排序后再存储和计算 hash。合法 `data: []` 必须成功，缺少可识别列表字段必须 contract 失败。

### P1-6：重试规则应落到可验收的状态转换

`requirements.md:186` 写“429 尊重 Retry-After”，但状态模型和测试没有要求保存实际 Retry-After；timeout/5xx 的重试次数、单组与整批关系也留给实现决定。

第一版无需复杂退避状态机：T-new 每个新增组最多尝试一次；429 使用响应头计算 `next_retry_at`；timeout/5xx 本轮该组失败，交给 daily 或显式 refresh；daily 本身不做逐组循环重试。这样比照搬 Sub2API 的启发式 cooldown 更容易验证。

### P1-7：T-new 的 ensure 范围没有锁死

`requirements.md:270-275` 先算了真正新增组 `refresh_set`，但调用 `ensure_coverage(...)` 时没有写明传 `refresh_set` 还是全部当前 groups。若传全部 groups，一次新组出现就会顺带为所有被人工删除/禁用 Token 的旧组创建或修复 managed Token，违背 `requirements.md:377` 所说“只在全量 ensure 路径恢复覆盖”。

应与已运行的 Sub2API 编排一致：T-new 只对 `refresh_set` ensure 和刷新；bootstrap/daily 才对全量 G ensure。

## 4. 简洁性评估

### 做得简洁的部分

- 继续使用现有 timer + oneshot、每站数据目录和同站锁，不增加常驻调度器或数据库。
- 只新增 `models_latest.json` 与 `models_events.jsonl`，不增加 `keys_index` 和 secret store。
- groups 是分组元数据唯一权威，models 文件只保存模型事实。
- 不引入跨协议继承体系，不删除或改写用户 Token。

### 过度工程化与重复

`requirements.md` 已接近 600 行，同时包含需求、函数级详细设计、数据模型、systemd 配置、测试计划、实施阶段、端到端流程、DoD 和决策摘要。以下内容重复表达同一决策：

- §0、§2、§4.6、§15、§16、§17、§19 多次重复默认关闭、触发器和完整性目标；
- §5.4/§5.5 与 §16 重复流程；
- §6 与计划中的后续 `data-model.md` 重复；
- §9 与计划中的 timer 文档重复；
- §14 属于实施 checklist，不是需求。

另外，“字段以站探针为准，可 env 覆盖”会把未经证实的差异变成大量配置。第一版只服务已探针的 torchai，应固定已验证载荷；botcf 通过独立 preflight 后，只有真实差异再增加最小配置。不要预先把每个 Token 字段做成 env。

建议将 `requirements.md` 收敛到约 200～300 行，保留五部分：

1. 目标、非目标和安全不变量；
2. torchai 已验证的 New-API 协议契约；
3. preflight/bootstrap/default-groups/daily 四条流程及门禁；
4. models 状态语义和失败矩阵，公共部分引用 Sub2API 已定稿 schema；
5. 可执行的验收与测试。

函数签名、模块边界、systemd 完整单元和落地阶段分别放进薄 design、timer 示例和 checklist。若不打算维护这些独立文档，就直接让收敛后的 requirements 成为唯一权威，不要再生成内容重复的 design/data-model。

## 5. README 与探针文档

`README.md` 作为目录入口足够简洁，但应在本评审采纳后增加 `review.md` 链接，并把状态写成“需求评审中，P0 未闭合”，避免当前“需求草案已写”被理解为可直接开发。还应说明 New-API groups 入口默认就是单轮执行，没有 `--once`。

`touch_ai.md` 适合作为 torchai 正常路径的证据，不应宣称适用于“任何 New-API 二次部署”。建议改成“仅 torchai rc.21 实测，其他站点需重新 preflight”。其中“一组且只有一个 Key”是一次操作结果，不是生产不变量；正式需求已正确升级为允许多 Token。文档当前已使用凭据占位符，`README.md` 中“凭据仅作历史样例”和 `requirements.md:578` 暗示仍含明文账号密码，建议改成“凭据均已脱敏，禁止填入真实值提交”。

## 6. 推荐定稿顺序

1. 补齐只读 token envelope/status/expiry/limits 探针，确认 PUT 修复能力和名称限制。
2. 定稿 inventory-suitable 与 secret hydration；先解决重复创建和模型子集问题。
3. 选择零 Token 策略，并锁死 refresh 必须在 bootstrap 后运行。
4. 修正 CLI/空 groups/skip 计数等与现网不一致处。
5. 删除重复章节，形成唯一权威的短 requirements。
6. 先实现只读 preflight，再实现 managed reconcile 故障测试，最后接 models store、daily timer 和 T-new。

完成这些修订后，方案可以直接沿用 Sub2API 已验证的存储与调度模式，同时把 New-API 特有风险限制在一个很小的 Token 适配层内。
