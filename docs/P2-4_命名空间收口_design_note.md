# P2-4 命名空间收口设计说明（`world3d.*`）—— P2 收尾

> 轨道：**P2 分层归位**的最后一批（P2-1 platform 抽出 / P2-2 core 越界清出 / P2-3 demos→examples，
> 本批 **P2-4 把 7 个顶层包收进 `world3d.*`**）。
> 相关：`scripts/migrate_p2_4.py`（迁移工具）· `scripts/check_imports.py`（门禁）·
> `world3d/compat.py` + `tests/test_compat.py`（旧路径过渡层）· CHANGELOG「P2-4」段。

## 1. 改了什么

```text
改前                                   改后
core/  logic/  engine/  presentation/  world3d/
games/ host/  cli/   _cordis_locate.py      ├── core/  logic/  engine/  presentation/
examples/ tests/ scripts/ configs/          ├── games/ host/  cli/  platform/
                                            ├── _cordis_locate.py
                                            └── compat.py   ← 旧路径过渡层
```

* **147 个文件 / 600 行 import** 被机械改写（三种形态都覆盖：`from core.x import Y`、
  **`from core import x as y`**、`import core.x`）；
* **54 处"字符串里的模块路径"**（正则改 import 永远碰不到）——`host/registry.py` 的
  `SERVICES` 表、`core/contracts.py` 的契约清单、`world3d/_info.py` 的公共面清单、
  `scripts/record_all.py` 的 `ost_fn=`；
* **14 处"仓库根推导"补一层 `dirname`**（包深了一层）；
* `configs/*.yml`、`.github/workflows/ci.yml`、各 `cli/*.py` 的用法 docstring 一并同步。

## 2. 两处有据的偏离（与规划原文不同，理由写在这里）

| # | 规划原文 | 实际做法 | 理由 |
|---|---------|---------|------|
| 1 | "一次性切 `src/` 并把顶层包收进 `world3d.*`" | **只收进 `world3d/`，不切 `src/`** | 本项目全部验收命令都是"从仓库根直接跑"：`python -m world3d.cli.config_boot all`、`python examples/xxx.py`、`python -m tests.xxx`。切 `src/` 之后这**三条在干净 clone 下都必须先 `pip install`（或设 PYTHONPATH）**才成立 —— 那会让"新拉下来就能验"这条前提断掉。而 `src/` 的好处（防止误 import 工作树）在这里几乎为零：项目就是这么跑的。**收益 < 代价，故不切。** |
| 2 | "旧路径留**兼容再导出层**一版" | 用 **`world3d.compat` 的元路径别名**，而不是给每个模块造桩文件 | 桩文件方案要给 `core/`、`engine/`… 每个**子模块**各写一个 `xxx.py`（`from world3d.core.xxx import *`）—— 那是**第二份公共面真相源**：新增/删除模块都得记得同步，且 `import core.x.y` 这种深路径还要逐层铺。**别名 finder 只有 40 行、零维护，而且拿到的是同一个模块对象**（桩文件做不到：`import *` 会复制名字、丢掉模块级单例与 `sys.modules` 状态）。兼容层是 **opt-in** 的（`install_legacy_aliases()`），等外部使用者迁完整体删除。 |

## 3. 判据（三层，都能被打穿）

| 层 | 判据 | 结果 |
|----|------|------|
| **可达性** | `world3d/` 下**每个** `.py` 都能 import（不是"被测试引用到的那些"） | `scripts/check_imports.py` → **117 个模块 0 失败** |
| **行为** | 275 项单测 + 10 棵配置树 + 帧图 16 种组合 + 37 个 demo | 全绿（见 §5） |
| **兼容层** | ① install **之前**旧路径必须不可用（反证"真搬走了"）；② install **之后**旧名与新名**是同一个对象**（含 3 层深子模块） | `tests/test_compat.py` **6/6** |
| **陈旧引用** | 全仓不允许再出现裸 `from core.` / `python -m cli.` | 扫描结果 **0 处**（排除解释性文本） |

> 第 1 条判据（`check_imports`）是这一批**唯一能抓"改漏"的东西**：
> 单测只覆盖被引用的模块，**没被任何测试 import 的模块改坏了单测不会红**。
> 117 个模块全 import 一遍才叫"全仓可达"。

## 4. 本批踩到的坑

| # | 症状 | 根因 |
|---|------|------|
| 33 | **顶层包别名成功、子模块却是两个对象** | 别名 finder 挂在 `sys.meta_path` **末尾** ⇒ `PathFinder` 顺父包 `__path__` 把 `world3d/host/tree.py` **又加载了一遍**（`host.tree is world3d.host.tree` 为 False）。模块级单例/缓存各存一份。**只测顶层包的话完全看不出来** ⇒ 改挂最前，并把"同一性"写进测试（含 3 层深子模块） |
| 34 | 迁移后 `configs/*.yml`、CI、`cli/*.py` 的 docstring 仍写旧命令 | 迁移脚本只覆盖 `.py` 的 import 与 `.md` 的 prose，**`.yml` / CI / docstring 用法行是三类独立载体**。⇒ 最后加一道"全仓陈旧引用扫描"，结果为 0 才算完 |
| 35 | heredoc 又吃反斜杠（本会话第 4 次） | `<<'PY'` 里写 `'\\'` 会被吞掉 ⇒ `SyntaxError`。**含反斜杠/嵌套引号的脚本一律落文件再跑**（`scripts/migrate_p2_4.py`、`scripts/check_imports.py` 都是这么来的） |

> 第 33 条的教训值得单列：**"别名"这种机制的判据必须是"同一性"，不是"能导入"。**
> 能导入只要名字能解析就行；同一性才保证"两份状态不会分叉"。

## 5. 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python scripts/check_imports.py` | **117 个模块 0 失败**（世界树全可达） |
| `python -m tests.test_compat` | **6/6** |
| `scripts/run_all_tests.py` | **14/14 模块 / 281 项** / `ALL_TESTS_PASSED` |
| `python -m world3d.cli.config_boot all` | **10/10** 棵配置树（这条会走 `registry` 的**字符串模块路径**） |
| `python -m world3d.cli.frame_plan --all-toggles` | **16/16** 组合 |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (37 demos)` |
| `main.py` / `kkrieger_demo.py` / `pyglet_3d_demo.py --smoke` | 三个顶层入口 `EXIT_OK` |
| 全仓陈旧引用扫描 | **0 处** |

## 6. 不足 / 下一步

1. **`_cordis_locate` 的 sys.path 副作用**：它仍会把 `vendor/cordis/src` 插进 `sys.path`
   （vendored cordis 的运行方式）。想彻底"零 path hack"得走 `pip install vendor/cordis`，
   但那会让"clone 即跑"失效 —— 与本批的取舍一致，保持现状；
2. **兼容层没有弃用期计时**：`world3d.compat` 是 opt-in，没有 `DeprecationWarning`
   （因为不 install 就没有旧路径，谈不上"用了旧路径要警告"）。
   若要真正约束外部使用者，可以给 `world3d/__init__.py` 加一个 `LEGACY_MIGRATION` 常量说明期限；
3. **`examples/` 仍是"脚本目录"而不是包**：它们靠 `sys.path.insert` 找 `world3d`。
   彻底包化（`world3d.examples`）会让 `python examples/x.py` 这类最常用的用法变别扭，
   同样按"收益 < 代价"保持现状；
4. **P2 到此收口**（P2-1~P2-4 全交付）⇒ 下一步是 **P4**（文档站 / 门禁 / 脚手架）。
