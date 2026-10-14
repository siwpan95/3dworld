# P3-b 帧图驱动渲染 设计说明（P3 收口）

> 轨道：**P3 帧图管线**。P3-a = 内核 + 编译期校验 + 门禁；**P3-b = 图驱动渲染（本批）**。
> 相关：`examples/frame_graph.py` · `engine/framegraph.py`（本批补了 `resolved`）·
> `tests/test_framegraph.py`（17 项）· `cli/frame_plan.py` · CHANGELOG「P3-b」段。

## 1. P3-a 留下的缺口

P3-a 的帧图能编译出 `plan.order` / `culled` / `aliases`，但**没有 consumer** ——
只有门禁与测试在读它。本批补上真正用它渲染的 demo，这也立刻暴露出一个**缺失的 API**：

**`plan.order` 不够用。** 渲染端还需要知道"每个 pass 执行时**实际**读哪张纹理"。
`disable('dof_pass')` 之后，`motionblur_pass` 读的已经不是 `hdr_dof` 而是 `hdr_fog` ——
但这个信息在编译期才算得出来。少了它，渲染器会去绑一张**已被剔除、没人写**的中间纹理 ⇒ 黑屏。

⇒ **`FramePlan.resolved` / `reads_of()` / `writes_of()`**。
一句话总结这条经验：**编译期算出来的信息，必须能交给执行端**；
否则"编译期校验"就只是自娱自乐。

## 2. "图驱动"到底落在哪三行

```python
# ① 目标：按资源声明创建（'screen' / (1024,1024) / r32f / rgba16f 都真的用到）
for name, res in self.plan.resources.items():
    if not res.external and res.kind == 'texture':
        self.targets[name] = self._make_target(name, res, w, h)

# ② 执行：遍历计划
for name in self.plan.order:
    getattr(self, '_pass_' + name)()

# ③ 输入：按 reads_of 在**每个 pass 内部**绑纹理
for unit, r in enumerate(self.plan.reads_of(pass_name)):
    bind_texture(unit, self.targets[r]['tex'])
```

第 ③ 行就是"关掉一个效果、下游自动改读直通资源"的落点 ——
**渲染端一行都不用改**，因为 `reads_of` 已经把重定向解析过了。
这不是省了几行代码，而是"顺序/依赖只在一个地方描述"。

## 3. 三种关法（同一个内核约束的三种表现）

| 关什么 | 怎么做 | 为什么 |
|--------|--------|--------|
| 纯生产者（`shadow_pass`，不读任何资源） | **建图时**不声明（重建图） | 运行期 `disable` 被内核拒绝：它没有上游可直通 ⇒ 硬关等于下游静默出错。**这条约束是对的** |
| 消费者（`fog/dof/motionblur`） | **运行期** `disable` | 输出直通到第一个输入，中间纹理被别名回收 —— 这才是"关掉效果的收益" |
| 整张图 | `standard_3d_plan(**kwargs)` | 门禁用得上（`--all-toggles` 跑 16 种组合） |

## 4. 本批两个坑（都是"报错位置离原因很远"）

| # | 症状 | 根因 |
|---|------|------|
| 31 | `--flag off=fog,dof,motionblur` **只有第一个生效** | 平台层的 `--flag` **按逗号切分**（`--flag a,b` ≡ `--flag a --flag b`）⇒ 到手是三个 token，而我只取最后一个 `off=`。现在两种写法都认（`off=fog` 与裸 `fog`），也支持重复写 |
| 32 | 同一个 flag 触发 `AttributeError: ... has no attribute 'targets'` | `setup()` 解析 flag 时就会调 `_free_extra_targets()`，而那时离屏目标还没建 ⇒ 异常**打断 setup 的循环**、后面的 flag 全被跳过。加上守卫即可 |

> 两个坑的共同点：**"看起来是渲染/资源的问题，实际是参数解析"**。
> 第 31 条尤其典型 —— 症状是"少关了一个效果"，看起来像帧图算错，
> 而真相在 CLI 那一层。⇒ **参数解析要有自己的判据**（现在 demo 的 `--flag` 四种写法都测过）。

## 5. 验收（实测）

| 门禁 | 结果 |
|------|------|
| `python -m tests.test_framegraph` | **17/17**（新增 `resolved` 随直通更新的守卫） |
| `python -m world3d.cli.frame_plan --all-toggles` | 16 种组合 → `== FRAME_PLAN_OK ==` |
| `python examples/frame_graph.py --smoke 5` | `EXIT_OK`，`重编译 → 8 pass，别名组 3` |
| `--flag off=fog,dof,motionblur` | 逐次重编译 **8 → 7 → 6 → 5** pass，剔除列表/别名组同步变化 |
| `--flag off=shadows` / `off=dof,off=fog` / 无 flag | 7 / 6 / 8 pass（四种写法都验过） |
| 剧照 | `docs/framegraph_01/02.png`（HUD 上直接读到 pass 列表与别名组数） |
| `scripts/run_all_tests.py` | **13/13 模块 / 275 项** |
| `python -m world3d.cli.config_boot all` | 10 个 OK 标记（零回归） |
| `scripts/verify_demos.py` 全量 | `ALL_DEMOS_OK (37 demos)` |

## 6. P3 收口总结与不足

从"注释里的顺序约定"到"编译期校验 + 门禁会红 + 渲染循环按计划走"，这条链每一步都有证据：

| 层 | 件 | 管什么 |
|----|----|--------|
| 编译期行为 | `tests/test_framegraph.py`（17 项） | 顺序 / 剔除 / 直通 / 别名 / 环与死链 |
| 组合门禁 | `cli/frame_plan.py --all-toggles` | **16 种效果组合**的顺序与合法性 |
| 执行端 | `examples/frame_graph.py` | 计划真的驱动了帧（HUD 可见） |

**已知不足（留待 P4 或后续）**：

1. **别名只算不用**：计划报出"哪些资源可共用内存"，但没做真实的纹理复用
   （要交换 FBO 附件或用 `glTextureView`）；
2. **单队列顺序执行**：没做 compute/graphics 并行、没有 barrier / 资源状态跟踪；
3. **没有异步与多帧重载**：`prev_view_proj` 是外部资源，逐帧由 demo 传入；
4. **没有 debug 层**：真实的帧图会给每个 pass 打时间戳、可视化中间资源
   （本 demo 只把计划打印到 HUD/终端）；
5. **`kind` 字段（graphics/compute/copy）目前只是元数据**，没有按类型分队列。
