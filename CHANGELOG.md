# 更新日志

本项目采用 CalVer（`YYYY.MM.DD`），与既有 tag `v2026.10.09` 一脉相承。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [2026.10.13] — 上线前全检修复

首个显式发布版本。本次以「上线前全检」（代码审查 + 安全审计 + QA 测试）的发现为准绳，
**修复全部集中在发布工程侧**，产品代码逻辑未做改动。

对应基线提交：`03c8c71`（`feat(demos): ③ 可视化导演台`）。

### 修复（发布阻断项）

- **依赖声明不完整导致环境不可复现**（P0，原 🔴 发布阻断）
  - `requirements.txt` 原本只有 `pyglet==2.1.16`，但 vendored `cordis` 硬依赖
    **PyYAML** 与 **watchdog**（上游把二者标为 optional extra，实为硬依赖：
    `cordis/__init__.py` 无条件 `from .hmr import Hmr`，而 `hmr.py` 在模块级把
    未安装 watchdog 时会变成 `None` 的 `FileSystemEventHandler` 当基类使用）。
  - 后果：全新克隆执行 `pip install -r requirements.txt` 后，7 个发行 demo
    （boxward / godhand / tower_defense / voidward / pulsefall / stillhall /
    swarmward）与 `core/` 全部共同核均无法 import。
  - 现状：`requirements.txt` 补全两者，全新克隆无需任何手工接线即可跑通。

- **误导性报错掩盖真实根因**（P0）
  - cordis 定位兜底此前统一报「未找到 cordis 包，请设置 `CORDIS_SRC`」。而
    `ModuleNotFoundError` 是 `ImportError` 的子类，所以「cordis **已找到**、
    只是自身依赖缺失」也会被同一段兜底捕获并改写成该文案 —— 给出的是一条
    **明确但永远无效**的修复指令。
  - 现状：定位与报错收口到 `_cordis_locate`，报错区分「未找到」与「已定位但导入
    失败」，后者直接带出真实根因并提示 `pip install -r requirements.txt`；
    同时捕获 `TypeError`（缺 watchdog 时抛的是 `TypeError` 而非 `ImportError`，
    此前会完全绕过兜底）。

- **cordis 定位逻辑重复 10 处且只认环境变量**（P0）
  - `core/_cordis.py` 与 `experiments/cordis/` 下 9 个内核各自复制一段兜底，
    且只查 `CORDIS_SRC`、**不查仓库自带的** `vendor/cordis/src`。
  - 现状：统一改为 `_cordis_locate.ensure()`；`CORDIS_SRC` 仍可显式覆盖，但需
    先通过 `os.path.isdir` 校验才允许进入 `sys.path`（安全审计 F-004）。

- **契约自检的误分类**（P0）
  - `core/contracts.py` 用 `if 'cordis' in str(e)` 判定，会把「依赖缺失」误记为
    `pending:cordis`，把排查方向从「装依赖」带偏到「去暴露 cordis」。
  - 现状：新增 `_root_cause()` 沿 `__cause__` 链取根因，按根因缺失的模块名分类。

### 新增

- `scripts/run_all_tests.py` —— 一键回归入口（原项目零 CI、零一键入口）。
  用子进程逐个跑 6 个测试模块，判定通过 = 退出码 0 **且**出现专属 PASSED 标记；
  以**实际执行数**为分母统计（跑挂的模块记为未执行，不混同于跳过）。
- `_cordis_locate.py` —— cordis 统一定位模块（含安全校验与可读报错）。
- `requirements.txt` 补齐 `PyYAML`、`watchdog`。

### 安全加固（P1）

- **F-004 环境变量目录注入 `sys.path[0]`**：`experiments/` 下两个试点脚本原本写成
  `os.environ.get('ZHUANGTAIJI_PATH', r'G:\repos\zhuangtaiji')` / `AN_ENGINE_PATH`
  默认 `G:\repos\an-engine`，然后模块级 `sys.path.insert(0, …)`。**带机器相关默认值
  意味着无需设置任何环境变量** —— 只要目标机器存在可写的 G 盘，放入同名包即自动
  激活，等同于随代码分发一条活性注入路径。现改为必须显式指定且先经 `os.path.isdir`
  校验，未配置时给出明确提示（不再是崩溃或隐性加载）。
- **F-002 配置树 entry 白名单**：cordis 会把 yml 里每条 entry 的 `name` 直接交给
  `importlib.import_module`，即"改配置 = 执行代码"，而 `.yml` 在评审者眼里通常是
  数据。`tree_boot.load_tree` 现强制校验 `name` 前缀，白名单外直接拒绝。

### 覆盖率（P2）

- 新增 `tests/test_timeline_ui.py`：`presentation/timeline_ui.py` 覆盖率
  **27% → 100%**（该文件是本次 HEAD 可视化导演台引入的新 UI，原为全仓最低覆盖盲区）。
  - 顺带修出一个真实边界缺陷：`_t_from_x()` 在 `duration` 为负时会返回负时间
    （`update()` 有 `duration <= 0` 守卫而它没有，两处口径不一致）。已在
    `_t_from_x` 补同款守卫。
  - 用例对「幕刻度必须存入 `self.act_ticks`」做了断言 —— pyglet `Batch` 不持有
    Shape 引用，不存会被 GC 回收而**静默不画**（M6 踩过的坑，注释里有记录）。

### 验收（本次实测）

- 单测：**138 / 138 全部通过**（7/7 模块；修复前全新克隆为 92/129，2 个模块崩溃、
  36 项从未执行；本次新增 timeline_ui 9 项）。
- Demo：**31 / 31 通过**（`ALL_DEMOS_OK`），含此前无法启动的 7 个 cordis 系 demo。
- 配置树：`config_boot` 对 puzzle / cores / td / hall / void / pulse 六棵树全部通过
  （验证 F-002 白名单未误伤合法配置）。

### 变更文件

- 新增：`_cordis_locate.py`、`scripts/run_all_tests.py`、`tests/test_timeline_ui.py`、
  `.github/workflows/ci.yml`、`VERSION`、`CHANGELOG.md`、`requirements-dev.txt`。
- 修改：`requirements.txt`、`core/_cordis.py`、`core/contracts.py`、
  `presentation/timeline_ui.py`、`experiments/cordis/` 下 9 个内核与引导模块、
  `experiments/` 下 2 个试点脚本。

### 说明

- 按决策 **`experiments/` 随包发布**：其中 `zhuangtaiji_orchestration/` 与
  `allin_decision/` 依赖本仓库之外的兄弟仓库（`core/orchestrator.py`、`allin_engine`
  均不存在），属 dev-only 死代码。未删除（按决策保留），但已改为**未配置时明确报错**
  而非隐性加载任意路径，随发布附带的风险已收敛。
- CI 目前只跑 `windows-latest`：渲染层依赖 OpenGL，Linux runner 无 GL 上下文时 GL
  用例会走 SKIP 分支（不失败但等于没测到）。接入 EGL/OSMesa 后再扩展矩阵。
- 文档截图（`docs/*.png`）由 `scripts/verify_demos.py` 重新生成，本次**未随修复提交**
  （66 个二进制文件会淹没代码改动）。需要刷新时单独执行该脚本并提交。
