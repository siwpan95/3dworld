# 更新日志

本项目采用 CalVer（`YYYY.MM.DD`），与既有 tag `v2026.10.09` 一脉相承。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

# 更新日志

本项目采用 CalVer（`YYYY.MM.DD`），与既有 tag `v2026.10.09` 一脉相承。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [0.2.0] — 2026-10-19 · demo 剥离：引擎仓 × 演示仓拆分

「将所有 demo 剥离，仅保留每个 demo 代表的能力」—— 39 个演示程序 + 两条主线
演出入口 + 成片/exe/截图/验收工具全部迁往独立仓 `siwpan/3dworld-demos`；
本仓收敛为纯引擎库。全哥拍板：五项能力全提取、拆独立仓、截图/成片/exe/
Gitee release 全部转移。

### 能力上提（先提取后删除，测试承重项逐位验证）

- **`world3d/logic/terrain.py`**（新）：程序化地形 —— 值噪声 fBm 高程场 +
  分层烘焙色 + `emit_row` 逐行网格发射。自 `examples/terrain_flyover.py` 上提；
  `test_terrain_row_emitter` 改导入本模块，逐行拼接 == 朴素拼接逐位一致
  （882 floats）✅
- **`world3d/logic/voxel.py`**（新）：体素世界 —— 高度图生成 + 面剔除网格化
  （可分段）。自 `examples/voxel_terrain.py` 上提；`test_voxel_mesh_invariants`
  改导入本模块，剔除率 86.1%、分段 == 整块逐位一致 ✅
- **`world3d/engine/particles.py`**（新）：GPU 实例化粒子系统（逐帧动画 100%
  在顶点着色器，CPU 每帧只更新标量；pyglet 2.1.16 `vertex_list_instanced`
  封装，三个坑位写进 docstring）。自 `examples/particles_gpu.py` 上提；
  新增 `test_particle_instance_data`（确定性/值域/几何尺寸）✅
- **`engine/shaders.py`**：SOURCES 19 → 22 项，新增 `particles` / `tunnel`
  （极坐标隧道）/ `raymarch`（SDF 光线步进）三对着色器（terrain/voxel 的
  着色器随其逻辑层留在 `logic/terrain.py` `logic/voxel.py`，自包含）

### 引擎仓删除

- `examples/`（39 demo + `_harness.py` + README）、根入口 `main.py` /
  `kkrieger_demo.py` / `pyglet_3d_demo.py` / `pyglet_3d_demo_1x.py`、
  空遗留目录 `demos/`、孤立实验产物 `experiments/`
- demo 工具链：`scripts/record_all.py` `verify_demos.py` `build_exe.py`
  `gen_sfx.py` `new_demo.py`（`new_demo.py` 一分为二：demo 模板随演示迁出，
  测试脚手架留仓为 `scripts/new_test.py`，`new_kernel.py` 的导入同步改指）

### 连带更新

- **README 重写**：首屏改引擎库定位 + 独立仓指引；「48 秒演出」「kkrieger」
  两节改为能力文档（真身在 `presentation/`）；新增「能力清单」一节
  （39 演示 → 引擎落点对照表）；项目结构树、快速开始、测试验收、门禁表
  （16 → 14 条，移除 `readme-demo-count` / `examples-index-pairs`）、
  脚手架、性能基准（改标注为 demo 期实测留存）全部同步
- **`scripts/verify.py`**：删 2 条 demo 门禁 + `real_demos()`；`doc-links` /
  `commands-exist` 扫描范围去 examples；`scaffold-templates-valid` 换靶
  `new_test.py`（只验测试模板）；selfcheck 破坏注入表同步
- **`world3d/platform/app.py`**：`PROJECT_ROOT` / `DOCS_DIR` 支持
  `WORLD3D_PROJECT_ROOT` / `WORLD3D_DOCS_DIR` 环境变量覆盖 —— pip 安装后
  三级上推会落到 site-packages，演示在独立仓运行必须显式指向（实测验证）
- **`docs/分发指南.md`** 重写为引擎库分发（PyPI 流程 + 凭据口径 + wheel
  内容口径），录制/打包/发行版手册随演示迁出（原全文在 3dworld-demos）
- docs 设计笔记里 7 处截图引用改标「已随 demo 迁往 3dworld-demos」；
  `docs/README.md` 索引页脚同步；configs 两处注释、`engine/audio.py` /
  `logic/fusion_audio.py` / `scene_build.py` 的脚本引用同步更新

### 验证收口（2026-10-19 实测）

- `verify.py` 14/14 通过（含 selfcheck 口径）；`check_imports.py` 133 模块
  失败 0；`run_all_tests.py` 21 模块 / 334 用例全过；`world3d-info` 全绿
  （着色器 22 个 / 服务表 24 条 / 配置树 10 棵）
- `python -m build` + `twine check` 双 PASSED；wheel 157 文件 =
  world3d（含 3 个新上提模块）+ vendored cordis，examples/scripts 零泄漏

## [0.1.0] — 2026-10-15 · PyPI 首发：发行名 `3dworld` / 导入名 `world3d`


CalVer 发行版轨道（Gitee release / exe / 成片）不变；本条为库级 SemVer 首次上 PyPI
（版本双轨口径见 `pyproject.toml` 头部注释）。

- **修 entry points 陈旧路径（真缺陷）**：P2-4 把 `cli/` `host/` 收进 `world3d/` 后，
  `[project.scripts]` 仍指向顶层 `cli.config_boot:main` / `host.pipeline:main` ——
  按原样发布，装上后这两个命令一执行即 ModuleNotFoundError。改为
  `world3d.cli.config_boot:main` / `world3d.host.pipeline:main`。
- **classifiers 补齐**：Development Status / Intended Audience / Python 3.11–3.14 /
  OS Independent / Games/Entertainment（与同作坊 an-engine、foolproof-operation 对齐）。
- **README 发布面治理**：首屏补仓库链接与许可行；尾部按统一惯例追加「仓库索引标注」
  （本地路径用 `<PATH>` 占位 —— 与 audio-generation / an-engine 2026-09-23 去本地化
  之后的口径一致；PyPI 上那三包的标注是去符盘之前的旧快照，带真实路径）。
- **打包实测（2026-10-15）**：
  - `python -m build` → `3dworld-0.1.0.tar.gz` + `3dworld-0.1.0-py3-none-any.whl`，
    `twine check` 双 PASSED；
  - wheel 仅含 `world3d`（132 文件，含 py.typed）+ vendored `cordis`（16 文件），
    examples/tests/scripts/docs/assets/recordings/configs **零泄漏**；
  - 干净 venv（Python 3.13）装 wheel 后 `world3d-info` 全绿：版本 0.1.0、依赖
    pyglet 2.1.16 / PyYAML / watchdog 齐、cordis 来自 site-packages、GL 4.6
    （RTX 3060）、19 着色器、24 服务；
  - 三个 console script（`world3d-info` / `world3d-config` / `world3d-pipeline`）
    全部可达（后两个正是本次修的入口）；
  - TestPyPI 沙盒链路：twine 上传成功、页面 200、从 TestPyPI 索引装进全新 venv
    复跑 `world3d-info` 依旧全绿。

## [未发布] — P5-d：让 CI 真跑（GitHub 通路实测 + 手动触发入口）

`regression` / `regression-linux` 两个 job 写好了，但有个前提一直没落实：
**本仓 remote 只有 Gitee，而 workflow 只有 GitHub 会执行** —— 也就是说 CI 从来没跑过。
本批把这件事查清了，并把"一旦通了就能立刻看结果"的入口留在里面。

### 实测（不是推测）

| 目标 | 结果 |
|------|------|
| `curl https://api.github.com` | **无响应**（`code=000`，1.4s 失败） |
| `curl https://github.com` | **无响应**（`code=000`，0.9s 失败） |
| `curl https://gitee.com` | 200（2.2s） |
| 本机代理 | 常见端口 7890/7897/7891/10809/10808/1080/8080/2080/3128 **全部未监听**；无 proxy 环境变量 |
| 本机 GitHub 凭据 | `~/.git-credentials` 里**只有 gitee**，没有 github |

⇒ **本机既没有到 GitHub 的网络，也没有凭据**，`git remote add github ...` 在这里是死路。

### 可行通路：**Gitee 的「仓库镜像管理」（Push 方向）**

Gitee 官方帮助写了这条路：`管理 → 仓库镜像管理 → 添加镜像 → 方向选 Push → 选/填目标
GitHub 仓库 → 填 GitHub 私人令牌（需 `repo` 权限）` ⇒ 之后**提交到 Gitee 时由 Gitee
服务器自动推给 GitHub**（分支/标签/提交都同步，最短触发间隔 5 分钟）。
它绕开的正是"本机连不上 GitHub"这一条。

需要你做的（我这边做不了，都要 GitHub 账号）：
① 在 GitHub 上建一个**空仓库**（建议同名 `3dworld`）；
② 在 Gitee 仓库设置里绑定 GitHub 账号 + 填 PAT（勾 `repo`）。

### 本批为"通了就能立刻看结果"做的准备

- **逐步操作手册**：`docs/P5-d_CI_运行与GitHub镜像.md`
  （前置 GitHub 空仓库 + PAT 权限、Gitee 表单四件事、5 分钟最短间隔、同步范围
  = 分支/标签/提交但**不含 release 附件**、功能可能不对普通仓库开放时的退路）。
- `.github/workflows/ci.yml` 新增 **`workflow_dispatch`**：镜像同步过去之后，可以先在
  GitHub 上**手动点一次**把两个 job 跑起来 —— 尤其是那个**从没在真 Linux runner 上跑过**
  的 `regression-linux`（它的无头 GL 探针目前是 `continue-on-error: true`，先拿一次真实输出，
  确认 EGL+llvmpipe 能建出上下文后再升级为阻塞步骤）。
- 门禁 `workflows-valid` 保证 workflow 本身可解析、结构完整、里面的命令可达
  （否则镜像过去只会得到一个"永远不绿的 CI"，而本机看不到任何提示）。
- ⚠ **我读不到 GitHub 的 CI 结果**（本机连不上 GitHub）—— 跑完请把 `Actions` 结论贴回来，
  或给一个可用代理，我自己读。

## [未发布] — P5-c：攒批一次性重建发行版 → `v2026.10.14`

按 `docs/分发指南.md` §3.0 的攒批铁律，把"待重建清单"**一次性清空**。
重建前先把"不可再生的附件"取回本地 —— 这一步是本次新踩出来的（见下）。

### 重建结果（实测）

| 项 | 结果 |
|----|------|
| 新发行版 | [`v2026.10.14`](https://gitee.com/siwpan/3dworld/releases/tag/v2026.10.14)（id 1204762，target `5c725f4`） |
| 附件 | **42 个** = **39 条成片** + `3dworld-demo-pack-20261009.zip` + 2 个自动生成的源码包（后加的 2 条**追加**即可，不必重建 —— 见下） |
| 成片 | **25 → 39 条**（`recordings/` 354MB）；`boxward_60s.mp4` **替换**（正交等距视角重录），其余 15 条新增 |
| 旧发行版 | `v2026.10.09` 已删除（附件配额 / 单一真相；**tag 保留**，源码包链接不受影响） |
| 匿名验收 | 发行版页 `releases/tag/v2026.10.14` → **200**；抽两条附件匿名下载并比 sha256 → **MATCH** |

### 新增成片（15 条）

- **④ 渲染四件套**：`csm_shadow_40s`（级联阴影）/ `volumetric_fog_60s`（体积雾与光轴）/
  `dof_focus_24s`（景深，用 demo 自带 `flag rack` 自动扫焦）/ `motion_blur_40s`（运动模糊）
- **P3 帧图**：`frame_graph_36s`
- **引擎特性**：`pointcloud_edl_30s` / `wboit_demo_30s` / `skinned_creature_30s` / `gpu_cull_field_30s`
- **玩法内核**：`voidward_60s` / `pulsefall_45s`（`flag auto`）、`scene_walker_40s`（`flag walk,interact`）、
  `brickward_60s`（BQM 致敬线）
- **交互型两条（补完，需先给 demo 加自演示入口）**：
  `stillhall_40s`（`--flag auto,pace=0.9` 循环重放点击序列）、
  `scene_viewer_45s`（`--flag orbit` 相机自转 8°/s ⇒ 45s 正好一圈）。
  ⚠ 这两条**默认都不自动动**（stillhall 跑完 5 步就静止、scene_viewer 相机只跟鼠标走），
  直接录会得到"一张静止的图"——所以给它们各加了一个显式的自演示入口
  （`stillhall` 的 `auto` 还会**循环**：跑完一轮停 2s 再重置，否则 40s 里只有 2.3s 是动的）。
- `record_all.py` 因此新增 14 条录制条目，并修了一个**会中断整批录制**的缺陷（见下）

### 追加而不重建（按 §3.0 的口径）

后加的这两条走 **`attach` 直接追加**（release 从 40 → 42 个附件），**没有**再删重建 ——
按 `docs/分发指南.md` §3.0：「追加新附件则不必重建；但标题/描述里的计数要同步」。
本次同步了 3 处：发行版**标题**的「37 → 39 条成片」、描述正文的「共 37 → 39 条」与
「24 → 39」、以及各分组清单里新增的两行。改完 **回读断言**（name/body 逐字一致，
3502 字 vs 3502 字）—— 别只看状态码。

> `PATCH /releases/{id}` 的坑（skill 记过，本次复现）：**必须带 `tag_name`**，
> 只传 `name` + `body` 会 400 `{"messages":["tag_name is missing"]}`。

### 修复

- **`record_all.find_app_class` 只认 `DemoApp`** ⇒ `pointcloud_edl` 一进来就
  `未找到 DemoApp 子类` 并让**整批录制中止**（`wboit_demo` / `skinned_creature` /
  `gpu_cull_field` / `voidward` / `pulsefall` **一条都没录**，实测第一批只产出 7 条就 EXIT=1）。
  `build_app` 其实支持三类宿主（harness `DemoApp` / engine `EngineApp` / presentation 裸 App），
  现在两类都认；那 6 条 `EngineApp` 宿主也显式写了 `cls=`。

### 重建流程里新踩的一条（已写进 `docs/分发指南.md` §3.0）

**删 release 会连附件一起清掉，而有些附件本地已经没有了。** 本项目的 `dist/` 被 gitignore，
`3dworld-demo-pack-20261009.zip`（63.9MB）当时**只存在于 Gitee** —— 所以重建的**第一步**
必须是"匿名把不可再生的附件取回本地并校验"（实测 66,966,891 字节 / sha256 `51930d79…` /
7 个条目 ✅）。没有这一步，重建就等于永久丢失 exe 包（重打包要装 PyInstaller，本机 venv 里没有）。

### 成片核查方式（也是本次踩出来的）

逐条核查：`ffmpeg -i` 读时长/分辨率 + `-f null -` 全解码 + **抽两帧比裸像素 md5** 判断"动没动"
（本机无 ffprobe，见 MEMORY）。13/13 通过。

> 核查脚本**第一版用 `-f framemd5` 读校验和，7 条全部误判"静止"** —— 那是工具链的错：
> framemd5 的输出字段不是猜的形态，正则匹配不到 ⇒ 两边都是 `None` ⇒ 判成"没动"。
> 改用 `-f rawvideo -pix_fmt rgb24` 落到 Python 里 hash，并把"抽帧失败"与"静止"分开报：
> **判据要能自证，取不到数据时不许把它算成"通过/静止"。**

## [未发布] — P5：内核脚手架 + CI 扩到 Linux

P4 之后的两件"自选收尾"。做完之后有个额外收获：**给内核写模板这个动作本身，
逼出了一个全仓隐患**（见下）。

### 新增

- **`scripts/new_kernel.py`** —— 生成新玩法内核 + 配置树 + 单测，并**自动登记三张表**
  （`host/registry.py` 的服务短名表、`host/tree.py` 的 `WANT_<名>`、`cli/config_boot.py`
  的 `CONFIGS`）与 `run_all_tests.MODULES`。写盘前一律 `ast.parse` 守卫。
  `--undo` 撤销（含从三张表摘除），**逐字节还原**。
- **`config_boot` 表驱动 + 通用 runner** —— `CONFIGS`（kind → yml / 所需服务 / 是否需要
  GL 就绪）+ `RUNNERS`，`all` 的顺序从 `CONFIGS` 派生；没在 `RUNNERS` 里的 kind 走
  `run_generic`（纯逻辑内核的通用 headless 验收：四条断言 —— 服务真挂上 / `inject == []`
  与 `require_gl=False` **配对校验** / `auto_step` 真跑到 `won` / `dispose` 后不可达）。
  于是"接入一个新内核"从**改两处代码**降到**加一行数据**。
- **门禁 `scaffold-kernel-valid`** —— 内核/配置树/测试模板 渲染 → `ast.parse` →
  **真 import** → **按 `run_generic` 的契约真跑到 `won`**（不是只对片段）。
- **门禁 `workflows-valid`** —— `.github/workflows/*.yml` 可解析、结构完整
  （`on` / `runs-on` / 非空 `steps` / `uses` 钉版本）**且里面的命令可达**。
  理由：**workflow 是本机跑不到的那一半**，它坏了本地没人会发现。
- **`check_imports.py` 第二条不变量** —— 「仓库内自带 `__init__.py` 的目录不允许出现在
  `sys.path` 上」。
- **CI 新增 Linux job**（`ubuntu-latest`）：`PYGLET_HEADLESS=1` + Mesa llvmpipe（EGL）
  让无头机器也有 GL 上下文；系统依赖清单是从**本仓锁定的 pyglet 源码里的 `load_library`
  调用**读出来的（X11 一族 + GL/GLX + EGL + `libgl1-mesa-dri`），不是照抄博客。
- `scripts/new_demo.py` 抽出 `register_test` / `unregister_test` / `write_preserving_eol`
  供内核脚手架复用。

### 修复（模板逼出来的全仓隐患）

- **8 个 `games/<名>/kernel.py` 的自举块少算一层**（只有 brickward 是对的）：把 `world3d/`
  推上 `sys.path` ⇒ **`world3d/platform/` 遮蔽标准库 `platform`**（pyglet 自己
  `import platform`）⇒ `platform.system()` 抛 `AttributeError: module 'platform' has no
  attribute 'system'`（实测复现）。已改成 4 层，并写清"为什么必须是四层"。
- `check_imports.py` 删掉声明了却从未使用的 `SKIP_PREFIX`（陈旧死代码）。
- 脚手架写盘改为**保持原文件行尾**：本仓 `core.autocrlf=true`，一律写 LF 会让
  `git status` 把内容相同的文件一直标成 `M`（内容哈希其实等于 HEAD）——
  "撤销干净"就变成"内容干净、状态栏不干净"，而人会开始忽略 `git status`。

### 验收（实测）

| 项 | 结果 |
|----|------|
| `python scripts/verify.py` | **16/16 通过** → `== VERIFY_OK ==` |
| `python scripts/verify.py --selfcheck` | **16/16 拦得住** |
| 脚手架实战（探针 `zzkern`） | `config_boot zzkern` → **`== ZZKERN_OK ==`**（won / 2.3s / 18 步）；单测 **7/7**；`verify.py` **精确红 3 条**（README 的内核 9→10、配置树 10→11、测试清单缺一行）；`--undo` 后 `git status` 干净、门禁回绿 |
| 配置透传 | 改 yml 的 `seed:` → 日志 `seed=424242`、`steps=16`（跟着变） |
| `check_imports.py` 反证 | `void` 改回 3 层 ⇒ 红且点名 `sys.path += world3d/`；还原 ⇒ 绿 |
| 脚手架行为反证 | 模板 `STEP_HZ` 改坏 ⇒ 门禁给**契约级**原因（"`auto_step` 未在 120s 内收尾 —— `run_generic` 会判它只是能构造"） |
| `scripts/run_all_tests.py` | **14/14 模块 / 281 项**（探针在场时 15/15 / 288 项） |
| `python -m world3d.cli.config_boot all` | **10/10** 棵配置树 |
| `python -m world3d.cli.frame_plan --all-toggles` | **16/16** |
| `scripts/check_imports.py` | **117 模块 / 0 失败**（1 个按设计跳过） |
| `scripts/verify_demos.py` | `ALL_DEMOS_OK (37 demos)` |
| `workflows-valid` | 1 个 workflow / **2 个 job** 结构完整、10 条命令可达 |

> ⚠ **一处必须说清的限制**：Linux job **未在真 runner 上跑过** —— 本机是 Windows，
> 没有 Docker、没有 WSL，无法复现。因此"无头 GL 探针"（`config_boot all`）暂设
> `continue-on-error: true`，等第一次真跑拿到输出后再升级为阻塞步骤。
> 另外：workflow 只有 **GitHub** 会执行，而本仓 remote 只有 Gitee ⇒
> **CI 目前不会被触发**，需要加 GitHub 镜像 remote（或改用 Gitee Go）。

## [未发布] — P4：机械门禁 + 文档站 + 脚手架 —— **路线全部收口**

引擎化的最后一环不是功能，而是**防止前面所有东西慢慢腐掉**。
本轮改造里"同一件事被手改过 5~7 次"（示例数在 README 里有 **7 处**、测试清单 14 行、
配置树/内核/共同核计数、文档相对链接 29 个文件、cli 用法 docstring），
每一次都可能漏一处 —— 而这种漏**没有任何测试会发现**。

**门禁上线当天就抓到一个真漂移**：`tests/test_timeline_ui` 注册在 `run_all_tests.py` 里
（14 个模块）却**没进 README 的测试清单**（只列了 13 个）。

### 新增

- **`scripts/verify.py`** —— 14 条机械门禁，**<1 秒、不联网、不跑构建、不改文件**：
  * 一致性：`readme-demo-count`（README 里**每一处**示例数 == 真实 demo 数）、
    `readme-test-modules`（README 表 ↔ `run_all_tests.MODULES`，**双向**）、
    `readme-test-counts`（每行「N 项」== 该模块声明的用例数）、
    `readme-config-count` / `-kernel-count` / `-core-count`、
    `examples-index-pairs`（`examples/README.md` 表格 ↔ `examples/*.py`，**双向**）、
    `commands-exist`、`cli-usage-names`、`readme-tree-real`（项目结构树里的路径都存在）；
  * 契约：`doc-links`（30 个文件的相对链接全部有效）、
    `pyrefs-exist`（文档与 CI 里每一处一行式 `python -c "import <模块>"` 的模块都真实存在）、
    `scaffold-templates-valid`（脚手架模板渲染 → `ast.parse` → **真 import** → 关键片段齐全）；
  * 新鲜度：`docs-index-fresh`（`docs/README.md` == 重新生成的结果）。
  * `--list / --only / --json / --verbose / --fix / --selfcheck`，退出码 0/1/2。
- **`scripts/verify.py --selfcheck`** —— 沙箱里给**每条**门禁注入一个针对性破坏，
  断言它变红：**14/14 全拦得住**。"永远为绿的门禁等于装饰品"，所以这一步不能省。
  沙箱只拷文本（跳过 assets/剧照/vendor），50MB 仓库缩到 ~2MB。
- **`scripts/docs_index.py` + `docs/README.md`** —— 文档站的第一块：**索引是生成物**
  （26 → 27 篇，按 `P0-`…`P4-` / `4-` / `BQM` / 专题分组，含标题与首段摘要）。
  三条自我约束：不含时间戳/解释器版本、不含行数（否则天天"过期"，人会无视这条门禁）、
  纯标准库且确定性。
- **`.githooks/pre-commit`**（已启用 `git config core.hooksPath .githooks`）——
  只跑 `verify.py`；**找不到 Python 就跳过而不是拦下**；失败时给下一步动作
  （`--fix` / `--verbose` / `--no-verify`）。两条路径用**真实 PortableGit 的 sh** 实测过。
- **`scripts/new_demo.py`** —— 脚手架：生成**开箱能跑**的新 demo / 新测试模块
  （`--register` 自动登记进 `run_all_tests.MODULES`）。
  模板里带着那些用血换来的行：`sys.path` 显式引导块（少它会让**所有** demo 一起
  `ModuleNotFoundError`）、`TITLE/HELP/SHOTS` 与 `setup/update/render` 钩子、
  测试模块的 runner + `ALL_*_TESTS_PASSED` 标记。实测用探针名 `zzprobe` 各生成一次：
  demo 模板 `--smoke 3 → EXIT_OK`、测试模板 **2/2 通过**（验证完即删，不入库）。
  两条实现坑：模板用 `string.Template`（GLSL 花括号会让 `str.format` KeyError）、
  `_register_test` 写盘**前先 `ast.parse`**（不留半个坏文件）。
- **门禁挂进 CI**（`.github/workflows/ci.yml` 新增 `verify.py` + `check_imports.py` 两步）——
  pre-commit 只在装了钩子的那台机器上跑，CI 才是"无论如何都会跑"的那份。
- README 新增「机械门禁」与「脚手架」两节；`docs/分发指南.md` 的发布前检查补上 `verify.py`。

### 门禁抓到的五处（这是它值钱的地方）

| # | 抓到什么 | 性质 |
|---|---------|------|
| 36 | `tests/test_timeline_ui` 没进 README 测试清单 | **真漂移** —— 典型"改了 A 忘了 B"，此前没人会注意到 |
| 37 | 用例数门禁只认 `tests = [...]` 一种形态 ⇒ 5 个用 `def test_*` 计数的旧模块全被记成 SKIP | **判据假设太窄**（"先 grep 真实措辞再写正则"）。修时顺手加了更强的检查：**两种形态同时存在时必须相等** ⇒ 能抓"定义了测试函数却没注册进 runner"（定义了但永远不跑） |
| 38 | `commands-exist` 把 `python -m venv`、`python -m PyInstaller`、`python x.py`（文档里的**模板占位符**）当死命令 | **假阳性**：工具模块要白名单；占位符写成 `python <文件>.py`（`<` 不在路径字符集里，天然被排除） |
| 39 | `.github/workflows/ci.yml` 里 `import _cordis_locate` —— P2-4 改名后**早就坏了** | **真 bug**：迁移脚本只改**行首** `import`，改不到**命令行字符串里**的；CI 只在 GitHub 上跑，本机验收看不见 ⇒ 新写了 `pyrefs-exist` 才暴露 |
| 40 | `docs/P0-1_核心抽离_design_note.md` 里 `from core import contracts` 也已经失效 | 同一个门禁抓到的**第二处** —— 证明这类失效**从来不是孤例** |

> 第 38 条最值得记：**门禁会抓它自己的文档** —— 这不是缺点，说明它真的在扫全仓文本。
> 连**"引用这个模式"的散文**它都抓（写 design note 时原样引用了一次，当场 6 处变红），
> 于是定下全仓文档约定：**引用模式时写占位符**（`python -c "import <模块>"`、
> `python <文件>.py`）。**能被机械校验的字面必须是"真命令"；只想说明形状就用占位符。**
> 第 39/40 条是另一个方向的价值：**门禁不只防"未来会漂"，还会揭"现在就已经错了"** ——
> 它上线时抓到的第一类漂移是意料之中的（README 手改漏行），这两条则是"我根本不知道它坏了"。

### `--selfcheck` 抓过我两次"假绿"（连自检本身都会假绿）

| # | 我的假绿 | 根因 |
|---|---------|------|
| 1 | `scaffold-templates-valid` 的片段断言写成 `'def render'` ⇒ 破坏用例把函数名改成 `render_broken`，**仍然匹配** | 断言太松：**"出现过" ≠ "存在"**。改成带括号的 `'def render(self):'` |
| 2 | 修完之后**破坏用例还是 PASS** | 加载 `new_demo.py` 用 `import`，而 **`sys.modules` 会缓存第一次导入** ⇒ 沙箱里改过的副本**永远不会被重新加载**。改按**路径 + 唯一模块名**（`SourceFileLoader`） |

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python scripts/verify.py` | **14/14 通过** → `== VERIFY_OK ==`（每条都带数字证据，如"7 处断言 == 37 个 demo"） |
| `python scripts/verify.py --selfcheck` | **14/14 拦得住** |
| `python scripts/docs_index.py --check` | 索引最新（27 篇登记） |
| pre-commit（真实 git sh） | 通过 `rc=0` / 失败 `rc=1` + 拦截指引 |
| 脚手架（新 demo / 新测试模块各生成一次） | `--smoke 3 → EXIT_OK`；测试 **2/2** |
| `scripts/run_all_tests.py` | **14/14 模块 / 281 项** |
| `python -m world3d.cli.config_boot all` | **10/10** 棵配置树 |
| `python -m world3d.cli.frame_plan --all-toggles` | **16/16** |
| `scripts/check_imports.py` | world3d/ 下 **117 模块、失败 0**（1 个按设计跳过） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (37 demos)` |

> **路线到此全部收口**：`P0 试点转正 ✅ → P1 库化 ✅ → P2 分层归位 ✅ → P3 帧图管线 ✅ →
> P4 收尾 ✅`；并行的 ④ 渲染扩展（CSM / 体积雾 / 景深 / 运动模糊）与 BQM 致敬线（B1~B4）
> 也都已交付。**剩余可做**：新内核 / 新配置树的脚手架模板（要同步的地方更多，等下次真新增时补）、
> 以及发布节点的**待重建清单**（`boxward_60s.mp4` 替换 + `brickward_60s.mp4` 新增
> + 6 个新 demo 的成片）。

## [未发布] — P2-4：命名空间收口（`world3d.*`）—— P2 收尾

引擎化的最后一块地基：**7 个顶层包收进 `world3d`**。
改完之后，"这个仓库是一个引擎项目"在**导入路径层面**才成立 —— 之前它只是"一堆并列的包"。

```text
改前                                 改后
core/ logic/ engine/ presentation/   world3d/
games/ host/ cli/ _cordis_locate.py    ├── core/ logic/ engine/ presentation/
examples/ tests/ scripts/ configs/     ├── games/ host/ cli/ platform/
                                       ├── _cordis_locate.py
                                       └── compat.py   ← 旧路径过渡层
```

### 新增

- **`scripts/migrate_p2_4.py`** —— 迁移工具（保留以便复查"到底改了什么"）。它覆盖**三类载体**：
  * **import 语句**的三种形态（`from core.x import Y` / **`from core import x as y`** / `import core.x`）；
  * **字符串里的模块路径**（`'games.foo.kernel:Bar'`）—— 正则改 import **永远碰不到这类**，
    而 `host/registry.py` 的 `SERVICES` 表、`core/contracts.py` 的契约清单、
    `world3d/_info.py` 的公共面清单全靠它，漏一处就是"配置树挂不起来"；
  * 包内**仓库根推导**补一层 `dirname`（包深了一层）—— 规划点名的迁移坑，P0-3 / P2-1 各踩过一次。
- **`scripts/check_imports.py`** —— **全仓可达性门禁**：把 `world3d/` 下**每个** `.py` 都 import 一遍。
  这是本批唯一能抓"改漏"的东西：**单测只覆盖被它引用到的模块**，
  没被任何测试 import 的模块改坏了单测不会红。实测 **117 个模块 0 失败**。
- **`world3d/compat.py` + `tests/test_compat.py`（6 项）** —— 旧路径过渡层，
  用**元路径别名**而不是桩文件：`install_legacy_aliases()` 之后
  `import engine.csm` 与 `import world3d.engine.csm` 拿到**同一个模块对象**
  （`is` 成立 ⇒ 模块级单例/缓存/`isinstance` 全部一致，桩文件做不到）。
- **`pyproject.toml` 简化**：`packages.find.include` 从 9 个模式缩到 `["world3d*", "cordis*"]`；
  **`py-modules = ["_cordis_locate"]` 整行删掉** —— 那个顶层模块也搬进了 `world3d/`，
  于是 P1 阶段那个"顶层模块必须单独声明"的打包陷阱**从根上消失**。
- README 的目录树按新结构整段重写；`configs/*.yml` 注释、`.github/workflows/ci.yml`、
  各 `cli/*.py` 的用法 docstring 一并同步。

### 两处**有据的偏离**（规划原文 vs 实际做法，理由见 `docs/P2-4_命名空间收口_design_note.md` §2）

| # | 规划原文 | 实际做法 | 理由 |
|---|---------|---------|------|
| 1 | "一次性切 `src/` 并…" | **只收进 `world3d/`，不切 `src/`** | 本项目全部验收命令都是"从仓库根直接跑"：`python -m world3d.cli.config_boot all`、`python examples/xxx.py`、`python -m tests.xxx`。切 `src/` 之后这三条在**干净 clone 下必须先 `pip install`**（或设 PYTHONPATH）才成立 —— 那会断掉"新拉下来就能验"这条前提，而 `src/` 的好处（防误 import 工作树）在这里几乎为零。**收益 < 代价。** |
| 2 | "旧路径留兼容再导出层一版" | **`world3d.compat` 元路径别名** | 桩文件方案要给每个子模块各写一个 `xxx.py`（`from world3d.core.xxx import *`）—— 那是**第二份公共面真相源**，新增/删除模块都要同步，`import core.x.y` 深路径还要逐层铺。别名 finder 40 行、零维护，且**同一性**成立。过渡件，外界迁完整体删除。 |

### 修复（本批三个）

| # | 症状 | 根因 |
|---|------|------|
| 33 | **顶层包别名成功、子模块却是两个模块对象** | 别名 finder 挂在 `sys.meta_path` **末尾** ⇒ `PathFinder` 顺着父包 `__path__` 把 `world3d/host/tree.py` **又加载了一遍**（`host.tree is world3d.host.tree` 为 False；模块级单例/缓存各存一份）。⇒ 改挂**最前**。**只测顶层包的话这个 bug 完全看不出来**，所以判据必须覆盖 3 层深子模块 |
| 34 | 迁移后 `configs/*.yml`、CI、`cli/*.py` 的 docstring 仍写旧命令 | 迁移脚本只覆盖 `.py` 的 import 与 `.md` 的散文，**`.yml` / CI / docstring 用法行是三类独立载体** ⇒ 最后加一道"全仓陈旧引用扫描"，结果为 **0** 才算完 |
| 35 | heredoc 又吃反斜杠（本会话第 4 次） | `<<'PY'` 里写 `'\\'` 会被吞掉 ⇒ `SyntaxError`。⇒ **含反斜杠/嵌套引号的脚本一律落文件再跑**（本批的迁移脚本与门禁脚本都是这么来的） |

> 第 33 条的教训值得单列：**"别名"这种机制的判据必须是"同一性"，不是"能导入"。**
> 能导入只要求名字能解析；同一性才保证"两份状态不会分叉"。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python scripts/check_imports.py` | **117 个模块 0 失败**（全仓可达） |
| `python -m tests.test_compat` | **6/6** |
| `scripts/run_all_tests.py` | **14/14 模块 / 281 项** / `ALL_TESTS_PASSED` |
| `python -m world3d.cli.config_boot all` | **10/10** 棵配置树（这条专门走 `registry` 的**字符串模块路径**） |
| `python -m world3d.cli.frame_plan --all-toggles` | **16/16** 组合 |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (37 demos)` |
| `main.py` / `kkrieger_demo.py` / `pyglet_3d_demo.py --smoke` | 三个顶层入口 `EXIT_OK` |
| 全仓陈旧引用扫描 | **0 处**（排除解释性文本） |

> **P2 到此全部收口**（P2-1 platform 抽出 · P2-2 core 越界清出 · P2-3 demos→examples ·
> P2-4 命名空间收口）⇒ 下一步是 **P4**（文档站 / 门禁 / 脚手架）。

## [未发布] — P3-b：帧图驱动渲染（帧图拿到了自己的 consumer）

P3-a 做出了帧图内核（声明 → 编译期校验 → 剔除 → 排序 → 别名），但它当时**没有 consumer** ——
只有门禁与测试在读 `plan.order`。本批补上真正用它渲染的 demo，证明那套东西不是纸面工作。

### 新增

- **`examples/frame_graph.py`**（第 37 个示例）：渲染循环就是
  `for name in self.plan.order: getattr(self, '_pass_' + name)()`。
  * **离屏目标按资源声明创建**（`'screen'` / `(1024, 1024)` / `r32f` / `rgba16f` 都真的被用到）；
  * **每个 pass 绑哪张纹理来自 `plan.reads_of(名字)`** —— 所以关掉一个效果时，
    下游**自动**改读直通后的资源（关景深 ⇒ 运动模糊直接读雾的输出），渲染端一行都不用改；
  * 按 `1`~`4` 现场开关四个效果会**重编译**，HUD 显示 pass 数 / 被剔除的 pass / 别名组数；
  * 四个效果的 GLSL（`csm_glsl(1)` + `volumetric_glsl(fn, 'csm_shadow')` + `dof_glsl` + `mb_glsl`）
    **拼在同一个片段里**运行。
- **`FramePlan.resolved` / `reads_of()` / `writes_of()`** —— 计划现在给出**重定向之后**
  每个 pass 真正读写的资源。这是写这个 demo 时才发现的**必需 API**：少了它，
  `disable()` 的"直通"在渲染端落不了地（渲染器会去绑一张已被剔除的中间纹理 ⇒ 黑屏）。
  **编译期算出来的信息，必须能交给执行端。**
- `tests/test_framegraph.py` 增至 **17 项**（新增 `resolved` 读写随直通更新的守卫）；
  README / examples/README 计数 36 → 37。

### 修复（本轮两个，都是"位置很远的报错"）

| # | 症状 | 根因 |
|---|------|------|
| 31 | `--flag off=fog,dof,motionblur` **只有第一个生效** | 平台层的 `--flag` **按逗号切分**（`--flag a,b` ≡ `--flag a --flag b`）⇒ 到手是 `{'off=fog','dof','motionblur'}` 三个 token，而我只取最后一个 `off=`。现在两种写法都认（`off=fog` 与裸 `fog`），并支持重复写 |
| 32 | 同一个 flag 触发 `AttributeError: 'FrameGraphApp' object has no attribute 'targets'` | `setup()` 解析 flag 时会调 `_free_extra_targets()`，而那时离屏目标还没建 ⇒ 异常**打断了 setup 的循环**、后面的 flag 全被跳过。**报错信息与真实原因（flag 解析）离得很远**。已加守卫 |

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_framegraph` | **17/17** → `ALL_FRAMEGRAPH_TESTS_PASSED` |
| `python -m world3d.cli.frame_plan --all-toggles` | **16 种组合**全部合法 → `== FRAME_PLAN_OK ==` |
| `scripts/run_all_tests.py` | **13/13 模块 / 275 项** / `ALL_TESTS_PASSED` |
| `python examples/frame_graph.py --smoke 5` | `EXIT_OK`（`[framegraph] 重编译 → 8 pass，别名组 3`） |
| `... --flag off=fog,dof,motionblur` | 逐次重编译 **8 → 7 → 6 → 5** pass，剔除列表与别名组数同步变化 |
| `... --shot` | `[shot] OK 2 files`（HUD 上直接读到 `帧图 8 pass [depth→shadow→scene→fog→dof→motionblur→tonemap→present] 别名组 3`） |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（零回归） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (37 demos)` |

> **P3 到此收口**（P3-a 内核 + 门禁，P3-b 图驱动渲染）。
> 从"注释里的顺序约定"到"编译期校验 + 门禁会红 + 渲染循环按计划走"，
> 这条链现在**每一步都有证据**：`tests/test_framegraph.py` 17 项管编译期行为、
> `cli/frame_plan` 管 16 种组合的顺序、`examples/frame_graph.py` 管"计划真的驱动了帧"。

## [未发布] — P3-a：帧图（Frame Graph）—— 把"这一帧跑哪些 pass"变成可编译的数据

④ 轨道那四个效果**互相共用资源**（三个都用同一张距离图、阴影图又给雾查），
于是"谁先谁后"不再是可以随手写的：顺序错了会**静默出错**（画面"有点不对"，但不报错、不崩）。
本批把这些约束从注释搬进**数据结构 + 编译期校验**。

### 新增

- **`engine/framegraph.py`**（纯逻辑，不 import GL）：
  * `FrameGraph` / `Pass` / `Resource` —— 声明式描述；
  * `compile()` —— **校验**（读不到生产者 / 有环 / 计划为空）→ **死 pass 剔除**
    （从输出反向遍历）→ **拓扑排序**（同层按声明顺序，保证确定性）→
    **瞬时资源别名**（生命周期不重叠的可共用内存）；
  * `disable()/enable()` —— **运行期开关**：把被关 pass 的输出**直通到它的第一个输入**，
    于是中间纹理不再需要（被别名回收）。重定向链**解析到不动点**；
  * `standard_3d_plan()` —— 标准 3D 帧：`depth → shadow → scene → fog → dof → motionblur →
    tonemap → present`；顺序常量 `SEQUENCE_OF_DEPTH_EFFECTS`。
- **`cli/frame_plan.py`** —— 帧图门禁（与 `cli/config_boot` 同风格）：
  `python -m world3d.cli.frame_plan --all-toggles` 把**16 种效果组合**跑一遍，
  断言每种组合的计划都合法且"雾 → 景深 → 运动模糊"顺序正确，最后打 `== FRAME_PLAN_OK ==`。
  **顺序一旦被改错，验收脚本会红** —— 而不是等画面看起来"有点不对"。
- **`tests/test_framegraph.py`（16 项）**：物理顺序、死 pass 剔除、present 的 side_effect
  （含**反例**）、读不到生产者、外部资源免生产者、环检测、输出必填、四类声明错误、
  直通需要输入、**三连关的不动点解析**（回归守卫）、开关的 pass 数阶梯、enable 恢复、
  别名（含"共活不许别名"的边界）、确定性、plan API。
- `scripts/run_all_tests.py` 注册第 13 个模块；README 补测试与门禁命令。

### 修复 / 实踩

| # | 症状 | 根因 |
|---|------|------|
| 29 | 三个效果**一起**关时报"资源 hdr_fog 没有生产者"；关单个却没事 | 直通重定向只映射了**一层**。链是 `hdr_mb → hdr_dof → hdr_fog → hdr` ⇒ 必须**解析到不动点**。这类"单点可用、组合才炸"的 bug 只有把组合也跑一遍才抓得到（门禁的 `--all-toggles` 正是为此） |
| 30 | 门禁 `--off dof` 报"没有名为 dof_pass 的 pass" | 我把两种关法混用了（建成期不声明 + 又运行期 `disable`）。**纯生产者 pass 只能建图时决定**（它没有上游可直通，内核拒绝是对的），**消费者 pass 才走运行期 disable** |

> 另有四处**测试判据自己写错**（本轮再次出现，值得记）：
> ① 用 `endswith('_pass')` 筛 pass —— `depth_prepass` 结尾是 `_prepass`，直接漏掉；
> ② 把"读 A 写 B"当成"A 与 B 生命周期不重叠"（那个 pass 里两者**共活**，前提不成立）；
> ③ 断言"t3 不出现在任何别名组"，而 t3 **可以**和别的不重叠资源配对（只该否掉 t2↔t3）；
> ④ 输出指向外部资源 ⇒ 计划合法地变空，而我在测试里当成了 bug。
> ⇒ 内核因此补了一条**响亮的报错**："编译后的计划是空的：没有任何 pass 喂到你声明的输出"
> （把"合法但无用"变成"编译期就喊出来"）。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_framegraph` | **16/16** → `ALL_FRAMEGRAPH_TESTS_PASSED` |
| `python -m world3d.cli.frame_plan --all-toggles` | **16 种组合**全部合法、顺序正确 → `== FRAME_PLAN_OK ==` |
| `scripts/run_all_tests.py` | **13/13 模块 / 274 项** / `ALL_TESTS_PASSED`（上一批 258 项） |
| 全开时的计划 | 8 pass；别名组 3 组（`distance⊕ldr` · `shadow⊕hdr_dof` · `hdr⊕hdr_mb`） |
| 关效果阶梯 | 关 0/1/2/3 个 ⇒ pass 数 **8 / 7 / 6 / 5**，别名组 3 / 2 / 2 / 1 |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（零回归） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (36 demos)` |

> **本批是 P3-a**（内核 + 门禁 + 测试）。**P3-b = 图驱动渲染的 demo**
> （按 `plan.order` 逐 pass 派发、开关效果时现场重编译、把剔除与别名显示在 HUD 上）
> 留下一步 —— 见 `docs/P3-a_帧图_design_note.md` §5。

## [未发布] — ④-4：运动模糊 —— 相机重投影（④ 轨道收口）

屏幕空间的"整屏方向模糊"（拿一个全局向量去采样）看起来像蒙了层纱：**它让所有像素糊得
一样多**。真实的运动模糊里**近处的东西扫过的像素多得多** —— 所以要每像素从自己的深度
反推世界坐标、再用**上一帧**的 view-projection 投回去。这样运动矢量天然带深度，
而且免费得到三条只有带深度才成立的律：

| 相机状态 | 运动矢量应当 | 本批实测 |
|---------|-------------|---------|
| **不动** | **恒为 0**（不需要额外的"静止检测"） | 9 位置 × 5 深度，最大 **2e-15** |
| **纯平移** | ``\|mv\| ∝ 1/z``（近处扫得远、远处几乎不动） | ``\|mv\|·z`` 恒为 **0.301571**（5 个深度相对差 < 1e-6） |
| **纯旋转** | **与深度无关**（远处的山也糊） | 5m~1e7m 的运动矢量**逐位相同**（< 1e-9） |

### 新增

- **`engine/motionblur.py`**（纯数学 + 可注入 GLSL，全部可 headless 断言）：
  * `reconstruct_world` —— 屏幕 uv + 线性距离 → 世界坐标（与体积雾的 `_world` 同式，
    一张距离图三处共用：雾 / 景深 / 运动模糊）；
  * `reproject` / `motion_vector` —— 投到上一帧、取运动矢量（含越界标记）；
  * `shutter_weights` —— 快门时间内的采样权重（`box` 等权 / `tent` 中心更重）；
  * `mb_glsl` —— 沿**每像素自己的**运动矢量加权累积、长度上限夹取、
    并按深度剔除"前景拖影污染"。
- **`examples/motion_blur.py`**（第 36 个示例）：`1` 平移 / `2` 原地转 / `3` 推近 /
  `4` 静止 四种相机运动可直接对比（**平移只有近处糊、原地转整幅一起糊、静止时模糊自动消失**）；
  `` ` `` 快门比例 · `N` 采样数 · `V` 直接看运动矢量图。地面加了棋盘
  （拖影在细纹理上最显眼）。
- **`tests/test_motionblur.py`（9 项）**：三条物理律 + 重建往返一致
  （`|重建点−相机|` 恰为 dist、投回去恰为同一 uv，误差 < 2e-15）+ 越界标记 +
  快门权重 + GLSL 结构 + **三模块共存编译**。
- `scripts/run_all_tests.py` 注册第 12 个模块；README / examples/README 计数 35 → 36。

### 修复（本轮两个）

| # | 症状 | 根因 |
|---|------|------|
| 27 | **静止相机竟有运动矢量、平移反而算出 0** | 反投出来的是**世界空间近平面点**，射线方向必须 `normalize(该点 − 相机位置)`；我第一版直接对它归一化（等于把相机当成原点）。这条由"静止相机运动矢量恒为 0"这条不变量抓出来 —— 换任何别的判据都不一定看得见 |
| 28 | "长度上限"形同虚设 | 像素长度写成 `length(mv)/height*1000`，**量纲就是错的**。正解 `length(mv * 分辨率)`。（这个错不会报错、也不会明显崩，只是夹取永远不生效） |

> 另有一处**测试自己配错**：构造"纯平移"时我让两台相机共用一个固定 target，
> 那其实是**平移 + 微转动**，于是"运动矢量与深度无关"被误判为不成立。
> 正确做法是 `target = position + 固定前向`。**构造判据的场景本身也要满足前提** ——
> 这是本会话第三次栽在"判据的前提"上。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_motionblur` | **9/9** → `ALL_MOTIONBLUR_TESTS_PASSED` |
| `scripts/run_all_tests.py` | **12/12 模块 / 258 项** / `ALL_TESTS_PASSED`（上一批 249 项） |
| `python examples/motion_blur.py --smoke 4` | `EXIT_OK` |
| `python examples/motion_blur.py --shot` | `[shot] OK 2 files`（两张 md5 不同） |
| 模糊的客观证据（画面水平梯度均值） | 平移中 **0.409** vs 静止 **0.473**（拖影确实压平了边缘） |
| 三模块共存 | `mb_glsl + csm_glsl + dof_glsl` 拼在一个片段里**编译通过**，21 个 uniform 全在位 |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（零回归） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (36 demos)` |

> **④ 轨道到此四项全部交付**（CSM / 体积雾 / 景深 / 运动模糊）。
> 四批的共同做法是：**先把数学抽成纯函数并写成"能被打穿"的判据，再写渲染** ——
> 于是 `engine/` 里躺着 4 个可 headless 断言的模块（20+12+12+9 = 53 项），
> 而 demo 只负责"把矩阵与纹理传下去"。
> 三者共用同一张**线性距离**图，也已在测试里验证能**拼进同一个片段**
> （CSM + 景深 + 运动模糊 = 21 个 uniform）—— 这正是 **P3 帧图管线**要收拢的东西。

## [未发布] — ④-3：景深（DoF）—— 弥散圆 / 超焦距 / 散景

景深不是「把背景高斯模糊一下」。真实相机是**每一点按弥散圆大小摊成一个圆盘**，
而弥散圆的大小由**光圈、焦距、对焦距离**决定 —— 于是"前景糊得快、背景糊得慢"这种
不对称是**算出来的**，不是调出来的。这一批把整条光学链做进了引擎层，
好处是它全是 19 世纪的公式：**可以用摄影常识当判据，而且容差能收到 1e-9**。

### 新增

- **`engine/dof.py`**（纯数学 + 可注入 GLSL，全部可 headless 断言）：
  * `circle_of_confusion` —— 带符号的弥散圆（前负后正、合焦面恰为 0）；
  * `coc_pixels` —— 折成像素（工程上"超过 1 像素才看得出虚化"）；
  * `hyperfocal` / `dof_limits` —— 超焦距与景深范围（`H = f²/(N·c)+f`）；
  * `bokeh_disk` —— 散景采样盘（黄金角螺旋、**面积均匀**、可切正多边形叶片）；
  * `dof_glsl` —— gather 式虚化：采样盘**烘进着色器常量**（不用随机数 ⇒ 静止画面
    不"沙沙响"、剧照可复现），并按"采样点自己的圆盘能否够到当前像素"剔除
    （否则前景会糊到背景上，边缘留一圈晕）。
- **`examples/dof_focus.py`**（第 35 个示例）：三趟渲染（深度 → 场景 → 合成）。
  `[` `]` 对焦距离 · `,` `.` 光圈 · `B` 散景叶片（圆/5/6/8）· `V` 直接看弥散圆 ·
  `A` 自动扫焦（24s 循环，3m ⇄ 90m，适合出成片）。HUD 实时报 6 个距离上的 CoC（像素）。
- **`tests/test_dof.py`（12 项）**，头号两条是**光学恒等式**：
  ① `focus = H` ⇒ `near` **恰为** `H/2`、`far` 为 ∞；
  ② 在 `near` / `far` 处算出的 CoC **恰好等于**容限 `c`（容差 1e-9）。
  另含：CoC 渐近线（`z→∞` 时趋于 `A·f/(z_f−f)`，且偏差严格遵从 `z_f/z` 的 1/z 律）、
  CoC 与光圈线性、散景盘平均半径 2/3（面积均匀，不是均匀取极角的 1/2）、
  多边形夹取，以及**真驱动编译 + 与 CSM 组合**。
- `scripts/run_all_tests.py` 注册第 11 个模块；README / examples/README 计数 34 → 35。

### 修复（本轮踩到五个，全在"数学对、判据错"这一类）

| # | 症状 | 根因 |
|---|------|------|
| 22 | 测试把 `z=2` 判成"背景" | 我随手写了 `focus=10mm + focal=50mm` —— **合焦面在镜头内部**这种物理上不存在的配置。公式照样算出一个数、符号却是反的 ⇒ 内核补了显式守卫 `focus <= focal → ValueError`（**数值不报错不等于配置合法**） |
| 23 | `focus=H` 时 `near` 比 `H/2` 差 0.08%，端点 CoC 恒等式只到 0.17% | `dof_limits` 的分母漏了 `2f`（写成 `H + (s−f)`，标准式是 `H + s − 2f`）。改成标准式后**两边都精确成立**，测试容差随即从"差不多"收紧到 1e-9 并立刻暴露出来 |
| 24 | 散景 3 叶片被判"超出内切圆 0.5" | **测试**在算多边形法线时多加了一个 `π/blades` 的偏移，于是两套多边形差半个扇区 —— 测试把**对的实现**判成了错。法线角度必须与实现逐字一致 |
| 25 | 一条没带消息的断言失败 | `coc_pixels` 与 `circle_of_confusion` 一样**保留符号**（前景为负），而测试拿带符号的值去比正数。现在断言里显式带 `abs()`，并额外断言"符号不能被悄悄丢掉" |
| 26 | 渐近线相对差 3.0e-6 超过我定的 1e-6 | 那不是 bug，是**真实的 1/z 收敛律**（`z_f/z = 3000/1e9`）。把 z 提到 1e12 并把阈值收到 1e-8，同时**断言偏差严格遵从 `z_f/z`** —— 比写死阈值更能排除"凑巧接近" |

> 这一批的五个坑有四个是**判据本身写错**（而不是实现错）。教训：
> **判据里的每一个数字都要有物理/数学出处**（单位自洽、方向、符号、量级），
> 否则"测试红了"和"实现错了"会被混为一谈 —— 本轮就出现了两次"测试把对的实现判错"。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_dof` | **12/12** → `ALL_DOF_TESTS_PASSED`（含真驱动编译 + CSM 组合） |
| `scripts/run_all_tests.py` | **11/11 模块 / 249 项** / `ALL_TESTS_PASSED`（上一批 237 项） |
| `python examples/dof_focus.py --smoke 4` | `EXIT_OK` |
| `python examples/dof_focus.py --shot` | `[shot] OK 2 files`（两张 md5 不同） |
| CoC 实测（对焦 8m / f2.8 / 85mm） | 1m **−85.6px** · 3m −20.4 · 8m **+0.0** · 20m +7.3 · 60m +10.6 · 200m **+11.7**（远侧**饱和**，近侧继续涨 ⇒ 摄影上的不对称） |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（零回归） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (35 demos)` |

## [未发布] — ④-2：体积雾与光轴（god rays）

「雾」最容易写成"沿射线采几步、每步乘 exp(-ρ·dt）" —— 能跑，但有两个硬问题：
**高度雾的密度随高度指数衰减时，逐步矩形求和会出层状条纹、而且改步数会让整幅雾变浓变淡**；
以及**各向同性散射看不见光柱**（雾的强前向散射才是"太阳在柱子后面出现光柱"的成因）。
这一批把这两件事都做对，并且把"对"做成了**可以在屏幕上并排看出来的对照**。

### 新增

- **`engine/volumetrics.py`**（纯数学 + 可注入 GLSL，全部可 headless 断言）：
  * `height_fog_segment` —— 高度雾**闭式积分**（`∫ρ·e^(−k(y−base))dt`），与步长无关；
  * `height_fog_column`  —— **数值参考实现**，只用来给闭式当对照（测试里两者差 < 1e-9）；
  * `henyey_greenstein`  —— HG 相函数（已除 4π 归一化，球面积分 = 1）；
  * `step_weights` / `dither_offset` —— 步长分布（linear/exp）与交错梯度噪声抖动；
  * `volumetric_glsl`    —— 沿射线推进 + 每步查阴影图求单次散射 + 同一个闭式积分；
    **`u_fog_analytic` 开关**让"闭式 vs 逐步矩形"可以并排对照。
- **`examples/volumetric_fog.py`**（第 34 个示例）：柱廊场景，四趟渲染
  （深度 → 阴影 → 场景 → 合成）。`F` 雾开关 · `S` 光轴开关 · `A` 闭式⇄逐步矩形 ·
  `↑/↓` 密度 · `G` 相函数 · `PGUP/PGDN` 步数。**阴影走 `engine/csm` 的单级**，
  于是"光轴"= 每步问一次"这点被柱子挡住没" —— 两个模块被证明可以拼在一个片段里。
- **`tests/test_volumetrics.py`（12 项）**，其中 4 项是硬不变量 / 回归守卫：
  ① 闭式 vs 4096 段数值积分（5 种形状，相对差 < 1e-9）；
  ② **可加性**（拆 2/7/64 段之和 == 整段），并断言"逐步矩形"在同一拆分下**明显漂移**
     （≈ 4%，正是条纹与整体亮度漂移的来源）；
  ③ HG 的**球面积分 = 1**（数值 + 原函数 `2/(1−g²)` 两路验证）；
  ④ GLSL **只声明一个采样器且必须真采样**（本轮实踩，见下）+ **阴影因子必须取反**。
- `scripts/run_all_tests.py` 注册第 10 个模块；README / examples/README 计数 33 → 34。

### 修复（本轮踩到四个）

| # | 症状 | 根因 |
|---|------|------|
| 18 | 画面**上下各出现一份一模一样的内容**（HUD 正常） | 全屏大三角的 `uv` 我直接当 NDC 用了（应为 `ndc*0.5+0.5`）⇒ 屏幕上的 uv 范围是 [−1,1]，采样 `u_scene` 时四周平铺/镜像。很容易误判成"场景画了两遍" |
| 19 | 光轴**完全看不见**，画面只是"雾很平" | `csm_shadow` 的约定是 **1 = 在阴影里**，我在雾里当成"照度"直接用 ⇒ 光轴画反（被挡住的空气反而发亮）。实测：光轴开关切换只差 **1.5/255**，符号修好后是 **9.9/255** |
| 20 | `prog['u_csm_split'] = [0.5]` 抛 `TypeError: must be real number` | **单元素**数组 uniform 在 pyglet 里退化成标量（GL 报 size=1 ⇒ 走标量 setter）—— 与 `mat4[]` 那条坑同源（pyglet 对"数组尺寸"的边界处理） |
| 21 | `ShaderException: A Uniform with the name 'u_fog_shadow' was not found` | 声明了一个"光轴委托给 `shadow_fn`"时**根本不会采样**的采样器 ⇒ 被驱动优化掉 ⇒ pyglet 赋值即崩。**改成根本不声明**（现在只声明 `u_fog_depth`，并有一个 `_scene_dist` 真的采样它） |

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_volumetrics` | **12/12** → `ALL_VOLUMETRICS_TESTS_PASSED`（含真驱动编译 + CSM 组合） |
| `scripts/run_all_tests.py` | **10/10 模块 / 237 项** / `ALL_TESTS_PASSED`（上一批 225 项） |
| `python examples/volumetric_fog.py --smoke 4` | `EXIT_OK` |
| `python examples/volumetric_fog.py --shot` | `[shot] OK 2 files` |
| 光轴贡献（像素均值，画面中央走廊） | **有光轴 88.31 / 只雾 78.45 / 无雾 77.11** |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（零回归） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (34 demos)` |

## [未发布] — ④-1：级联阴影（CSM）—— 把一张阴影图切成 N 级

「单张阴影图精度固定」这件事的正面解法：按视距分区，**第 0 级只罩最近一段**
（正交盒小 ⇒ 纹素细 ⇒ 近处锐），往后逐级变大，远处也还有影子。
单张要么近处糊成锯齿（半径给大），要么远处整片没阴影（半径给小）——CSM 两个都要。

### 新增

- **`engine/csm.py`**（纯数学 + 可注入 GLSL，全部可 headless 断言）：
  * `split_scheme`   —— PSSM 线性/对数混合分割（λ=0 等分、λ=1 等比，端点精确）；
  * `cascade_corners`—— 一段视锥的 8 个世界角点（不依赖 GL，只用视图矩阵求逆）；
  * `light_ortho_box`—— 8 角点 → **紧贴**它们的正交光盒（正方盒 + 深度向两侧撑 `z_extra`）；
  * `texel_snap`     —— 把光空间平移吸附到**纹素栅格**（消抖动；这是最易漏的一步，静帧看不出来）；
  * `select_cascade` / `csm_matrices` / `csm_uniforms` / `csm_glsl`。
- **`examples/csm_shadow.py`**（第 33 个示例）：柱林 + 程序化棋盘地面；`C` 切 1~4 级、
  `V` **按级染色**（青/紫/橙/绿 —— 级联边界一眼可见）、`P` PCF 半径、`S` 阴影开关；
  左上是某一级的深度图小窗（render-to-texture 自证）。剧照默认开染色（静帧不染色看不出机制）。
- **`tests/test_csm.py`（20 项）**，其中 3 项是硬不变量 / 回归守卫：
  ① 8 个视锥角点必须正好落在 NDC 立方体的角上（fov/near/far/朝 -z 任一处写错都会炸）；
  ② 纹素吸附后世界原点必须**正好**落在纹素栅格上（残差 0）且位移 < 1 纹素；
  ③ 生成的 GLSL **不许出现非常量下标索引 `sampler2D` 数组**（GL 3.3 不支持）。
- `scripts/run_all_tests.py` 注册第 9 个模块（`tests.test_csm`）。

### 修复（本轮踩到的四个，全是"画面看起来正常、其实机制没生效"）

| # | 症状 | 根因 |
|---|------|------|
| 14 | 改机位**完全没反应**；地面整片消失 | 从未设置 `window.view` —— 平台层只在 resize 时设 `projection`，`view` 一直是**单位矩阵** ⇒ 相机形同不存在，而地面正好落在相机高度上被看成零厚度 |
| 15 | 完全没有阴影，其余一切正常 | `glActiveTexture(GL_TEXTURE0+i); tex.bind()` —— pyglet 的 `Texture.bind()` **默认绑到 0 号单元**，于是 N 张阴影图全挤在 0 号上，1~3 号采到未绑定单元。正解：`tex.bind(i)` |
| 16 | `prog['u_csm_vp'] = [64 个 float]` 直接 AssertionError | pyglet 2.1.16 对 `uniform mat4 u_vp[4]` 的 `_c_array` 只按 **4** 个 float 分配（把 GL 报的"数组长度 4"当成"分量数"）。改成**每级一个独立 `mat4` uniform**（选级本来就是展开的，正好一致） |
| 17 | 深度图小窗一片黑，以为 render-to-texture 没跑 | 小窗显示的是**第 0 级**，而相机离场景 195m ⇒ 0~60m 那段本就是空空间，那张图**本来就该全远（全黑）**。改显示中间那一级 |

> 另有两处 GLSL 层面的硬约束写进了代码注释（不是文档）：
> **`uniform block` 必须在每个用到它的着色器阶段各声明一遍**（只在 VS 声明、FS 里用会
> `ERROR: 'window' : undeclared identifier`）；
> 而**选级必须是 if/else 展开链**（GL 3.3 不允许动态下标索引 `sampler2D` 数组）。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_csm` | **20/20** → `ALL_CSM_TESTS_PASSED`（含真驱动编译 + uniform 上传） |
| `scripts/run_all_tests.py` | **9/9 模块 / 225 项** / `ALL_TESTS_PASSED`（上一批 205 项） |
| `python examples/csm_shadow.py --smoke 3` | `EXIT_OK` |
| `python examples/csm_shadow.py --shot` | `[shot] OK 2 files`（两张分别展示不同光向 + 级联染色边界） |
| 级联实测数据 | 3 级 → 半径 54.6 / 140.4 / 593.7，纹素 **0.022 / 0.130 / 0.548 世界单位**（近处比最远级细 25 倍） |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（零回归） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (33 demos)` |

## [未发布] — 《砖境 / Brickward》B4：成片（已录、**未挂**）

BQM 致敬线的收尾。等距视角与程序化 SFX 其实在 B3 就顺手做完了，B4 只剩**录制**
与**攒批**两件事 —— 而"攒批"是硬约束：Gitee 的 release 附件**只能追加、不能单独替换**，
所以要替换 `boxward_60s.mp4`（视角改造后旧片已过时）就必然要删整个 release 重传 362MB。

### 新增

- **`recordings/brickward_60s.mp4`**（60.00s / 1800 帧 / 1280×720 / 0.7MB / h264@30fps）：
  求解器 auto 连闯 7 关（直推教学 → 钥匙金门 → 拉杆切门 → 炸药开路 → 火球烧箱 →
  滚石垫路 → 引怪入瓮）。
- **`record_all.py` 新条目 `brickward_60s`**，`flags='auto,pace=0.45'`；
  demo 新增 `--flag pace=<秒>` 调 auto 回放步长（默认 0.30s 是给剧照/验收用的，
  录成片要放慢到能看清机关）。
- `docs/分发指南.md` §3.0 的**待重建清单**更新：`boxward_60s.mp4`（替换）+ 
  `brickward_60s.mp4`（新增）—— 后者本可直接追加，凑一趟是因为前者**必须重建**。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `scripts/record_all.py --only brickward` | `[ok] brickward_60s: 1800 帧 / 60s / 1280x720 / 0.7MB` → `ALL_RECORDED (1 files)` |
| `ffmpeg -i` | `Duration: 00:01:00.00` / `1280x720` / `h264 yuv420p 30fps`（**无 ffprobe，用 ffmpeg 本身读**） |
| `ffmpeg -f null -` 全片解码 | 零错误（不是"能读头但中间是坏帧"） |
| 抽帧 4 张（t=4/20/40/55s） | md5 两两不同；肉眼核对：t=20s 是「拉杆切门」、t=40s 是「滚石垫路」（状态行与棋盘一致） |
| `python examples/brickward.py --smoke 2 --flag auto,pace=0.45` | `EXIT_OK` |

> **未做**：挂到 Gitee release。按 §3.0 攒批铁律，等发布节点与 `boxward_60s.mp4`
> 的替换一起处理。`recordings/` 在 `.gitignore` 里，成片不入库。

## [未发布] — 《砖境 / Brickward》B3：谜题编辑器 + 可玩 demo

B1/B2 把内核立住了（11 类元素、连接绑定、BFS 求解器、13/13 可解），但**只有 headless
断言，没人能真的玩**。B3 补两件：**关卡 IO**（编辑器/CLI/内核同一个 schema）与
**可玩 + 可编辑的 demo**。至此这条线才算"有产品形态"。

### 新增

- **关卡 IO（`games/brickward/kernel.py`）**：`level_to_json` / `level_from_json` /
  `LEVEL_JSON_KEYS`。schema 就是内核吃的那个 dict（`name/h/w/rows/links/par`）。
  * 导出侧拒绝**非矩形** `rows` —— `parse_level` 会用 `.` 补齐，那是"静默改关卡"；
  * 导入侧把坐标**列表还原成元组**，并且导入即校验（不合法抛 `ValueError`）。
- **编辑器纯函数（同文件）**：`level_set_cell` / `level_add_link` /
  `level_clear_links` / `level_blank` / `prune_links`。
  **规则全在内核**、全部可 headless 测 —— demo 只负责把 `ValueError` 变成屏幕提示。
- **`examples/brickward.py`**（交互 demo，32 个示例之一）：
  * 渲染：墙走 `world3d.engine.greedy_mesh.layer_mesh` 一次建网（与 boxward 同一套地基），
    其余元素用共享小网格（cube / sphere / quad / 自建 pyramid）逐个 batch；
    **正交等距视角**（偏航 +45°，与 boxward 同一套看的），`Q/E` 切 90° 档、`P` 循环俯角；
  * 游玩：WASD/方向键走·推·点火，`Z` 重开、`N` 下一关、`R` 种子关；`--flag auto`
    = **求解器回放**（与 `config_boot brickward` 的 headless 验收同源）；
  * **编辑器**（`M`）：左键摆当前道具、右键擦成地板、`[`/`]`/滚轮换道具（15 格道具栏，
    HUD 高亮当前项 + 光标处道具预览）、`C` **连接模式**（先点开关、再点门）、
    `T` 换连接模式（open/toggle/close）、`SHIFT+C` 清空连接、`V` 校验+试解、
    `E` 导出 JSON 到 `levels/`、`I` 载入 `levels/*.json`、`B` 新建空白关；
  * 表现：程序化 SFX（走/推/点火/滚动/开锁/爆炸/失败/胜利）+ 通关/失败叠层 +
    **引信越短闪得越快**的炸弹 + 本步爆炸火光 + 编辑模式的连接虚线示意。
- **配置树**：`cordis.brickward.yml` 增加 `render`（demo 要用 `ctx.render` 记账），
  `WANT_BRICKWARD = ('brickward', 'render')`；`config_boot` 对 brickward 传
  `require_gl=False`（内核从不取 GL 句柄，否则那套"等 `live_count() > 0`"永远超时）。
- **9 项新单测**（`tests/test_brickward.py` 56 → **65 项**），其中 2 项是回归守卫。
- 剧照 `docs/brickward_a.png` / `b.png`（auto 回放的两个不同关卡中段）；
  `README.md` / `examples/README.md` 同步（31 → 32 个示例）。

### 修复 / 决策

| # | 内容 | 说明 |
|---|------|------|
| 11 | **导入必须把坐标列表还原成元组** | `validate_level` 用集合成员判定（`lk['from'] in (buttons \| levers)`），而 JSON 读回来的 `[1, 4] != (1, 4)` ⇒ 不还原的话**每一关都被判"连接源不是按钮/拉杆"**，带连接的关卡一个都读不进来。守护：`test_level_from_json_restores_tuples_regression` |
| 12 | **摆放不能做完整性校验** | 第一版 `level_set_cell` 末尾直接 `validate_level` ⇒ 空白板上摆第一格时"必然既没有 P 也没有 E" ⇒ **一步都摆不下去**，编辑器等于废掉。改成"摆放只拦结构性错误（越界/边框锁死/未知元素/唯一性），完整性是试解与导出的门槛"。守护：`test_editor_blank_board_is_buildable_step_by_step` |
| 13 | **剧照两张逐像素一样** | `--shot` 靠 auto 回放取两个时刻，而 auto 的"计划"只在换关路径里算 —— **不给任何 flag 时一次换关都不会发生**，计划恒为空、一步不动。这不是剧照机制坏了，是计划没算。守护：**两张剧照的 md5 必须不同**（渲染本身有像素级噪声，所以不引具体 hash，只引"两张不同"这个事实） |

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_brickward` | **65/65** → `ALL_BRICKWARD_TESTS_PASSED` |
| `scripts/run_all_tests.py` | 8/8 模块 / **205 项** / `ALL_TESTS_PASSED`（上一批 196 项） |
| `python -m world3d.cli.config_boot brickward` | 7 手工关 + 6 种子 = **13/13** 可解且回放到胜 → `BRICKWARD_OK` |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（零回归） |
| `python examples/brickward.py --smoke 3` | `EXIT_OK` |
| `python examples/brickward.py --shot` | `[shot] OK 2 files`（**两张 md5 不同** —— 同一张图重复输出会被这条抓住） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (32 demos)` |

## [未发布] — 《砖境 / Brickward》B2b：滚石与史莱姆

机关内核的**第三个扩展包**，也是 B2 的最后一块。B2a 加的是"会自己炸"的元件，
B2b 加的是"**会自己滚 / 会自己走**"的元件 —— 它们把"世界的一步"真正变成一个
**有自己行为的东西**，而不只是玩家动作的副作用。

### 新增

- **滚石 `r`**：推动 = 朝该方向**一直滚到底**（不可停）。沿途**压死史莱姆、压灭火球、
  把尖刺压失效**，且**都不减速**。被墙 / 箱子 / 炸弹 / 另一块滚石 / 关着的门挡住，
  开着的门不挡。
  > **滚石压不到玩家 —— 这条不需要判定，是可以证明的**：它只朝"推它的人的反方向"滚，
  > 推的人一定在后方，而一次滚动是一条不走回头路的直线 ⇒ 玩家不可能出现在路径前方。
- **史莱姆 `s`**：每 `SLIME_PERIOD = 2` 步行动一次，朝玩家**贪心**挪一格（先走偏离大的
  那条轴，被挡就换轴，都挡则不动）。纯贪心、无随机 ⇒ 整局依旧确定。
  * 玩家走进它的格 → 玩家死（它不是"挡路"，是"送死"）；
  * 它走进玩家的格 → 玩家死；
  * 它自己踩尖刺 → 死（**尖刺仍在**，不是同归于尽）；
  * 它踩到炸弹 → 炸弹引爆、同归于尽（"用炸弹引怪"是正经解法）；
  * 被爆炸 / 火球 / 滚石 → 死。
- **世界时钟 `tick`**：指纹里放 **`tick % SLIME_PERIOD` 相位**而不是绝对值。
- **两个手工关**：
  - 「**滚石垫路**」：滚石挡在唯一通路上，推一下它一路滚过去，**顺手把整条尖刺走廊
    压失效**（5 个），玩家随后走过去才安全 —— 12 步；
  - 「**引怪入瓮**」：史莱姆堵住唯一下行口（1 格宽走廊里"走过去"和"等它过来"都是送死），
    只能推滚石把它压死 —— 13 步。
- **14 项新单测**（`tests/test_brickward.py` 42 → **56 项**）。

### 修复 / 决策

| # | 内容 | 说明 |
|---|------|------|
| 9 | `tick` **只进相位、不进绝对值** | 史莱姆的行动只取决于"这一步该不该动"。放绝对值 = 把同一局面拆成 `PERIOD` 份白烧内存；完全不放 = 行动时刻算错。指纹里是 `tick % SLIME_PERIOD` |
| 10 | **机制在册，但随包关卡从没触发过** | 第一版「引怪入瓮」把滚石放在玩家手边，第 2 步就把史莱姆压死了 ⇒ **史莱姆 AI 一次都没跑过**。关卡能通关、单测全绿，机制却等于没上线。改成"滚石离玩家 2 格、史莱姆堵住唯一下行口"，玩家走 3 步才够得着石头，这段时间史莱姆已经挪了一格 |

> 第 10 条是这一批最值得记的**验收**教训：**"能通关" ≠ "机制被驱动过"**。
> 前者求解器一跑就知道，后者得逐元素点名检查。
> 同类的机械门禁是 `test_elements_table_covers_art`（表里每个元素都必须在某关出现过），
> B2b 让它多了一层"要真的动起来"。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_brickward` | **56/56** → `ALL_BRICKWARD_TESTS_PASSED` |
| `scripts/run_all_tests.py` | 8/8 模块 / **196 项** / `ALL_TESTS_PASSED`（上一批 182 项） |
| `python -m world3d.cli.config_boot brickward` | 7 手工关 + 6 种子 = **13/13** 可解且回放到胜 → `BRICKWARD_OK` |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（零回归） |

## [未发布] — 《砖境 / Brickward》B2a：炸弹与火球

机关内核的**第二个扩展包**。B1 已经把确定性模拟 + 连接绑定 + BFS 求解器立住，
B2a 加的是"**会自己动、会自己炸**"的元件 —— 它们把"一步"从"玩家的一步"
扩展成"玩家的一步 + 世界的一步"，也是最容易写出静默 bug 的一类。

### 新增

- **炸弹 `b`**（`games/brickward/kernel.py`）：
  - 可推；**推动即点引信**（`FUSE = 2`），世界推进里每步减 1，归零在该格爆炸；
    推着它连续走是合法技巧（每推一次重置引信）；
  - 爆炸形状 `BLAST_SHAPE` = **十字 1 格**（自身 + 上下左右），不是 3×3；
  - 爆炸**摧毁**：箱子、炸弹（**连锁**，队列展开）、火球（扑灭）；
    **不影响**：墙、门、金门、尖刺、钥匙、按钮、拉杆、出口；玩家在波及格内 → 死；
  - `blasts` 记录**本步**的波及格（含不可摧毁物）供渲染画火光，属瞬态，不进状态指纹。
- **火球 `f`**：
  - 可推；**推动即点燃**并朝被推方向滚，此后每步滚一格（本步先不滚，
    否则会"追着玩家走"）；
  - 撞墙 / 关着的门 / 未开金门 → **熄灭**；撞炸弹 → 引爆它并熄灭；
    撞箱子 → 烧毁箱子并熄灭；撞另一个火球 → 双双熄灭；撞玩家 → 玩家死；
  - **未被推过的火球静止不动**（方向为 `None`）。
- **两个手工教学关**：
  - 「**炸药开路**」：出口区的 1 格宽竖井被箱子堵死、箱子上面就是出口（推上去 = 自己堵死出口），
    **必须**用炸弹炸掉它 —— 9 步；
  - 「**火球烧箱**」：推火球一下就开始滚，烧掉挡路的箱子后熄灭 —— 10 步。
- **12 项新单测**（`tests/test_brickward.py` 30 → **42 项**），其中 2 项是回归守卫。

### 修复

| # | 症状 | 根因 |
|---|------|------|
| 7 | 炸弹搬走后**原格凭空爆炸**，把已走开的玩家炸死，整关被 BFS 判"不可解" | 引信写在 `fuses[格子]` 上，`_push` 只往新格写、忘 `pop` 旧格 —— **引信挂错了主体**（它是炸弹的属性，不是格子属性） |
| 8 | 静止火球一装载就"滚" → `KeyError: None`，整个求解器炸掉 | `_advance_world` 对所有火球都调 `_roll_fireball`，而"没被推过"的火球方向是 `None`，`DIRS[None]` 直接炸 |

顺带在重构里堵掉一处**同源缺陷**：`_flush_switches`（触发集对账）一步里要跑两次
（玩家动作后 + 世界推进后），而拉杆翻转原先嵌在"结算"里 ⇒ 站在拉杆上会被翻两次
（净效果为 0）、撞墙不动更会反复翻。现在拉杆翻转独立成 `_step_on_lever()`，
**只在真的移动了的那一步**调用，`_flush_switches` 因此可以安全地跑任意多次。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_brickward` | **42/42** → `ALL_BRICKWARD_TESTS_PASSED` |
| `scripts/run_all_tests.py` | 8/8 模块 / **182 项** / `ALL_TESTS_PASSED`（上一批 170 项） |
| `python -m world3d.cli.config_boot brickward` | 5 手工关 + 6 种子 = **11/11** 可解且回放到胜 → `BRICKWARD_OK` |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（零回归） |

> 数值是**实测定**的：`FUSE=2` + 十字 1 格。1 步等于自杀，3 步会让"1 格宽走廊"
> 失去设计空间，3×3 在 1 格宽走廊里必然连推炸弹的玩家一起炸。
> 第一版「炸药开路」把炸弹放在侧边走廊，要"右推两下再绕到下方上推"，中途必然多花 2 步、
> 引信正好烧完、玩家被炸死 —— BFS 直接判"不可解"。**数值与关卡互相约束，
> 必须先定数值 → 再画关 → 用求解器证明可解。**

## [未发布] — 《砖境 / Brickward》B1：机关内核 + 连接绑定 + 求解器

BQM（《砖块迷宫建造者》）**机制致敬**的第二阶段落地。合规边界见
`docs/BQM机制致敬_复刻可行性.md` §2 —— 名称/美术/音效/关卡/联网经济一律自创或不做，
只吸收玩法机制（方块地牢 + 一格一步 + 机关组合 + 编辑器）。

### 新增

- **`games/brickward/kernel.py`**（纯逻辑，`inject = []`，零 GL、零外部依赖）：
  - **元素表 11 类**：`#` 墙 / `.` 地板 / `P` 玩家 / `C` 箱子 / `o` 按钮 / `L` 拉杆 /
    `D` 门 / `g` 金门 / `k` 钥匙 / `^` 尖刺 / `E` 出口；`ELEMENTS` 表带
    `block` / `pushable` 标志，供编辑器（B3）与渲染（B4）共用；
  - **一格一步的确定性模拟**：同关卡 + 同输入 ⇒ 同结果（可回放、可断言）；
  - **「连接」绑定**（BQM 编辑器的核心机制）：用 P0-5 自研的
    `world3d.engine.orchestration.Bus` 做级联，`mode = open | close | toggle`，
    事件 `src_on` / `src_off` / `src_flip`；
  - **BFS 最短解求解器**：状态指纹去重 + `DIR_ORDER` 固定 ⇒ **同关同解**
    （方向串可直接当断言用）；`make_offline(level)` 是 headless 求解/试解的唯一入口；
  - **`validate_level`**：装载前拒绝缺 `P` / 缺 `E` / 坏连接源 / 坏连接目标 /
    连到金门 / 未知模式 —— 全部给**明确原因**（编辑器要用）；
  - **3 个手工教学关**（直推教学 / 钥匙金门 / 拉杆切门）+ **`generate(seed)`**
    种子生成器（模板 + 换手 + 撒尖刺，**由求解器把关可解**，同种子 byte 级一致）；
  - **越界即挡**：`step` 不再假设"画图的人写了边框"（此前会走出地图）。
- **`configs/cordis.brickward.yml`** + `host/registry.py` 注册短名 `brickward`
  + `host/tree.py` 的 `WANT_BRICKWARD`（**不列 `render`** —— B1 压根不用 GL）。
- **`cli/config_boot.py`** 新增 `run_brickward`：手工关 + 6 个种子全部
  「求解器解出 → **回放到胜** → 再解一次逐字一致」；`--seeds` 对
  `brickward` 生效。
- **`tests/test_brickward.py`**（**30 项**，零 GL）：元素表完备性、校验器正反例、
  墙/边界、门与拉杆、三种连接模式真值表、金门与钥匙、箱子/按钮/尖刺、
  状态指纹完备性、克隆独立性、求解器最短性/确定性/不可解、生成器、服务面与快照。

### 修复（都是"静默失效"，不报错、测试还是绿的）

| # | 症状 | 根因 |
|---|------|------|
| 1 | 三关全报"可解"，回放却全是**穿模** | `doors` 一个集合既表示"哪里有门"又表示"哪扇门开着" ⇒ 关着的门能穿、开着的门挡人 |
| 2 | 拉杆关是纯装饰 | `_settle` 查 `self.levers`（本意是"on 的集合"，装载后恒为空）⇒ 拉杆从不翻转 |
| 3 | BFS 解全是错的 | `clone_from` 里带 `_settle` ⇒ "人正站在拉杆上"的节点被克隆时把拉杆**扳回去** |
| 4 | 拉杆关"能用"纯属巧合 | `_bind_links` 三种事件全绑，flip 处理函数**写死 `mode='toggle'`**、无视连接声明的 mode ⇒ `open` 连接每步"先开再翻回"、靠回调顺序抵销 |
| 5 | `mode='close'` 的门永远堵死 | `src_on`/`src_off` 只在**变化**时发，"装载时本来就没触发"不算变化 ⇒ 等不到第一发 `src_off`（新增 `_resync_links` 装载后算到位） |
| 6 | 求解器回溯 `KeyError` | 终态从不入队，父指针表里没有它的条目 ⇒ 发现胜利时**先补写父指针**再回溯 |

现在门/金门各有两套数据（`*_cells` = "哪里有"、`doors`/`gold_open` = "此刻是否开着"），
`_bind_links` **按 mode 只订阅一类事件**；`_settle` 三类事件一次算清并**排序**后遍历
（集合迭代序不进玩法语义）。这六条已全部写成回归守卫。

### 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_brickward` | **30/30** → `ALL_BRICKWARD_TESTS_PASSED` |
| `scripts/run_all_tests.py` | 8/8 模块 / **170 项** / `ALL_TESTS_PASSED`（原 140 项 +30） |
| `python -m world3d.cli.config_boot brickward` | 3 手工关 + 6 种子 = 9/9 可解且回放到胜 → `BRICKWARD_OK` |
| `python -m world3d.cli.config_boot all` | **10 个** OK 标记（新增 brickward） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK`（零回归） |

## [未发布] — 引擎化改造 P2-3：`demos/` → `examples/`

引擎项目里那些单文件示例与仓库工具是**示例**，不是产品本体；目录名改回它该有的语义。
`git mv` 保历史，全仓引用**一次改完（68 个文件）**：

- 30 个示例自身的用法行（`python examples/<名>.py`）；
- `scripts/verify_demos.py`（`DEMOS_DIR` → `EXAMPLES_DIR`）、
  **`scripts/record_all.py` 的 22 条 ENTRIES 路径**、`scripts/build_exe.py` 的 13 条打包条目
  （这两处最脆：录制/打包按路径 import 模块，改错就直接跑不起来）；
- `pyproject.toml` 的 `exclude = ["examples*"]`；9 棵配置树的注释；
  `core` / `engine` / `host` / `cli` 里的文档串；活文档与 `examples/README.md`。

> **迁移映射规则（长期有效）**：**2026-10-14 起 `demos/` ⇒ `examples/`**。
> 有日期的 design note 与 CHANGELOG 历史条目里的 `demos/` 是当时的记录，**不追改**。

### 验收（实测，未设 `CORDIS_SRC`）

| 门禁 | 结果 |
|------|------|
| 示例直跑 | `python examples/boids.py --smoke 1` → `EXIT_OK` |
| **录制脚本**（改名后最脆的一环） | `record_all.py --only boids --dur-override 1` → `ALL_RECORDED`；随后恢复完整 45s 成片 |
| `scripts/run_all_tests.py` | 7/7 模块 / **140 项** / `ALL_TESTS_PASSED` |
| `python main.py --shot` / `kkrieger_demo.py --shot` | `EXIT_OK` |
| `python -m world3d.cli.config_boot all` | 9 个 OK 标记 |
| `scripts/verify_demos.py` 全量 | **ALL_DEMOS_OK (31 examples)** |

> 顺带一个操作教训：**全仓文本替换会把"描述迁移本身"的句子也改掉**
> （`demos/ → examples/` 被改成 `examples/ → examples/`）—— 这类**映射句必须人工复核**。

## [未发布] — 引擎化改造 P2-1/P2-2：分层归位

依据 `docs/5_引擎化改造规划.md` §6-P2。P1 让仓库"能装"，P2 让**分层真的成立**。

### P2-1 平台层抽出（`examples/_harness.py` → `world3d/platform/app.py`）

`_harness.py` 是与任何具体 demo 都无关的**平台层**（32 个 demo/脚本靠它开窗口、解析命令行、
抓帧、HUD、主循环），却寄生在 `examples/` 里。现在正名为 `world3d/platform/app.py`：

- 全部 20 个 demo + 2 个脚本（`verify_demos` / `record_all`）的导入统一为
  `from world3d.platform.app import ...`；顺手修掉 `examples/scene_viewer.py` 里
  `from demos._harness import ...` 这个**全仓唯一的不一致形态**（它会让同一个文件被当成两个
  模块导入、`DemoApp` 变成两个类对象，`issubclass` 判定失败）；
- `world3d/platform/app.py` 收口了 `__all__`（兼容层靠它转发，所以必须显式声明）；
- **旧路径 `examples/_harness.py` 保留为薄兼容层**（`from world3d.platform.app import *`），
  实测旧用法仍可用且是同一个类对象。

#### ⚠️ 硬约束：顶层包不能叫 `platform`

一开始按规划写成顶层 `platform/`，**立刻炸**：它**遮蔽标准库的 `platform` 模块**
（pyglet 自己就 `import platform` 取 `platform.architecture()`），单测与 `main.py` 同时报
`AttributeError: module 'platform' has no attribute 'architecture'`。
而规划 §3.2 的目标结构本来就是 `world3d/platform/` —— 于是直接落到**最终位置**，
一举免掉 P2-4 的二次搬迁。**同类命名约束**：导入包名不能以数字开头（P1 的 `world3d`）。

### P2-2 `core/` 越界清出

| 旧（越界） | 新（归位） | 理由 |
|-----------|-----------|------|
| `core/renderer.py`（`WorldRenderer`） | `presentation/world_renderer.py` | 是 `main.py` 48s 演出的渲染器 |
| `core/kkrieger_scene.py`（`KKRenderer`） | `presentation/kkrieger_scene.py` | 同理，kkrieger 漫游的表现层渲染器 |
| `core/nordic_scene.py`（`build_nordic_scene`） | `logic/nordic_scene.py` | 是场景**内容/布局**，被 `logic/nordic_saga.py` 与 demo 消费 |

同步 7 个消费方 + 3 处测试导入 + 文档对齐说明；`docs/架构设计_3D动画方向.md` 那张
2026-10-08 的分层快照加了失效注（指向当前权威文档）。
`core/geometry.py` / `core/resources.py` 是否随之迁走**留作后续评估**。

### 两个已踩的迁移坑（写进规划 §6 检查表）

1. **引用形态三种**都要 grep：`from world3d.core.<mod> import X` / **`from core import <mod> as y`** /
   `import world3d.core.<mod>` —— P2-2 漏了第二种，被 `test_nordic_scene_geometry` 抓出来；
2. **路径层级推导要同步改**：`world3d/platform/app.py` 找仓库根要 `dirname` 三次
   （`examples/_harness.py` 是两次）。"少一层就炸"这类问题在 P0-3（内核 `_ROOT`）也踩过一次。

### 验收（实测，未设 `CORDIS_SRC`）

| 门禁 | 结果 |
|------|------|
| 兼容层自检 | `examples/_harness.DemoApp is world3d.platform.app.DemoApp` → **True**；`__all__` 全部转发到位 |
| `scripts/run_all_tests.py` | 7/7 模块 / **140 项** / `ALL_TESTS_PASSED` |
| `python main.py --shot` / `python kkrieger_demo.py --shot` | `EXIT_OK`（4 张 / 9 张） |
| `scripts/verify_demos.py`（31 个全量冒烟 + 截图） | 见提交信息（本批的最强门禁：所有 demo 都改了导入） |
| `python -m world3d.cli.config_boot all` | 9 个 OK 标记 |

## [未发布] — 引擎化改造 P1：库化（可安装 + 公共 API 面 + CLI 入口）

依据 `docs/5_引擎化改造规划.md` §6-P1。P0 把试点全量转正后，P1 让仓库**能像库一样被安装使用**。

### 新增

- **`pyproject.toml`**（PEP 621，单一事实来源）：元数据 / 依赖 / extras（`dev`、`pyglet1x`）/
  库级入口点 / 打包范围 / ruff 配置。**库版本用 SemVer，从 `0.1.0` 起步**（发行版仍用 CalVer）。
- **`world3d/` 薄门面包**：把引擎公共面从 `engine` 转发出来（公共面由 `world3d.engine.__all__` 收口，
  不维护第二份清单）；含 `py.typed`。**发行名 `3dworld` / 导入名 `world3d`** —— 导入包名不能以
  数字开头（`import 3dworld` 是语法错、`'3dworld'.isidentifier()` 为 False），PEP 508 发行名允许。
- **库级 CLI 入口**：`world3d-info`（环境自检：版本/依赖/GL/cordis/配置树，任何安装方式可用）、
  `world3d-config`（配置树 headless 验收）、`world3d-pipeline`（场景校验流水线）。
- `world3d/_info.py`：自检实现（全部带兜底，缺 GL / 缺可选件都不炸）。

### 变更

- **依赖收口为一份**：`requirements.txt` 改为**转发**（内容就是 `-e .`）—— 此前它与项目元数据是
  两份清单、必然漂移（上线前全检的 P0 发布阻断即"依赖没声明全"）。`requirements-pyglet1x.txt`
  作为 cocos2d 兼容路线备用保留。
- `host/tree.py`：配置树目录支持 **`WORLD3D_CONFIGS`** 环境变量覆盖（`configs/` 是仓库资产、
  不随 wheel 分发，非 editable 安装下需要逃生口）。
- `engine/__init__.py`：公共面补 `unit_cube` / `unit_sphere`（与 `layer_mesh` / `merge_rects` 同族）。
- `README.md`：安装段重写（方式 A 项目 venv / 方式 B 当库装）+ 说明"库 vs 仓库资产"的边界。

### 修掉的两个打包缺陷（**在干净 venv 里装一遍才发现**）

1. **顶层模块没被打包**：`_cordis_locate.py` 是**模块不是包**，`packages.find` 只找包 →
   装上后 `world3d.host.tree` 报 `ModuleNotFoundError: No module named '_cordis_locate'`。
   **editable 安装看不出来**（源码本来就在 `sys.path` 上），只有非 editable 装才暴露。
   修复：`[tool.setuptools] py-modules = ["_cordis_locate"]`。
2. `world3d-info` 取依赖版本用 `__import__(n).__version__` → `watchdog 4.x` 没有该属性，
   直接 `AttributeError`。改为 `importlib.metadata.version('<发行名>')`。

### 验收（实测：另建一个干净 venv，用 `E:\Python314` 的完整 Python）

| 步骤 | 结果 |
|------|------|
| `pip install .`（非 editable） | `import world3d` → **0.1.0**，公共面 28 项；`engine/core/logic/presentation/host/cli/games` 全部可导入（旧路径零破坏） |
| `world3d-info` | `pyglet=2.1.16 PyYAML=6.0.3 watchdog=6.0.0`；`cordis` 解析到**装出来的副本**（vendored 打包成功）；GL `(4,6) Intel UHD 630`；着色器 19 / 服务表 23；配置树缺失时给出 `WORLD3D_CONFIGS` 提示 |
| `pip install -e .` | `cordis` 解析回仓库 `vendor/cordis/src` |
| `scripts/run_all_tests.py`（用该 venv 跑） | 7/7 模块 / **140 项** / `ALL_TESTS_PASSED` |
| `world3d-config all` | **9 个 OK 标记**（TD/GODHAND/FPS/BOXWARD/STORM/HALL/VOID/PULSE/CORES） |
| `world3d-pipeline` | `PIPELINE_OK` |

## [未发布] — 引擎化改造 P0：`experiments/` 全量转正

依据 `docs/5_引擎化改造规划.md`（全哥立项）。目标：把仓库从"演示合集"改造为
**3D 引擎项目**——本批（P0）先把 `experiments/` 里的试点（6516 行 / 45 个文件）
全部**转正或退役**，做到"**没有试点目录，只有正式层**"。

### ⚠️ 破坏性变更（Breaking）

| 旧 | 新 |
|----|----|
| `experiments/cordis/<x>_kernel.py` | **`games/<x>/kernel.py`**（8 个玩法内核） |
| `experiments/cordis/tree_boot.py` | **`host/tree.py`** |
| `experiments/cordis/config_boot.py` | **`cli/config_boot.py`**，调用方式改 `python -m world3d.cli.config_boot <kind>` |
| `experiments/cordis/cordis.*.yml` | **`configs/*.yml`**（9 棵） |
| `experiments/cordis/services/*.py`（24 个 5 行壳） | **删除** → `host/registry.py` 的服务短名表（yml 里写 `- name: economy`） |
| `experiments/cordis/scene_pipeline.py` | **`host/pipeline.py`**（`python -m host.pipeline`） |
| `experiments/fixed_timestep/compare.py` | **`tests/bench_fixedstep.py`**（从对照脚本变**带断言的回归基准**） |
| `experiments/cordis/levels/`（boxward 关卡编辑器导出目录） | 仓库顶层 **`levels/`**（已加 `.gitignore`） |

`import experiments.*` 一律失效；本仓库内已全部改完（`grep` 复查无活引用）。

### 新增

- **`engine/fsm.py`**：确定性有限状态机（时间窗 + 事件转移 + 进出钩子 + 轨迹）。
  自研替代 `experiments/allin_decision/`（原依赖外部仓库 allin_engine，其源码本机已离线）。
- **`engine/orchestration.py`**：事件总线 + 声明式 `when→then` 级联 + 环检测。
  自研替代 `experiments/zhuangtaiji_orchestration/`（同上，源库已离线）。
- **`engine/baked_mesh.py`**：`unit_cube` / `unit_sphere`（烘焙明暗三角汤）
  —— 原先住在塔防内核里被**六个内核横向 import**，上提后消除"游戏依赖游戏"。
- **`engine/greedy_mesh.py`**（P0-1）：单层体素贪心合批，从内核上提。
- **`host/registry.py`**：服务短名 → 实现类（23 条 = 13 共同核 + 10 内核）。
- **`docs/cordis集成.md`**：Cordis 集成的正式文档（前身是试点目录里的 README）。
- 测试：`tests/test_engine.py` +2 项（FSM / 事件总线）→ 全套 **140 项**。

### 试点去向（18 项，逐项交代）

| 试点 | 处置 | 依据 |
|------|------|------|
| 8 个玩法内核（4381 行） | **转正** → `games/<名>/kernel.py` | 早被 8 个 demo 正式依赖，"试点"身份名不副实 |
| 20 个服务壳 + 宿主 + 9 棵 yml | **转正** → `host/` + `configs/` + `cli/` | 见上表 |
| `greedy_mesh.py` | **转正** → `engine/greedy_mesh.py` | 零依赖纯函数，属引擎算法件 |
| `scene_pipeline.py` | **转正** → `host/pipeline.py` | Cordis 三服务流水线（checker→loader→reporter），`PIPELINE_OK` |
| `fixed_timestep/compare.py` | **转正** → `tests/bench_fixedstep.py` | 两组对照结论加断言，成回归基准 |
| `allin_decision/`（FSM 决策） | **能力自研**（`engine/fsm.py`），试点退役 | 依赖外部仓库；本机 G 盘已移除，不可复现 |
| `zhuangtaiji_orchestration/`（编排） | **能力自研**（`engine/orchestration.py`），试点退役 | 同上 |
| `bloodmusic_ost/remake_act6.py` | **退役**（能力已有） | 原生 OST 已在 `logic/kkrieger_audio.py`（第六幕） |
| `scene_walker/walk.py` | **退役**（被取代） | 正式版 `examples/scene_walker.py`（652 行）⊃ 试点（422 行），且多出热重载/限时挑战/结算 |
| `scene_walker/bgm.py`、`gen_sfx.py` | **退役**（被取代） | 已转正为 `engine/adaptive_music.py` 与 `scripts/gen_sfx.py` |

### 顺手修掉的两个真缺陷

1. **玩法内核之间的横向依赖**（见 `docs/引擎设计.md` §三.12）：共享几何上提 engine。
2. **配置树挂载失败的误导性报错**：cordis 的 Loader 会吞掉插件构造期异常，
   只表现为"服务不就绪"。已在 `host/tree.py` 的超时错误里加定位提示。

### 验收（全部实测，且刻意不设 `CORDIS_SRC`）

- `scripts/run_all_tests.py` → 7/7 模块 / **140 项** / `ALL_TESTS_PASSED`；
- `python -m world3d.cli.config_boot all` → **9 个 OK 标记**（TD/GODHAND/FPS/BOXWARD/STORM/HALL/VOID/PULSE/CORES）；
- `python -m host.pipeline` → `PIPELINE_OK`（含卸载回滚与 coeffect 断言）；
- `python -m tests.bench_fixedstep` → `BENCH_OK`（固定步长帧率无关 Δx=0；变步长 Δ=0.0333m；
  0.5s 卡帧下 变步长穿墙 / 固定步长拦住）；
- `scripts/verify_demos.py`（31 个 demo 全量冒烟 + 截图）→ `ALL_DEMOS_OK`。

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
