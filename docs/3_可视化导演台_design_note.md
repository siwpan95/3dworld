# ③ 可视化导演台（Director）design note

> 状态：**已落地** · 2026-10-12 · 依据「剩余内容盘点」B 线缺口「**可视化导演台**」
> 全哥决策（4 问全定）：① 载体 **新建 `demos/director.py`**；② 编辑对象 **相机 + 物件灯光**（通用化）；
> ③ **要打通录制**（`record_all.py --shot-json`）；④ 插值默认 **CUBICSPLINE**。

---

## 1. 现状与缺口（实测）

3dworld 已有**全套底层零件**，但缺"把它们串起来给人用"的那一层：

| 已有 | 位置 | 说明 |
|------|------|------|
| 关键帧系统 | `engine/timeline.py` | `Sampler`（LINEAR/CUBICSPLINE 插值）/ `Channel` / `Timeline` |
| 时间轴服务 | `core/timeline_core.py` | `make_sampler/channel/timeline/playback`（已服务化，进契约） |
| 时间轴 UI | `presentation/timeline_ui.py` | `TimelineUI`：播放/循环/**拖动 seek**/幕标记（**已直接复用**） |
| 场景序列化 | `engine/serialize.py` | `dumps/loads/save_scene/load_scene/SceneWatcher`（**场景**格式，非演出） |
| 场景查看器 | `demos/scene_viewer.py` | 加载场景 JSON + 热重载 |

❌ **缺口（本项已补）**：
1. **运镜（相机演出）无法可视化编辑** —— kkrieger/nordic 的运镜**硬编码在 Python 里**，
   改一个机位要改代码、重跑；
2. **演出无法导出/复用** —— 没有"演出 JSON"格式，录制脚本（`record_all.py`）只能跑死代码；
3. **没有"所见即所得"的编排界面** —— 时间轴 UI 有了，但没和"相机关键帧"打通。

---

## 2. 设计（按实现回填）

### 2.1 "演出 JSON"（`shot` 格式）—— 实际落地的样子

```json
{
  "version": 1,
  "name": "altar_dolly",
  "duration": 20.0,
  "scene": "docs/llm_scene_demo.json",     // 场景只存**相对仓库根**的路径引用，不内嵌
  "acts": [{"t": 0.0, "label": "开场"}, {"t": 7.5, "label": "升镜"}],
  "notes": "绕祭坛一整圈（闭环可无缝循环）；太阳随相机扫过，主体始终受光",
  "channels": [
    {"node": "$camera", "prop": "position", "interpolation": "CUBICSPLINE",
     "times": [0.0, 2.5, 5.0], "values": [[8.84,4.0,8.84], [0.0,4.66,12.5], [-8.84,4.5,8.84]]},
    {"node": "core_knot", "prop": "rotation", "interpolation": "CUBICSPLINE",
     "times": [0.0, 7.0], "values": [[1.0,0.0,0.0,0.0], [0.7071,0.0,0.7071,0.0]]},
    {"node": "$env", "prop": "sun_intensity", "interpolation": "CUBICSPLINE",
     "times": [0.0, 4.0], "values": [1.05, 1.45]}
  ]
}
```

相对原设计（`camera.channels` 嵌套）改为**扁平 `channels` + `node` 字段**：全哥选了
"相机 + 物件灯光"的**通用**编辑对象，扁平结构才能同时装下 `$camera` / `$env` / 任意场景节点。

* 三个**伪节点**：`$camera`（position / target / fov）、`$env`（sun_intensity / sun_dir /
  ibl_intensity / sky_intensity）、其余按名字对应场景图节点（translation / rotation / scale /
  tint / glow）；
* 关键帧只存**裸值**（float / (x,y,z) / (w,x,y,z)）；`CUBICSPLINE` 的 in/out 切线由
  `world3d.engine.timeline.make_sampler` **自动生成**（Catmull-Rom 有限差分）——手写演出 JSON
  不需要算切线；**两关键帧时自动退化为精确线性**（不会过冲）；
* `Sampler` / `Channel` / `Timeline` **零转换**复用（单一事实来源）——只为公开的
  `PROPS` 白名单补上了相机/灯光类属性，并新增 `make_sampler` / `auto_tangents` 两个纯函数。

### 2.2 新增模块（已交付）

| 模块 | 职责 |
|------|------|
| `engine/shot.py` | **演出模型**：`Shot`（关键帧增删改 / 采样 / JSON 往返）、`ShotPlayer`（按 t 写回相机·环境光·场景节点）、`CAMERA_NODE`/`ENV_NODE`/`CAMERA_PROPS`/`ENV_PROPS`/`prop_kind` |
| `demos/director.py` | **导演台界面**：3D 预览 + `TimelineUI` 时间轴 + **右侧关键帧检察器**（通道行 / 插值 / 键数 / 键刻度 / 播头线，可点行选中、点键 seek） |
| `engine/timeline.py`（扩展） | `make_sampler`（裸值 → Sampler，自动切线）/ `auto_tangents` / `Channel.PROPS` 补相机与灯光属性 |

**导演台操作**（`T` 循环切换编辑目标：`$camera` → `$env` → 场景对象…）：

| 键 | 作用 |
|----|------|
| `SPACE` / 拖时间轴 | 播放暂停 / seek（seek 同时暂停） |
| 拖视口 / 滚轮 | 环绕相机 / 推拉 |
| `T` | 切换编辑目标（相机 → 环境光 → 场景对象 → …） |
| `K` | 在当前播头把**编辑目标的当前值**记为关键帧 |
| `DEL` | 删除编辑目标上离播头最近的关键帧 |
| `[` `]` | 选中通道上/下；`I` 换插值（LINEAR→CUBICSPLINE→STEP） |
| `M` | 加幕标记 |
| `W A S D` / `Q E` / `Z X` / `C V` | 对象：XZ 移动 / 上下 / 统一缩放 / 绕 Y 旋转；环境光：`Z X` 日照强度、`C V` 日照方位 |
| `F2`（或 `CTRL+S`）/ `O` / `N` / `R` | 导出 / 载入 / 新建 / 重载场景 JSON |

### 2.3 与录制打通

`demos/director.py` 支持 `--shot-json <path>`，并把演出时长暴露为
`app.timeline.duration`；`record_all.py` 的既有驱动（`app.time = t` → `update(dt)` →
`on_draw`）即可**离线确定性**录出"导演台排的运镜"。默认演出（`default_shot()`，20s）
已登记进 `record_all.py` 的 `ENTRIES`（`director_20s`）。

---

## 3. 落地步骤（全部完成）

| 步 | 内容 | 验收（实测） |
|----|------|------|
| 1 | `engine/shot.py`：`Shot` 模型（JSON ↔ Timeline 双向） | 往返 `to_dict()` 逐字段相等；float/vec3/quat 三类通道采样正确（关键帧处精确命中、边界外钳制、两帧线性退化） |
| 2 | `demos/director.py`：预览 + 时间轴 + 关键帧增删改 | `--smoke 4` → `EXIT_OK`；`--shot` → 4 张 PNG，逐张人工核对 |
| 3 | 导出/加载 JSON | `save()` → `load()` → `to_dict()` 等价；`shots/*.shot.json` 可被 `--shot-json` 回放 |
| 4 | `record_all.py` 集成 | `ENTRIES` 加 `director_20s`（`dur=20.0`，与演出闭环同长） |
| 5 | 单测 + 文档 | `test_engine` +2 项（含 P0 回归守卫）；本 note + `demos/README.md` + `README.md` 回填 |

---

## 4. 风险与对策

| 风险 | 对策 |
|------|------|
| 相机关键帧 CUBICSPLINE **过冲**（穿墙） | 只给"值"、切线走**有限差分**；两帧自动退化为线性；预览即所见；每通道可切 LINEAR |
| 与 `serialize.py` 场景格式耦合 | `shot.scene` 只是**路径引用**（导出时转成相对仓库根），不内嵌场景 |
| 导演台变成"半成品玩具" | 验收闭环：编排 → 导出 → 被 `Shot` 读回 → 回放/录制（已跑通） |
| 手写 JSON 时间戳乱序/重复让 `Sampler` 断言炸 | `Shot.add_channel` 内部**排序 + 去重**（同 t 取后者） |

---

## 5. 实测坑位（本项踩到并修掉的，写下来省下一次）

1. **pyglet 2.1.16 的 `Batch` 不持有 `Shape`/`Label` 对象引用** —— 创建后没人引用的
   图元会被 GC 回收、**根本不画**：右侧关键帧面板第一版整片空白（只有被 `self.` 引用
   的标题和播头线在）。最小探针实测：持有引用的红块渲染、不持有引用的绿块全黑。
   **连带修掉一个既有缺陷**：`presentation/timeline_ui.py` 的**幕分隔刻度**从 M6 加进
   就没被引用过（代码在跑、刻度从来没出现过），现在存进 `self.act_ticks`。
2. **`Hud.layout` 把帮助行钉在 y=16**，而 `TimelineUI` 面板占 y=14..78 —— 默认位置正好
   压在时间轴上（糊成一片）。导演台把帮助行抬到面板顶边之上（`_place_help()`，resize 后重摆）。
3. **逆光 = 主体"消失"**：`moonlit-altar` 是深色石作 + 夜空，相机绕到太阳**对面**时主体
   整片发黑、整帧只剩天空。对策不是调材质，而是**让太阳方位跟着相机走**
   （`_sun_from_cam(cam_pos, lift=0.52, skew=0.42)`，skew 保留侧向明暗）。
4. **构图要量**：主体是"低矮平台"（台阶半径 6.6、含拱顶高 7），放 17~18 单位外缩成
   地平线一条扁带；抬太高、俯角太大又会因为**平台顶面与地面亮度几乎相同**（隐藏地面
   全局均值只变 2.0）而糊成一片。最终定为半径 12.5、眼高 3.8↔5.2 的**近水平**环绕。
5. **`Curve`/插值名**：`world3d.engine.timeline.INTERPOLATIONS` 里是 `CUBICSPLINE`，**不是 `CUBIC`**。
6. **本项之外挖到的 P0**：`Mesh.draw` 的模型矩阵 uniform 去重键放错（按网格而非按
   program），多网格静态场景**第 2 帧起图形全部塌到最后一个绘制物的变换上**。详见
   `docs/引擎设计.md` §三.11 —— 这条比导演台本身更值钱。

---

## 6. 待确认（4 问，带推荐）—— 已由全哥裁决

| # | 问题 | 全哥裁决 | 落地情况 |
|---|------|---------|---------|
| 1 | 载体：新建 `demos/director.py`？还是扩 `scene_viewer.py`？ | **新建** | `demos/director.py`（查看器保持"只读看"） |
| 2 | 编辑对象：只编相机？还是也支援物件/灯光属性？ | **相机 + 物件灯光** | 三个伪节点/节点类，物件 TRS 可键盘编辑 |
| 3 | 与录制打通：加不加 `record_all.py --shot-json`？ | **要** | `--shot-json` + `ENTRIES` 登记 |
| 4 | 插值默认：CUBICSPLINE 还是 LINEAR？ | **CUBICSPLINE** | 默认 CUBICSPLINE，`I` 键可逐通道切换 |
