# Provider Profiles

> Publication：formal
> Status：planned
> Contract IDs：`provider/sub2api-v1`、`provider/newapi-legacy-groups-v1`、`provider/torchai-rc21-models-v1`

第三方 API 无法由本仓库永久冻结。Provider contract 冻结的是本仓库声称支持的 deployment/version、已验证 endpoint/envelope、漂移失败策略、sanitized fixtures 和 capability。

## 1. Profile 元数据

每个 profile 必须声明：

```yaml
id: torchai-rc21-models-v1
status: planned
owners: [repository-maintainers]
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

必须记录 owner、探活日期、可观察上游版本、已支持 capability 和 blocked capability。禁止只写“compatible”。

## 2. `provider/sub2api-v1`

目标 profile 覆盖：

- password login 与 refresh token；
- Bearer groups；
- Key list/create/bind；
- API Key `/v1/models`；
- AIAPIBANK、PinAI 等逐站验证表。

每个新站必须单独验证 paths、分页、完整 secret 回读、create/bind envelope 和 models envelope。endpoint 名相同不足以获得 mutation capability。

## 3. `provider/newapi-legacy-groups-v1`

BotCF/TorchAI 已验证的共同 groups 形状：

- `POST /api/user/login?turnstile=`，turnstile 空；
- session cookie；
- `GET /api/user/self/groups`；
- TorchAI 需要 `new-api-user: <login data.id>`；
- redirect 禁止跟随；
- groups data 是非空 object；
- ratio 是有限非负数。

这是明确的 legacy session adapter，不承诺 New-API 全系，也不自动授予 models mutation。

## 4. `provider/torchai-rc21-models-v1`

目标能力：

- session + `new-api-user` management；
- Token list pagination；
- Token secret read；
- managed create/update；
- API Key `/v1/models`。

当前 profile 的 `managed_create` 和 `managed_repair` 必须保持 pending，直至以下探针全部脱敏固化：

- list envelope、page 起点、`total/has_more`；
- status enum、expiry 单位与 `-1`；
- quota/model limits/allow_ips；
- HTTP 2xx + `success=false`；
- secret endpoint 不轮换、不 mutation；
- PUT 修复语义；
- Token name UTF-8 byte 上限与唯一性；
- create/update success envelope；
- unknown outcome 可由 re-list 认领。

## 5. 明确不支持

- BotCF models，直至独立 profile/探针完成；
- 任意 New-API main 或未来版本；
- dashboard token 等未验证鉴权；
- 零 seed Token 的自动首创；
- 未证明的 delete、bulk update 或 secret rotation。

## 6. Sanitized fixtures

每个 profile 至少需要：

- auth success、HTTP 2xx + business failure、401/403；
- Cloudflare/region HTML、429 + Retry-After；
- malformed/empty envelope；
- multi-page、short-but-total-unsatisfied、repeat/no-progress；
- models empty 与 malformed id；
- create/update success、known failure、unknown outcome。

fixtures 必须通过 secret scanner，禁止真实 email/password、cookie/session、access/refresh token、API Key、proxy credential 或可还原身份的信息。

## 7. Drift policy

发现响应不符合 profile：

1. 当前 group/site contract fail；
2. 保留最后成功 snapshot；
3. 禁止自动兼容新字段类型或 business failure；
4. 只读脱敏探针确认；
5. 更新 profile、fixtures、tests 和 `last_reviewed`；
6. 语义改变时升 profile/contract version 后再启用。

站级事实记录见 [BotCF](../../websites/botcf.md) 和 [TorchAI](../../websites/torchai.md)。
