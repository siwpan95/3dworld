# 3dworld — pyglet 三维世界示例

用 Python + pyglet 从零构建一个简单三维世界：棋盘格地面 + 环形彩色立方体 +
轨道相机（鼠标拖动旋转 / 滚轮缩放，ESC 退出）。

## 两个版本，对应两条 pyglet 技术路线

| 文件 | 适配版本 | 渲染管线 | 适用环境 |
|------|----------|----------|----------|
| `pyglet_3d_demo.py` | pyglet 2.x | 可编程管线（Mat4 / Batch） | 新项目、独立 venv |
| `pyglet_3d_demo_1x.py` | pyglet 1.x | 固定管线（gluPerspective 等） | 与 cocos2d 共存的环境 |

> 注意：pyglet 1.x 与 2.x API 不互通（2.x 已移除 GLU），教程代码不可混用。
> 2.x 版示例不依赖 `Mat4.look_at`（部分过渡版有 `cross_product` 改名缺陷），
> 相机矩阵为内置纯算术实现，任何 2.x 小版本均可运行。

## 项目结构（M1 分层重构后）

```
core/            核心层：窗口/渲染/几何/资源（允许 pyglet.gl）
  geometry.py        立方体与棋盘地面构建（ShaderProgram.vertex_list）
  renderer.py        窗口、透视投影、Batch 上传与绘制
  resources.py       外部模型加载（当前 .obj；pyglet 升级后 .glb 即插即用）
logic/           逻辑层：场景与动画（禁止 pyglet.gl，可无窗口单测）
  camera.py          轨道相机状态机 + 手写 look-at 矩阵
  animation.py       时间轴/关键帧/插值（glTF 对齐，四元数 slerp）
  playback.py        播放控制器：播放/暂停/循环/倍速/seek
  skeleton.py        节点级层级动画（骨骼语义节点树，父子 TRS 继承）
  events.py          事件总线（pyglet EventDispatcher）
presentation/    表现层：装配窗口事件 → 逻辑层 → 渲染
  app.py             WorldApp 装配与事件接线
  timeline_ui.py     底部时间轴面板（播放/循环/拖动刷取）
tests/           逻辑层无窗口单测：python -m tests.run_tests
main.py          入口（pyglet_3d_demo.py 为兼容 shim）
docs/            设计文档
```

依赖方向：presentation → logic → core，反向禁止。

## 快速开始（推荐：项目级 venv 隔离）

```bat
:: 在项目根目录执行（路径不含盘符，随处可用）
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

- `requirements.txt` 锁定 pyglet 2.1.16（2.x 版示例）。
- 若你的环境绑死 cocos2d（pyglet 1.x），无需安装任何东西，
  直接用系统 Python 跑 `pyglet_3d_demo_1x.py` 即可。
- `requirements-pyglet1x.txt` 备有 1.x 锁版清单，需要时可自建 1.x venv。

## 隔离原则

- 本项目依赖只装进 `.venv/`（已 gitignore），不污染全局环境；
- 升级 / 回滚只影响本项目：`pip install pyglet==<版本>`；
- cocos2d 等其他项目各建各的 venv，互不干扰。

## 素材来源

- pyglet 官方示例 `examples/opengl/opengl.py`（旋转圆环）；
- pyglet 编程指南（math / graphics 章节）；
- Stack Overflow「How can I create 3D with pyglet?」（黑屏 = 默认正交投影裁剪）。

## 许可

MIT，见 [LICENSE](LICENSE)。
