# Configuration Contracts

> 状态：draft  
> Contract IDs：`config/sub2api-v1`、`config/newapi-legacy-v1`

## 1. 事实来源

冻结后 config manifest 是变量名、类型、默认值、secret 属性和 backend 适用范围的唯一事实来源，并用于生成或校验：

- `sites/*.env.example`；
- README 配置表；
- `--validate` tests；
- install script 读取项。

禁止继续手工维护三份独立字段表。

## 2. Manifest 字段

```yaml
name: MONITOR_BASE_URL
type: origin_url
required: true
secret: false
backends: [sub2api, newapi-legacy]
default: null
validation: https_only_no_path_query_fragment
environment_override: true
affects_remote_mutation: false
```

每个变量必须有：name、type、required、secret、backends、default、validation、override 和 remote-mutation 标记。

## 3. 通用不变量

- env 文件必须 0600；
- `MONITOR_SITE_ID` 必须与 env stem 相同；Sub2API legacy 差异应通过过渡验证消除；
- site id 只允许小写字母、数字和内部连字符；
- base URL 必须是 HTTPS origin，禁止 userinfo/path/query/fragment；
- process environment 明确覆盖 env file；
- parser 禁止修改 `os.environ`；
- timeout 必须为正；
- proxy URL 禁止完整输出日志；
- 每个 site 必须有独立 data dir 和 lock；
- data dir 禁止混用 site/backend；
- API path 必须是固定绝对相对路径：以 `/` 开始，禁止 scheme、`..`、query、fragment 和反斜杠。

## 4. 远端写控制面

远端写不是一个布尔开关，而是四层同时成立：

| 层 | 控制 | 语义 |
|---|---|---|
| CLI | `--models-bootstrap` / `--models-refresh` | 显式请求 full mutation-capable mode |
| 状态 | `bootstrap_completed_at` | refresh/T-new 前置；不可由 env 伪造 |
| Env | `MONITOR_MODELS_INCREMENTAL_ENABLE` | 只允许 true T-new；默认 false；不允许 cold bootstrap |
| Deployment | installer `--enable-models` | 只开启 daily schedule；不绕过 bootstrap/provider gate |

必须：

- `--models-preflight` 无 mutation；
- `--models-bootstrap` 是唯一 cold 自动 create 入口；
- `--models-refresh` 无 bootstrap 时零 mutation/零 models request；
- incremental env 默认 false；
- daily timer 默认 disabled；
- enable timer 前应当验证 bootstrap 和 provider capability；
- 任一层未满足时 fail closed。

## 5. Sub2API schema 范围

除 common 字段外可以包含：

- `MONITOR_SITE_NAME`；
- login/refresh/groups/keys/models paths；
- username field；
- poll interval；
- connect/read timeout；
- refresh margin；
- request jitter；
- data dir/token state path；
- events retention；
- incremental enable；
- legacy `AIAPIBANK_*` fallback。

Legacy fallback 必须标 deprecated，并有删除版本；新 example 禁止生成 legacy 名。

## 6. New-API legacy schema 范围

除 common 字段外可以包含：

- `REQUIRE_NEW_API_USER_HEADER`；
- connect/read timeout；
- incremental enable；
- proxy；
- log level。

login/groups/token paths 当前由 provider profile 固定，不为了“通用性”暴露无验证 env。若未来某站需要 override，先扩 provider/config contract。

## 7. Validation side effects

`--validate` 可以创建/检查 data dir，但：

- 禁止 provider call；
- 禁止 auth、groups、models 业务文件；
- 禁止 remote mutation；
- 禁止 enable/restart systemd；
- 错误返回 2。

如果未来需要严格零写验证，应另加 mode，不静默改变现有 `--validate`。
