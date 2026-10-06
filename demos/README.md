# demos — 单文件 3D 示例集

每个 demo 都是**独立可跑的单文件脚本**，共用 `_harness.py` 里的 `DemoApp`
骨架（窗口 / DPI / 命令行 / HUD / 截图 / 暂停），不改变主工程
`core → logic → presentation` 的分层约定。

## 统一用法

```bat
python demos\<名字>.py                  正常运行
python demos\<名字>.py --smoke [秒]     冒烟：N 秒后自动关闭（默认 2.0）
python demos\<名字>.py --shot           按时抓帧写 docs/ 后自动关闭
python demos\<名字>.py --size 960x540   指定窗口尺寸
python demos\<名字>.py --fps 30         指定逻辑刷新率
python demos\<名字>.py --duration 12    覆盖演示循环时长
```

批量验收（冒烟 + 截图 + 退出码，逐个串行跑真实 GL 窗口）：

```bat
python scripts\verify_demos.py                全部
python scripts\verify_demos.py --smoke-only   只跑冒烟
python scripts\verify_demos.py --only boids,water
```

> `--shot` 的截图落在 `docs/`，文件名见下表；脚本退出码非 0 = 该 demo 有缺陷。

## 清单

| demo | 时长 | 演示的技术点 | 截图 |
|------|------|--------------|------|
| `demoscene_tunnel.py` | 24s | **纯片元着色器**：极坐标隧道、程序化面板、行进光带、色散/扫描线/暗角 | `demo_tunnel_a/b.png` |
| `raymarch_sdf.py` | 32s | **SDF 光线步进**：球/盒/环 smooth-min 融合、软阴影、AO、地面反射雾 | `demo_raymarch_a/b.png` |
| `particles_gpu.py` | 30s | **原生实例化**（`vertex_list_instanced`）：4.5 万粒子的位置/颜色全部在顶点着色器里算 | `demo_particles_a/b.png` |
| `solar_system.py` | 60s | **层级变换 + 压缩轨道力学**：UV 球体生成、土星环、月球绕地、实例化小行星带、按键聚焦 | `demo_solar_a/b.png` |
| `terrain_flyover.py` | 48s | **程序化几何**：值噪声 + fBm 高程图、按高度/坡度分层着色、烘焙光照、航线飞行 | `demo_terrain_a/b.png` |
| `voxel_terrain.py` | 36s | **体素面剔除**：只生成暴露面、`H` 键对比全局面数（剔除率实测） | `demo_voxel_a/b.png` |
| `water_waves.py` | 40s | **顶点着色器动画**：Gerstner 波位移 + 解析法线、菲涅尔天空、CPU/GPU 共用一套波函数 | `demo_water_a/b.png` |
| `boids.py` | 45s | **逻辑层纯数学 + 实例化渲染**：Reynolds 群集（分离/对齐/聚合），着色器按速度定向 | `demo_boids_a/b.png` |
| `shadow_map.py` | 30s | **两趟渲染 / render-to-texture**：光源视角深度图 + PCF 阴影 | `demo_shadow_a/b.png` |

`logic/boids.py`、`logic/waves.py` 是其中两个 demo 的**逻辑层模块**：
不 import `pyglet.gl`，可无窗口导入与单测（`python -m tests.run_tests`）。

## 参考样板

新写 demo 请先看 `demoscene_tunnel.py`——它是最短的完整例子，
演示了 `DemoApp` 的全部覆盖点（`setup/update/render/on_key` 与 `SHOTS` 声明）。

## 踩过的坑（写在这里省得每篇重踩）

- **几何 API**：pyglet 2.1.16 已移除 `Batch.add` / `c3f`，一律用
  `program.vertex_list(...)`；着色器里用到的属性必须都声明，否则
  未使用的属性会被 GLSL 优化掉、pyglet 报 `ShaderException`。
- **实例化**：2.1.16 原生支持 `ShaderProgram.vertex_list_instanced(...)` +
  `add_instance(...)`（域初始 `_instances=1`），**不要**手搓 VAO/VBO；
  每帧只更新少量实例属性可以，几万条逐帧更新会掉帧。
- **DPI**：`pyglet.options['dpi_scaling'] = 'real'` 必须在建窗口前设置，
  `_harness` 已经统一处理；截图走 `glReadPixels` 按实际 buffer 尺寸读，
  不用 `get_color_buffer()`（后者会按被 DPI 放大的 VIEWPORT 读，出黑边）。
- **旋转**：用 `logic.animation.quat_to_mat4`，pyglet 自带
  `Quaternion.to_mat4()` 实测是转置的。
- **路径**：不许写死盘符，一律用 `_harness.PROJECT_ROOT` / `DOCS_DIR`。
