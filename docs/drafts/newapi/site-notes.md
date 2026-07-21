# BotCF / TorchAI 站级说明与探活（修订）

手册：`docs/websites/botcf.md`、`torchai.md`（**凭据已改为占位符**；真实值仅 `sites/*.env`）。

本适配器名称：**legacy session 协议（BotCF/TorchAI 已探活）**，不承诺 New-API 全系。

---

## 1. 固定契约（两站相同）

| 项 | 值 |
|----|-----|
| Login | `POST /api/user/login?turnstile=`（turnstile **恒空**） |
| Body | `{"username":"<user>","password":"<pass>"}` |
| Groups | `GET /api/user/self/groups` |
| 鉴权 | Cookie 名 `session` |
| Redirect | **禁止**跟随 |

---

## 2. BotCF（`botcf`）

| 项 | 值 |
|----|-----|
| Origin | `https://botcf.com` |
| 浏览器页 | `/login`（UI 可能需切到用户名登录；API 不依赖该 UI） |
| `REQUIRE_NEW_API_USER_HEADER` | `0` |
| 手册量级 | 约数十个可选组（会变） |

### env 示例

```bash
MONITOR_SITE_ID=botcf
MONITOR_BASE_URL=https://botcf.com
MONITOR_USERNAME=<REDACTED>
MONITOR_PASSWORD=<REDACTED>
REQUIRE_NEW_API_USER_HEADER=0
LOG_LEVEL=INFO
```

### 探活

```bash
curl -sS -c /tmp/botcf.jar -X POST 'https://botcf.com/api/user/login?turnstile=' \
  -H 'Content-Type: application/json' \
  --max-redirs 0 \
  -d '{"username":"<from env>","password":"<from env>"}'
# 检查 HTTP 200、JSON success=true、jar 含 session；勿长期保存 jar 到仓库

curl -sS -b /tmp/botcf.jar 'https://botcf.com/api/user/self/groups'
```

---

## 3. TorchAI（`torchai`）

| 项 | 值 |
|----|-----|
| Origin | `https://torchai.ai` |
| 浏览器页 | `/sign-in` |
| `REQUIRE_NEW_API_USER_HEADER` | **`1`** |
| user id | 登录 `data.id`（正整数，**不是**邮箱） |
| 缺头典型错误 | `未提供 New-Api-User` / success=false |

### env 示例

```bash
MONITOR_SITE_ID=torchai
MONITOR_BASE_URL=https://torchai.ai
MONITOR_USERNAME=<REDACTED>
MONITOR_PASSWORD=<REDACTED>
REQUIRE_NEW_API_USER_HEADER=1
LOG_LEVEL=INFO
```

### 探活

```bash
curl -sS -c /tmp/torch.jar -X POST 'https://torchai.ai/api/user/login?turnstile=' \
  -H 'Content-Type: application/json' \
  --max-redirs 0 \
  -d '{"username":"<from env>","password":"<from env>"}'
# 解析 data.id

curl -sS -b /tmp/torch.jar 'https://torchai.ai/api/user/self/groups' \
  -H 'new-api-user: <id>'
```

---

## 4. 两站差异

| 项 | BotCF | TorchAI |
|----|-------|---------|
| `new-api-user` | 否 | **是** |
| 其余 API 形状 | 同 | 同 |

一个客户端 + `REQUIRE_NEW_API_USER_HEADER` 即可。

---

## 5. ratio 与上游演进

- v1 只接受有限非负数值。  
- 若返回 `"自动"` 等：整包 **contract**（不假装通用）。  
- 上游 main 线认证可能迁往 dashboard token；**本适配器不跟风自动兼容**；第三站/大版本变更前重新探活。

---

## 6. 探活通过标准

- [ ] login：200 + success=true + session  
- [ ] Torch：合法正整数 id  
- [ ] groups：success=true + 非空 object  
- [ ] 第二轮进程 **不**密码登录（复用 session）  
- [ ] 空 data / 非法 ratio 不覆盖 latest  
- [ ] 强制验证码时：`captcha` 停止，不刷登录  

---

## 7. 安全

- 密码轮换：若手册明文曾进 Git，见 architecture §2。  
- 探活用的 cookie jar 用完删除。  
- 勿把真实密码写回 websites 文档或示例。  
