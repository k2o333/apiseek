# Sub2API 多站点监控模块结构

本文描述目标系统包含哪些模块、每个模块负责什么，以及模块之间的依赖关系。详细实施约束见
[Sub2API 多站点分组监控设计与实施方案](./sub2api-multi-site-monitor-design.md)。

## 1. 总体模块结构

```mermaid
flowchart TB
    Operator["运维人员<br/>新增站点、调整周期、启停服务"]
    Website["Sub2API 网站<br/>登录、刷新、分组 API"]

    subgraph Control["控制与配置层"]
        CLI["CLI 入口<br/>--site / --once / --validate"]
        Registry["站点注册表加载器<br/>读取 sites.yaml、合并默认值"]
        Validator["配置校验器<br/>校验 URL、路径、站点 ID、目录和权限"]
        SecretLoader["凭据加载器<br/>只加载当前站点的 mode-600 env"]
    end

    subgraph Runtime["单站点运行层"]
        SiteRunner["站点运行器<br/>组织一次轮询和成功周期"]
        Scheduler["调度器<br/>轮询间隔、抖动、停止信号"]
        Retry["重试控制器<br/>错误分类、有界退避、Retry-After"]
        AuthClient["认证客户端<br/>密码登录、refresh、401 恢复"]
        TokenManager["Token 管理器<br/>过期判断、内存状态、原子轮换"]
        GroupClient["分组客户端<br/>只读请求、超时、Connection close"]
        Normalizer["分组规范化器<br/>排序、稳定 JSON、内容 hash"]
        DiffEngine["变化检测器<br/>新增、删除、修改、无变化"]
    end

    subgraph Persistence["持久化层"]
        TokenStore["私有 Token 存储<br/>data/site/token.json，0600"]
        LatestStore["当前快照<br/>groups_latest.json，原子替换"]
        EventStore["变化历史<br/>变化型 JSONL 或 SQLite WAL"]
        RunStore["轮询运行记录<br/>成功、失败、耗时、数量"]
    end

    subgraph Operations["运行保障层"]
        Systemd["systemd 模板<br/>sub2api-monitor@site.service"]
        Logger["结构化日志<br/>脱敏状态和错误类别"]
        Metrics["运行指标<br/>最后成功、失败次数、分组数量"]
        Alerting["告警适配器<br/>认证失败、数据变化、持续不可用"]
    end

    Operator -->|"编辑非敏感配置"| Registry
    Operator -->|"写入每站凭据"| SecretLoader
    Operator -->|"启停实例"| Systemd
    Systemd --> CLI
    CLI --> Registry
    Registry --> Validator
    Validator --> SecretLoader
    Validator --> SiteRunner
    SecretLoader --> AuthClient

    SiteRunner <--> Scheduler
    SiteRunner <--> Retry
    SiteRunner --> AuthClient
    AuthClient <--> TokenManager
    TokenManager <--> TokenStore
    AuthClient <-->|"登录和刷新"| Website
    SiteRunner --> GroupClient
    GroupClient <-->|"获取分组"| Website
    GroupClient --> Normalizer
    Normalizer --> DiffEngine
    DiffEngine --> LatestStore
    DiffEngine --> EventStore
    SiteRunner --> RunStore

    SiteRunner --> Logger
    SiteRunner --> Metrics
    DiffEngine --> Alerting
    Retry --> Metrics
    Metrics --> Alerting
```

## 2. 模块职责分解

```mermaid
flowchart LR
    subgraph ConfigModules["配置模块"]
        A1["RegistryLoader<br/>解析 YAML"]
        A2["ConfigMerger<br/>默认值 + 站点覆盖"]
        A3["ConfigValidator<br/>类型、安全和路径校验"]
        A4["CredentialLoader<br/>读取当前站点 env"]
        A1 --> A2 --> A3 --> A4
    end

    subgraph AuthModules["认证模块"]
        B1["AuthClient<br/>HTTP 登录/刷新"]
        B2["JwtExpiryReader<br/>只读取 exp"]
        B3["TokenState<br/>内存 token 状态"]
        B4["AtomicTokenStore<br/>0600 原子保存"]
        B1 <--> B3
        B2 --> B3
        B3 <--> B4
    end

    subgraph GroupModules["分组模块"]
        C1["GroupClient<br/>GET available groups"]
        C2["ResponseValidator<br/>验证 envelope 和数组"]
        C3["Canonicalizer<br/>规范化和 hash"]
        C4["GroupDiffer<br/>计算新增/删除/修改"]
        C1 --> C2 --> C3 --> C4
    end

    subgraph StorageModules["存储模块"]
        D1["LatestWriter<br/>覆盖当前完整快照"]
        D2["EventWriter<br/>仅写变化事件"]
        D3["HeartbeatWriter<br/>低频存活证据"]
        D4["PollRunWriter<br/>轮询结果和耗时"]
    end

    A4 --> B1
    B3 --> C1
    C4 --> D1
    C4 --> D2
    C4 --> D3
    C1 --> D4
```

## 3. 单站点进程内部结构

```mermaid
flowchart TB
    Main["main(site_id)"] --> Load["加载并校验站点配置"]
    Load --> Loop["MonitorLoop"]

    subgraph MonitorLoop["单站点 MonitorLoop"]
        Poll["PollCoordinator<br/>协调一次轮询"]
        Auth["AuthenticatedSession<br/>确保有效 access token"]
        Fetch["GroupFetcher<br/>调用只读分组接口"]
        Process["GroupProcessor<br/>校验、规范化、diff"]
        Persist["PersistenceFacade<br/>latest、events、runs"]
        Backoff["FailurePolicy<br/>分类和退避"]

        Poll --> Auth --> Fetch --> Process --> Persist
        Auth -.失败.-> Backoff
        Fetch -.失败.-> Backoff
        Process -.契约变化.-> Backoff
        Backoff --> Poll
        Persist --> Poll
    end

    Stop["SIGTERM / SIGINT"] --> Loop
    Loop --> Exit["完成当前安全边界后退出"]
```

## 4. 多站点部署结构

```mermaid
flowchart TB
    Registry["共享 sites.yaml<br/>只读非敏感配置"]
    Code["共享 Python 程序和虚拟环境"]

    subgraph SiteA["aiapibank 实例"]
        ServiceA["sub2api-monitor@aiapibank"]
        SecretA["secrets/aiapibank.env"]
        DataA["data/aiapibank/"]
        ServiceA --> SecretA
        ServiceA --> DataA
    end

    subgraph SiteB["pinaic 实例"]
        ServiceB["sub2api-monitor@pinaic"]
        SecretB["secrets/pinaic.env"]
        DataB["data/pinaic/"]
        ServiceB --> SecretB
        ServiceB --> DataB
    end

    subgraph SiteN["后续站点实例"]
        ServiceN["sub2api-monitor@site-n"]
        SecretN["secrets/site-n.env"]
        DataN["data/site-n/"]
        ServiceN --> SecretN
        ServiceN --> DataN
    end

    Registry --> ServiceA
    Registry --> ServiceB
    Registry --> ServiceN
    Code --> ServiceA
    Code --> ServiceB
    Code --> ServiceN

    ServiceA -.故障隔离.-> Isolation["单站失败不影响其他实例"]
    ServiceB -.故障隔离.-> Isolation
    ServiceN -.故障隔离.-> Isolation
```

## 5. 存储模块演进结构

```mermaid
flowchart LR
    Processor["GroupProcessor"] --> Interface["GroupRepository 接口"]

    Interface --> JsonImpl["JSON/JSONL 实现<br/>适合少量站点"]
    Interface --> SqliteImpl["SQLite WAL 实现<br/>适合高频和跨站查询"]

    JsonImpl --> Latest["groups_latest.json"]
    JsonImpl --> Events["groups_events.jsonl"]
    JsonImpl --> Runs["poll_runs.jsonl"]

    SqliteImpl --> CurrentTable["group_current"]
    SqliteImpl --> EventTable["group_events"]
    SqliteImpl --> RunTable["poll_runs"]

    TokenBoundary["TokenRepository<br/>始终独立、0600"] -.不进入业务数据库.-> Interface
```

## 6. 代码模块建议

```mermaid
flowchart TB
    Package["sub2api_monitor/"]
    Package --> ConfigPy["config.py<br/>YAML、合并、校验、凭据"]
    Package --> ModelsPy["models.py<br/>配置、token、group 数据模型"]
    Package --> AuthPy["auth.py<br/>登录、refresh、过期状态机"]
    Package --> ClientPy["client.py<br/>HTTP session 和分组请求"]
    Package --> PollerPy["poller.py<br/>调度、抖动、退避"]
    Package --> DiffPy["diff.py<br/>规范化、hash、变化计算"]
    Package --> StoragePy["storage.py<br/>原子文件和 repository 接口"]
    Package --> SqlitePy["sqlite_storage.py<br/>可选 SQLite WAL 实现"]
    Package --> ObservePy["observability.py<br/>日志、指标、告警事件"]
    Package --> CliPy["cli.py<br/>--site、--once、--validate"]
```

