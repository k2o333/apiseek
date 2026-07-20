# Torch AI 普通用户令牌分组查询手册

> **安全提示**：本文档包含用于登录 Torch AI 控制台的用户名和密码。请仅在受控环境内部分享，避免将明文凭据提交到公共仓库或聊天频道。如用于自动化脚本，建议使用环境变量或密钥管理服务存储凭据。

---

## 1. 目标

通过用户名和密码登录 Torch AI（`https://torchai.ai`），获取**普通用户**在创建/编辑 API 令牌时可以选择的**令牌分组**完整列表。

---

## 2. 前置信息

| 项目 | 值 |
| :--- | :--- |
| 站点地址 | `https://torchai.ai` |
| 登录页 | `https://torchai.ai/sign-in` |
| 令牌管理页 | `https://torchai.ai/keys` |
| 用户名 | `zyc0926@gmail.com` |
| 密码 | `bblswuji1` |
| 平台底层 | New-API (`x-new-api-version: v1.0.0-rc.21`) |
| 登录接口 | `POST https://torchai.ai/api/user/login?turnstile=` |
| 分组数据源 API | `GET https://torchai.ai/api/user/self/groups` |
| 必需请求头 | `new-api-user: <用户ID>` |

---

## 3. 登录流程

### 3.1 打开登录页

使用浏览器访问：

```text
https://torchai.ai/sign-in
```

页面会显示 **Torch AI** 的登录界面。

### 3.2 填写账号密码并勾选协议

在表单中填入：

| 字段 | 值 |
| :--- | :--- |
| 用户名或电子邮件 | `zyc0926@gmail.com` |
| 密码 | `bblswuji1` |

勾选复选框：

```text
我已阅读并同意 用户协议 and the 隐私政策.
```

### 3.3 点击登录

点击 **登录** 按钮。登录成功后，浏览器会跳转到控制台首页：

```text
https://torchai.ai/dashboard/overview
```

此时用户已处于登录状态，Cookie 中会写入 `session`，后续请求会自动携带该凭证。

---

## 4. 查询令牌分组（推荐：API 方式）

登录后，请求分组数据源接口即可拿到完整列表。该接口**不需要额外参数**，但依赖登录 Cookie 鉴权，并且**必须附加 `new-api-user` 请求头**。

### 4.1 请求示例

```http
GET https://torchai.ai/api/user/self/groups
Cookie: session=<登录后的 session cookie>
new-api-user: <登录后返回的用户ID>
```

或使用浏览器控制台执行：

```javascript
const uid = localStorage.getItem('uid');
const res = await fetch('/api/user/self/groups', {
  credentials: 'include',
  headers: { 'new-api-user': uid }
});
const json = await res.json();
console.log(json.data);
```

> **注意**：用户 ID 在登录响应中返回，也可以通过 `localStorage.getItem('uid')` 或 `JSON.parse(localStorage.getItem('user')).id` 获取。

### 4.2 响应结构

```json
{
  "success": true,
  "data": {
    "分组名称": {
      "desc": "分组描述",
      "ratio": 1.0
    }
  }
}
```

- `data` 的键即为可选分组名称。
- `desc` 为该分组的描述。
- `ratio` 为该分组的倍率。

### 4.3 当前可选项数量

对于用户 `zyc0926@gmail.com`，当前接口返回 **11 个**可选分组。

---

## 5. 查询令牌分组（UI 验证方式）

如果希望通过前端界面确认分组选项，可按以下步骤操作：

### 5.1 进入 API 密钥管理页

登录后访问：

```text
https://torchai.ai/keys
```

### 5.2 查看已有密钥或创建密钥

在密钥列表中找到任意一行，点击右侧的 **编辑** 按钮，或在列表中查看 **分组** 列。

### 5.3 展开令牌分组下拉框

在创建/编辑对话框中找到分组选择字段，点击下拉框会展开一个可选分组列表。列表中的每一项即为一个可选分组，与 `/api/user/self/groups` 返回的分组一致。

### 5.4 注意事项

- 如需完整列表，**务必以 `/api/user/self/groups` 接口返回为准**。
- 在页面中直接调用该接口时，必须同时满足：
  1. 已登录并携带 `session` Cookie；
  2. 请求头中包含 `new-api-user: <用户ID>`。
- 缺少 `new-api-user` 头会返回 `401`：
  ```json
  { "message": "无权进行此操作，未提供 New-Api-User", "success": false }
  ```

---

## 6. 完整分组列表（11 个）

| 分组名称 | 倍率 | 描述 |
| :--- | :---: | :--- |
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
| 特价codex | 0.13x | 特价codex（非pro） |

---

## 7. 自动化脚本示例

以下是一个可用于自动化获取分组列表的脚本示例（使用 Python + requests）：

```python
import requests

BASE_URL = "https://torchai.ai"
LOGIN_URL = f"{BASE_URL}/api/user/login"
GROUPS_URL = f"{BASE_URL}/api/user/self/groups"

session = requests.Session()

# 1. 登录
resp = session.post(
    LOGIN_URL,
    json={
        "username": "zyc0926@gmail.com",
        "password": "bblswuji1"
    },
    params={"turnstile": ""}
)
resp.raise_for_status()
login_data = resp.json()["data"]
user_id = login_data["id"]

# 2. 获取分组
resp = session.get(
    GROUPS_URL,
    headers={"new-api-user": str(user_id)}
)
resp.raise_for_status()
groups = resp.json()["data"]

for name, info in groups.items():
    print(f"{name}: 倍率={info['ratio']}, 描述={info['desc']}")
```

> **注意**：
> - 登录接口可能包含 Turnstile 验证码校验。在受信任环境或测试账号场景下，`turnstile=` 空值可能通过；生产环境需根据实际验证码策略处理。
> - 密码明文仅用于示例，实际请使用环境变量。
> - 请求分组接口时，`new-api-user` 头必须为用户 ID（数字），不是用户名。

---

## 8. 常见问题

**Q：为什么直接调用 `/api/user/self/groups` 返回 401？**

A：Torch AI 基于 New-API，分组接口除了需要登录 Cookie 外，还必须在请求头中提供 `new-api-user`。该值可在登录响应的 `id` 字段或浏览器 `localStorage` 的 `uid`/`user.id` 中获取。

**Q：为什么接口返回的分组数量和模型广场看到的不一致？**

A：模型广场可能展示的是平台全部分组或按模型筛选后的分组，而 `/api/user/self/groups` 只返回当前登录用户实际可选择的令牌分组。两者用途不同，以接口实时返回为准。

**Q：接口返回的分组数量会变吗？**

A：会。管理员新增、删除、隐藏分组，或调整用户可见性后，`/api/user/self/groups` 的返回结果都会变化。因此自动化流程中应以接口实时返回为准。

---

## 9. 关键接口速查

| 用途 | 方法 | URL |
| :--- | :--- | :--- |
| 登录 | POST | `https://torchai.ai/api/user/login?turnstile=` |
| 获取当前用户信息 | GET | `https://torchai.ai/api/user/self` |
| 获取当前用户可选分组 | GET | `https://torchai.ai/api/user/self/groups` |
| 获取 API 密钥列表 | GET | `https://torchai.ai/api/token/?p=1&size=20` |
| 获取模型定价 | GET | `https://torchai.ai/pricing` |
