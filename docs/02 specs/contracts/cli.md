# CLI Contracts

> Publication：formal
> Status：draft
> Contract IDs：`cli/sub2api-v1`、`cli/newapi-legacy-v1`

## 1. 退出码

| Code | 目标含义 |
|---:|---|
| 0 | 请求 mode 完成，或该 mode 明确定义的非致命降级 |
| 1 | provider/auth/contract/运行批次失败、preflight 未通过、full 有 failed/skipped |
| 2 | 参数、配置、文件权限或锁等本地前置条件失败 |

锁不可用统一返回 2，表示本轮未取得执行资格。systemd 仍将任何非零视为失败。精确日志文本不冻结；mode、副作用、error class 和退出码冻结。

## 2. Sub2API mode

| Mode | 远端读取 | 远端 mutation | 本地业务写 | 锁 | 退出语义 |
|---|---|---|---|---|---|
| `--validate` | 无 | 禁止 | 可创建/验证 data dir；禁业务 snapshot | 无业务锁 | valid=0；config=2 |
| 默认无 flag | auth/groups loop | cold 禁止；已 bootstrap 且开 incremental 时仅 T-new | token/groups，可选 models | 非阻塞，进程持有 | 正常 signal 停止=0 |
| `--once` | auth/groups，有界 transient retry | 同默认门禁 | token/groups，可选 models | 非阻塞 | round success=0；fail=1 |
| `--models-preflight` | groups/keys/models | 禁止 create/bind/delete | auth 可更新；models 文件禁止 | 非阻塞 | pass=0；fail=1；lock=2 |
| `--models-bootstrap` | preflight + re-GET + keys/models | 可 create/bind managed Key | groups/models/auth | 有界等待 | 全目标成功=0；partial=1 |
| `--models-refresh` | groups/keys/models | 已 bootstrap 后可 ensure managed Key | groups/models/auth | 有界等待 | failed/skipped=1；success=0 |

参数规则：

- `--env-file` 必须提供；
- `--once` 禁止与 models flags 组合；
- 三个 models flags 必须互斥；
- `--once-attempts >= 1`；
- 参数冲突必须在 provider call 和业务写之前返回 2；
- `--validate` 与其他 mode 的组合必须在冻结前明确互斥，不能静默覆盖。

目标 contract 要求 cold `--models-refresh` 在 list/create/bind/models 之前返回 1，且不创建 models 文件。当前 Sub2API 实现尚未完成该 gate，不能冻结这一项。

## 3. New-API legacy mode

| Mode | 远端读取 | 远端 mutation | 本地业务写 | 锁 | 退出语义 |
|---|---|---|---|---|---|
| `--validate` | 无 | 禁止 | 可创建/验证 data dir；禁业务 snapshot | 无业务锁 | valid=0；config=2 |
| 默认无 flag | session/groups 单轮 | cold 禁止；bootstrap+incremental 时仅 true T-new | auth/groups，可选 models | 非阻塞 | groups success=0；T-new partial=0 |
| `--models-preflight` | groups/token list/secret/models | 禁止 create/update/delete | auth 可更新；models 文件禁止 | 非阻塞 | pass=0；fail=1；lock=2 |
| `--models-bootstrap` | preflight + groups/token/models | 可 create/repair exact managed Token | auth/groups/models | 有界等待 | failed/skipped=1；success=0 |
| `--models-refresh` | groups/token/models | 已 bootstrap 后可 ensure managed Token | auth/groups/models | 有界等待 | failed/skipped=1；success=0 |

New-API 默认入口本身是单轮，禁止为了表面对齐增加 `--once`。三种 models flag 必须由 parser 互斥。

### Preflight 的只读语义

“只读”指禁止远端 mutation，不要求 HTTP method 全部为 GET。provider 可以用 POST `/api/token/{id}/key` 读取 secret，但 profile 必须证明它不创建、不轮换、不修改状态，且 secret 不落盘、不进日志。

### T-new 非致命

默认 mode 的主职责是 groups。groups 成功后，增量 models 失败：

- groups latest/event 仍有效；
- models entry 记录失败并保留旧成功；
- 默认 mode 返回 0；
- 显式 bootstrap/refresh 以 models 为主，相同失败返回 1。

### Auth domain

- management auth 最多恢复一次；
- `/v1/models` 401/403 属于 API Key domain，禁止触发 Session 重登；
- cold refresh 必须在 token list/create/update/models 前返回 1。

## 4. Stdout

preflight 和 full summary 当前按 human-readable JSON 使用。若被自动化消费，冻结前必须二选一：

1. 明确标记 human-only，不保证字段稳定；或
2. 发布带 `schema_version/backend/site_id/phase` 的 `cli-output-v1`。

禁止自动化依赖 argparse help 或日志措辞。

## 5. 冻结所需 tests

- 每个 mode 的 remote read/mutation call 集合；
- 每个 mode 的本地文件集合；
- 参数冲突零副作用返回 2；
- preflight fail=1、lock=2；
- cold refresh gate；
- cold 默认零 mutation、零 models request；
- T-new 只处理 true added group；
- default T-new partial=0、full partial=1；
- models 401 不触发 management login；
- Sub2API loop/signal 与 once 退出语义。
