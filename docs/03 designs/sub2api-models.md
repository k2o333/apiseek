# Sub2API Models Collection

> 状态：active implementation design
> 最近复核：2026-07-23
> 实现：`sub2api_models.py`、`sub2api_monitor.py`

## 1. 目标与安全默认值

分组继续高频只读；模型列表低频采集。模型链路可能创建/绑定 API Key，因此 cold 默认关闭：

- 默认 groups loop/once 不创建 Key、不请求 models；
- preflight 只读，不能 create/bind/delete；
- bootstrap 是显式 cold mutation 入口；
- daily refresh 由独立 timer 驱动，installer 默认不 enable；
- T-new 默认关闭，仅在已 bootstrap 且 incremental env 开启时处理真正新增组。

适用前提是该站 Key 列表分页可证明完整，并且能回读完整 secret。否则 preflight 失败且禁止 create。

## 2. CLI 与流程

```bash
.venv/bin/python sub2api_monitor.py --env-file sites/<id>.env --models-preflight
.venv/bin/python sub2api_monitor.py --env-file sites/<id>.env --models-bootstrap
.venv/bin/python sub2api_monitor.py --env-file sites/<id>.env --models-refresh
```

### Preflight

```text
auth -> GET groups -> list all Keys -> prove paging complete
     -> find readable usable secret -> GET /v1/models -> print summary
```

它可以更新 auth cache，但不写 models 文件，不做 Key mutation。

### Bootstrap

```text
bounded lock wait -> mandatory preflight -> re-GET groups
-> persist groups -> list complete Key inventory
-> ensure managed coverage -> models per group -> checkpoint
-> full success sets bootstrap_completed_at
```

### Refresh

当前实现 re-GET groups、ensure 全量 coverage、逐组 refresh，并以 partial failure 返回 1。正式 CLI contract 要求 refresh 在无 bootstrap 时提前拒绝；Sub2API 当前尚未补齐这个代码 gate，因此 operator 必须继续遵守“先 bootstrap 后 enable daily”。

### T-new

groups 成功后，只有同时满足以下条件才执行：

- `MONITOR_MODELS_INCREMENTAL_ENABLE=1`；
- `models_latest.bootstrap_completed_at` 存在；
- 上一份 groups latest 可用；
- 当前 group 是真正新增；
- `next_retry_at` 已到。

T-new 只 ensure 新增集合；models 失败不反转本轮 groups 成功。

## 3. Key coverage

```text
norm_id(x) = str(x).strip()
managed name = "sub2api-monitor:g:" + norm_id(group_id)
```

Coverage 要求 Key 已绑定目标组、可用且能取得 secret。分页 incomplete 时整批 create=0。

Reconcile 顺序：

1. 已有 usable Key：复用，不写；
2. 存在同名未绑定 managed Key：bind 后 re-list；
3. 存在同名已绑定 managed Key：不重复 create；
4. 无 managed：create 一次，再 bind；
5. create outcome 不确定：re-list 认领，禁止盲目第二次 POST；
6. mutation 后 re-list，只有可用状态被观察到才用于 models。

实现永不自动 delete。用户已有 Key 不因模型采集而被删除或改名。

## 4. Auth domain

```text
JWT access/refresh -> groups 和 Key management
API Key Bearer     -> /v1/models
```

Key 401/403 是 `key_auth`，只尝试其他候选 Key，禁止触发 JWT login/refresh。management auth recovery 预算不随组数或 Key 数量增长。

## 5. 持久化

```text
data/<id>/models_latest.json
data/<id>/models_events.jsonl
```

每组保存 `key_id/models/content_hash/last_success_at/last_attempt_at/last_error/next_retry_at/source`。成功和尝试正交：失败只更新 attempt/error/retry，保留最后成功 models/hash/key_id。

`models=null` 表示从未成功，`models=[]` 表示 provider 成功返回空列表。每组成功后立即 checkpoint，避免长任务末尾失败丢掉已完成结果。

## 6. 调度与锁

`sub2api-models-daily@.timer`：上海 00:00、0～300s 抖动、Persistent。Service 运行 `--models-refresh`，超时 600s。

```bash
./install_service.sh --enable-models <id>
```

Installer 会安装 template，但默认不 enable models。Groups 与 models 共用同站 lock：daily 有界等待；groups 非阻塞，抢锁时可以跳过一轮。

## 7. 当前实现与目标 contract 的差距

当前生产兼容行为必须如实保留，直至显式 migration：

| 项 | 当前实现 | 目标 `storage/models-v1` |
|---|---|---|
| 顶层 backend | 缺失 | 必须 `backend=sub2api` |
| full result | `{target,ok,failed}`；deadline 合入 failed | `{target,ok,failed,skipped}` |
| daily source | `daily` | `refresh` |
| model parser | 接受多种 envelope，可能跳过 malformed row | strict object row + string id，整组失败 |
| normalization | hash 侧规范化，落盘列表未完全统一 | trim/去重/排序同时用于落盘和 hash |
| cold refresh gate | 未在入口强制 bootstrap | 无 bootstrap 时零 mutation/零 models |
| event envelope | 缺 backend/schema_version | 两者必填 |
| unit hardening | daily 缺 `UMask` | 全 credential unit 对齐 hardening |

禁止用 read-only reader 无条件回写这些 legacy 文件。目标 migration 必须幂等、可回滚且不生成伪 `models_changed` event。

历史需求、评审和探针保留于 `docs/drafts/get-models/`；后续工程判断以正式 contract 和本文差距表为入口。
