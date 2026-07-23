# Remote Mutation Safety Contract

> Publication：formal
> Status：draft
> Contract ID：`safety/remote-mutation-v1`
> Applies to：Sub2API Key、New-API legacy Token 的自动 create/repair

## 1. 绝对不变量

1. cold 默认 groups mode 禁止 create/update/delete；
2. preflight 禁止 remote mutation；
3. 分页无法证明完整时禁止 create；
4. secret hydration 不确定时禁止把 unknown 当 missing；
5. 只允许修改精确 managed identity 的资源；
6. 用户命名资源禁止 update/bind/rename/disable/delete；
7. 所有 backend 永远禁止自动 delete；
8. create 结果未知时禁止第二次盲目 POST；
9. mutation 后必须 re-list/re-hydrate 并验证目标状态；
10. 完整 secret/session/JWT 禁止进入普通落盘、stdout、stderr 和日志；
11. full partial failure 必须计入结果并返回 1；
12. 自动写必须可追溯到显式 bootstrap、已 bootstrap refresh 或 opt-in true T-new。

Provider profile 只能收紧这些规则，禁止放宽。

## 2. 写权限门禁

```text
mode allows mutation?
  no  -> read only
  yes -> explicit bootstrap or bootstrap_completed_at?
          no  -> reject
          yes -> provider capability verified?
                  no  -> reject
                  yes -> groups valid and non-empty?
                          no  -> reject
                          yes -> inventory complete?
                                  no  -> reject
                                  yes -> coverage known?
                                          no  -> reject affected group
                                          yes -> reconcile missing only
```

T-new 还必须满足：incremental env 为 true、已有 bootstrap、上一份 groups latest 合法、group 确实新增、cooldown 允许，并且只 reconcile 本轮 refresh set。

## 3. Managed identity

Sub2API：

```text
"sub2api-monitor:g:" + str(group_id).strip()
```

New-API legacy 短名称：

```text
"newapi-monitor:g:" + str(group_name).strip()
```

group identity trim 后必须非空。只有精确名称匹配才是 managed。

New-API 长名称的 UTF-8 byte 上限、截断、separator 和 hash 后缀必须由 provider 探针和 identity vectors 冻结；在此之前禁止把推测算法授予生产 mutation capability。修改命名算法属于 breaking change。

## 4. Coverage 状态

| 状态 | 含义 | 是否可 create |
|---|---|---|
| `covered` | 至少一个 inventory-suitable resource | 否 |
| `missing` | 完整 inventory 明确无 suitable resource | 按 mode 可以 |
| `unknown` | 分页、hydration 或关键字段不确定 | 否 |
| `unsupported` | profile 不支持必要操作 | 否 |

exception、timeout、masked secret 和缺字段都不能映射为 missing。

## 5. Pagination fail closed

分页必须：

1. 从 profile 指定页码开始；
2. 以稳定 id merge/dedup；
3. `has_more=true` 时继续，即使短页；
4. total 存在且 merged 不足时继续；
5. 拒绝 total 漂移、非法 id、重复页无进展和页码异常；
6. 达到 max_pages 仍不能证明完整时返回 incomplete；
7. incomplete 时整批 create=0；
8. create/repair 后的 re-list incomplete 时停止后续 mutation。

短页只有在不与 `has_more/total` 冲突时才是完成证据。

## 6. Unknown create outcome

```text
create request
  -> known business success
       -> re-list + validate exact managed resource
  -> known business failure
       -> record failure; no blind retry
  -> timeout/network/response lost
       -> re-list complete inventory
            -> exact managed suitable: claim committed outcome
            -> exact managed unsuitable: failure or verified safe repair
            -> no exact managed: unknown failure
            -> inventory incomplete: unknown failure
       -> never issue second create in the same reconcile
```

HTTP 2xx 不等于业务成功；profile 中的 `success=false` 必须视为 known failure。

## 7. Resource policy

用户资源可以在 suitable 时用于 models 读取，但无论 disabled、expired、受限或错绑都禁止 mutation。

Managed resource 只有在 profile 验证 update 语义后才可修复 group、status、expiry、quota、model/IP limits。修复后必须 re-list/re-hydrate；若目标状态未出现，则失败且不能继续记录 models success。

## 8. New-API inventory-suitable

Token 必须同时满足：

- group 精确匹配；
- id 符合 profile；
- 本轮 secret hydration 成功且非空；
- `status == 1`；
- profile 证明未过期；rc.21 目标为 `expired_time == -1`；
- `unlimited_quota == true`；
- `model_limits_enabled == false`；
- `allow_ips` 为空；
- profile 规定的其他限制均为 unrestricted。

受限 Token 返回的 models 子集禁止冒充完整 inventory。

## 9. Auth domain

- management 使用 JWT 或 session；
- `/v1/models` 使用 group API Key；
- Key 401/403 只尝试下一个 suitable Key；
- Key domain failure 禁止触发 management re-login；
- management auth 恢复预算不随 Key 数量增加。

## 10. 冻结所需 vectors

- existing user suitable -> create 0；
- hydration timeout -> unknown/create 0；
- incomplete pagination -> batch create 0；
- managed disabled/expired/wrong group -> verified repair；
- user resource in same states -> update 0；
- create success -> re-list/hydrate；
- response lost but committed -> claim without second POST；
- response lost and not committed -> failure without second POST；
- update success response but state unchanged -> failure；
- models 401 -> next suitable Key，session login=0；
- stdout/log/snapshot secret scan。
