# LLM 场景生成工作流

> 定位：把「自然语言 → 可渲染 3D 场景」接进 3dworld 的最小闭环。
> 理念与 Scene Language（ICLR 2025）、PSDL（程序 + 本地无 LLM 纠错）同源：
> **LLM 只负责"写数据"，合法性由本地校验器把关，渲染由引擎负责。**

## 一、为什么是这个架构

- 项目哲学「零外部素材 + 一切程序化」的延伸：场景 JSON 里**不存顶点**，
  只存「生成器名 + 参数 + 材质 + 变换」——几何在构建时由 `engine.geometry` 现算。
- 双端分离：
  - **生成端（LLM / 人）**：只需要懂生成器清单与 JSON 格式（见第三节 + 第四节 Prompt 模板）
  - **执行端（引擎）**：`engine/scene_build.py` 校验 + 重建；`demos/scene_viewer.py` 渲染 + 热重载
- **校验器即"本地纠错器"**：LLM 的空间推理不可靠，但它写的每个参数都会被
  **实际试构建**（未知生成器 / 未知参数 / 数组形状错误 → 立即报错）——
  这是 PSDL 论文「无 LLM 本地纠错」思路的工程化雏形。

## 二、三步工作流

```bash
# 1. 生成：让 LLM 按 Prompt 模板输出场景 JSON，保存为 docs/xxx.json
#    （也可以手写；参考 docs/llm_scene_demo.json）
# 2. 校验：纯 CPU、秒级；错误会指出精确路径（$.root.children[3].mesh.args...）
python scripts/scene_build.py docs/xxx.json --validate
python scripts/scene_build.py docs/xxx.json --dry-run     # 再跑全量 CPU 构建

# 3. 查看：打开查看器，编辑 JSON 保存即热重载（R 键手动重载）
python demos/scene_viewer.py --file docs/xxx.json
```

热重载行为：改文件保存 → 自动重载；**含错误的 JSON 会被拒绝并保留旧场景**
（终端打印错误清单），所以可以放心高频迭代。

## 三、JSON 参考

### 顶层

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 否 | 场景名 |
| `environment` | 否 | 光照环境（`LightEnvironment` 参数：`sun_dir` / `sun_color` / `sun_intensity` / `sky_zenith` / `sky_horizon` / `sky_ground` / `ibl_intensity` / `sky_intensity`），省略则用默认白昼 |
| `root` | 是 | 根节点（递归 children） |

### 节点

| 字段 | 说明 |
|------|------|
| `name` | 节点名（建议唯一——重复会警告，`find()` 有歧义） |
| `translation` | `[x, y, z]`（Y 轴向上；地面在 y=0） |
| `rotation` | 四元数 `[x, y, z, w]`（默认 `[0,0,0,1]`；模长≠1 会警告） |
| `scale` | `[sx, sy, sz]`（默认 `[1,1,1]`） |
| `mesh` | `{"builder": 生成器名, "args": {...}, "color": [r,g,b(,a)]}`，省略 = 纯结构节点 |
| `material` | `{"preset": 预设名, ...覆盖字段}`（见下表） |
| `children` | 子节点数组（层级变换，父节点带动子节点） |

### 生成器清单（`mesh.builder` 可选值）

| builder | 参数（默认值） | 说明 |
|---------|----------------|------|
| `box` | `sx, sy, sz, offset=[0,0,0]` | 盒体（尺寸 + 中心偏移） |
| `cube` | `center=[0,0,0], size=1.0` | 立方体 |
| `sphere` | `radius=1.0, segments=24, rings=16` | UV 球 |
| `plane` | `width=1.0, depth=1.0, segs=1, y=0.0` | 水平面 |
| `cylinder` | `radius=0.5, height=1.0, segments=24, capped=true` | 圆柱（可带盖） |
| `torus` | `major=1.0, minor=0.3, major_segs=32, minor_segs=16` | 圆环（XZ 平面） |
| `ground_grid` | `half=10, tile=1.0, color_a, color_b` | 棋盘格地面（y=0） |
| `lathe` | `profile=[[r,y],...], segments=24, closed_top, closed_bottom` | **车削体**：2D 轮廓绕 Y 轴旋转（花瓶/柱/塔） |
| `tube` | `points=[[x,y,z],...], radius=0.1, radial=8, caps=true` | **管扫掠**：沿 3D 折线扫管（拱门/线缆/轨道） |
| `torus_knot` | `p=2, q=3, major=1.0, tube_r=0.22, tubular=96, radial=10` | **环面结**（数学结） |

所有生成器都支持可选 `color`（顶点色 `[r,g,b(,a)]`，PBR 下建议优先用材质）。

### 材质（`material`）

`preset` 可选：`gold` `copper` `steel` `iron` `plastic_red` `rubber`
`ceramic` `marble` `emerald_light` `neon`（发光）。省略 preset 则从白模起。

覆盖字段：`base_color` `metallic` `roughness` `emissive` `alpha`
`alpha_mode`（`OPAQUE`/`BLEND`）`alpha_cutoff` `double_sided`。

## 四、Prompt 模板（复制给 LLM）

```text
你在为一个 Python 程序化 3D 引擎（pyglet，Y 轴向上）编写场景 JSON。

严格输出一个 JSON 对象（不要 markdown 代码围栏、不要注释），结构：
- 顶层：{"name": 字符串, "environment": 可选, "root": 节点}
- 节点：{"name", "translation": [x,y,z], "rotation": [x,y,z,w] 可选,
        "mesh": {"builder": ..., "args": {...}}, "material": {...},
        "children": [节点...]}
- environment（可选）：sun_dir / sun_color / sun_intensity / sky_zenith /
  sky_horizon / sky_ground / ibl_intensity / sky_intensity

可用生成器（builder）与参数（只用这些名字，不要发明参数）：
- box: sx, sy, sz, offset
- cube: center, size
- sphere: radius, segments, rings
- plane: width, depth, segs, y
- cylinder: radius, height, segments, capped
- torus: major, minor, major_segs, minor_segs
- ground_grid: half, tile, color_a, color_b
- lathe: profile（[[r,y],...] 按 y 单调排列的轮廓）, segments, closed_top, closed_bottom
- tube: points（[[x,y,z],...] ≥2 个点）, radius, radial, caps
- torus_knot: p, q, major, tube_r, tubular, radial

材质：material.preset 可取 gold/copper/steel/iron/plastic_red/rubber/
ceramic/marble/emerald_light/neon，另可覆盖 base_color/metallic/roughness/
emissive/alpha/alpha_mode。

硬性约束：
1. 坐标系：Y 向上，地面在 y=0；场景尺度建议 20~60 米；
2. 物体不要悬空（除非刻意悬浮，且垫高合理）、不要互相穿插；
3. 细分参数保持克制：segments/rings/major_segs ≤ 64，tubular ≤ 160，
   radial ≤ 12（否则构建慢、画面卡）；
4. 节点 name 唯一、用英文小写+下划线；
5. 只用上面列出的字段与参数名——多写一个校验就会报错。

场景主题：<在这里写你的描述，例如「月夜下的石柱祭坛，中央悬浮发光核心」>
```

## 五、示例

`docs/llm_scene_demo.json`（「月夜祭坛」）——官方样例：3 层台阶 + 8 根车削
立柱 + 环面结发光核心 + 金环 + 管道拱 + 悬浮光球 + 远月。用第三节的工具链
即可校验、查看、迭代。

## 六、来源与延伸

- 理念来源：Scene Language（ICLR 2025）、PSDL（程序 + 本地纠错）——
  见 `docs/../` 调研记录《3dworld 拓展方向调研》（工作区文档）；
- 延伸方向：让校验器输出**结构化反馈**喂回 LLM 迭代（生成-校验-修复环）；
  把 `--validate` 接进 CI；场景 JSON 的版本化与画廊（每场景一份 JSON + 一图）。
