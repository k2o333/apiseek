# 全站分组汇总

由各站 `data/<site>/groups_latest.json` **合并**而成（最后一次成功快照）。

| 文件 | 说明 |
|------|------|
| [groups_all.md](./groups_all.md) | 可读大表（Markdown） |
| [groups_all.csv](./groups_all.csv) | 表格工具 / Excel |
| [groups_all.json](./groups_all.json) | 程序读取 |
| [groups_rates.csv](./groups_rates.csv) | 倍率专用：raw + effective + divisor |
| [groups_rates.json](./groups_rates.json) | 同上，含站级元数据 |

**倍率字段：** `rate_multiplier`（远端原样）、`rate_multiplier_effective`（`raw / MONITOR_RATE_DIVISOR`）、`rate_divisor`。pinaic/hubway 通常 divisor=10。

更新方式：

```bash
# 倍率表（推荐）
.venv/bin/python scripts/export_groups_rates.py

# groups_all.* 仍可由 agent/其它合并脚本从 latest 生成
```
