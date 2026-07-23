# 契约边界现状盘点

> 类型：as-is inventory，不是规范。  
> 采样时间：2026-07-22。  
> 本文件允许随实现变化而更新；不得用它覆盖 frozen contract。

## 1. 可复现基线

命令：

```bash
.venv/bin/python -m pytest -q
```

结果：

```text
172 passed, 7 subtests passed in 20.03s
```

New-API `newapi_monitor.py` 已使用 argparse mutually-exclusive group 暴露：

- `--models-preflight`；
- `--models-bootstrap`；
- `--models-refresh`。

`main()` 已完成分发。当前剩余工作是 contract/schema/副作用断言和生产 enable 门禁，不是 CLI 接线。

## 2. 生产文件事实

当前以下 Sub2API 站点已有 `models_latest.json`：

```text
aiapibank aresaicode hubway iaiguo klinkw littleapi pinaic yybb
```

抽样文件共同特征：

- `schema_version: 1`；
- 没有 `backend`；
- `last_full_result` 为 `{target,ok,failed}`；
- 已有 `bootstrap_completed_at`；
- models 列表和 hash 已被生产/运维使用。

因此“models 尚未发布，可无迁移直接重定义 v1”不成立。New-API models 尚在扩展中，但共享 schema 必须兼容已落盘的 Sub2API legacy v1。

现有聚合产物：

```text
docs/websites/table/groups_all.csv
docs/websites/table/groups_all.json
docs/websites/table/groups_all.md
```

未来 `models_all` 与现有 `groups_all` 都应经 per-site contract reader 读取。

## 3. Groups storage 差异

| 维度 | Sub2API 当前实现 | New-API 当前实现 |
|---|---|---|
| latest version | 无 `schema_version` | `schema_version: 1` |
| backend | 无 | `backend: newapi` |
| group shape | 完整上游 object | 固定四字段规范化 object |
| event hash | `content_hash` | `before_hash/after_hash` |
| diff | id 列表 | 完整 added/removed + before/after modified |
| initial | diff 常为空 | added 为完整初始集合 |
| dedup | 扫描全 JSONL 历史 hash | 最后一条完整事件 after_hash |
| A->B->A | 恢复事件可能被吞 | 会记录恢复事件 |
| retention | 进程内按天 prune | v1 不在线 prune |

### 3.1 Sub2API 当前 hash

- stable id：有 `id` 用 id，否则用 name，最后 `str()`；
- sort：`(stable_id, json.dumps(full_object, sort_keys=True, ensure_ascii=False))`；
- hash payload：排序后的完整 object 列表，`sort_keys=True`、紧凑 separators、UTF-8；
- 上游新增任意字段或上游 timestamp 改变都可能改变 hash；
- number id 与 string id 的 stable sort key 可能相同，但完整 JSON 不同，因此 hash 不同。

## 4. Models storage 差异

| 维度 | Sub2API 当前实现 | New-API 当前实现 |
|---|---|---|
| parser | string trim；dict id/name/model 转 string 后 trim；空值丢弃；顺序保留、不去重 | OpenAI `data` list；item id 当前也会 `str()`；之后 trim/去重/排序 |
| apply_success | 保存传入顺序和重复 | 保存规范化列表 |
| hash | `sorted(str(m) for m in models)`，不自行 trim/去重 | 对规范化列表 hash |
| full result | 三字段；deadline skip 合并为 failed | 四字段；skipped 独立 |
| backend | 无 | 无 |
| reader | 缺字段 setdefault；未严格拒绝错 site/version | 同类宽松行为 |
| empty bootstrap | target=0 可标完成 | groups 非空 contract 下正常不可达 |

两套 full refresh 的 `source` 当前也不同：Sub2API 使用 `daily`，New-API 使用
`refresh`。目标 contract 已决定按 CLI mode 统一为 `refresh`；daily 只是调度方式。

目标 contract 将改变以下当前行为：

- 两 backend 都严格要求原始 model id 为非空字符串；
- 两 backend 都使用 trim/去重/排序列表；
- 两 backend writer 都输出 backend 和四字段 full result；
- legacy Sub2API 文件通过受限兼容 reader/migration 进入目标格式。

## 5. CLI 与锁

当前共同退出码大体为：

- 0：成功；
- 1：provider/contract/批次运行失败；
- 2：配置、参数、锁等本地前置条件失败。

当前 Sub2API：

- 默认是常驻 loop；`--once` 是 timer 单轮；
- models flags 在 `main()` 手工互斥；
- preflight 非阻塞锁；bootstrap/refresh 有界等待；
- full deadline skip 合并进 failed；
- refresh 代码本身尚未强制检查 `bootstrap_completed_at`，主要依赖运维流程。

当前 New-API：

- 默认入口即单轮，不接受 `--once`；
- models flags 由 argparse 互斥；
- refresh 明确要求已有 bootstrap；
- preflight 非阻塞锁；full 有界等待；
- groups 成功后的 incremental 部分失败不改变 groups 成功码。

## 6. systemd hardening gap

| Unit | UMask | Nice | NNP/PrivateTmp/ProtectSystem/ReadWritePaths | Timeout |
|---|---:|---:|---:|---:|
| `newapi-monitor-once@` | yes | yes | yes | 240 |
| `sub2api-monitor-once@` | no | yes | yes | 240 |
| `sub2api-models-daily@` | no | yes | yes | 600 |
| `newapi-models-daily@` | no | no | no | 600 |
| `sub2api-monitor@` legacy simple | no | no | partial | none |

当前唯一包含完整目标集合的 unit 是 `newapi-monitor-once@.service`。冻结目标不把当前 gap 合法化，而是要求所有 credential-bearing oneshot/daily unit 对齐；legacy simple 标记 deprecated。

## 7. 文档与事实来源 gap

- `skills/aiapibank-monitor-groups/references/monitor-contract.md` 仍偏向 legacy simple supervision；
- README 已把 timer + oneshot 作为生产路径；
- Sub2API models data-model 写三字段 full result；New-API requirements 写四字段并称同构；
- models event retention 在文档和实现中未完全一致；
- provider 探针文档含正常路径，但 New-API 写路径的字段/限制验证清单仍需形成可复现 fixtures。

这些 gap 应通过 contract 引用收敛，不应继续复制多份字段表。
