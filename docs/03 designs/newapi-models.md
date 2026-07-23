# New-API Legacy Models Collection

> 状态：active implementation design
> 最近复核：2026-07-23
> 实现：`newapi_models.py`、`newapi_monitor.py`

## 1. 范围

模型链路当前围绕 TorchAI rc.21 形状实现。BotCF models 不在正式支持范围；其他 New-API 部署必须重新 preflight 和建立 provider profile。

管理面使用 session + `new-api-user`，模型面使用 Token secret 作为 Bearer API Key。两个 auth domain 必须分离。

## 2. 默认门禁

- cold 默认 groups 单轮零 Token mutation、零 models 请求；
- `--models-preflight` 禁止 create/update/delete；
- `--models-bootstrap` 是显式 cold mutation 入口；
- `--models-refresh` 必须已有 `bootstrap_completed_at`；
- incremental 默认关闭，且只有已 bootstrap 的 true T-new 可触发；
- daily template 默认只安装，不 enable；
- 第一版要求至少一个人工 seed Token，零 Token preflight 失败。

## 3. CLI 流程

```bash
.venv/bin/python newapi_monitor.py --env-file sites/torchai.env --models-preflight
.venv/bin/python newapi_monitor.py --env-file sites/torchai.env --models-bootstrap
.venv/bin/python newapi_monitor.py --env-file sites/torchai.env --models-refresh
```

### Preflight

```text
session/user_id -> groups non-empty -> list all Tokens
-> hydrate candidate secrets -> seed secret -> /v1/models envelope
```

Secret hydration 可能使用 provider 的 POST read endpoint，但必须无资源 mutation、无 secret rotation，且 secret 只存在于内存。

### Bootstrap

```text
bounded lock -> mandatory preflight -> re-GET groups
-> list + hydrate -> ensure coverage for all groups
-> refresh each group -> per-group checkpoint
-> full success sets bootstrap_completed_at
```

### Refresh

在取锁前和取锁后都检查 bootstrap，防止等待期间状态变化。随后 re-GET groups、ensure 全量覆盖、刷新所有组。failed 或 skipped 均返回 1。

### T-new

默认 groups 成功后，仅在 incremental 开启、已 bootstrap、上一份 groups latest 通过 version/site/backend/hash 校验时计算真正新增组。Ensure 只接收 refresh set，不能借新增组顺便修复所有旧组。T-new partial 不改变 groups 退出码。

## 4. Token inventory 与 coverage

Token list 先分页取全，再按 id 稳定 merge。`has_more`、total、短页、重复页无进展和 max pages 共同决定 completeness；无法证明完整时 create=0。

Token secret 在计算 Missing 前 hydrate。secret timeout/5xx/contract 不明是 `coverage_unknown`，不是 missing。

Inventory-suitable 要求：

- group 精确匹配；
- secret 本轮回读成功；
- enabled、未过期、unlimited quota；
- model limits 关闭；
- IP restriction 为空；
- 其他 profile 限制不缩小 models inventory。

受限 Token 返回的模型子集不能写成完整成功。

## 5. Managed Token reconcile

```text
managed name = "newapi-monitor:g:" + normalized group name
```

只有精确 managed name 的 Token 可以修复。用户 Token 永不 update/delete。

Reconcile：已有 suitable 则复用；managed unsuitable 时按 profile 修复；无 managed 时 create 一次；mutation 后 re-list + hydrate + verify。POST outcome 不确定时通过 re-list 认领，禁止盲目二次 create。

长 group name 的 UTF-8 命名规则尚未由 provider limit vectors 冻结，因此相关 capability 必须 fail closed。

## 6. Models 采集与状态

`/v1/models` 的合法空 `data=[]` 表示成功空列表。模型 id 经过 trim、去重、排序后用于落盘和 hash。当前 parser 会拒绝缺 id/复杂类型，但仍会把部分非字符串 scalar 转成字符串并忽略空 id；目标 contract 更严格，要求每个 id 原生为非空 string，否则整组失败。

每组成功/失败状态正交：

- success 更新 models/hash/key_id/success/attempt，清 error/retry；
- failure 保留最后成功值，只更新 attempt/error/retry；
- `/v1/models` 401/403 尝试下一个 suitable Token，禁止 session login；
- per-group checkpoint 保护长批次已完成结果；
- full result 是 `{target,ok,failed,skipped}`，只有全成功更新 full success/bootstrap。

当前 models latest/event 尚缺目标 `backend`，event 也缺 `schema_version`；这是 legacy gap，不得当作 frozen profile。

## 7. 调度与锁

`newapi-models-daily@.timer` 在上海 00:00 运行、0～300s 抖动、Persistent。Service 执行 `--models-refresh`，timeout 600s。

```bash
./install_newapi_service.sh --enable-models torchai
```

Models 与 groups 共用同站 lock；full 有界等待，groups 非阻塞。当前 installer 未强制检查 bootstrap/provider capability，operator 必须先成功 preflight/bootstrap。

## 8. 已知契约与部署差距

| 项 | 当前状态 | 目标 |
|---|---|---|
| model id | 部分 scalar 转 string，空 id 忽略 | 原生 non-empty string，整组 fail |
| models backend | latest/event 缺失 | 必填 `backend=newapi` |
| event schema_version | 缺失 | 必填 v1 |
| provider mutation | 实现存在，profile evidence 未冻结 | 探针/fixtures/capability 完整后冻结 |
| daily hardening | 仅 Type/paths/timeout | 对齐 UMask/Nice/NoNewPrivileges/PrivateTmp/ProtectSystem/ReadWritePaths |
| installer gate | 直接 enable | 强制 bootstrap/provider capability |

历史详细需求、评审和 TorchAI 探针保留于 `docs/drafts/newapi/get-models/`。
