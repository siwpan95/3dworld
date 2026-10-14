# P4 机械门禁与文档站 设计说明（**P4 收口 = 全部路线完成**）

> 轨道：**P4 收尾（文档站 / 门禁 / 脚手架）**。P0 ✅ / P1 ✅ / P2 ✅ / P3 ✅ / **P4 ✅**。
> 相关：`scripts/verify.py`（12 条门禁 + `--selfcheck`）· `scripts/docs_index.py`（索引生成器）·
> `docs/README.md`（生成物）· `.githooks/pre-commit` · CHANGELOG「P4」段。

## 1. 为什么这个项目**特别**需要门禁

本轮改造里，**同一件事被手改过 5~7 次**，每一次都可能漏一处：

| 事实 | 写在几处 | 本轮手改次数 |
|------|---------|-------------|
| 示例数（37） | README **7 处** | 6 |
| 测试模块清单 + 逐模块用例数 | README 14 行 | 5 |
| 配置树 / 内核 / 共同核计数 | README 各 1~2 处 | 3 |
| 文档间相对链接 | 29 个文件、28 条 | 1（但 P2-4 搬了 205 个文件） |
| `cli` 用法 docstring | 2 个模块 | 1（P2-4 改命令名） |

**门禁上线当天就抓到一个真漂移**：`tests/test_timeline_ui` 注册在 `run_all_tests.py` 里
（14 个模块）却**没进 README 的测试清单**（只列了 13 个）——
这正是"改了 A 忘了 B"的典型形态，而它在此之前**没人会注意到**。

## 2. 12 条门禁（全部 <1 秒、不联网、不跑构建、不改文件）

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
| 新鲜度 | `docs-index-fresh` | `docs/README.md` == `scripts/docs_index.py` 重新生成的结果 |

> **豁免"快照"，只校验"现状"**：`CHANGELOG.md` 里的历史计数（"上一批 281 项"）是快照，
> **不校验**（改反而破坏证据链）；README 里"当前有 37 个示例"是现状，**必须同步**。

## 3. 判据本身也被"打穿"过（`--selfcheck`）

"永远为绿的门禁等于装饰品"，所以每条门禁都配了**针对性破坏用例**：
在沙箱里注入（把 README 的示例数改成 99 / 删掉一行测试命令 / 把索引塞一行假的 / 把目录树
里的文件名改坏 …），断言该门禁**变红**。实测 **12/12 全拦得住**，且失败信息都点名了两边实际值。

沙箱只拷**文本**（跳过 assets / 剧照 / vendor），50MB 的仓库因此缩到 ~2MB；
每条用例在独立副本里跑，互不污染。

## 4. 门禁上线时抓到的**三处真问题**（这是它值钱的地方）

| # | 抓到什么 | 性质 |
|---|---------|------|
| 1 | `tests/test_timeline_ui` 没进 README 测试清单 | **真漂移** |
| 2 | 我的 `test_case_count` 只认 `tests = [...]` 一种形态 ⇒ 5 个旧式模块（`def test_*` 计数）全被记成 SKIP | **判据假设太窄**（skill 的原话："先 grep 文档真实措辞，再写正则"）。修的时候顺手加了一条更强的检查：**两种形态同时存在时必须相等** —— 那能抓"定义了测试函数却没注册进 runner"（定义了但永远不跑） |
| 3 | `commands-exist` 把 `python -m venv`、`python -m PyInstaller`、`python x.py`（文档里的**模板占位符**）当成死命令 | **假阳性**：工具模块要白名单；占位符要写成 `python <文件>.py`（`<` 不在路径字符集里，天然被排除） |

> 第 3 条最值得记：**门禁会抓它自己的文档。** 这不是缺点 —— 说明它真的在扫全仓文本。

## 5. 文档站：`docs/README.md` 是**生成物**

26 篇文档手写索引**一定会腐**（加了文档忘了加一行）。所以：

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

## 7. 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python scripts/verify.py` | **12/12 通过** → `== VERIFY_OK ==`（每条都带数字证据） |
| `python scripts/verify.py --selfcheck` | **12/12 拦得住**（每条都有针对性破坏用例） |
| `python scripts/docs_index.py --check` | 索引最新（26 篇登记） |
| pre-commit 通过路径 / 失败路径（真实 git sh） | `rc=0` / `rc=1` + 拦截指引 |
| `scripts/run_all_tests.py` | 14/14 模块 / 281 项 |
| `python -m world3d.cli.config_boot all` | 10/10 棵配置树 |
| `python -m world3d.cli.frame_plan --all-toggles` | 16/16 |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (37 demos)` |

## 8. 不足 / 未做

1. **脚手架（模板）还没做**：本批把 P4 的"门禁 + 文档站"做实了，"新 demo / 新内核 /
   新测试模块的模板"留作后续（`scripts/new_demo.py` 之类）；
   理由：模板的价值取决于"新增频率"，而当前更有价值的是**防止已有事实漂移**；
2. **门禁没覆盖 `docs/*.md` 内部的"散文数字"**：例如某篇设计说明里写"12 个共同核"而实际
   13 个 —— 这类**散文里的数字**要机器校验就得引入更强的自然语言约束，收益/成本不划算。
   当前只覆盖"有固定措辞的结构化断言"（README 的计数行、表格、树、链接）；
3. **`--selfcheck` 沙箱不含 `assets/`**：若将来某条门禁需要图片/二进制，要按
   `repo-consistency-gates` 的"稀疏占位文件"手法补；
4. **没有 CI**：`.github/workflows/ci.yml` 里跑的是 `import world3d.core.contracts` 那条，
   可以把 `verify.py` 也加进去（本地 pre-commit 已经覆盖，CI 属锦上添花）。
