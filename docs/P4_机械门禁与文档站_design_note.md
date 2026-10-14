# P4 机械门禁与文档站 设计说明（**P4 收口 = 全部路线完成**）

> 轨道：**P4 收尾（文档站 / 门禁 / 脚手架 / CI）**。P0 ✅ / P1 ✅ / P2 ✅ / P3 ✅ / **P4 ✅**。
> 相关：`scripts/verify.py`（**14 条门禁** + `--selfcheck`；P5-a 又加了 2 条 → **16 条**，
> 见 `docs/P5_内核脚手架与CI_design_note.md`）· `scripts/docs_index.py`（索引生成器）·
> `docs/README.md`（生成物）· `scripts/new_demo.py`（脚手架）· `.githooks/pre-commit` ·
> `.github/workflows/ci.yml`（已挂门禁）· CHANGELOG「P4」段。
>
> 本文分两批写：§1–§6 是 **P4-a**（门禁 + 文档站 + pre-commit），§7–§9 是 **P4-b**
> （脚手架 + 门禁挂进 CI，以及随之抓出的两个真 bug），§10 是**不足 / 未做**。

## 1. 为什么这个项目**特别**需要门禁

本轮改造里，**同一件事被手改过 5~7 次**，每一次都可能漏一处：

| 事实 | 写在几处 | 本轮手改次数 |
|------|---------|-------------|
| 示例数（37） | README **7 处** | 6 |
| 测试模块清单 + 逐模块用例数 | README 14 行 | 5 |
| 配置树 / 内核 / 共同核计数 | README 各 1~2 处 | 3 |
| 文档间相对链接 | 30 个文件、29 条 | 1（但 P2-4 搬了 205 个文件） |
| `cli` 用法 docstring | 2 个模块 | 1（P2-4 改命令名） |
| 文档/CI 里的一行式 `python -c "import <模块>"` | 7 处（含 `.github/workflows`） | 1（P2-4 改名后 CI 直接 `ModuleNotFoundError`） |

**门禁上线当天就抓到一个真漂移**：`tests/test_timeline_ui` 注册在 `run_all_tests.py` 里
（14 个模块）却**没进 README 的测试清单**（只列了 13 个）——
这正是"改了 A 忘了 B"的典型形态，而它在此之前**没人会注意到**。

## 2. 14 条门禁（全部 <1 秒、不联网、不跑构建、不改文件）

| 类别 | 门禁 | 断言 |
|------|------|------|
| 一致性 | `readme-demo-count` | README **每一处**示例数 == `examples/*.py` 真实可发现数（减 `verify_demos.SKIP`） |
| 一致性 | `readme-test-modules` | README 测试命令表 ↔ `run_all_tests.MODULES`（**双向**） |
| 一致性 | `readme-test-counts` | README 每行「N 项」== 该模块**声明**的用例数 |
| 一致性 | `readme-config-count` / `-kernel-count` / `-core-count` | 配置树 / 内核 / 共同核计数 |
| 一致性 | `examples-index-pairs` | `examples/README.md` 表格 ↔ `examples/*.py`（**双向**） |
| 一致性 | `commands-exist` | 文档里每条 `python -m …` / `python <文件>.py` 目标都存在 |
| 一致性 | `cli-usage-names` | `cli/*.py` 用法 docstring 的模块名 == 真实模块名 |
| 一致性 | `readme-tree-real` | README「项目结构」树里列出的路径都真实存在 |
| 契约 | `doc-links` | README / docs / examples 的**相对链接**都指得到真实文件 |
| 契约 | `pyrefs-exist` | 文档与 CI 里**每一处**一行式 `python -c "import <模块>"` 引用的模块**真实存在**（P4-b 加） |
| 契约 | `scaffold-templates-valid` | 脚手架模板渲染 → `ast.parse` → **真 import** → 关键片段齐全（P4-b 加） |
| 新鲜度 | `docs-index-fresh` | `docs/README.md` == `scripts/docs_index.py` 重新生成的结果 |

> **`pyrefs-exist` 为什么要单独一条**：P2-4 把 `_cordis_locate` 收进 `world3d.*` 之后，
> **CI 里那行 `python -c "import <旧模块名>"` 一直是坏的** —— 因为迁移脚本只改**行首**的
> `import X`，改不到**命令行字符串里的** `import`；而 CI 在 GitHub 上跑，本机验收根本不执行
> workflow，所以**没有任何测试或门禁会碰到它**。
>
> 它只扫 **`python -c "…"` 一行式命令**（`.md` / `.yml` / `.yaml`），判定用**文件系统存在性**
> （`_module_exists`：只按仓库根查 `X.py` 或 `X/__init__.py`）。四条范围约束都是踩出来的：
> ① 扫全文任意 `import \w+` ⇒ 把散文里的"import 与"、占位符 `import X` 算成模块（45 处假阳性）；
> ② 不限定语句开头 ⇒ 把 `from core import contracts` 里的 `contracts` 当顶层模块；
> ③ `_module_exists` 里给 `world3d/` 加兜底 ⇒ **假绿**（`core` 已不存在，却能靠
> `world3d/core` 蒙过去）；**门禁太松和太紧一样有害 —— 前者让人开始无视它。**
> ④ **它连"引用这个模式"的散文一起抓**：写本文档时我原样引用了这个模式（`python -c` +
> 一个带引号的 `import …`），当场 6 处变红 —— **门禁逼我定下一条文档约定：引用模式时写占位符
> `python -c "import <模块>"`**（`<` 不在正则字符集里，天然排除）。
> 这条约定和 `commands-exist` 的 `python <文件>.py` 同源：**能被机械校验的字面，必须是"真命令"；
> 只想说明形状，就用占位符。**

> **豁免"快照"，只校验"现状"**：`CHANGELOG.md` 里的历史计数（"上一批 281 项"）是快照，
> **不校验**（改反而破坏证据链）；README 里"当前有 37 个示例"是现状，**必须同步**。

## 3. 判据本身也被"打穿"过（`--selfcheck`）

"永远为绿的门禁等于装饰品"，所以每条门禁都配了**针对性破坏用例**：
在沙箱里注入（把 README 的示例数改成 99 / 删掉一行测试命令 / 把索引塞一行假的 / 把目录树
里的文件名改坏 / 把 CI 里的 `import` 改成不存在的模块 / 把脚手架模板的 `def render` 改名 …），
断言该门禁**变红**。实测 **14/14 全拦得住**，且失败信息都点名了两边实际值。

沙箱只拷**文本**（跳过 assets / 剧照 / vendor），50MB 的仓库因此缩到 ~2MB；
每条用例在独立副本里跑，互不污染。

**selfcheck 抓过我两次，都是"假绿"**：

| # | 我的假绿 | 根因 |
|---|---------|------|
| 1 | `scaffold-templates-valid` 的片段断言写成 `'def render'` ⇒ 破坏用例把函数名改成 `render_broken`，**仍然匹配** | 断言太松：**"出现过"≠"存在"**。改成带括号的 `'def render(self):'` |
| 2 | 修完之后**破坏用例还是 PASS** | 加载 `new_demo.py` 用了 `import`，而 **`sys.modules` 会缓存第一次导入** ⇒ 沙箱里改过的副本**永远不会被重新加载**。改按**路径 + 唯一模块名**（`SourceFileLoader(f'_scaffold_{hash(root)}', path)`） |

> 第 2 条尤其值得记：**selfcheck 本身也会假绿**，而且它假绿的方式（缓存/单例/全局状态）
> 在真实门禁里同样会发生。所以"破坏用例 FAIL 了"这句话，必须看一眼**失败原因是不是我注入的那条**。

## 4. 门禁抓到的**五处真问题**（这是它值钱的地方）

| # | 抓到什么 | 性质 |
|---|---------|------|
| 1 | `tests/test_timeline_ui` 没进 README 测试清单 | **真漂移** |
| 2 | 我的 `test_case_count` 只认 `tests = [...]` 一种形态 ⇒ 5 个旧式模块（`def test_*` 计数）全被记成 SKIP | **判据假设太窄**（skill 的原话："先 grep 文档真实措辞，再写正则"）。修的时候顺手加了一条更强的检查：**两种形态同时存在时必须相等** —— 那能抓"定义了测试函数却没注册进 runner"（定义了但永远不跑） |
| 3 | `commands-exist` 把 `python -m venv`、`python -m PyInstaller`、`python x.py`（文档里的**模板占位符**）当成死命令 | **假阳性**：工具模块要白名单；占位符要写成 `python <文件>.py`（`<` 不在路径字符集里，天然被排除） |
| 4 | `.github/workflows/ci.yml` 里 `python -c "import <旧模块名>; …"` —— P2-4 之后**早就坏了** | **真 bug**：迁移脚本只改**行首** `import`，改不到命令行字符串里的；CI 只在 GitHub 跑，本机验收看不见 ⇒ **新写了 `pyrefs-exist` 才暴露出来** |
| 5 | `docs/P0-1_核心抽离_design_note.md` 里 `from core import contracts` 也已经不存在 | 同一个门禁抓到的**第二处** —— 证明这类失效**从来不是孤例**（一处失效，通常还有一群） |

> 第 3 条最值得记：**门禁会抓它自己的文档。** 这不是缺点 —— 说明它真的在扫全仓文本。
> 第 4/5 条是另一个方向的价值：**门禁不只防"未来会漂"，还会揭"现在就已经错了"** ——
> 它上线时抓到的第一类漂移是"意料之中"的（README 手改漏行），
> 这两条则是"我根本不知道它坏了"。后者才是把机械门禁做出来的原始理由。

## 5. 文档站：`docs/README.md` 是**生成物**

27 篇文档手写索引**一定会腐**（加了文档忘了加一行）。所以：

* `scripts/docs_index.py` 按**文件名前缀**分类（`P0-`…`P4-` / `4-` / `BQM` / 其余专题），
  抽取标题与首段**散文**摘要，渲染成表；
* 门禁 `docs-index-fresh` 核对磁盘内容 == 重新生成的结果 ⇒ "加了文档但忘了登记"会**变红**；
* 三条自我约束：**不含时间戳/解释器版本**（否则换机误报）、**不含行数/字数**
  （否则索引天天过期，人会开始无视这条门禁）、**纯标准库 + 确定性**（同状态逐字节相同）。

写 `_summary` 时踩了一个小坑（第一版摘要列全是 `---`）：**分隔线不以 `#`/`>`/`|` 开头**，
还会吃到 ASCII 图的坐标轴行 ⇒ 必须**按 ```` ``` ```` 开关状态跳过整个围栏块**，再跳过分隔线。

## 6. pre-commit 钩子（`.githooks/pre-commit`，已启用）

```bat
git config core.hooksPath .githooks     # 装（本仓库已启用；--unset 卸载）
```

三条设计约束（skill 的实测结论）：**找不到 Python 就跳过而不是拦下**（别让人因为没装
Python 就无法提交）；失败时**给下一步动作**（`--fix` / `--verbose` / `--no-verify`）；
只跑 `verify.py`（<1 秒），**不把单测/全量验收塞进 pre-commit**。

两条路径都用**真实 PortableGit 的 sh** 实测过：通过路径 `rc=0`、失败路径 `rc=1`
且打印拦截指引。

---

# P4-b（第二批）：脚手架 + 门禁挂进 CI

## 7. 脚手架 `scripts/new_demo.py`：让"新增"变成**可枚举的清单**

`generate × 2`：`examples/<名>.py`（demo）与 `tests/test_<名>.py`（测试模块；
`--register` 顺带登记进 `run_all_tests.MODULES`，幂等）。

```bat
python scripts\new_demo.py <名字>                     :: → examples/<名字>.py
python scripts\new_demo.py --test <名字> --register   :: → tests/test_<名字>.py + 登记进 MODULES
python scripts\new_demo.py <名字> --force             :: 覆盖已存在的文件
```

（命令里的 `<名字>` 是**占位符**：门禁只校验"能当命令跑"的字面，所以占位符一律写 `<…>`。）

**为什么值得做**：本会话我手写了 2 个 demo + 7 个测试模块，每个都要抄同一套样板。
"抄错一处就是半小时"——本会话真踩过（漏 `sys.path` 引导块 ⇒ **30 个 demo 一起
`ModuleNotFoundError`**）。所以模板里**必须**带上那些用血换来的行：

* `sys.path.insert(0, 项目根)` 显式引导（P2-1 起不再隐式插根）；
* `TITLE / HELP / SHOTS` + `setup / update / render` 钩子（`DemoApp` 的契约）；
* 测试模块的 runner + `ALL_<名>_TESTS_PASSED` / `_FAILED` 标记。

生成之后**紧接着跑 `verify.py`**：门禁会把"还差哪几处"直接列出来
（示例索引行 / README 的示例数 / 测试清单）—— **这才是我要的形态：新增东西要同步的地方
由机器列，不靠记性。**

两条实现上的坑：

1. **模板必须用 `string.Template`（`$name`），不能用 `str.format`** ——
   模板体里有 GLSL 的 `uniform WindowBlock { … }`，`format` 会把花括号当占位符
   （`KeyError: '\n    mat4 projection;…'`）。**模板里有大括号就该用 `Template`。**
2. `_register_test` 写盘**之前先 `ast.parse`** 改后的全文 —— 改坏了立刻抛，
   不留一个"MODULES 列表被截断"的半个坏文件。

门禁 `scaffold-templates-valid` 把模板**钉在现状上**：渲染 → `ast.parse` →
**真 import**（抓"导入了不存在的符号"，`ast` 抓不到）→ 关键片段齐全。
理由很直接：**模板腐掉时没有任何测试会发现**，直到下一个用它的人踩坑。
（"模板能开窗真跑"由我在本批**手测**过：`zzprobe --smoke 3 → EXIT_OK`、
`tests/test_zzprobe 2/2 通过`，随后清理探针；门禁只做静态 + import —— 真跑要 GL，会破坏 <1s 预算。）

## 8. 门禁挂进 CI（`.github/workflows/ci.yml`）

新增两步：`python scripts/verify.py` 与 `python scripts/check_imports.py`。

* 门禁在 CI 上的价值与本地**不一样**：pre-commit **只在你记得装钩子的那台机器上跑**
  （新克隆 / 换机就没了），CI 是"无论如何都会跑"的那一份。
* 挂进 CI 的**第一件事就是它抓到了 CI 自己的 bug**（§4 第 4 条：`import _cordis_locate` 已失效）
  —— 顺序上有意思：**先修 CI，再让 CI 检查 CI**。反过来的话，你会先拿到一个红的 CI
  却分不清是"新加的门禁在报历史旧账"还是"门禁自己写错了"。

## 9. 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python scripts/verify.py` | **14/14 通过** → `== VERIFY_OK ==`（每条都带数字证据） |
| `python scripts/verify.py --selfcheck` | **14/14 拦得住**（每条都有针对性破坏用例） |
| `python scripts/docs_index.py --check` | 索引最新（27 篇登记） |
| pre-commit 通过路径 / 失败路径（真实 git sh） | `rc=0` / `rc=1` + 拦截指引 |
| 脚手架实测（`zzprobe`，用完即删） | `examples/zzprobe.py --smoke 3` → `EXIT_OK`；`python -m tests.test_zzprobe` → **2/2** |
| CI 改动 | `ci.yml` 里 `import _cordis_locate` → `import world3d._cordis_locate as L`；新增 `verify.py` / `check_imports.py` 两步 |
| `scripts/run_all_tests.py` | 14/14 模块 / 281 项 |
| `python -m world3d.cli.config_boot all` | 10/10 棵配置树 |
| `python -m world3d.cli.frame_plan --all-toggles` | 16/16 |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (37 demos)` |
| `python scripts/check_imports.py` | world3d/ 下 **117 个模块、失败 0**（1 个照设计跳过：`world3d.platform.app` 会开窗口/连设备） |

## 10. 不足 / 未做

1. **门禁没覆盖 `docs/*.md` 内部的"散文数字"**：例如某篇设计说明里写"12 个共同核"而实际
   13 个 —— 这类**散文里的数字**要机器校验就得引入更强的自然语言约束，收益/成本不划算。
   当前只覆盖"有固定措辞的结构化断言"（README 的计数行、表格、树、链接、一行式命令）；
2. **`--selfcheck` 沙箱不含 `assets/`**：若将来某条门禁需要图片/二进制，要按
   `repo-consistency-gates` 的"稀疏占位文件"手法补；
3. **脚手架只覆盖 demo 与测试模块**：新内核（`games/<名>/kernel.py`）与新配置树
   （`configs/cordis.*.yml`）还没有模板 —— 这两样要同步的地方更多（`host/registry.py`
   服务短名表、`config_boot` 期望的 OK 标记、README 的内核计数），
   等下一次真新增时再按"今天踩了什么"补模板，比现在凭想象写更准；
4. ~~**CI 只在 `windows-latest` 上跑**：Linux runner 无 GL 上下文，GL 用例会走
   `_gl_available()` 的 SKIP 分支（不是失败，但等于没测到），等接入 EGL/OSMesa 再扩。~~
   → **P5-b 已扩**（`PYGLET_HEADLESS=1` + Mesa llvmpipe/EGL）。但那条 Linux job
   **未在真 runner 上验证过**，且**本仓 remote 只有 Gitee ⇒ workflow 不会被触发** ——
   两条限制见 `docs/P5_内核脚手架与CI_design_note.md` §5、§8；
5. **`pyrefs-exist` 的判定是"文件存在"而不是"真能 import"**：所以它能抓"模块被改名/搬走"，
   抓不到"模块在但符号被删"（后者由 `scaffold-templates-valid` 的真 import 在模板范围内覆盖）。
   要全仓覆盖需要把 117 个模块全 import 一遍 —— 那是 `scripts/check_imports.py` 的职责，
   两条门禁分工不重叠。
