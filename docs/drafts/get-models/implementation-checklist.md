# 实现检查清单（定稿草案）

对照 `design.md` / `data-model.md` / `review.md` 采纳项。

## Phase 0 — 方案

- [x] 三站探针验证 Key + models
- [x] 初版方案文档
- [x] 评审 `review.md` 并回写定稿

## Phase 1 — 只读 preflight + 存储骨架

- [ ] `list_keys_all` 分页；无法取全 → fail closed（禁 create）
- [ ] `norm_id` 统一
- [ ] `--models-preflight`（groups / paging / secret 可回读 / models envelope）
- [ ] `ModelsStore` 最小 schema（成功/尝试正交；`models: null` vs `[]`）
- [ ] 逐组 checkpoint 原子写
- [ ] secret 扫描（日志与落盘）
- [ ] 单测：分页、norm_id、null vs []

**验收：** preflight 对 littleapi 通过；不创建任何 Key。

## Phase 2 — 幂等 reconcile

- [ ] managed 名 `sub2api-monitor:g:<id>`
- [ ] `usable_keys` / pick 算法（无 secret / disabled 不算 coverage）
- [ ] POST 未知结果 → list 认领，禁止盲重 POST
- [ ] bind 后强制 `list_keys_all` → 返回 `keys_after`
- [ ] mock：超时已创建、bind 失败、响应丢失、重启 → managed Key ≤1
- [ ] 模块拆分 `sub2api_models.py`

**验收：** 故障注入后二次 ensure `created=0` 且无游离堆积策略可解释。

## Phase 3 — bootstrap / refresh

- [ ] `--models-bootstrap`：preflight → ensure → 全组 models → `bootstrap_completed_at`
- [ ] `--models-refresh`：强制 re-GET groups；有界等锁；进程内有限重试
- [ ] 部分失败 exit≠0；`last_full_attempt_at` / `last_full_success_at` / `last_full_result`
- [ ] models 401 不 JWT login；候选 Key 回退
- [ ] 单站 bootstrap×2：`created=0`；models 文件稳定

## Phase 4 — T-new（默认关）

- [ ] `MONITOR_MODELS_INCREMENTAL_ENABLE` 默认 `0`
- [ ] 无 `bootstrap_completed_at` 时 `--once` 零 models/create
- [ ] refresh_set **仅** 本轮真正 `added`（非「缺快照全集」）
- [ ] `next_retry_at` / contract 冷却至 daily
- [ ] groups 成功 + 增量部分失败 → exit 0

**验收：** 部署新代码默认不写远端；灰度站新组出现后增量一条 models。

## Phase 5 — systemd 日更 + 全站

- [ ] `sub2api-models-daily@`：`OnCalendar=*-*-* 00:00:00 Asia/Shanghai` + `RandomizedDelaySec=300`
- [ ] `TimeoutStartSec=600`；锁碰撞验收
- [ ] install 脚本：不默认对未 bootstrap 站强行开启写路径
- [x] 站表：aiapibank, aresaicode, hubway, iaiguo, klinkw, littleapi, pinaic, yybb（**aijws 已剔除**，env → `aijws.env.disabled`）

## Phase 6 — 汇总与文档

- [ ] `models_all` 合并（join groups_latest）
- [ ] 根 README 更新
- [ ] 探针脚本注明生产用 monitor CLI；避免 0644 key_preview

## 安全门禁

- [ ] 无真实密码/完整 sk 进库
- [ ] models_latest/events/table 无 api_key
- [ ] 无删除远端 Key 代码路径
- [ ] 默认关闭增量

## 明确不做

- [ ] 每 5 分钟全量 models
- [ ] keys_index / 默认 keys_secrets
- [ ] New-API 统一
- [ ] 自动删 Key
- [ ] Python 侧 daily 时区权威

## Definition of Done

1. 目标站 preflight + bootstrap 后，active 组均有成功 models 或可解释 `last_error`，且失败不抹掉旧成功列表。  
2. 二次 bootstrap `created=0`；分页不完整时从不 create。  
3. 默认发布不产生远端写；仅显式 enable 站会 create。  
4. 模型仅 T-new（真新组）/ daily / 显式 bootstrap；无缺快照每 5 分钟死循环。  
5. daily 与 groups 并发下不因单次非阻塞锁失败而「静默整天不跑」（有界等待+有限重试可验证）。  
6. 单测 + 既有 monitor 回归通过。  
