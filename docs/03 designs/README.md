# 当前实现设计

> 状态：active
> 最近复核：2026-07-23

本目录描述仓库当前实际采用的架构、数据流和运维形态，是开发与排障的正式设计入口。

| 主题 | 文件 | 实现入口 |
|---|---|---|
| Sub2API groups | [sub2api-monitor.md](./sub2api-monitor.md) | `sub2api_monitor.py` |
| Sub2API models | [sub2api-models.md](./sub2api-models.md) | `sub2api_models.py` |
| New-API legacy groups | [newapi-monitor.md](./newapi-monitor.md) | `newapi_monitor.py`、`monitor_storage.py` |
| New-API legacy models | [newapi-models.md](./newapi-models.md) | `newapi_models.py` |

## 设计与契约的关系

- 本目录回答“代码现在怎样组织和运行”；
- [docs/02 specs](../02%20specs/README.md) 回答“准备冻结哪些兼容边界”；
- contract 为 draft 时，本文会列出实现差异；
- contract frozen 后，发生冲突时以 machine contract 与对应 tests 为准；
- 评审原文、探针和替代方案继续保留在 [docs/drafts](../drafts/)。

## 共同架构

```text
sites/<id>.env
      |
systemd timer -> per-site oneshot CLI -> provider HTTP
                                      -> data/<id>/auth cache
                                      -> groups latest/events
                                      -> models latest/events (opt-in)
```

共同原则：每站配置与数据隔离；生产使用 timer + oneshot；groups 高频、models 低频；远端写默认关闭；失败不覆盖最后成功业务快照；同站任务通过 `monitor.lock` 互斥。
