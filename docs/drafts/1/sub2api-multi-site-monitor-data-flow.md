# Sub2API 多站点监控数据流

本文描述数据从配置和网站进入监控系统后，如何经过认证、轮询、处理、持久化和告警。
模块职责见
[Sub2API 多站点监控模块结构](./sub2api-multi-site-monitor-architecture.md)。

## 1. 端到端数据流

```mermaid
flowchart LR
    SitesYaml[("sites.yaml<br/>URL、路径、周期")]
    SecretEnv[("secrets/site.env<br/>用户名、密码")]
    TokenFile[("data/site/token.json<br/>access/refresh token")]
    Website["Sub2API 网站 API"]

    Loader["配置与凭据加载"]
    Auth["认证状态机"]
    Fetch["分组请求"]
    Validate["响应校验"]
    Canonical["规范化 + hash"]
    Diff["新旧状态对比"]

    Latest[("groups_latest.json")]
    Events[("变化事件 JSONL/SQLite")]
    Runs[("poll_runs")]
    Metrics["指标与日志"]
    Alert["变化/故障告警"]

    SitesYaml --> Loader
    SecretEnv --> Loader
    Loader --> Auth
    TokenFile <--> Auth
    Auth <-->|"登录或 refresh"| Website
    Auth -->|"有效 access token，仅在内存传递"| Fetch
    SitesYaml -->|"groups_path、timeout"| Fetch
    Fetch <-->|"Bearer 请求 / JSON 响应"| Website
    Fetch --> Validate
    Validate --> Canonical
    Canonical --> Diff
    Latest -->|"上一份 content_hash"| Diff
    Diff -->|"每次成功原子覆盖"| Latest
    Diff -->|"仅内容变化或 heartbeat"| Events
    Fetch -->|"耗时、状态、数量"| Runs
    Auth -->|"脱敏认证状态"| Metrics
    Fetch -->|"脱敏请求状态"| Metrics
    Diff -->|"分组变化摘要"| Metrics
    Metrics --> Alert
    Diff -->|"新增、删除、修改"| Alert
```

## 2. 启动与配置数据流

```mermaid
sequenceDiagram
    autonumber
    participant SD as systemd 实例
    participant CLI as CLI
    participant REG as RegistryLoader
    participant VAL as ConfigValidator
    participant SEC as CredentialLoader
    participant RUN as SiteRunner

    SD->>CLI: 启动 --registry sites.yaml --site pinaic
    CLI->>REG: 请求指定 site_id 配置
    REG->>REG: 读取 defaults
    REG->>REG: 合并 sites.pinaic 覆盖值
    REG-->>VAL: 完整非敏感站点配置
    VAL->>VAL: 校验 site_id、HTTPS、API path、data_dir
    VAL->>SEC: 传递已解析的 credentials_file
    SEC->>SEC: 检查目录 0700、文件 0600
    SEC-->>RUN: 仅返回当前站点用户名和密码
    VAL-->>RUN: 返回已验证站点配置
    RUN->>RUN: 建立单站点轮询上下文
```

## 3. 登录、Refresh 与 Token 数据流

```mermaid
sequenceDiagram
    autonumber
    participant POLL as PollCoordinator
    participant TM as TokenManager
    participant TS as TokenStore
    participant AUTH as AuthClient
    participant API as Sub2API Auth API
    participant GF as GroupFetcher

    POLL->>TM: 获取可用 access token
    TM->>TS: 读取该站 token.json
    TS-->>TM: access token、refresh token、expiry

    alt 没有 access token
        TM->>AUTH: 请求密码登录
        AUTH->>API: POST /auth/login<br/>email + password
        API-->>AUTH: access + refresh + expires
        AUTH->>TS: mode-600 原子替换 token.json
        AUTH-->>TM: 新 access token
    else access token 临近过期且有 refresh token
        TM->>AUTH: 请求 refresh
        AUTH->>API: POST /auth/refresh<br/>refresh_token
        API-->>AUTH: 新 access token<br/>可选轮换 refresh token
        AUTH->>TS: 原子保存整组新 token
        AUTH-->>TM: 新 access token
    else access token 仍有效
        TM-->>POLL: 复用内存中的 access token
    end

    TM-->>GF: 只在内存传递 Bearer token
    Note over TM,GF: token 不进入日志、YAML、分组快照或业务数据库
```

## 4. 401 恢复数据流

```mermaid
sequenceDiagram
    autonumber
    participant GF as GroupFetcher
    participant API as Groups API
    participant TM as TokenManager
    participant AUTH as AuthClient
    participant TS as TokenStore

    GF->>API: GET /groups/available<br/>旧 Bearer token
    API-->>GF: 401 或 403
    GF->>TM: 请求恢复认证，仅允许一次

    alt 有 refresh token
        TM->>AUTH: refresh
        AUTH->>API: POST /auth/refresh
        alt refresh 成功
            API-->>AUTH: 新 token
            AUTH->>TS: 原子保存
            AUTH-->>GF: 新 access token
        else refresh 被拒绝
            API-->>AUTH: 400/401/403
            AUTH->>API: POST /auth/login<br/>用户名 + 密码，仅一次
            API-->>AUTH: 新 token 或登录失败
            AUTH->>TS: 登录成功时原子保存
            AUTH-->>GF: 新 access token 或失败
        end
    else 没有 refresh token
        TM->>AUTH: 密码登录，仅一次
        AUTH->>API: POST /auth/login
        API-->>AUTH: 新 token 或失败
        AUTH->>TS: 登录成功时原子保存
        AUTH-->>GF: 新 access token 或失败
    end

    opt 恢复成功
        GF->>API: 重试一次 GET /groups/available
        API-->>GF: 200 groups
    end
```

## 5. 分组响应处理数据流

```mermaid
flowchart TB
    Response["HTTP 200 JSON 响应"] --> Envelope["验证 code/message/data envelope"]
    Envelope --> IsList{"data 是否为数组?"}
    IsList -->|否| ContractError["记录契约错误<br/>保留 last-known-good"]
    IsList -->|是| Sort["按稳定 group ID 排序"]
    Sort --> StableJson["key 排序 + 固定 JSON 分隔符"]
    StableJson --> Hash["计算 SHA-256 content_hash"]
    Hash --> Compare{"与 latest hash 比较"}

    Compare -->|首次抓取| Initial["生成 initial 事件"]
    Compare -->|hash 不同| GroupDiff["按 group ID 计算 diff"]
    Compare -->|hash 相同| NoChange["标记无变化"]

    GroupDiff --> Added["added<br/>新分组"]
    GroupDiff --> Removed["removed<br/>删除分组"]
    GroupDiff --> Modified["modified<br/>字段或倍率变化"]

    Initial --> LatestWrite["原子写 latest"]
    Added --> EventWrite["写变化事件"]
    Removed --> EventWrite
    Modified --> EventWrite
    EventWrite --> LatestWrite
    NoChange --> LatestWrite
    NoChange --> Heartbeat{"到达 heartbeat 周期?"}
    Heartbeat -->|是| HeartbeatWrite["写轻量 heartbeat"]
    Heartbeat -->|否| Finish["不追加历史"]
    LatestWrite --> Finish
    HeartbeatWrite --> Finish
```

## 6. 文件持久化数据流

```mermaid
flowchart LR
    NewTokens["新 token 状态"] --> TokenTemp["token.json.tmp<br/>写入 + chmod 0600 + fsync"]
    TokenTemp --> TokenReplace["atomic replace"]
    TokenReplace --> TokenFinal[("token.json 0600")]

    NewSnapshot["新完整分组快照"] --> LatestTemp["groups_latest.json.tmp<br/>写入 + fsync"]
    LatestTemp --> LatestReplace["atomic replace"]
    LatestReplace --> LatestFinal[("groups_latest.json")]

    ChangeEvent["变化事件"] --> EventAppend["单行 JSON 追加"]
    EventAppend --> EventFinal[("groups_events.jsonl")]

    PollResult["轮询结果"] --> RunAppend["成功/失败摘要追加"]
    RunAppend --> RunFinal[("poll_runs.jsonl")]
```

## 7. SQLite WAL 数据流

```mermaid
flowchart LR
    DiffResult["规范化分组 + diff"] --> Tx["BEGIN IMMEDIATE"]
    PollResult["轮询状态和耗时"] --> Tx

    Tx --> Upsert["UPSERT group_current"]
    Tx --> InsertEvents["INSERT group_events<br/>仅变化"]
    Tx --> InsertRun["INSERT poll_runs"]
    Upsert --> Commit["COMMIT"]
    InsertEvents --> Commit
    InsertRun --> Commit

    Commit --> DB[("monitor.db")]
    DB --> WAL[("monitor.db-wal")]
    WAL --> Readers["报表、查询和告警读取器"]

    Token[("token.json 0600")] -."严格隔离，不进入 SQLite".-> DB
```

## 8. 成功周期与调度数据流

```mermaid
sequenceDiagram
    autonumber
    participant SCH as Scheduler
    participant POLL as PollCoordinator
    participant STORE as Persistence
    participant CLOCK as Clock

    SCH->>CLOCK: 记录周期开始时间
    SCH->>POLL: 执行一次轮询
    POLL->>STORE: 写 latest / event / poll_run
    STORE-->>POLL: 持久化完成
    POLL-->>SCH: 成功
    SCH->>CLOCK: 计算 interval - 已耗时
    SCH->>SCH: 加入站点抖动
    SCH->>SCH: 等待至下一成功周期
    SCH->>POLL: 开始下一次轮询
```

## 9. 失败与退避数据流

```mermaid
flowchart TB
    Failure["轮询失败"] --> Classify{"错误分类"}

    Classify -->|401/403| AuthRecovery["refresh 一次<br/>必要时登录一次"]
    Classify -->|ConnectTimeout| FreshConnect["保留 token<br/>新连接重试"]
    Classify -->|ReadTimeout| FreshRead["保留 token<br/>新连接重试"]
    Classify -->|429| RetryAfter["读取 Retry-After"]
    Classify -->|5xx| ServerBackoff["有界指数退避"]
    Classify -->|响应结构错误| Contract["标记契约变化<br/>保留 latest"]
    Classify -->|凭据失败| Credential["降低重试频率<br/>触发告警"]
    Classify -->|地区限制| Region["记录出口国家<br/>等待路由修复"]

    AuthRecovery --> RetryDecision{"恢复成功?"}
    FreshConnect --> Backoff["10/30/60/120/300 秒"]
    FreshRead --> Backoff
    RetryAfter --> Backoff
    ServerBackoff --> Backoff
    Contract --> Alert["告警"]
    Credential --> Alert
    Region --> Alert

    RetryDecision -->|是| RetryOnce["重试当前分组请求一次"]
    RetryDecision -->|否| Backoff
    RetryOnce --> Success["成功后失败计数归零"]
    Backoff --> NextAttempt["下一次尝试"]
    Alert --> NextAttempt

    Failure -."绝不覆盖".-> LastGood[("last-known-good latest")]
```

## 10. 告警数据流

```mermaid
flowchart LR
    PollRuns[("poll_runs")]
    Events[("group_events")]
    Runtime["实时运行状态"]

    Evaluator["告警规则评估"]
    Dedupe["去重和冷却"]
    Channel["邮件/企业微信/其他渠道"]

    PollRuns -->|"连续失败、最后成功时间"| Evaluator
    Events -->|"新增、删除、倍率和状态变化"| Evaluator
    Runtime -->|"认证、地区、权限异常"| Evaluator
    Evaluator --> Dedupe
    Dedupe --> Channel

    Channel --> Operator["运维人员"]
    Operator -->|"修复路由、凭据或配置"| Runtime
```

## 11. 新增站点的数据流

```mermaid
flowchart TB
    Input["输入登录 URL<br/>凭据文件<br/>轮询频率"] --> Inspect["检查前端和网络请求"]
    Inspect --> Compatible{"Sub2API 兼容?"}
    Compatible -->|否| Adapter["转为独立站点适配器设计"]
    Compatible -->|是| Contract["记录 login/refresh/groups 契约"]
    Contract --> Registry["添加 sites.yaml 配置块"]
    Contract --> Secret["创建 secrets/site.env 0600"]
    Registry --> Validate["--validate"]
    Secret --> Validate
    Validate --> Once["--once 真实登录和抓取"]
    Once --> Verify["检查 token/latest/events 权限和内容"]
    Verify --> Enable["enable --now sub2api-monitor@site"]
    Enable --> Observe["观察至少两个轮询周期"]
    Observe --> Complete["接入完成"]
```

