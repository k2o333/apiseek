# 契约冻结实施门禁

> 类型：工程 checklist，不进入 frozen bundle。  
> 状态：draft。

## 1. 决策与治理

- [x] D1-D9 已记录；
- [x] final root 选定为 `docs/02 specs/contracts/`；
- [x] as-is inventory 与 target contract 拆分；
- [ ] owner 名单确认，不再使用占位 `repository-maintainers`；
- [ ] 在 `docs/01 governance/` 发布 governance；
- [ ] 创建 frozen bundle `CHANGELOG.md`。

## 2. Storage

- [ ] 写 `sub2api-groups-legacy` schema/识别器；
- [ ] 写 Sub2API legacy hash golden vectors；
- [ ] 写 `newapi-groups-v1` latest/event JSON Schemas；
- [ ] 写 A->B->A、半行、event-before-latest vectors；
- [ ] 写统一 `models-v1` latest/event schemas；
- [ ] Sub2API/New-API parser 严格拒绝 malformed/non-string id；
- [ ] 两 backend models 统一 trim/去重/排序；
- [ ] 两 backend writer 输出 backend + 四字段 full result；
- [ ] legacy models compatibility reader；
- [ ] 显式幂等 migration，不产生业务 event；
- [ ] reader 严格验证 version/site/backend；
- [ ] 两 backend writer 都使用 `source=bootstrap|refresh|incremental`；daily timer 写 refresh。

## 3. CLI

- [x] New-API models flags 已接入且 parser 互斥；
- [ ] Sub2API parser 层互斥或保留手工互斥的 contract 测试；
- [ ] `--validate` 与 models flags 的组合策略写死；
- [ ] preflight fail=1、lock=2 的两 backend tests；
- [ ] Sub2API refresh 增加 bootstrap cold gate；
- [ ] default cold 零 mutation/models request tests；
- [ ] T-new nonfatal/default 与 full fatal 的对照测试；
- [ ] 决定并发布 stdout JSON schema 或 human-only 声明。

## 4. Remote mutation/provider

- [ ] TorchAI 写路径只读脱敏探针全部闭合；
- [ ] BotCF models 保持 unsupported，未误启用；
- [ ] pagination fail-closed vectors；
- [ ] hydration unknown/create=0 vectors；
- [ ] unknown POST 不二次盲发 vectors；
- [ ] managed identity UTF-8/碰撞 vectors；
- [ ] user resource 永不 update/delete tests；
- [ ] models Key 401 不 management login tests；
- [ ] provider fixtures 通过 secret scan。

## 5. Config/deployment/security

- [ ] 写 common/sub2api/newapi config manifests；
- [ ] 生成或校验全部 `.env.example`；
- [ ] incremental/bootstrap/daily enable 四层门禁测试；
- [ ] `sub2api-monitor-once@` 增加 UMask；
- [ ] `sub2api-models-daily@` 增加 UMask；
- [ ] `newapi-models-daily@` 对齐完整 hardening；
- [ ] legacy simple 标 deprecated；
- [ ] installer 默认不 enable models；
- [ ] enable models 强校验 bootstrap/provider capability；
- [ ] 所有 unit 通过 `systemd-analyze verify` 和静态 contract tests；
- [ ] stdout/stderr/log/latest/events 全面 secret scan。

## 6. 文档与发布

- [ ] frozen contract 每项 owner/status/last_reviewed 完整；
- [ ] machine schema 与人读不变量无重复冲突；
- [ ] README/skills/design 改为链接 frozen contract；
- [ ] 旧 simple/reference 标 deprecated；
- [ ] 全量测试通过；
- [ ] migration 在现网文件副本上演练；
- [ ] changelog、rollback 和 effective date 完整；
- [ ] 从 drafts 复制/提升到 final root 后重新跑 CI；
- [ ] 二次评审签核 frozen status。

## 7. 当前基线

2026-07-22：

```text
172 passed, 7 subtests passed
```

这只是实现基线。上述 machine schema、migration、hardening 和 provider 探针未完成前，manifest 必须保持 `status: draft`。
