# 实现检查单（修订）

冲突以 architecture / data-model 为准。完成 §A 安全与 §E 验收前不得宣称生产就绪。

---

## A. 安全闸门（P0，优先）

- [ ] 轮换 BotCF / TorchAI 密码并失效旧 session  
- [ ] `docs/websites/*.md` 无明文凭据（占位符）  
- [ ] 评估 Git 历史是否含密；必要时 filter-repo / 限制分享  
- [ ] 引入或运行秘密扫描（含历史，如 gitleaks）  
- [ ] 真实凭据仅 `sites/*.env` 0600  

---

## B. 文档

- [x] architecture / data-model / timer-units / site-notes / checklist 吸收 design-review  
- [x] review-adoption.md  

---

## C. 代码

- [x] `newapi_monitor.py`（默认单次；`--validate`）  
- [x] `monitor_storage.py`（尾事件去重，非全文件 hash 扫描）  
- [x] `sites/botcf.env.example`、`torchai.env.example`  
- [x] 本机真实 env（gitignore）  

---

## D. systemd / 安装

- [x] `newapi-monitor-once@.{service,timer}`  
- [x] `install_newapi_service.sh`  
- [x] site_id 全局唯一扫描  
- [x] 两站 timer 已 enable  

---

## E. 测试

- [x] `tests/test_newapi_monitor.py`（含 A→B→A、半行、redirect、契约等）  
- [x] Sub2API `tests/test_monitor.py` 无回归  

---

## F. 现场

- [x] 两站真实采集：botcf 34 组、torchai 11 组  
- [x] 第二轮 session 复用（无再次 login）  
- [x] timer 已启用  
- [ ] 密码轮换 / Git 历史秘密清理（运维项，见 architecture §2）  
- [ ] 长期 stale 巡检 / journal 秘密扫描  

---

## G. 实现后文档

- [x] 根 README 增加 New-API 章节  
- [x] 本检查单更新为已实现主路径  

---

## 推荐顺序（与评审 §9 一致）

1. 安全闸门 A  
2. 冻结两站脱敏 contract 样本  
3. 数据层测试（去重、ratio、backend）  
4. 精简 once 客户端  
5. deadline / redirect / 日志测试  
6. unit + 短安装脚本  
7. 真实 once → timer → freshness  
8. 可选：共享 storage 时修 Sub2API 旧去重  
