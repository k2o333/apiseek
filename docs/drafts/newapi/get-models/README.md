# New-API get-models（分组 Token 覆盖 + 模型采集）

## 文档

| 文档 | 说明 |
|------|------|
| [requirements.md](./requirements.md) | **唯一需求权威**（已吸收评审 P0/P1） |
| [review.md](./review.md) | 2026-07-22 评审原文 |
| [touch_ai.md](./touch_ai.md) | torchai rc.21 正常路径探针（凭据已脱敏；**非**通用 New-API 证明） |
| [goal-prompt.txt](./goal-prompt.txt) | `/goal` 实现用完整提示词 |

## 状态

**需求评审修订完成；P0 未闭合前不编码 create/models 写路径。**  
须先完成 requirements §6 只读 Token 契约探针，再实现 preflight → reconcile → store。

## 与现网

- Groups：`newapi_monitor.py` 默认 **单轮**采集（**无** `--once` 旗标）。  
- Models：本主题新增；CLI 仅加 `--models-preflight` / `--models-bootstrap` / `--models-refresh`。  
- Sub2API 对照：`docs/drafts/get-models/`（已实现）。

## 一句话

> 先 hydration 再算 Missing；仅 inventory-suitable 代表全量模型；seed + preflight + bootstrap；refresh 须已 bootstrap。
