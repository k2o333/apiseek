---
name: "sub2api-gateway-automation"
description: "Automates login and data extraction on sub2api-based AI API gateway sites (fetch groups, API keys, endpoints). Invoke when user asks to interact with a sub2api deployment, e.g. login, list groups, inspect keys."
---

# sub2api Gateway Automation

This skill automates interaction with sub2api-based AI API gateway sites. These platforms share common UI routes, API endpoints, and authentication patterns.

## When to Invoke

Use this skill when the user wants to:
- Log in to a sub2api-based site.
- Retrieve available model groups and rate multipliers.
- Inspect API keys, endpoints, or account overview.
- Verify whether an unknown site is running sub2api.

## How to Identify a sub2api Deployment

Look for these signatures:
- Routes: `/login`, `/register`, `/dashboard`, `/keys`, `/usage`, `/subscriptions`, `/purchase`, `/orders`, `/redeem`, `/profile`, `/affiliate`.
- Site title/branding often contains "AI API Gateway" or "Subscription to API Conversion Platform".
- After login, the dashboard shows balance, API endpoint, and navigation items matching the routes above.
- LocalStorage stores the JWT under key `auth_token` and serialized user info under `auth_user`.
- Group API endpoint: `GET /api/v1/groups/available` returns JSON with fields like `name`, `platform`, `rate_multiplier`, `status`, `description`.

## Required Tools

Prefer the Chrome DevTools MCP (`mcp_chrome-devtools`) for browser automation:
- `navigate_page`
- `take_snapshot`
- `fill_form`
- `click`
- `evaluate_script`
- `wait_for`

## Standard Workflow

### 1. Login

1. Navigate to `https://<domain>/login`.
2. Take a snapshot to locate the email and password textboxes and the submit button.
3. Fill the form with credentials provided by the user.
4. Click the login button.
5. Wait for dashboard indicators such as "仪表盘", "Dashboard", "API 密钥", or "keys".

Common login field labels:
- Email: `邮箱`, `Email`, `E-mail`
- Password: `密码`, `Password`
- Submit: `登录`, `Login`, `Sign in`

If login fails, confirm the password with the user before retrying.

### 2. Retrieve Available Groups

Use the authenticated API endpoint directly:

```javascript
async () => {
  const token = localStorage.getItem('auth_token');
  const res = await fetch('/api/v1/groups/available', {
    headers: { 'Authorization': `Bearer ${token}` }
  });
  return { status: res.status, data: await res.json() };
}
```

Expected successful response shape:

```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "id": 1,
      "name": "Group Name",
      "description": "...",
      "platform": "openai|anthropic|gemini|grok|...",
      "rate_multiplier": 0.1,
      "status": "active",
      "subscription_type": "standard",
      "rpm_limit": 0
    }
  ]
}
```

Present the groups in a concise table: **Name**, **Platform**, **Rate Multiplier**, **Status**, **Description**.

### 3. Inspect API Keys (Optional)

Navigate to `/keys`. The page lists existing keys with columns such as:
- Name
- API key (masked)
- Current group
- Usage
- Status
- Created time

To see all available groups for a key, click the group selector next to a key (label often includes `选择分组` or `点击更换分组`).

### 4. Determine If Site Is sub2api

If the user asks to identify the platform:
1. Check the page title/description and route structure.
2. Verify the presence of `auth_token` in `localStorage` after login.
3. Confirm `GET /api/v1/groups/available` exists and matches the response shape above.
4. Compare with known sub2api deployments (e.g. `aiapibank.com`, `little-api.top`).

## Important Notes

- Do not hardcode user credentials in any generated artifacts.
- The JWT in `auth_token` has an expiration time; refresh or re-login if requests return 401.
- Some keys may be bound to groups that no longer appear in `/api/v1/groups/available` (disabled/removed groups).
- Rate limits (`rpm_limit`) and group exclusivity (`is_exclusive`) should be reported when relevant.
