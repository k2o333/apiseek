# torchai.ai 自动化操作指南（New-API 部署）

> 目标：登录 New-API 站点 `https://torchai.ai/`，拉取当前用户可选分组，为缺失分组创建/绑定 API Key，并获取各 Key 的模型列表。
>
> **适用范围：仅 torchai.ai（New-API rc.21）实测正常路径。** 其他 New-API 站须独立 preflight，不得默认套用本页分页/status/载荷。
>
> **生产不变量：** 正式需求见 [requirements.md](./requirements.md)——允许多 Token；managed 名 `newapi-monitor:g:…`；列表 key 脱敏须 `/key` hydration；「一组一 Key」仅为某次探针结果，不是系统保证。
>
> 凭据均已脱敏，禁止填入真实值提交仓库。

## 1. 前置知识：New-API 部署识别

Torch AI 基于 **New-API**（响应头 `x-new-api-version: v1.0.0-rc.21`），与 sub2api 在接口路径、鉴权方式和分组语义上有明显区别：

- 前端技术栈：React / Next.js 风格控制台（控制台路由以 `/dashboard` 开头）。
- 常见路由：`/sign-in`、`/dashboard/overview`、`/keys`、`/usage-logs/common`、`/wallet`、`/profile`、`/pricing`。
- 登录后写入 `session` Cookie，并在 `localStorage` 中写入 `uid`（用户 ID）和 `user`（序列化用户信息）。
- 所有管理接口**必须同时携带** `session` Cookie 和请求头 `new-api-user: <用户ID>`。
- 分组接口：`GET /api/user/self/groups`，返回以**分组名称为键**的对象。
- API Key 接口：`GET /api/token/`、创建 `POST /api/token/`、更新 `PUT /api/token/`、获取完整 Key `POST /api/token/{id}/key`。

## 2. 登录

### 2.1 浏览器方式（Chrome DevTools MCP）

1. 打开 `https://torchai.ai/sign-in`。
2. 使用 `take_snapshot` 定位邮箱、密码输入框、协议复选框及登录按钮。
3. 填写邮箱与密码（**仅本机 env / 密钥设施**；禁止写入仓库，文档用占位符 `<EMAIL>` / `<PASSWORD>`）。
4. 勾选协议复选框（"我已阅读并同意 用户协议 and the 隐私政策"）。
5. 点击**登录**按钮，等待页面跳转到 `https://torchai.ai/dashboard/overview`。
6. 验证 `localStorage.getItem('uid')` 是否存在。

### 2.2 API 方式（curl / Python）

```bash
curl -s -X POST "https://torchai.ai/api/user/login?turnstile=" \
  -H "Content-Type: application/json" \
  -d '{"username":"<EMAIL>","password":"<PASSWORD>"}' \
  -c session.txt
```

返回示例（字段示意，数值已脱敏）：

```json
{
  "success": true,
  "message": "",
  "data": {
    "id": 0,
    "username": "<EMAIL>",
    "email": "<EMAIL>",
    "status": 1,
    "quota": 0
  }
}
```

后续请求必须：

- 携带登录后的 `session` Cookie（`requests.Session()` 或 curl `-b session.txt`）。
- 在请求头中附加 `new-api-user: <用户ID>`（即登录响应中的 `data.id`）。

> 安全提醒：真实密码应通过环境变量注入，不要写进代码或文档中提交仓库。

## 3. 获取用户信息与验证登录

通过浏览器脚本读取 `localStorage`：

```javascript
async () => {
  const uid = localStorage.getItem('uid');
  const res = await fetch('/api/user/self', {
    credentials: 'include',
    headers: { 'new-api-user': uid },
  });
  const json = await res.json();
  return {
    hasUid: !!uid,
    uid: uid,
    user: json.data,
  };
}
```

返回示例：

```json
{
  "hasUid": true,
  "uid": "<USER_ID>",
  "user": {
    "id": 0,
    "username": "<EMAIL>",
    "email": "<EMAIL>",
    "status": 1,
    "quota": 0
  }
}
```

## 4. 获取可用分组

端点：`GET /api/user/self/groups`

浏览器脚本：

```javascript
async () => {
  const uid = localStorage.getItem('uid');
  const res = await fetch('/api/user/self/groups', {
    credentials: 'include',
    headers: { 'new-api-user': uid },
  });
  return await res.json();
}
```

返回示例：

```json
{
  "success": true,
  "message": "",
  "data": {
    "Gemini": { "desc": "", "ratio": 0.4 },
    "adobe图": { "desc": "", "ratio": 1.0 },
    "claude-max": { "desc": "max号池", "ratio": 1.4 },
    "claude-max-高并发": { "desc": "claude-max-高并发", "ratio": 1.6 },
    "claude福利": { "desc": "", "ratio": 2.0 },
    "codex-pro": { "desc": "pro池", "ratio": 0.3 },
    "default": { "desc": "默认分组", "ratio": 1.0 },
    "gemini画图": { "desc": "gemini画图", "ratio": 1.0 },
    "gpt-image": { "desc": "画图池", "ratio": 0.65 },
    "grok-逆": { "desc": "", "ratio": 0.03 },
    "特价codex": { "desc": "特价codex（非pro）", "ratio": 0.15 }
  }
}
```

本案例中 torchai.ai 的 11 个分组：

| 分组名称 | 倍率 | 描述 |
|---|---|---|
| Gemini | 0.40x | — |
| adobe图 | 1.00x | — |
| claude-max | 1.40x | max号池 |
| claude-max-高并发 | 1.60x | claude-max-高并发 |
| claude福利 | 2.00x | — |
| codex-pro | 0.30x | pro池 |
| default | 1.00x | 默认分组 |
| gemini画图 | 1.00x | gemini画图 |
| gpt-image | 0.65x | 画图池 |
| grok-逆 | 0.03x | — |
| 特价codex | 0.15x | 特价codex（非pro） |

## 5. 获取现有 API Key 及其分组

端点：`GET /api/token/?p=1&size=20`

浏览器脚本：

```javascript
async () => {
  const uid = localStorage.getItem('uid');
  const res = await fetch('/api/token/?p=1&size=100', {
    credentials: 'include',
    headers: { 'new-api-user': uid },
  });
  const json = await res.json();
  return json.data.items.map(k => ({
    id: k.id,
    name: k.name,
    group: k.group,
    status: k.status,
    key_masked: k.key,
  }));
}
```

本案例中最初只有 1 个已有 Key：

| Key ID | 名称 | 分组 |
|---|---|---|
| 1636 | KV | 特价codex |

目标：保证每个分组都有且只有一个对应的 API Key。

## 6. 创建 API Key 并绑定分组（完整策略）

核心策略：

1. 获取所有可选分组（`GET /api/user/self/groups`），得到集合 `G = {g1, g2, ..., gn}`（分组名称字符串）。
2. 获取所有现有 API Key（`GET /api/token/`），读取每个 Key 已绑定的 `group` 名称，得到已被覆盖的分组集合 `C`。
3. 计算缺失分组：`Missing = G - C`。
4. 对 `Missing` 中的每个分组，调用 `POST /api/token/` **一次性**创建并绑定分组（New-API 在创建时即可指定 `group`）。
5. 重新拉取 Key 列表，验证每个分组都被至少一个 Key 覆盖。

> 注意：与 sub2api 不同，New-API 创建 Key 时直接传入 `group` 名称即可完成绑定，无需先创建再 PUT 更新。

### 6.1 创建 Key 并绑定分组

端点：`POST /api/token/`
请求体：

```json
{
  "name": "Gemini",
  "remain_quota": 0,
  "expired_time": -1,
  "unlimited_quota": true,
  "model_limits_enabled": false,
  "model_limits": "",
  "allow_ips": "",
  "group": "Gemini",
  "cross_group_retry": false
}
```

浏览器脚本：

```javascript
async () => {
  const uid = localStorage.getItem('uid');
  const headers = { 'new-api-user': uid, 'Content-Type': 'application/json' };
  const res = await fetch('/api/token/', {
    method: 'POST',
    credentials: 'include',
    headers,
    body: JSON.stringify({
      name: 'Gemini',
      remain_quota: 0,
      expired_time: -1,
      unlimited_quota: true,
      model_limits_enabled: false,
      model_limits: '',
      allow_ips: '',
      group: 'Gemini',
      cross_group_retry: false,
    }),
  });
  return await res.json();
}
```

返回示例：

```json
{ "message": "", "success": true }
```

> 创建接口只返回成功信息，不返回 Key 明文。需要后续调用 `POST /api/token/{id}/key` 获取完整 Key。

### 6.2 批量创建脚本

```javascript
async () => {
  const uid = localStorage.getItem('uid');
  const headers = { 'new-api-user': uid, 'Content-Type': 'application/json' };

  const groupsRes = await fetch('/api/user/self/groups', {
    credentials: 'include',
    headers: { 'new-api-user': uid },
  }).then(r => r.json());
  const keysRes = await fetch('/api/token/?p=1&size=100', {
    credentials: 'include',
    headers: { 'new-api-user': uid },
  }).then(r => r.json());

  const groups = Object.keys(groupsRes.data);
  const usedGroups = new Set(keysRes.data.items.map(k => k.group));
  const missing = groups.filter(g => !usedGroups.has(g));

  const results = [];
  for (const group of missing) {
    const res = await fetch('/api/token/', {
      method: 'POST',
      credentials: 'include',
      headers,
      body: JSON.stringify({
        name: group,
        remain_quota: 0,
        expired_time: -1,
        unlimited_quota: true,
        model_limits_enabled: false,
        model_limits: '',
        allow_ips: '',
        group: group,
        cross_group_retry: false,
      }),
    });
    results.push({ group, success: (await res.json()).success });
  }
  return results;
}
```

### 6.3 最终映射

执行后 11 个分组各对应 1 个 Key：

| API Key ID | 名称 | 分组 |
|---|---|---|
| 1708 | Gemini | Gemini |
| 1709 | adobe图 | adobe图 |
| 1710 | claude-max | claude-max |
| 1711 | claude-max-高并发 | claude-max-高并发 |
| 1712 | claude福利 | claude福利 |
| 1713 | codex-pro | codex-pro |
| 1707 | default | default |
| 1714 | gemini画图 | gemini画图 |
| 1715 | gpt-image | gpt-image |
| 1716 | grok-逆 | grok-逆 |
| 1636 | KV | 特价codex |

验证脚本：

```javascript
async () => {
  const uid = localStorage.getItem('uid');
  const headers = { 'new-api-user': uid };
  const [groupsRes, keysRes] = await Promise.all([
    fetch('/api/user/self/groups', { credentials: 'include', headers }).then(r => r.json()),
    fetch('/api/token/?p=1&size=100', { credentials: 'include', headers }).then(r => r.json()),
  ]);
  const groupNames = Object.keys(groupsRes.data);
  const usedGroups = new Set(keysRes.data.items.map(k => k.group));
  const missing = groupNames.filter(g => !usedGroups.has(g));
  return {
    total_keys: keysRes.data.items.length,
    total_groups: groupNames.length,
    missing_groups: missing,
  };
}
```

验证结果：`total_keys: 11`，`total_groups: 11`，`missing_groups: []`。

## 7. 获取每个分组对应 API Key 的模型列表

### 7.1 获取完整 API Key

Key 列表中的 `key` 字段是脱敏的（如 `sk-rBni**********mjhJ`），需要调用 `POST /api/token/{id}/key` 获取明文：

```javascript
async () => {
  const uid = localStorage.getItem('uid');
  const res = await fetch('/api/token/1708/key', {
    method: 'POST',
    credentials: 'include',
    headers: { 'new-api-user': uid },
  });
  return await res.json();
}
```

返回示例：

```json
{
  "success": true,
  "message": "",
  "data": {
    "key": "4Rb4...stVu"
  }
}
```

> 注意：API 返回的 Key 明文**不含** `sk-` 前缀，UI 上显示的 `sk-` 仅为前端展示。调用 `/v1/models` 时使用完整明文即可（如 `4Rb4...stVu`），也可在部分客户端使用 `sk-4Rb4...stVu`。

### 7.2 确认调用格式

探测端点：

```
GET https://torchai.ai/v1/models
```

认证头：

```
Authorization: Bearer <api_key_plaintext>
```

curl 示例：

```bash
curl -s https://torchai.ai/v1/models \
  -H "Authorization: Bearer 4Rb4...stVu"
```

### 7.3 批量探测模型列表

```javascript
async () => {
  const uid = localStorage.getItem('uid');
  const headers = { 'new-api-user': uid };
  const keysRes = await fetch('/api/token/?p=1&size=100', {
    credentials: 'include',
    headers,
  }).then(r => r.json());
  const keys = keysRes.data.items;

  const results = [];
  for (const k of keys) {
    const keyRes = await fetch(`/api/token/${k.id}/key`, {
      method: 'POST',
      credentials: 'include',
      headers,
    }).then(r => r.json());
    const fullKey = keyRes.data.key;

    const r = await fetch('/v1/models', {
      headers: { 'Authorization': `Bearer ${fullKey}` },
    });
    const body = await r.json();
    const list = body?.data || [];
    results.push({
      key_id: k.id,
      name: k.name,
      group: k.group,
      status: r.status,
      model_count: list.length,
      models: list.map(m => m.id),
    });
  }
  return results;
}
```

### 7.4 各分组模型列表结果

| 分组 | Key ID | 模型数 | 模型列表 |
|---|---|---|---|
| Gemini | 1708 | 10 | gemini-3-flash-preview-thinking-128, gemini-3.1-flash-lite-preview, gemini-3.1-flash-lite, gemini-3-flash-thinking-128, gemini-3.1-pro-preview-thinking-128, gemini-3.1-pro-preview-thinking-low, gemini-3.1-pro-preview, gemini-3-flash-preview, gemini-3.5-flash, gemini-3-pro-preview |
| adobe图 | 1709 | 1 | gpt-image-2 |
| claude-max | 1710 | 6 | claude-opus-4-6, claude-sonnet-5, claude-opus-4-8, claude-haiku-4-5-20251001, claude-sonnet-4-6, claude-opus-4-7 |
| claude-max-高并发 | 1711 | 7 | claude-haiku-4-5-20251001, claude-sonnet-4-6, claude-opus-4-7, claude-fable-5, claude-opus-4-6, claude-sonnet-5, claude-opus-4-8 |
| claude福利 | 1712 | 0 | （该分组当前无可用模型） |
| codex-pro | 1713 | 24 | gpt-5.3-codex-spark, gpt-5.4-mini(xhigh), gpt-5.4(high)[1M], gpt-5.4-mini, gpt-5.4, gpt-5.4-mini(medium), gpt-5.4-mini(high), codex-auto-review, gpt-5.5(low), gpt-5.5, gpt-5.5(medium), gpt-5.4-mini(low), gpt-5.4(medium)[1M], gpt-image-2, gpt-5.5-openai-compact, gpt-5.6-terra, gpt-5.4-openai-compact, gpt-5.6-luna, gpt-5.4(xhigh)[1M], gpt-5.4(low)[1M], gpt-5.4(xhigh), gpt-5.5(xhigh), gpt-5.5(high), gpt-5.6-sol |
| default | 1707 | 24 | 与 codex-pro 相同 |
| gemini画图 | 1714 | 3 | gemini-3-pro-image-preview, gemini-3.1-flash-image-preview, gemini-3.1-flash-lite-image |
| gpt-image | 1715 | 1 | gpt-image-2 |
| grok-逆 | 1716 | 1 | grok-4.5 |
| 特价codex | 1636 | 6 | gpt-5.6-luna, gpt-5.5, gpt-5.4-mini, gpt-5.4, gpt-5.6-terra, gpt-5.6-sol |

## 8. 注意事项

1. **不要写死密码**：文档中的邮箱/密码仅用于本次示例，自动化脚本请用环境变量或密钥管理工具注入。
2. **必须同时携带 Cookie 和 `new-api-user` 头**：缺少任意一个都会导致 401，错误信息通常为 "无权进行此操作，未提供 New-Api-User"。
3. **分组以名称字符串标识**：New-API 中 `group` 字段直接传分组名称（如 `"Gemini"`），不是数字 ID。
4. **创建 Key 时一并绑定分组**：`POST /api/token/` 的请求体里直接包含 `group`，无需像 sub2api 那样先创建再 PUT 更新。
5. **Key 明文需要单独获取**：创建/列表接口只返回脱敏 Key，完整 Key 通过 `POST /api/token/{id}/key` 获取。
6. **API Key 调用模型列表**：使用 `GET https://torchai.ai/v1/models`，认证头 `Authorization: Bearer <明文Key>`。返回的模型按 Key 所属分组隔离。
7. **不要删除已有 Key**：除非用户明确要求，否则只新增缺失分组对应的 Key。本案例中只新增了缺失分组的 Key，未触碰已有的 `KV` Key。
8. **Turnstile 验证码**：登录接口 `POST /api/user/login?turnstile=` 目前 `turnstile=` 空值可通过，但生产环境需根据实际策略处理。

## 9. 完整端到端 Python 脚本示例

```python
import os
import requests

BASE_URL = "https://torchai.ai"
EMAIL = os.environ["TORCHAI_EMAIL"]
PASSWORD = os.environ["TORCHAI_PASSWORD"]


def login(email: str, password: str) -> tuple[int, requests.Session]:
    """登录并返回 (user_id, session)。"""
    session = requests.Session()
    r = session.post(
        f"{BASE_URL}/api/user/login",
        params={"turnstile": ""},
        headers={"Content-Type": "application/json"},
        json={"username": email, "password": password},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()["data"]
    return data["id"], session


def api_get(session: requests.Session, user_id: int, path: str):
    r = session.get(
        f"{BASE_URL}{path}",
        headers={"new-api-user": str(user_id)},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def api_post(session: requests.Session, user_id: int, path: str, json_data: dict):
    r = session.post(
        f"{BASE_URL}{path}",
        headers={"new-api-user": str(user_id), "Content-Type": "application/json"},
        json=json_data,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_groups(session: requests.Session, user_id: int) -> dict:
    return api_get(session, user_id, "/api/user/self/groups")["data"]


def get_keys(session: requests.Session, user_id: int) -> list:
    return api_get(session, user_id, "/api/token/?p=1&size=100")["data"]["items"]


def create_key(session: requests.Session, user_id: int, group: str):
    return api_post(session, user_id, "/api/token/", {
        "name": group,
        "remain_quota": 0,
        "expired_time": -1,
        "unlimited_quota": True,
        "model_limits_enabled": False,
        "model_limits": "",
        "allow_ips": "",
        "group": group,
        "cross_group_retry": False,
    })


def get_full_key(session: requests.Session, user_id: int, key_id: int) -> str:
    r = api_post(session, user_id, f"/api/token/{key_id}/key", {})
    return r["data"]["key"]


def list_models(api_key: str):
    r = requests.get(
        f"{BASE_URL}/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    r.raise_for_status()
    return [m["id"] for m in r.json().get("data", [])]


def ensure_keys_cover_all_groups(user_id: int, session: requests.Session):
    """
    1. 拉取所有分组和现有 Key。
    2. 找出还没有 Key 的分组。
    3. 为每个缺失分组创建一个 Key（创建时直接绑定分组）。
    4. 返回是否所有分组都已覆盖。
    """
    groups = get_groups(session, user_id)
    keys = get_keys(session, user_id)

    used_groups = {k["group"] for k in keys if k.get("group")}

    for group in groups:
        if group in used_groups:
            print(f"[跳过] 分组 {group} 已有 Key")
            continue
        create_key(session, user_id, group)
        used_groups.add(group)
        print(f"[创建] 分组 {group}")

    # 再次拉取并验证
    keys = get_keys(session, user_id)
    covered = {k["group"] for k in keys if k.get("group")}
    missing = [g for g in groups if g not in covered]
    if missing:
        raise RuntimeError(f"仍有分组未被覆盖: {missing}")

    print(f"[完成] 共 {len(groups)} 个分组，每个分组都有 1 个 Key 对应。")
    return keys


def main():
    user_id, session = login(EMAIL, PASSWORD)
    keys = ensure_keys_cover_all_groups(user_id, session)

    # 对每个 Key 探测可用模型
    for k in keys:
        full_key = get_full_key(session, user_id, k["id"])
        models = list_models(full_key)
        print(f"\n分组: {k['group']} ({len(models)} 个模型)")
        print(", ".join(models[:5]) + ("..." if len(models) > 5 else ""))


if __name__ == "__main__":
    main()
```

## 10. 关键接口速查

| 用途 | 方法 | URL | 特殊说明 |
|---|---|---|---|
| 登录 | POST | `/api/user/login?turnstile=` | 请求体 `{"username": ..., "password": ...}` |
| 获取当前用户信息 | GET | `/api/user/self` | 需 `new-api-user` 头 |
| 获取可选分组 | GET | `/api/user/self/groups` | 返回对象，键为分组名 |
| 获取 API Key 列表 | GET | `/api/token/?p=1&size=20` | 需 `new-api-user` 头 |
| 创建 API Key | POST | `/api/token/` | 请求体直接含 `group` 名称 |
| 更新 API Key | PUT | `/api/token/` | 请求体需包含 `id` |
| 获取 Key 明文 | POST | `/api/token/{id}/key` | 返回完整 Key |
| 获取模型列表 | GET | `/v1/models` | 用 Key 作为 `Authorization: Bearer` |

## 11. 溯源信息

- 站点：`https://torchai.ai/`
- 技术栈：New-API（`x-new-api-version: v1.0.0-rc.21`）
- 分析日期：2026-07-21
- 工具：Chrome DevTools MCP + 浏览器内 `fetch`
- 用户 ID：见本机 auth_state / 登录响应（文档不落真实 id）
