# AGENTS.md

## 项目简介

多站点 API 分组/模型采集仓库：

- **Sub2API**（JWT）：`sub2api_monitor.py`、`sub2api_models.py`
- **New-API**（session）：`newapi_monitor.py`、`newapi_models.py`、`monitor_storage.py`

每站一份 `sites/<id>.env` + `data/<id>/`；生产用 systemd timer + oneshot，不跑应用内常驻多线程。密钥与 `data/` 不入库。

## Python 环境

```bash
cd /root/projects/zhongzhuan
# 优先用仓库内虚拟环境
.venv/bin/python -m pip install -r requirements.txt

# 运行 / 测试请显式用 .venv
.venv/bin/python sub2api_monitor.py --env-file sites/<id>.env --validate
.venv/bin/python -m unittest discover -s tests -v
```

不要默认用系统 `python3` 装依赖；有 `.venv` 时一律用 `.venv/bin/python`。

## 重要文档导航

| 主题 | 路径 |
|------|------|
| 运维入口 / 怎么跑 | [README.md](README.md) |
| 文档治理 / 权威顺序 | [docs/01 governance/](docs/01%20governance/) |
| 契约 / 兼容边界 | [docs/02 specs/](docs/02%20specs/) |
| 当前实现设计（Sub2API / New-API / models / invite） | [docs/03 designs/](docs/03%20designs/) |
| 邀请链接 CLI | [docs/03 designs/invite-links.md](docs/03%20designs/invite-links.md) · `invite_links.py` |
| 站级说明 | [docs/websites/](docs/websites/) |
| Sub2API 监控 skill | [skills/aiapibank-monitor-groups/SKILL.md](skills/aiapibank-monitor-groups/SKILL.md) |
| 鉴权探活 skill | [skills/aiapibank-inspect-auth/SKILL.md](skills/aiapibank-inspect-auth/SKILL.md) |

改代码或排障前，先按主题打开上表对应正式文档；评审、探针、inventory 和历史方案在 `docs/drafts/`，不作为日常权威入口。契约状态以 `docs/02 specs/contracts/manifest.yaml` 为准：只有 `frozen` contract 才覆盖实现；`draft/planned` 是收敛目标，当前行为以代码、测试和 `docs/03 designs/` 为准。本文件只做导航。
