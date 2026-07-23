# 分组倍率 — 实现清单（draft）

## Phase 0 — 文档

- [x] `README.md` 目标与范围  
- [x] `design.md` 配置 / 数据流 / hash  
- [x] `data-model.md` 字段与不变量  
- [x] 评审：确认 pinaic/hubway 仅主字段 ÷10；其它站默认 1  

## Phase 1 — 配置与纯函数

- [x] `MONITOR_RATE_DIVISOR` 解析（默认 `1`，`>0` 有限）  
- [x] `annotate_group_rates(groups, divisor)`（`monitor_rates.py`）单元测试：  
  - [x] `16 / 10 -> 1.6`  
  - [x] `0.12 / 1 -> 0.12`  
  - [x] 非法 divisor → ConfigError  
- [x] `--validate` 打印或至少接受 divisor  

## Phase 2 — Sub2API 写路径

- [x] 成功 poll 后 annotate，再 hash/diff/写盘  
- [x] `groups_latest` 顶层 `rate_divisor`  
- [x] group 含 `rate_multiplier_effective`  
- [x] `sites/pinaic.env`、`sites/hubway.env` 写入 `MONITOR_RATE_DIVISOR=10`  
- [x] `sites/*.env.example` 注释  
- [x] 手工：`sub2api_monitor.py --env-file sites/pinaic.env --once` 抽查 CCMAX 16→1.6  

## Phase 3 — New-API 对齐

- [x] `load_config` + annotate  
- [x] `monitor_storage` hash/diff 纳入 effective  
- [x] botcf/torchai 默认 1 时 effective==raw 仍落盘  
- [x] 单测更新 `test_newapi_monitor.py`  

## Phase 4 — 导出与文档回写

- [x] 导出脚本/表增加 `rate_multiplier_effective`、`rate_divisor`（`scripts/export_groups_rates.py`）  
- [x] 实现稳定后摘要写入 `docs/03 designs/sub2api-monitor.md` / `newapi-monitor.md`  
- [x] 本目录保持 drafts；实现以 `docs/03 designs/` + 代码为准  

## 验收口令

```bash
# pinaic
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --once
.venv/bin/python -c '
import json
from pathlib import Path
d=json.loads(Path("data/pinaic/groups_latest.json").read_text())
assert d.get("rate_divisor") == 10
for g in d["groups"]:
    assert "rate_multiplier_effective" in g
    if g.get("rate_multiplier") == 16:
        assert abs(g["rate_multiplier_effective"] - 1.6) < 1e-9
print("pinaic ok", len(d["groups"]))
'

# hubway
.venv/bin/python sub2api_monitor.py --env-file sites/hubway.env --once
# 任取一组：effective == raw/10

# 默认站（如 littleapi）
.venv/bin/python sub2api_monitor.py --env-file sites/littleapi.env --once
# rate_divisor 缺省或 1；effective == raw
```

## 非目标（本清单不勾选）

- models bootstrap / T-new  
- image/video/peak 倍率换算  
- 新 systemd unit
