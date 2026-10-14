# P5 内核脚手架 + CI 扩到 Linux 设计说明

> 轨道：**P5（P4 之后的自选收尾）**。P0 ✅ / P1 ✅ / P2 ✅ / P3 ✅ / P4 ✅ / **P5-a ✅ / P5-b ✅**。
> 相关：`scripts/new_kernel.py`（新）· `world3d/cli/config_boot.py`（表驱动重构）·
> `scripts/check_imports.py`（新增 sys.path 不变量）· `scripts/verify.py`（14 → **16 条门禁**）·
> `.github/workflows/ci.yml`（新增 Linux job）· `scripts/new_demo.py`（抽出 `register_test` /
> `unregister_test` / `write_preserving_eol` 复用）。
>
> 分两批：**§1–§4 P5-a**（内核脚手架 + 实战检验 + 顺手抓到的 8 个内核隐患）、
> **§5–§6 P5-b**（CI 扩 Linux）、**§7** 验收、**§8** 不足。

---

## 1. 为什么还要一个脚手架：`new_demo.py` 不动注册表

`new_demo.py`（P4-b）生成的是"一个单文件示例"，**不碰**引擎的任何注册表。
而新增一个**玩法内核**要同步 **7 处**（本批实测清点，不是估计）：

| # | 同步点 | 漏了会怎样 |
|---|--------|-----------|
| 1 | `world3d/games/<名>/kernel.py` + `__init__.py` | 内核本体 |
| 2 | `configs/cordis.<名>.yml` | 配置树（门禁 `readme-config-count` 数它） |
| 3 | `world3d/host/registry.py` 的 `SERVICES` | **配置树挂不起来** |
| 4 | `world3d/host/tree.py` 的 `WANT_<名>` | 就绪判定缺服务 |
| 5 | `world3d/cli/config_boot.py` 的 `CONFIGS` | `config_boot` 里没有它 |
| 6 | `tests/test_<名>.py` + `run_all_tests.MODULES` | 单测不跑 |
| 7 | README 的内核/配置树/示例计数 + 测试清单 | 门禁 `readme-*-count` 变红 |

3/4/5 是**纯机械的三张表**，而它们"漏一处就静默失效"：`registry` 漏了 → cordis 的
Loader **吞掉构造期异常** → `await_ready` 8 秒超时、只报"服务不就绪"，真正的原因
（`KeyError: 未知服务短名`）看不见 —— P0-3 为此查了半天。所以这三张表交给脚本改，
**改完立刻 `ast.parse` 守卫，改坏就抛、不写盘**（`new_kernel._rewrite`）。

## 2. 让"接入新内核"降到 **1 行**：config_boot 表驱动

原来 `run_one` 是一串 `if/elif`：新增内核要改**两处**（`CONFIGS` + 一个分支）。
而脚手架**只能安全地改数据行** —— 自动插一个 `elif` 分支等于重写控制流，
那是最容易改坏的一类改动。于是先把 `config_boot` 改成表：

```python
CONFIGS = { 'td': ('cordis.td.yml', WANT_TD, True), ... }   # kind → (yml, 想要的服务, 需要 GL 吗)
ALL_KINDS = tuple(CONFIGS)                                  # `all` 的顺序从这里派生（不必两处维护）
RUNNERS  = { 'td': run_td, ... }                            # 没在表里的 kind → run_generic
```

第三项 `require_gl` 判的**不是**"这棵树会不会用 GL"，而是**内核自己取不取 GL 句柄**：
`await_ready(require_gl=True)` 等的是 `ctx.render.live_count() > 0`，纯逻辑内核
（`inject = []`）永远等不到，8 秒后抛"未就绪"。

`run_generic` 是**通用 headless 验收**，按一份明写的约定工作（四条断言，都能被反例打穿）：

1. 服务真的挂上，且**行为自检**（`status()` 初值合法）—— 不用 `isinstance`
   （`ctx.<名>` 每次访问返回**代理**，`is` 比对恒假，P0 实踩过）；
2. `inject == []` —— 与配置树的 `require_gl=False` **配对校验**：内核悄悄改成
   `inject = ['render']` 而树里仍是 False，就会得到"服务就绪但内核没跑"的假绿；
3. **`auto_step` 跑到终局**（`won`）—— 证明内核真能收尾，而不是"能构造就算数"；
4. **`dispose` 后服务不可达** —— 回滚彻底。

于是脚手架生成的内核**零代码**接入：`CONFIGS` 加一行（脚本自己加）。

## 3. 实战检验（本批真跑过，不是"应该能行"）

探针名字 `zzkern`，全程真跑，验收后 `--undo` 撤销。**每一步的实际输出**：

| 步骤 | 命令 | 实测结果 |
|------|------|---------|
| 生成 | `python scripts/new_kernel.py zzkern` | 4 个文件（`__init__` / `kernel` / `cordis.zzkern.yml` / `test_zzkern`）+ 4 处登记（registry / tree / config_boot / run_all_tests） |
| headless 验收 | `python -m world3d.cli.config_boot zzkern` | `[config] kind=zzkern inject=[] status=playing seed=20261014` → `RESULT state=won t=2.3s steps=18` → **`== ZZKERN_OK ==`** |
| 单测 | `python -m tests.test_zzkern` | **7/7**（碎片表确定性 / 可胜性 / 到 won 且不超收 / 步数时间量化 / 走廊边界走遍 / 轨迹逐项相同 / 终局幂等）→ `ALL_ZZKERN_TESTS_PASSED` |
| **配置真的到达服务** | 把 yml 的 `seed:` 改成 `424242` 再跑 | `seed=424242`、`steps=16`（**跟着变** ⇒ 证明 `config:` 透传生效），改回后 `seed=20261014` |
| 整体回归（探针在场） | `run_all_tests` / `config_boot all` / `check_imports` | **15/15 模块 288 项** / **11 个 OK 标记（含 ZZKERN_OK）** / **119 模块 0 失败** |
| **门禁列出待同步处** | `python scripts/verify.py` | **红 3 条**，且精确点名：`readme-test-modules 注册了但 README 没列：['test_zzkern']`、`readme-config-count 真实 11 棵，README 写 10`、`readme-kernel-count 真实 10 个内核，README 写 9（2 处）` |
| 撤销 | `python scripts/new_kernel.py zzkern --undo` | 摘除 7 行登记 + 删除 4 个文件 → `git status` **只剩我自己的改动**、门禁回 `VERIFY_OK` |

> 第 6 行才是"脚手架 + 门禁"配合的意义：**新增东西要同步的地方由机器列出来，不靠记性。**

### 3.1 撤销必须**逐字节精确**（两个坑）

`--undo` 的承诺是"仓库回到生成前"，实测一开始**不成立**：

1. **空行垫高**：`host/tree.py` 的插入原本在 WANT_ 常量后补了 `\n\n\n`，撤销只摘标记行
   ⇒ 留下 2 个空行，`git status` 里 tree.py 一直 `M`。改法：把新常量**紧贴**原常量插
   （匹配"WANT_* 区末尾到 `def config_path` 之间的空行"整段替换），摘 2 行即精确还原。
2. **行尾**：本仓库 `core.autocrlf=true`（工作区 CRLF、库里 LF）。脚本若一律写 LF，
   **归一化后内容完全相同**（`git hash-object` 与 HEAD 一致），但 `git status` 会一直
   标 `M` / `needs update`。于是抽了 `new_demo.write_preserving_eol`：**按原文件的行尾写回**
   （新建文件仍用 LF，入库时由 git 归一化）。

第 2 条不修也能用（内容是对的），但"脚手架撤销干净"这句话就会变成"内容干净、状态栏不干净"，
而**人会开始忽略 `git status`** —— 那比这个 bug 本身更贵。

## 4. 顺手抓到的真隐患：**8 个内核把 `world3d/` 塞进了 `sys.path`**（`platform` 被遮蔽）

写模板时我要确定"自举块该写几层 `dirname`"，于是去 grep 现有 9 个内核 —— 发现
**8 个是 3 层**（只有 brickward 是 4 层）。3 层算出来的不是仓库根，而是 `world3d/` 自己。

复现（**实测**）：

```text
$ python -c "import world3d.games.void.kernel; import platform; print(platform.__file__)"
D:\projects\3dworld\world3d\platform\__init__.py          ← 标准库 platform 被遮蔽了

$ python -c "import world3d.games.void.kernel; import platform; platform.system()"
AttributeError: module 'platform' has no attribute 'system'
```

而 pyglet 自己就 `import platform`：`pyglet/app/__init__.py:35`（`:53` 用 `platform.machine()`）、
`pyglet/info.py:24`、`pyglet/libs/...` 多处。**报错位置与真实原因毫无关系** ——
典型的"跑起来才炸、且炸在别人家里"。

* **修复**：8 个内核的自举块改成 4 层（并在原处写下为什么必须是四层）。
* **守卫**：`scripts/check_imports.py` 新增第二条不变量 ——
  **"仓库内自带 `__init__.py` 的目录不允许出现在 `sys.path` 上"**。
  判据用不变量而不是"数 `dirname` 层数"：层数写法将来会换（`Path(__file__).parents[3]`、
  `__package__` 推导…），不变量不会。
* **反证**：把 `void/kernel.py` 改回 3 层 → 门禁红并点名
  `sys.path += world3d/（自带 __init__.py ⇒ 其子目录成了顶层包名）`；还原 → 绿。
* **顺带**：删掉 `check_imports.py` 里一个声明了却从未使用的 `SKIP_PREFIX`（陈旧死代码）。

> 这条是"**用脚手架逼着自己去看现状**"的收益：我本来只想抄一份自举块。

## 5. P5-b：CI 扩到 Linux —— 依赖清单从**源码**里读，不照抄博客

原先只有 `windows-latest` 一个 job，理由是"Linux runner 无 GL 上下文，GL 用例会走 SKIP
（不是失败，但等于没测到）"。P5-b 把它补上，并把 SKIP 变成**尽量真跑**。

### 5.1 无头 GL 怎么做（本仓锁定的 pyglet 2.1.16 源码为证）

* pyglet 在 Linux 默认走 **Xlib**，没有 `DISPLAY` 时第一次 import 显示后端就抛
  `pyglet.display.xlib.NoSuchDisplayException: Cannot connect to "None"`。
* `pyglet.options['headless'] = True` 让它改用 **EGL**（`pyglet/display/__init__.py:35`
  → `pyglet.display.headless`），窗口走 `pyglet.window.headless`（`window/__init__.py:1894`）
  —— 无头窗口**仍有 framebuffer**，所以 GL 用例能真跑、能 `glReadPixels`。
* **环境变量形态可用**：`pyglet/__init__.py:319` 会读 `PYGLET_<选项名大写>`
  ⇒ CI 里写 `PYGLET_HEADLESS: '1'` 即可，**不用改一行业务代码**，
  也天然满足"必须在 `import pyglet` 之前生效"。

要装的系统库同样是**从源码里的 `load_library` 调用读出来的**（pyglet 用 ctypes 直接 dlopen）：

| pyglet 源文件 | 需要 .so | Ubuntu 包 |
|--------------|---------|-----------|
| `libs/x11/xlib.py` | `X11` | `libx11-6` |
| `libs/x11/xf86vmode.py` | `Xxf86vm` | `libxxf86vm1` |
| `libs/x11/xinerama.py` | `Xinerama` | `libxinerama1` |
| `libs/x11/xinput.py` | `Xi` | `libxi6` |
| `libs/x11/xrandr.py` | `Xrandr` | `libxrandr2` |
| `libs/x11/xrender.py` | `Xrender` | `libxrender1` |
| `libs/x11/xsync.py` | `Xext` | `libxext6` |
| `gl/lib_glx.py` | `GL` | `libgl1` + `libglx-mesa0` |
| `libs/egl/egl.py` | `EGL` | `libegl1` + **`libgl1-mesa-dri`**（llvmpipe/swrast，无 GPU 也能建上下文） |

⚠ **即使开了 headless，X11 那几个库也必须装**：`import pyglet` 会无条件走到
`pyglet/app/__init__.py` 的 else 分支（`from pyglet.app.xlib import XlibEventLoop`）。
这一条只有读源码才知道，照抄"headless 只需 EGL"的博客会在 CI 上炸。

### 5.2 哪些步骤阻塞、哪些只是探针（诚实划线）

本机是 Windows，**没有 Docker / 没有 WSL**（`docker --version` → command not found、
`wsl -l -v` → 未安装用于 Linux 的 Windows 子系统），所以 **Linux job 无法在本地实测**。
我把这条限制变成设计的一部分：

| 步骤 | 阻塞？ | 理由 |
|------|-------|------|
| `verify.py` / `check_imports.py` / `frame_plan --all-toggles` / 契约自检 | ✅ 阻塞 | 纯逻辑，与平台无关 |
| `world3d-info` | ✅ 阻塞 | 它每一项都有兜底（缺 GL 只打印 `<异常>`），**永不失败**，角色是"记录证据"（无头成功的标志是 renderer 出现 `llvmpipe`） |
| `run_all_tests.py` | ✅ 阻塞 | GL 用例是 `try: Window(...) except: SKIP` ⇒ 无头没配好表现为**跳过**而不是失败：这一份 job 要保证的是"没有显示器的机器上非 GL 部分全绿" |
| **无头 GL 探针**（`config_boot all` + GL 渲染器打印） | ⚠️ `continue-on-error: true` | **尚未在真 Linux runner 上验证过**。先跑一次拿真实输出，确认能建出上下文后再去掉该开关、并把它提升为阻塞步骤 |

> 另外加了一条**新门禁** `workflows-valid`：解析 `.github/workflows/*.yml`，
> 检查结构（`on` / 每个 job 的 `runs-on` 与非空 `steps` / 每个 step 有 `run` 或 `uses` /
> `uses` 必须钉版本）**以及里面的命令可达**。理由很直接：**workflow 是本机跑不到的那一半**，
> 它坏了本地没人会发现 —— 正好是"机械门禁该管的事"。
> 顺带记一个 YAML 坑：**YAML 1.1 会把裸 `on:` 解析成布尔 `True`**（`{True: [...]}`），
> 所以"有没有 `on`"必须同时认 `'on'` 与 `True` 两个键。

### 5.3 ⚠️ 一个必须说清的前提：**这仓的 CI 目前不会跑**

`git remote -v` 只有 `origin → gitee.com/siwpan/3dworld`，而 workflow 只有 GitHub 会执行。
所以 `.github/workflows/ci.yml` 现在**是准备好的、但不会被触发的**配置。要让它真跑，二选一：
把仓库镜像/推一份到 GitHub（再加一个 remote），或改用 Gitee Go。**这一条我没有替你做**
（需要你的 GitHub 账号），见 §8 第 4 条。

## 6. 顺带修的（都很小，但都是"静默"的）

* `scripts/new_demo.py` 抽出 `register_test` / `unregister_test`（登记 / 摘除 runner 行）
  与 `write_preserving_eol`，供 `new_kernel.py` 复用（而不是复制一份）。
* `new_demo._register_test` 改名 `register_test`（去掉私有前缀 —— 现在跨文件用了）。
* `config_boot.run_generic` 的日志加上 `seed=`（若内核有该属性）：这是**证明配置树
  `config:` 真的到达了服务**的成本最低的办法（改 yml 一行，日志跟着变）。

### P5-c 追补的两条（重建发行版时踩出来的）

* **`pyrefs-exist` 的"非项目模块白名单"改成标准库判定**：原来手维护
  `{'os','sys','json',…}`，结果在 `docs/分发指南.md` 里写一句
  `python -c "import zipfile, hashlib; …"` 就被判成"模块不存在"。现在用
  `sys.stdlib_module_names`（权威、自动跟进版本），白名单只剩**第三方**那三个
  （`pyglet` / `yaml` / `cordis`）。计数从 7 → 10，且 10 处**全是真实存在的 `world3d.*`**。
* **`record_all.find_app_class` 只认 `DemoApp`** ⇒ `pointcloud_edl` 一进来就
  `未找到 DemoApp 子类`，而且**整批录制随之中断**（实测第一批只产出 7 条就 EXIT=1，
  `wboit_demo` / `skinned_creature` / `gpu_cull_field` / `voidward` / `pulsefall` 一条没录）。
  现在 `DemoApp` | `EngineApp` 两类都认。
* **成片核查判据要能自证**：第一版用 `-f framemd5` 读校验和，**7 条全部误判"静止"**
  （输出字段不是猜的 `MD5=…` 形态 ⇒ 两边都是 `None` ⇒ 判成"没动"）。改用
  `-f rawvideo -pix_fmt rgb24` 落到 Python 里 hash，并把"**抽帧失败**"与"**静止**"
  分开报 —— **取不到数据时不许把它算成"通过/静止"**。

## 7. 验收（实测，本机 venv）

| 项 | 结果 |
|----|------|
| `scripts/verify.py` | **16/16 通过** → `== VERIFY_OK ==` |
| `scripts/verify.py --selfcheck` | **16/16 拦得住** |
| 脚手架实战（探针 `zzkern`） | 生成 → `== ZZKERN_OK ==`（2.3s/18 步）→ 单测 **7/7** → 门禁**精确红 3 条** → `--undo` 后 `git status` 干净 |
| 脚手架行为反证 | 把模板 `STEP_HZ` 改坏 ⇒ 门禁红并给**契约级**原因（`auto_step 未在 120s 内收尾 —— run_generic 会判它"只是能构造"`） |
| `check_imports.py` 新不变量反证 | 把 `void` 改回 3 层 ⇒ 红并点名 `sys.path += world3d/`；还原 ⇒ 绿 |
| `scripts/run_all_tests.py` | **14/14 模块 / 281 项**（探针在场时 15/15 / 288 项） |
| `python -m world3d.cli.config_boot all` | **10/10**（含探针时 11/11） |
| `python -m world3d.cli.frame_plan --all-toggles` | **16/16** |
| `scripts/check_imports.py` | **117 模块 / 0 失败**（1 个按设计跳过） |
| `scripts/verify_demos.py` | `ALL_DEMOS_OK (37 demos)` |
| `workflows-valid` | 1 个 workflow / **2 个 job** 结构完整、10 条命令可达 |

## 8. 不足 / 未做

1. **Linux job 未在真 runner 上跑过**（本机无 Docker/WSL）。已知的未知：EGL+llvmpipe 下
   `config_boot all` 的 8 棵 `require_gl=True` 树能否通过。已按 §5.2 划成非阻塞探针，
   **第一次真跑之后应当去掉 `continue-on-error` 并把它升级为阻塞步骤**。
2. **`verify_demos.py` 不进 CI**（要真窗口 + 出剧照，且 14 分钟）。CI 里只覆盖
   "能 import / 能 headless 验收"，成片与视觉验证仍是本机的事。
3. **脚手架没做"新共同核"模板**（`world3d/core/<x>_core.py` + `contracts.CORES` +
   `WANT_CORES`）：共同核还牵涉契约注册表与 `run_cores` 的自检项，比内核多两处同步点。
   等真新增时再按"今天踩了什么"补 —— 比现在凭想象写准。
4. **CI 的触发前提没落实**（§5.3）：仓库当前只有 Gitee remote，workflow 不会执行。
   需要你的 GitHub 账号（加 mirror remote）或改用 Gitee Go。
5. **`--undo` 只覆盖脚手架自己生成的东西**：若你在生成后又手改了那 4 个文件，
   `--undo` 会一并删掉（它会打印删了哪些文件，但不会拦你）。
