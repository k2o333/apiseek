# Provider Profiles

> 状态：draft  
> Contract IDs：`provider/sub2api-v1`、`provider/torchai-rc21-models-v1`

## 1. 原则

第三方 API 不受本仓库控制，无法被本仓库永久冻结。Provider contract 冻结的是：

- 本仓库声称支持的部署/版本；
- 已验证 endpoint、鉴权和 envelope；
- 哪些漂移必须 contract fail；
- sanitized fixtures 和最后探活日期。

未列入 profile 的部署禁止自动继承远端写权限。

## 2. Profile 元数据

每个 profile 必须声明：

```yaml
id: torchai-rc21-models-v1
status: draft
provider_family: newapi-legacy
verified_deployments:
  - host: torchai.ai
    upstream_version: v1.0.0-rc.21
    verified_at: "2026-07-22"
capabilities:
  groups_read: true
  models_read: true
  managed_create: pending
  managed_repair: pending
```

必须有 owner、探活日期、上游可观察版本、支持能力和 blocked capabilities。禁止只写“New-API compatible”。

## 3. 当前范围

### 3.1 Sub2API

目标 profile 包含：

- password login；
- refresh token；
- Bearer groups；
- Key list/create/bind；
- API Key `/v1/models`；
- AIAPIBANK、PinAI 等逐站验证表。

每个新站必须验证 paths、完整 secret 回读和分页；仅 endpoint 名相同不足以获得 mutation capability。

### 3.2 New-API legacy groups

BotCF/TorchAI groups 已验证共同形状：

- login session cookie；
- `GET /api/user/self/groups`；
- TorchAI 需要 `new-api-user`；
- groups data 是非空 object；
- ratio 是有限非负数。

这只支持 groups read，不自动授予 models mutation。

### 3.3 TorchAI rc.21 models

目标范围：

- session + `new-api-user` management；
- token list pagination；
- secret read；
- create/update；
- API Key `/v1/models`。

自动 create/repair 前必须关闭 §5 探针门禁。

### 3.4 明确不支持

- BotCF models，直至独立探针完成；
- 任意 New-API main/未来版本；
- dashboard token 等未验证鉴权；
- 零 seed Token 自动首创；
- provider 未证明的 delete、bulk update 或 secret rotation。

## 4. Sanitized fixtures

每个 profile 至少包含：

- login/auth success；
- HTTP 200 + business failure；
- 401/403 auth；
- Cloudflare/region HTML；
- 429 + Retry-After；
- malformed/empty envelope；
- pagination multi-page、short-but-total-unsatisfied、repeat/no-progress；
- models `data=[]`；
- malformed model id；
- create/update success、known failure、unknown outcome。

fixture 必须通过 secret scanner，禁止真实：

- email/password；
- cookie/session；
- access/refresh token；
- API Key；
- proxy credential；
- 可还原身份的信息。

## 5. TorchAI 写路径探针门禁

- [ ] token list 完整 envelope；
- [ ] page parameter、起点、total/has_more；
- [ ] status enum；
- [ ] `expired_time` 单位与 `-1`；
- [ ] quota、model limits、allow_ips 等限制语义；
- [ ] HTTP 200 + `success=false`；
- [ ] secret endpoint 不轮换/不 mutation；
- [ ] PUT 修复 group/status/expiry/limits；
- [ ] Token name UTF-8 byte 上限和唯一性；
- [ ] create/update 成功 envelope；
- [ ] unknown outcome 可通过 re-list 认领；
- [ ] 全部结果已脱敏落入 fixtures。

未全部完成：profile 的 `managed_create/managed_repair` 必须保持 pending，生产 timer 不得获得该 capability。

## 6. Drift policy

发现上游响应不符合 profile：

1. 当前 group/site contract fail；
2. 保留最后成功 snapshot；
3. 禁止自动兼容新字段类型或业务失败；
4. 通过只读脱敏探针确认；
5. 更新 profile、fixtures、测试和 last_reviewed；
6. 若改变语义，升 profile/contract version 后再启用。
