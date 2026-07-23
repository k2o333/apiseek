# 正式契约索引

> 发布状态：formal controlled workspace
> Bundle 状态：`draft`
> 最近复核：2026-07-23

本目录是仓库契约的正式入口。**当前 bundle 尚未冻结**：人读边界已经进入受控目录，但 machine schema、vectors、provider 探针和部分实现对齐尚未完成。逐项状态以 [contracts/manifest.yaml](./contracts/manifest.yaml) 为唯一清单。

## 契约范围

| 类别 | 文件 | 当前状态 | 冻结对象 |
|---|---|---|---|
| Storage | [storage.md](./contracts/storage.md) | draft | latest/events、hash、失败保旧、legacy reader |
| CLI | [cli.md](./contracts/cli.md) | draft | mode、副作用、锁、退出码 |
| Remote mutation | [remote-mutation.md](./contracts/remote-mutation.md) | draft | managed identity、coverage、fail closed |
| Provider | [provider-profiles.md](./contracts/provider-profiles.md) | planned | 已探活 endpoint/envelope/capability |
| Config | [config.md](./contracts/config.md) | planned | env 类型、默认值、secret 与写门禁 |
| Deployment/security | [deployment-security.md](./contracts/deployment-security.md) | draft | timer、installer、hardening、secret 边界 |

## 当前支持边界

- `sub2api-v1`：仅逐站验证过 login/refresh/groups/keys/models 形状的部署；
- `newapi-legacy-groups-v1`：BotCF/TorchAI 已探活的 session groups 协议；
- `torchai-rc21-models-v1`：目标 profile，自动 create/repair capability 在探针门禁闭合前不得视为 frozen；
- BotCF models、任意 New-API 版本和 dashboard token 不在正式支持承诺内。

## 已确定的目标决策

1. Sub2API groups 保持 legacy 格式，不因“统一”主动迁 v2；
2. models 目标统一 `backend`、四字段 full result、trim/去重/排序；
3. legacy models 只允许严格兼容读取，禁止 write-on-read；
4. malformed/non-string model id 使整组 contract fail，失败保旧；
5. New-API groups 使用 event tail dedup，models v1 保持 at-least-once；
6. remote mutation 在分页、hydration、provider 语义或写结果不确定时 fail closed；
7. models 自动写默认关闭，仅显式 bootstrap、已 bootstrap refresh 或 opt-in T-new 可以触发。

## 尚未冻结

- JSON Schema、config manifest 和完整 vectors 尚未发布；
- Sub2API models 仍存在缺 `backend`、三字段 full result、`source=daily` 等 legacy 行为；
- 两 backend 的 strict model-id 行为尚未完全一致；
- 部分 credential-bearing unit 尚未对齐 `UMask` 和 hardening；
- installer 尚未全部强制验证 bootstrap/provider capability；
- TorchAI mutation profile 的脱敏探针门禁尚未形成 frozen evidence；
- contract owner 仍是占位团队名。

因此，`docs/02 specs` 目前用于指导实现收敛和评审，不能作为已兑现的兼容声明。

## 与其他目录的关系

- 治理规则：[docs/01 governance](../01%20governance/README.md)
- 当前实现设计：[docs/03 designs](../03%20designs/README.md)
- 原始评审、inventory 与实施门禁：[docs/drafts/specs](../drafts/specs/README.md)

发生冲突时按 [契约治理规则](../01%20governance/contract-governance.md) 的证据优先级处理。
