# BotCF 普通用户令牌分组查询手册

> **安全提示**：本文档包含用于登录 BotCF 控制台的用户名和密码。请仅在受控环境内部分享，避免将明文凭据提交到公共仓库或聊天频道。如用于自动化脚本，建议使用环境变量或密钥管理服务存储凭据。

---

## 1. 目标

通过用户名和密码登录 BotCF（`https://botcf.com`），获取**普通用户**在创建/编辑 API 令牌时可以选择的**令牌分组**完整列表。

---

## 2. 前置信息

| 项目 | 值 |
| :--- | :--- |
| 站点地址 | `https://botcf.com` |
| 登录页 | `https://botcf.com/login` |
| 令牌管理页 | `https://botcf.com/console/token` |
| 用户名 | `k2o333` |
| 密码 | `bblswuji1` |
| 分组数据源 API | `GET https://botcf.com/api/user/self/groups` |

---

## 3. 登录流程

### 3.1 打开登录页

使用浏览器访问：

```text
https://botcf.com/login
```

页面会显示 **Bot Compute Fabric / 机器人算力网** 的登录界面。

### 3.2 选择邮箱/用户名登录

页面默认展示 OAuth 登录选项（GitHub、Discord、Google 等）。需要点击：

```text
使用 邮箱或用户名 登录
```

才会切换为账号密码表单。

### 3.3 填写账号密码并勾选协议

在表单中填入：

| 字段 | 值 |
| :--- | :--- |
| 用户名或邮箱 | `k2o333` |
| 密码 | `bblswuji1` |

勾选复选框：

```text
我已阅读并同意用户协议和隐私政策
```

### 3.4 点击继续

点击 **继续** 按钮。登录成功后，浏览器会跳转到控制台首页：

```text
https://botcf.com/console
```

此时用户已处于登录状态，Cookie 中会写入 `session`，后续请求会自动携带该凭证。

---

## 4. 查询令牌分组（推荐：API 方式）

登录后，直接请求分组数据源接口即可拿到完整列表。该接口**不需要额外参数**，依赖登录 Cookie 鉴权。

### 4.1 请求示例

```http
GET https://botcf.com/api/user/self/groups
Cookie: session=<登录后的 session cookie>
```

或使用浏览器控制台执行：

```javascript
const res = await fetch('/api/user/self/groups', { credentials: 'include' });
const json = await res.json();
console.log(Object.keys(json.data));
```

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

- `data` 的键即为可选分组名称（包含 emoji 图标）。
- `desc` 为该分组的描述。
- `ratio` 为该分组的倍率。

### 4.3 当前可选项数量

对于用户 `k2o333`，当前接口返回 **34 个**可选分组。

---

## 5. 查询令牌分组（UI 验证方式）

如果希望通过前端界面确认分组选项，可按以下步骤操作：

### 5.1 进入令牌管理页

登录后访问：

```text
https://botcf.com/console/token
```

### 5.2 编辑任意令牌

在令牌列表中找到任意一行，点击右侧的 **编辑** 按钮，弹出对话框：

```text
更新令牌信息
```

### 5.3 展开令牌分组下拉框

在对话框中找到字段：

```text
令牌分组(可多选,分组异常自动切换,建议2-3个分组确保服务稳定性。)
```

点击该下拉框，会展开一个可滚动的多选列表。列表中的每一项即为一个可选分组，与 `/api/user/self/groups` 返回的分组一致。

### 5.4 注意事项

- 下拉框为虚拟滚动或懒加载列表，截图或快照可能只显示前几个分组（如 `Azure-OpenAI限时特惠`、`Codex-Plus`、`Codex-Fast`、`Openai-Azure`、`CN高速开源权重` 等）。
- 如需完整列表，**务必以 `/api/user/self/groups` 接口返回为准**。

---

## 6. 完整分组列表（34 个）

按前端页面中的分类整理如下：

### Anthropic / Claude（6 个）

| 图标 | 分组名称 | 倍率 | 描述 |
| :---: | :--- | :---: | :--- |
| 👋 | Claude-AWS-B | 2.4x | — |
| 😡 | Claude-Max-外接 | 0.98x | 五分钟缓存，可给 Claude Code 外使用 |
| 😡 | Claude-Max | 0.88x | 一小时缓存，仅限 Claude Code 使用 |
| 🍊 | Claude-Kiro-Power | 0.18x | — |
| 🍊 | Claude-Kiro | 0.12x | 圣人反代 |
| 😅 | Claude-Web | 0.15x | 网页反代 |

### OpenAI / Codex（5 个）

| 图标 | 分组名称 | 倍率 | 描述 |
| :---: | :--- | :---: | :--- |
| 🃏 | Openai-Azure | 1.6x | 速刷蒸馏 |
| 😡 | Codex-Pro | 0.25x | PRO 号池 |
| ⚡ | Codex-Fast | 0.18x | 强制优先混池加大量 pro 兜底 |
| ⚓ | Codex-Plus | 0.08x | 目前性价比之选 plus 号池 |
| — | Azure-OpenAI限时特惠 | 1.0x | Azure OpenAI 限时特惠 |

### 其他国外模型（3 个）

| 图标 | 分组名称 | 倍率 | 描述 |
| :---: | :--- | :---: | :--- |
| 🫳 | Gemini-CLI | 1.2x | 不稳定经常死号 |
| 🤡 | Gemini-Pro | 0.28x | 小丑爱用 |
| 💃 | Grok-Mix | 0.15x | grokmix |

### 开源 / 自部署（3 个）

| 图标 | 分组名称 | 倍率 | 描述 |
| :---: | :--- | :---: | :--- |
| 😡 | KIMI-K3特供 | 0.47x | OpenCode Go Kimi K3 三路特供 |
| 🇨🇳 | 高速开源权重 | 0.5x | 高速开源权重模型线路 |
| 🇨🇳 | 高速满血GLM5.2 | 0.3x | GLM-5.2 高速线路 |

### 生图（4 个）

| 图标 | 分组名称 | 倍率 | 描述 |
| :---: | :--- | :---: | :--- |
| 🌆 | Image2-生图-BotCF01 | 1.0x | 文生图 / 图生图 / 改图，支持 1K/2K/4K |
| 🍌 | Nana-Banana-生图-BotCF01 | 1.0x | 文生图 / 图生图，异步模型可用 |
| 🖼️ | Gemini-生图-BotCF01 | 1.0x | Gemini 图片，推荐 /v1/chat/completions |
| 🧠 | Grok-生图-BotCF01 | 1.0x | Grok Imagine 文生图 / 图生图 |

### 视频（7 个）

| 图标 | 分组名称 | 倍率 | 描述 |
| :---: | :--- | :---: | :--- |
| 🎥 | SeedDance-Pro-视频-BotCF01 | 1.0x | POST /v1/videos，duration 4-15 秒 |
| 🎞️ | SeedDance-Fast-视频-BotCF01 | 1.0x | POST /v1/videos，fast / mini |
| 🎞️ | SD2 Fast/Standard视频 | 1.0x | — |
| 🎞️ | SeedDance-Fast-Mini-视频-BOTCF03(国内满血渠道) | 1.5x | 国内满血，不支持 NSFW |
| 🎞️ | SeedDance-Fast-视频-BOTCF02(国外满血渠道可NSFW) | 1.5x | 国外满血，支持 NSFW |
| 🎥 | SeedDance-Pro-视频-BOTCF02(国外满血渠道可NSFW) | 1.5x | 国外满血，支持 NSFW |
| 🎥 | SeedDance-Pro-视频-BOTCF03(国内满血渠道) | 1.5x | 国内满血，不支持 NSFW |

### 多模态工具（2 个）

| 图标 | 分组名称 | 倍率 | 描述 |
| :---: | :--- | :---: | :--- |
| 🧬 | Embedding | 1.0x | 文本向量化 |
| 🔎 | Reranker | 1.0x | 检索重排 |

### 其他福利分组（4 个）

| 图标 | 分组名称 | 倍率 | 描述 |
| :---: | :--- | :---: | :--- |
| 🇨🇳 | 高速无缓开源权重 | 0.2x | — |
| 😵 | 骨折价高速无缓GLM5.2 | 0.1x | 海外羊毛 |
| 😭 | 黄叔叔公益喵 | 0.0x | 黄伟达 |
| 😭 | 这集神了:BOTCF零元购牢马不限速 | 0.01x | — |

---

## 7. 自动化脚本示例

以下是一个可用于自动化获取分组列表的脚本示例（使用 Python + requests）：

```python
import requests

BASE_URL = "https://botcf.com"
LOGIN_URL = f"{BASE_URL}/api/user/login"
GROUPS_URL = f"{BASE_URL}/api/user/self/groups"

session = requests.Session()

# 1. 登录
resp = session.post(
    LOGIN_URL,
    json={
        "username": "k2o333",
        "password": "bblswuji1"
    },
    params={"turnstile": ""}
)
resp.raise_for_status()

# 2. 获取分组
resp = session.get(GROUPS_URL)
resp.raise_for_status()
groups = resp.json()["data"]

for name, info in groups.items():
    print(f"{name}: 倍率={info['ratio']}, 描述={info['desc']}")
```

> **注意**：
> - 登录接口可能包含 Turnstile 验证码校验。在受信任环境或测试账号场景下，`turnstile=` 空值可能通过；生产环境需根据实际验证码策略处理。
> - 密码明文仅用于示例，实际请使用环境变量。

---

## 8. 常见问题

**Q：为什么模型广场页面看到的分组数量与令牌分组不一致？**

模型广场页面左侧的"可用令牌分组"默认只展示当前用户可见的分组，与 `/api/user/self/groups` 返回结果一致。如果某些分组未显示，可能是因为该分组被管理员隐藏、仅对特定用户可见，或尚未启用。

**Q：配置文件 `botcf-pricing-group-order.json` 中分组更多，为什么接口只返回 34 个？**

`botcf-pricing-group-order.json` 中定义了站点的全部分组配置（包含管理员分组、测试分组等），但 `/api/user/self/groups` 会根据当前登录用户的权限过滤，只返回该用户实际可以选择的分组。

**Q：接口返回的分组数量会变吗？**

会。管理员新增、删除、隐藏分组，或调整用户可见性后，`/api/user/self/groups` 的返回结果都会变化。因此自动化流程中应以接口实时返回为准。

---

## 9. 关键接口速查

| 用途 | 方法 | URL |
| :--- | :--- | :--- |
| 登录 | POST | `https://botcf.com/api/user/login?turnstile=` |
| 获取当前用户信息 | GET | `https://botcf.com/api/user/self` |
| 获取当前用户可选分组 | GET | `https://botcf.com/api/user/self/groups` |
| 获取令牌列表 | GET | `https://botcf.com/api/token/?p=1&size=10` |
| 获取模型定价 | GET | `https://botcf.com/api/pricing` |
