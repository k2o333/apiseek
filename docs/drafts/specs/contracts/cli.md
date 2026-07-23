# CLI Contracts

> 状态：draft  
> Contract IDs：`cli/sub2api-v1`、`cli/newapi-legacy-v1`

## 1. 退出码

| Code | 规范含义 |
|---:|---|
| 0 | 请求 mode 完成，或该 mode 明确定义的非致命降级 |
| 1 | provider/auth/contract/运行批次失败、preflight 检查未通过、full 有 failed/skipped |
| 2 | 参数、配置、文件权限、锁不可用等本地前置条件失败 |

所有锁不可用，包括非阻塞冲突和有界等待耗尽，统一返回 2。这表示本轮没有取得执行资格，不表示 provider 运行失败。systemd 仍将任何非零视为失败。

精确日志/错误文本不冻结；mode、副作用、error class 和退出码冻结。

## 2. Sub2API mode 矩阵

| Mode | 远端读取 | 远端 mutation | 本地业务写 | 锁 | 退出语义 |
|---|---|---|---|---|---|
| `--validate` | 无 | 禁止 | 可以创建/验证 DATA_DIR；禁止业务 snapshot | 无业务锁 | valid=0；config=2 |
| 默认无 flag | auth/groups loop | cold 默认禁止；已 bootstrap + incremental 开关只允许 T-new | token/groups，可选 models | 非阻塞，进程持有 | SIGTERM/SIGINT 正常停止=0 |
| `--once` | auth/groups，有界 transient retry | 与默认门禁相同 | token/groups，可选 models | 非阻塞 | round success=0；round fail=1 |
| `--models-preflight` | groups/keys/models | 禁止 create/bind/delete | auth cache 可以更新；models 文件禁止 | 非阻塞 | checks pass=0；checks fail=1；lock=2 |
| `--models-bootstrap` | preflight + re-GET groups/keys/models | 可以 create/bind managed Key | groups/models/auth | 有界等待 | 全目标成功=0；partial=1；lock=2 |
| `--models-refresh` | groups/keys/models | 已 bootstrap 后可以 ensure managed Key | groups/models/auth | 有界等待 | failed/skipped=1；success=0；lock=2 |

### 2.1 参数规则

- `--env-file` 必须提供；
- `--once` 禁止与任一 models flag 组合；
- 三个 models flag 必须互斥；
- `--once-attempts` 必须 `>=1`；
- 参数冲突必须在任何 provider call/业务写之前返回 2。

### 2.2 Loop 与 T-new rationale

Sub2API loop 是长期服务：

- 单轮 provider 失败写 journal、backoff 后继续；
- 进程后来被正常 signal 停止时返回 0，即使历史某轮失败；
- timer 的成功判断必须使用 `--once`，不能用 loop 最终退出码代表某一轮；
- T-new 是附加低频能力，不能让成功的 groups 事实被回滚。

### 2.3 Refresh bootstrap gate

目标 contract 要求 `--models-refresh` 在无 `bootstrap_completed_at` 时：

- 禁止 list/create/bind key；
- 禁止 `/v1/models`；
- 禁止创建 models 文件；
- 返回 1。

当前 Sub2API 实现主要依赖运维流程，冻结前必须补成代码和 contract test。

## 3. New-API legacy mode 矩阵

| Mode | 远端读取 | 远端 mutation | 本地业务写 | 锁 | 退出语义 |
|---|---|---|---|---|---|
| `--validate` | 无 | 禁止 | 可以创建/验证 DATA_DIR；禁止业务 snapshot | 无业务锁 | valid=0；config=2 |
| 默认无 flag | session/groups | cold 禁止 Token 写；bootstrap+incremental 时只允许 true T-new | auth/groups，可选 models | 非阻塞 | groups success=0；T-new partial 仍 0 |
| `--models-preflight` | groups/token list/secret read/models | 禁止 create/update/delete | auth cache 可以更新；models 文件禁止 | 非阻塞 | checks pass=0；checks fail=1；lock=2 |
| `--models-bootstrap` | preflight + re-GET groups/token/models | 可以 create/repair exact managed Token | auth/groups/models | 有界等待 | failed/skipped=1；success=0；lock=2 |
| `--models-refresh` | groups/token/models | 已 bootstrap 后可以 ensure managed Token | auth/groups/models | 有界等待 | failed/skipped=1；success=0；lock=2 |

### 3.1 参数规则

- 默认入口本身是单轮，禁止增加 `--once`；
- 三个 models flags 必须由 parser mutually-exclusive group 约束；
- `--validate` 与 models mode 同时出现的策略冻结前必须测试；推荐 parser 互斥，而不是 validate 静默覆盖；
- 参数错误必须在 provider call 前返回 2。

### 3.2 Preflight 的“只读”

只读指语义上禁止远端 mutation，不要求 HTTP method 全部为 GET。provider 可以使用 POST `/api/token/{id}/key` 读取 secret，但必须通过 profile 证明该操作：

- 不创建资源；
- 不轮换 secret；
- 不修改 Token 状态；
- 不把 secret 落盘或写日志。

### 3.3 T-new nonfatal rationale

默认 mode 的主职责是高频 groups 采集。groups 已成功时，T-new create/models 失败：

- groups latest/event 仍是有效成功事实；
- models entry 记录失败并保留旧成功；
- 默认 mode 返回 0，避免 systemd 把 groups 成功误判为整轮失败；
- 显式 bootstrap/refresh 的主职责就是 models，因此相同 models 失败必须返回 1。

### 3.4 Auth domain

- management auth 最多恢复一次；第二次失败返回 1；
- `/v1/models` 401/403 属于 API Key domain，禁止触发 Session 重登；
- refresh 无 bootstrap 必须在 token list/create/update/models 之前返回 1，且零 models 文件。

## 4. Stdout JSON

preflight 和 full summary 如果被自动化消费，必须有独立 schema 和 `schema_version/backend/site_id/phase`。冻结前必须二选一：

1. 标记 human-only，不保证字段稳定；或
2. 发布 `cli-output-v1` schema。

推荐发布最小稳定 summary，详细 failures/log text 保持可扩展。禁止自动化依赖 argparse help 或日志文本。

## 5. 必需 contract tests

- 每个 mode 的远端 read/mutation call 集合；
- 每个 mode 的本地文件集合；
- parser 互斥和非法参数在零副作用下返回 2；
- preflight fail=1、lock=2；
- refresh cold gate；
- default cold 零 Token/Key mutation、零 models request；
- T-new 只处理 true added group；
- default T-new partial=0，显式 full partial=1；
- models 401 不触发 management login；
- signal/loop 与 once 退出语义。
