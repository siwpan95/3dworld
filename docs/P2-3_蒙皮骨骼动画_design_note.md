# P2-3 · 蒙皮骨骼动画（design note）

> 状态：**✅ 已执行并交付**（2026-10-10）· 依据 `docs/补完计划.md` §2 的 P2-3（§5 决策③：P2 起点）
> 目标：把「节点级层级动画」升级为**真·蒙皮网格**（glTF `skin` + `JOINTS_0`/`WEIGHTS_0` + 关节矩阵管线）。

---

## 1. 关键发现：2026-09-29 的"不可行"结论前提已失效

`logic/skeleton.py` 顶部 docstring 记录了当时的 Spike 结论：

> pyglet 2.1.16 发行包**无 glTF 解码器**（model/codecs/ 仅 OBJ），蒙皮骨骼动画在此版本**不可行**。
> 故走兜底路线：把"骨骼"建模为**节点树**。**真实蒙皮的演进前提：pyglet 升级带 glTF+skin，或自研解码器 + 蒙皮着色器。**

**该前提现已满足**——3dworld 已**自研 glTF 解码器**（`engine/gltf.py`，412 行）：

| 能力 | 现状（实证） |
|------|--------------|
| glTF 2.0 / GLB 解析 | ✅ `engine/gltf.py`（accessor / bufferView / sparse / material） |
| 顶点属性 | ✅ POSITION / NORMAL / TEXCOORD_0 / TANGENT / COLOR_0 / indices（`primitive_meshdata`，gltf.py:197） |
| 动画 | ✅ channel/sampler → `world3d.engine.timeline.Sampler`（gltf.py:317） |
| **skin / joints / weights** | ❌ **无**（`grep skin\|joint\|weight\|JOINTS` 仅命中动画 weights 的"暂不支持"注释） |
| 蒙皮着色器 | ❌ **无**（`PBR_VERT`/`PBR_INST_VERT` 只处理 position/normals/uvs/tangents） |
| 骨骼 | 🔶 `logic/skeleton.py` = **节点级** BoneNode 树（纯数学，非蒙皮） |

**结论：走"自研解码器 + 蒙皮着色器"这条演进路径现在是可行且正确的。**

---

## 2. P2-3 范围（4 项）

| # | 项 | 交付物 |
|---|----|--------|
| **S1** glTF **skin 解析** | `skins[]`（`joints` / `inverseBindMatrices`）+ 顶点属性 `JOINTS_0` / `WEIGHTS_0` | `engine/gltf.py` 扩展 + `MeshData` 增 `joints`/`weights` 字段 |
| **S2** **蒙皮着色器** | 新 `PBR_SKIN_VERT`：`jointMatrix = Σ wᵢ·u_joints[jᵢ]`，变换位置/法线/切线；关节矩阵以 **uniform mat4 数组**上传 | `engine/shaders.py` + `engine/mesh.py`（SkinnedMesh 绘制） |
| **S3** **关节矩阵管线** | 采样 joint 节点 TRS → 世界矩阵 → `world @ inverseBind` → 上传 | 新 `engine/skin.py`（或 `logic/skin.py`） |
| **S4** **演示载体** | 端到端 demo（见 §5 待确认①） | `demos/skinned_creature.py` + 生成脚本 |

---

## 3. 设计要点

### 3.1 数据结构
```python
# MeshData 增两个可选字段（缺省 None -> 非蒙皮网格，行为不变）
joints:  list[int]    # 每顶点 4 个关节索引（JOINTS_0，UNSIGNED_BYTE/SHORT）
weights: list[float]  # 每顶点 4 个权重（WEIGHTS_0，FLOAT/NORMALIZED_UBYTE），和为 1

# 蒙皮（S3）
class Skin:
    joints: list[Node]          # glTF skin.joints 指向的节点
    inverse_bind: list[Mat4]    # inverseBindMatrices
    def joint_matrices(self) -> list[Mat4]:   # world @ inverse_bind
```

### 3.2 着色器（S2）
```glsl
// PBR_SKIN_VERT 增补
in vec4 joints;      // 关节索引（float 承载，取整）
in vec4 weights;
uniform mat4 u_joints[MAX_JOINTS];   // 关节矩阵数组
mat4 skin = weights.x * u_joints[int(joints.x)]
          + weights.y * u_joints[int(joints.y)]
          + weights.z * u_joints[int(joints.z)]
          + weights.w * u_joints[int(joints.w)];
vec4 wp = model * skin * vec4(position, 1.0);
vec3 wn = normal_mat * mat3(skin) * normals;
```
> GLSL 330 允许对**非 sampler** 数组动态下标（本项目踩坑记录里的限制只针对 sampler 数组）——可行。

### 3.3 与现有 `logic/skeleton.py` 的关系
**并存**（见 §5 待确认④）：节点级 BoneNode 树仍服务机械臂/摆锤等；新增蒙皮路径独立。**不删旧代码**。

---

## 4. 落地步骤（每步可独立验收）

1. `MeshData` 加 `joints`/`weights`（默认 None）→ 现有单测须全绿（零回归）；
2. `gltf.py` 解析 `JOINTS_0`/`WEIGHTS_0` + `skins`/`inverseBindMatrices`；
3. `engine/skin.py`：Skin 类 + `joint_matrices()`（纯数学，可无窗口单测）；
4. `shaders.py` 加 `PBR_SKIN_VERT`；`mesh.py` 加 SkinnedMesh（关节矩阵 uniform 上传）；
5. `demos/skinned_creature.py`：加载/构造蒙皮模型 + 播放骨骼动画；
6. 测试：`tests/test_skin.py`（joints/weights 解析、joint_matrix 数值、蒙皮顶点变换对照 CPU 实现）。

---

## 5. 待确认（4 问，带推荐）

| # | 问题 | 推荐 |
|---|------|------|
| 1 | **演示载体**：(a) **端到端** —— 写 `scripts/make_skinned_gltf.py` 程序化生成一个带 skin 的 `.gltf`（如"骨节蛇/触手"）存入 `assets/`，demo 加载它（覆盖"解析 skin + 蒙皮渲染"全链路，符合项目"程序化生成物"惯例，如 `make_gem_obj.py`）；(b) 仅程序化蒙皮（代码直接构造骨骼+权重，不产 glTF 文件） | **(a) 端到端** —— 最有价值，且零外部素材 |
| 2 | **关节矩阵上传方式**：uniform `mat4` 数组 vs 关节纹理（joint texture） | **uniform 数组**（骨骼 ≤ 64 时最简单、GLSL 330 够用） |
| 3 | **最大骨骼数** `MAX_JOINTS` | **64**（够 demo；后续可按需调） |
| 4 | **与 `logic/skeleton.py` 关系**：并存 / 替换 | **并存**（旧的节点级动画仍用于机械臂等，不删） |

---

## 6. 风险与对策

| 风险 | 对策 |
|------|------|
| `MeshData` 加字段破坏现有单测/渲染 | 字段**默认 None**；非蒙皮路径一行不改 |
| pyglet 设 `uniform mat4[]` 数组的写法不确定 | 先做**最小验证脚本**（设一个 2 元素数组并渲染），确认后再铺开 |
| 权重未归一化 / 关节索引越界 → 画面崩坏 | CPU 侧**归一化 + 钳制**（`joints.clamp(0, MAX-1)`），单测覆盖 |
| 程序化导出的 glTF 不符合规范 | 用现有 `engine/gltf.py` **自解析回读**做闭环校验（导出的文件必须能被自己读回且数值一致） |

---

## 7. 验收标准

- `python -m tests.run_tests` / `test_engine` / `test_cores` **全绿**（零回归）；
- 新增 `tests/test_skin.py`：joints/weights 解析、`joint_matrices()` 数值、蒙皮变换与 CPU 参考实现一致；
- `python demos/skinned_creature.py --smoke` 无报错；`--shot` 出图；
- 程序化生成的 `.gltf` 能被 `engine/gltf.py` 读回，且蒙皮顶点数与权重一致。

---

## 8. 执行结果（2026-10-10 ✅ 已交付）

**决策落实**：① 端到端（程序化生成 skinned glTF）✅ ② uniform 数组 ✅（实测发现 pyglet 限制，改直调 GL，见坑 1）
③ MAX_JOINTS=64 ✅ ④ 与 `logic/skeleton.py` **并存**（未改旧文件）✅

**落地清单**：

| 项 | 文件 |
|----|------|
| S1 glTF skin 解析 | `engine/gltf.py`：`skins()` + `JOINTS_0`/`WEIGHTS_0` + `_normalize_weights` + `build_scene_ex`（返回 node_map） |
| S2 蒙皮着色器 | `engine/shaders.py`：`PBR_SKIN_VERT` + `SKIN_MAX_JOINTS=64` + `'pbr_skin'` 注册 |
| S3 关节矩阵管线 | `engine/skin.py`：`Skin` / `make_skin_from_gltf` / `MAX_JOINTS` |
| 蒙皮网格 | `engine/mesh.py`：`SkinnedMesh` + `Mesh.PROGRAM_NAME` 机制 |
| 数据 | `engine/geometry.py`：`MeshData.joints`/`weights` + `skinned` |
| 渲染器 | `engine/renderer.py`：按 `PROGRAM_NAME` 分组选程序 |
| 生成器 | `scripts/make_skinned_gltf.py` → `assets/skinned_snake.gltf`（8 关节 / 300 顶点 / 24.8KB） |
| demo | `demos/skinned_creature.py` → `docs/skinned_01..03.png` |
| 测试 | `tests/test_skin.py`（8 项） |

**验收（全绿）**：`tests.run_tests` / `test_engine` / `test_cores` / `test_skin` 全 `ALL_*_PASSED`；
`config_boot all` **4/4 OK**（td/godhand/fps/cores）；demo `--smoke` `EXIT_OK`，出图显示蛇**弯曲 + 材质正确**。

**实测踩坑（3 个真实缺陷，全部有据）**：

1. **pyglet 数组 uniform 只写第 0 个元素**（最隐蔽）——
   `program['u_joints'] = [...]` 内部走 `_Uniform` 的 setter，固定 `glUniformMatrix4fv(loc, 1, ...)`（**count=1**），
   其余槽位**保持 0**。后果：`skin` 矩阵对绝大多数顶点为 0 ⇒ 顶点塌缩到原点、网格整片消失，
   **且不报任何错**（`program.uniforms['u_joints']` 报 size=64 看似正常）。
   **修**：绕过 pyglet，`SkinnedMesh._upload_joint_matrices` 里直接
   `glUniformMatrix4fv(loc, MAX_JOINTS, GL_FALSE, arr)` 整体上传。
2. **renderer 硬编码 program 选择** —— `_draw_list` 里 `'pbr_inst' if InstancedMesh else 'pbr'`，
   `SkinnedMesh` 被拿 **`PBR_VERT`（无蒙皮逻辑）** 渲染 ⇒ 属性绑定错位 ⇒ **纯黑**。
   **修**：引入 `Mesh.PROGRAM_NAME` 类属性（`pbr` / `pbr_inst` / `pbr_skin`），renderer 按它分组。
3. **四元数分量顺序** —— glTF JSON 的 `rotation` 是 **xyzw**，而 `mat4_from_trs` 用 **wxyz**；
   生成脚本写 `(0,0,0,1)` 被解释成"绕 Z 轴 180°" ⇒ 绑定矩阵翻转（绑定姿态 ≠ I）。
   **修**：脚本单位四元数写 `(1,0,0,0)`。

**另外三个 API 陷阱（顺手记录）**：
- `pyglet.graphics.shader.ShaderException` 继承 **`BaseException`**（不是 `Exception`）——`except Exception` 抓不到；
- `Scene.drawables()` **有缓存**，setup 里新挂 `node.drawable` 后必须 `scene.invalidate()`，否则渲染器"看不到"任何东西；
- `Node.update_world()` 在 `parent_matrix=None` 时**把该节点当根**——`traverse` 后逐节点调用会**丢掉父链**，只应对 root 调一次。

---

*本 design note 已执行完毕；下一批见 `docs/补完计划.md` §5 批次顺序（P3-1 GPU-Driven / P1-④ Elite 太空）。*
