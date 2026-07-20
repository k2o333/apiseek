# CLI / 模块接口草图（实现备忘）

本文仅作实现时的接口备忘，细节以主方案为准。

---

## CLI

```text
sub2api_monitor.py
  (--env-file PATH | --sites-dir DIR)
  [--once] [--validate]
  [--only id1,id2] [--exclude id3]
  [--stagger-step SECONDS]
  [--max-inflight N]          # 可选全局 HTTP 并发
  [--worker-restart-limit N]
```

退出码：

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | 运行期部分/全部站 poll 失败（once） |
| 2 | 配置错误（validate 或无任何可加载站） |

---

## 建议新增函数

```python
def discover_site_env_files(sites_dir: Path) -> list[Path]:
    """*.env except *.example; sorted by name."""

def assert_no_storage_conflicts(configs: list[MonitorConfig]) -> None:
    """Raise ConfigError if DATA_DIR or TOKEN paths overlap."""

def run_single_site(
    config: MonitorConfig,
    *,
    once: bool,
    stop_flag: Callable[[], bool] | None = None,
    initial_delay: float = 0.0,
) -> int:
    """Lock + monitor loop or once; return 0/1."""

class MultiSiteSupervisor:
    def __init__(self, configs: list[MonitorConfig], ...): ...
    def run_forever(self) -> int: ...
    def run_once(self) -> int: ...
```

---

## 线程命名

```text
MainThread
site-pinaic
site-aiapibank
site-<id>
```

便于 `threading.enumerate()` 与日志。

---

## 状态文件（阶段 B 可选）

`data/_supervisor/status.json`：

```json
{
  "started_at": "...",
  "sites": {
    "pinaic": {
      "state": "running",
      "last_success_at": "...",
      "last_error": null,
      "failures": 0,
      "thread": "site-pinaic"
    }
  }
}
```

不含 token、密码。  
