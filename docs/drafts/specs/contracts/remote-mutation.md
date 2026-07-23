# Remote Mutation Safety Contract

> 状态：draft  
> Contract ID：`safety/remote-mutation-v1`  
> 适用：Sub2API Key、New-API legacy Token 的自动 create/repair。

## 1. 绝对不变量

1. cold 默认 groups mode 禁止远端 create/update/delete；
2. preflight 禁止远端 mutation；
3. 分页不能证明完整时禁止 create；
4. secret hydration 不确定时禁止把 unknown 当 missing；
5. 只允许修改精确 managed identity 的资源；
6. 用户命名资源禁止 update/bind/rename/disable/delete；
7. 所有 backend 永远禁止自动 delete；
8. create 结果未知时禁止第二次盲目 POST；
9. mutation 后必须 re-list/re-hydrate 并验证目标状态；
10. 完整 secret/session/JWT 禁止进入普通落盘、stdout、stderr 和日志；
11. full partial failure 必须进入结果计数并返回 1；
12. 每次自动写都必须能追溯到显式 bootstrap、已 bootstrap refresh 或已开启的 true T-new。

任何 provider profile 都只能收紧这些规则，禁止放宽。

## 2. 写权限门禁

```text
requested mode allows mutation?
  no  -> read only
  yes -> bootstrap completed or explicit bootstrap?
          no  -> reject
          yes -> provider profile verified?
                  no  -> reject
                  yes -> groups valid/non-empty?
                          no  -> reject
                          yes -> inventory paging complete?
                                  no  -> reject
                                  yes -> coverage known?
                                          no  -> reject affected group
                                          yes -> reconcile missing only
```

T-new 额外要求：

- `MONITOR_MODELS_INCREMENTAL_ENABLE=true`；
- `bootstrap_completed_at` 已存在；
- group 确实不在上一份有效 groups latest；
- `should_attempt_now` 允许；
- 只对 refresh set reconcile，禁止顺手 ensure 全站。

## 3. Managed identity

### 3.1 Sub2API

```text
managed_key_name(group_id) = "sub2api-monitor:g:" + str(group_id).strip()
```

group id trim 后必须非空。精确名称匹配才算 managed。

### 3.2 New-API legacy

短 group name：

```text
"newapi-monitor:g:" + str(group_name).strip()
```

超过 provider name UTF-8 byte 上限时：

```text
prefix + utf8_safe_visible_prefix + ":" + first_12_hex(sha256(full_group_name))
```

算法、byte 上限、separator 和 digest 长度必须由 identity vectors 冻结。必须覆盖 ASCII、中文、多字节边界和两个相同可见前缀的不同长名称。

修改命名算法属于 breaking change，因为旧 managed resource 会被误判为 missing。

## 4. Coverage 状态

每个 group 只能处于：

| 状态 | 含义 | create |
|---|---|---|
| covered | 至少一个 inventory-suitable resource | 禁止 |
| missing | inventory 完整且确定没有 suitable resource | 可以按 mode |
| unknown | 分页、hydration 或关键字段不确定 | 禁止 |
| unsupported | provider profile 不支持必要操作 | 禁止 |

禁止把 exception、timeout、masked secret 或缺字段映射成 missing。

## 5. Pagination fail closed

分页必须：

1. 从 profile 指定页码开始；
2. 按稳定 id merge/dedup；
3. `has_more=true` 时继续，即使当前是短页；
4. total 存在且 merged 未满足时继续；
5. 拒绝 total 漂移、非法 id、重复页无进展、页码异常；
6. 达到 max_pages 仍未证明完整时返回 incomplete；
7. incomplete 时整批禁止 create；
8. create/repair 后的 re-list incomplete 时停止后续 mutation。

短页只能在不与 `has_more/total` 冲突时作为完成证据。

## 6. Unknown create outcome

```text
create request
  -> known business success
       -> re-list + validate exact managed resource
  -> known business failure
       -> record failure; no blind retry
  -> timeout/network/response lost
       -> re-list complete inventory
            -> exact managed resource suitable: claim committed outcome
            -> exact managed exists but unsuitable: report failure/repair only if safe
            -> no exact managed: report unknown failure
            -> inventory incomplete: report unknown failure
       -> never issue second create in same reconcile
```

HTTP 200 不等于业务成功；profile 的 `success=false` 必须是 known failure。

## 7. Resource repair

### 7.1 用户资源

用户资源可以在 inventory-suitable 时用于读取 models，但禁止任何 mutation。即使它 disabled、expired 或绑定错误，也只能忽略或报告。

### 7.2 Managed resource

只有 profile 已验证相应 update 语义时才可修复：

- group 绑定；
- enabled status；
- expiry；
- unlimited quota；
- model/IP 限制。

修复后必须 re-list、re-hydrate、重新计算 suitability。update 返回成功但目标状态未出现，必须失败，禁止继续 models success。

## 8. New-API inventory-suitable

Token 必须同时满足：

- group 精确规范化匹配；
- id 类型和值符合 profile；
- secret 本轮读取成功且非空；
- `status == 1`；
- profile 证明未过期，当前 rc.21 目标为 `expired_time == -1`；
- `unlimited_quota == true`；
- `model_limits_enabled == false`；
- `allow_ips` 为空；
- profile 指定的其他限制均为无限制。

受限 Token 的 `/v1/models` 结果禁止冒充完整 inventory。

## 9. Auth domain 分离

- management API 使用 JWT 或 session；
- `/v1/models` 使用 group API Key；
- Key 401/403 只尝试下一个 suitable Key；
- Key domain failure 禁止触发 management re-login；
- management auth 恢复预算不因 models Key 数量而增加。

## 10. 必需状态向量

- complete inventory + existing user suitable -> create 0；
- masked user secret hydration success -> create 0；
- hydration timeout -> unknown/create 0；
- incomplete pagination -> batch create 0；
- managed disabled/expired/wrong group -> verified repair；
- user disabled/expired/wrong group -> update 0；
- create success -> re-list/hydrate；
- create response lost but committed -> claim without second POST；
- create response lost and not committed -> failure without second POST；
- repair response success but state unchanged -> failure；
- models 401 -> next suitable Key，不 session login；
- log/snapshot secret scan。
