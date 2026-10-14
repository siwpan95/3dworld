# P5-d：让 CI 真跑 —— 本机网络实测 / GitHub 镜像逐步操作 / 通了之后做什么

CI 定义已经就绪但**一次都没跑过**（本仓只有 Gitee remote，而 workflow 只有 GitHub 执行）。
本文给出：本机到 GitHub 的连通性实测、三条可行通路的对比、Gitee Push 镜像的逐步操作，
以及镜像通了之后该做的三件事。

> 背景：`.github/workflows/ci.yml` 有两个 job（`regression` windows + `regression-linux` ubuntu），
> 但**本仓 remote 只有 Gitee，而 workflow 只有 GitHub 会执行** —— 所以 CI 一次都没跑过。
> 本文把"为什么本机不能直推""可行通路怎么一步步配""配好之后怎么验收"写清楚。

---

## 1. 本机实测（不是推测）

| 目标 / 手段 | 结果 |
|------|------|
| `curl https://api.github.com` | **无响应**（`code=000`，1.4s 失败） |
| `curl https://github.com` | **无响应**（`code=000`，0.9s 失败） |
| `curl https://gitee.com` | 200（2.2s） |
| 本机代理端口 | 7890 / 7897 / 7891 / 10809 / 10808 / 1080 / 8080 / 2080 / 3128 **全部未监听**；无 `*_proxy` 环境变量 |
| Git 凭据 | `~/.git-credentials` 里**只有 gitee**，没有 github |

⇒ **本机既没有到 GitHub 的网络，也没有凭据。**`git remote add github …` 在这台机器上
是死路（`git push` 会挂在网络层，不是权限层）。

## 2. 三条通路，按"本机能否独立完成"排序

| # | 通路 | 谁来执行 | 本机网络要求 | 能否持续同步 |
|---|------|---------|-------------|-------------|
| A | **Gitee「仓库镜像管理」Push 方向** ✅ 已选 | Gitee 服务器 → GitHub | **不需要** | ✅ 每次提交 Gitee 后自动（最短间隔 5 分钟） |
| B | 本机配代理后直推 | 本机 | 需要可用代理 | ✅（但 CI 结果也只有配了代理才看得到） |
| C | GitHub 网页「Import a repository」 | GitHub 服务器 → Gitee | **不需要** | ❌ 一次性导入，之后不自动同步 |

> C 是"想马上看到 CI 跑起来"的最短路径：GitHub 那边 `New repository` → `Import a repository`
> → 填 `https://gitee.com/siwpan/3dworld.git` ⇒ 由 GitHub 从 Gitee 拉全量历史与标签。
> 但它**不持续同步**，之后每次改动都要再导一次，所以只适合"先看一眼 CI 能不能过"。

## 3. 通路 A 的逐步操作（Gitee Push 镜像）

### 3.1 前置：GitHub 侧（需要你的账号）

1. **建一个空仓库**：`New repository` → 名字建议 `3dworld` → **不要勾** Add a README /
   .gitignore / license。
   ⚠ 关键：仓库里只要有一个初始提交，镜像推送就可能撞上"非快进"而失败或需要强推。
2. **生成一个 PAT**：`Settings → Developer settings → Personal access tokens → Tokens (classic)`
   → 勾 **`repo`**（`Generate new token (classic)`）。
   - 若镜像报权限不足，再补勾 **`workflow`** —— GitHub 对"用令牌推送
     `.github/workflows/*` 文件"有额外要求，而本仓的 CI 定义就在那个目录里。
   - ⚠ 令牌**只填进 Gitee 的镜像表单**：不要写进仓库、不要贴到聊天里、不要提交到任何文件。

### 3.2 Gitee 侧

1. 进仓库 → **管理** → 左侧 **仓库镜像管理** → **添加镜像**。
   （若首次使用，会先弹窗要求**绑定 GitHub 帐号**，按提示授权即可。）
2. 表单四件事：
   - **镜像方向** → **Push**
   - **镜像仓库** → 选/填你刚建的 GitHub 仓库
   - **私人令牌** → 粘贴 3.1 的 PAT
   - 保存。
3. **触发同步**：向 Gitee 提交一次代码，或点「**手动更新镜像**」。
   ⚠ 镜像触发**最短间隔 5 分钟**（Gitee 官方说明），刚配完点一下没反应等一会儿再点。

> ⚠ **功能可用性**：Gitee 的仓库镜像管理在 2021 年上线时曾标注"限时开放（对 GVP 及推荐项目）"，
> 官方帮助文档现在仍在。**如果你的仓库页里找不到「仓库镜像管理」或提示无权限**，
> 说明该功能对你的仓库不可用 ⇒ 退回通路 B（代理）或 C（一次性导入）。

### 3.3 同步范围（决定你能在 GitHub 上看到什么）

镜像同步 **分支 / 标签 / 提交**。
**不同步**：Gitee 的 **release 附件**（成片、pack zip）—— 那是 Gitee 平台资产，不是 git 对象。
所以 GitHub 上会有：完整代码 + 标签（`v2026.10.14` 等）+ `.github/workflows/ci.yml`（CI 就此生效）；
**不会**有成片附件。附件仍在 Gitee release 上（`docs/分发指南.md` 那套流程不变）。

### 3.4 通了之后（我这边的下一步）

1. **先手动跑一次**：GitHub → `Actions` → 选 `CI` → `Run workflow`（本批已加 `workflow_dispatch`），
   两个 job 都会跑起来。重点是 `regression-linux` —— 它**从没在真 Linux runner 上验证过**。
2. 把 `regression-linux` 里那一步「无头 GL 探针（EGL + llvmpipe）」的
   `continue-on-error: true` **去掉**，并把 `config_boot all` 提升为阻塞步骤 ——
   前提是第一次真实输出确认 EGL 能建出上下文（`world3d-info` 的 renderer 应为 `llvmpipe`）。
3. 如果 CI 红，按失败信息修（`workflows-valid` 门禁已经在本机保证了"可解析 / 结构完整 /
   命令可达"，剩下的多半是平台差异，比如缺哪个 `.so`）。

> ⚠ **我读不到 GitHub 的 CI 结果**：本机连不上 GitHub（§1）。跑完之后请把
> `Actions` 页的结论（哪一步红、原始日志）贴回来，或者给我一个可用代理，我自己读。

## 4. 本机侧已经准备好的东西（不依赖上面任何一步）

| 已就绪 | 说明 |
|--------|------|
| `.github/workflows/ci.yml` | 两个 job：windows（发布口径）/ linux（无头 EGL + Mesa llvmpipe）；`workflow_dispatch` 手动入口 |
| 门禁 `workflows-valid` | 保证 workflow 可解析、结构完整（`on`/`runs-on`/`steps`/`uses` 钉版本）、**里面的命令都可达** —— 否则镜像过去只会得到一个"永远不绿的 CI"，而本机看不到任何提示 |
| 本机可跑的等价检查 | `verify.py`（16 条）+ `check_imports.py` + 单测 281 项 + `config_boot all` 10/10 + `frame_plan` 16/16 + `verify_demos` 37 demos —— CI 里跑的就是这些（减去需要真窗口的 `verify_demos`） |
