# Configuration Contracts

> Publication：formal
> Status：planned
> Contract IDs：`config/sub2api-v1`、`config/newapi-legacy-v1`

## 1. 事实来源

冻结后 config manifest 是变量名、类型、默认值、secret 属性和 backend 范围的唯一事实来源，并用于生成或校验：

- `sites/*.env.example`；
- README 配置表；
- `--validate` tests；
- installer 读取项。

当前尚未生成 machine-readable config manifests，因此本 contract 为 planned。

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

每个变量必须声明 name、type、required、secret、backend、default、validation、override 和 remote-mutation 标记。

## 3. 通用不变量

- env 文件必须 0600；
- `MONITOR_SITE_ID` 必须与 env stem 相同；
- site id 只允许小写字母、数字和内部连字符；
- base URL 必须为 HTTPS origin，禁止 userinfo/path/query/fragment；
- process environment 明确覆盖 env file；
- parser 禁止修改 `os.environ`；
- timeout 必须为正；
- proxy URL 禁止完整输出日志；
- 每站独立 data dir 和 lock；
- data dir 禁止跨 site/backend 混用；
- API path 以 `/` 开始，禁止 scheme、`..`、query、fragment 和反斜杠。

## 4. 远端写控制面

远端写由四层共同控制：

| 层 | 控制 | 语义 |
|---|---|---|
| CLI | `--models-bootstrap` / `--models-refresh` | 显式请求 mutation-capable full mode |
| 状态 | `bootstrap_completed_at` | refresh/T-new 前置，不能由 env 伪造 |
| Env | `MONITOR_MODELS_INCREMENTAL_ENABLE` | 只允许 true T-new，默认 false |
| Deployment | installer `--enable-models` | 只开启 daily schedule，不绕过其他 gate |

必须保证：preflight 零 mutation；bootstrap 是唯一 cold create 入口；cold refresh 零 mutation/零 models request；incremental 默认 false；daily 默认 disabled；任一层不满足时 fail closed。

## 5. Sub2API 范围

除 common 字段外可以包含：

- `MONITOR_SITE_NAME`；
- login/refresh/groups/keys/models paths；
- username field；
- poll interval、connect/read timeout、refresh margin、request jitter；
- data dir/token state path；
- events retention；
- incremental enable；
- legacy `AIAPIBANK_*` fallback。

Legacy fallback 必须标 deprecated 并定义删除版本；新 example 禁止新增 legacy 名。

## 6. New-API legacy 范围

除 common 字段外可以包含：

- `REQUIRE_NEW_API_USER_HEADER`；
- connect/read timeout；
- incremental enable；
- proxy 和 log level。

login/groups/token paths 当前由 provider profile 固定。新站若需要 override，必须先扩 provider/config contract，不能先增加“可能有用”的 env。

## 7. Validate side effects

`--validate` 可以创建或检查 data dir，但必须禁止 provider call、auth/groups/models 业务文件、remote mutation 和 systemd 状态变化；配置错误返回 2。

若未来需要严格零写验证，应新增明确 mode，不静默改变现有 `--validate`。

## 8. 冻结前工作

- 发布 common/sub2api/newapi config manifests；
- examples 与 manifests 双向校验；
- 明确 `--validate` 与其他 flags 的互斥；
- 消除 env stem/site id 过渡差异；
- 对 paths、URLs、timeouts、boolean 和权限写 negative tests；
- 固化四层 mutation gate tests。
