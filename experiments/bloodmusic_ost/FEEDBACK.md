# bloodmusic 需求反馈（来自 3dworld 试点）

> 来源：3dworld `experiments/bloodmusic_ost/`（kkrieger 第六幕重制试点，2026-10-09）。
> 定位：**用实际集成需求驱动 bloodmusic 迭代**——以下均为真实踩到/真实需要，
> 按「工程 / 表达力 / 工作流 / 生态」整理，供 bloodmusic 排期参考。
> （采纳与否、排期由 bloodmusic 项目决定；此为需求方视角的原始反馈。）

## 一、工程与依赖（试点中被真实 blocked 过）

1. **Python 3.13 的 `audioop` 移除未在依赖中声明**
   - 现象：`album_maker` 混音阶段 `ModuleNotFoundError: pyaudioop` → 需手动补 `audioop-lts`。
   - 建议：将 `audioop-lts`（`python_version >= "3.13"` 条件依赖）写入 pyproject/
     requirements，或在导入失败处给出可读的安装提示。
2. **`sounddevice` 被包 `__init__` 顶层硬 import**
   - 现象：`python -m bloodmusic.autowav`（号称零依赖）也因顶层 `playback` 链路
     `import sounddevice` 直接失败——**无音频设备/无 GUI 的环境无法使用离线渲染**。
   - 建议：`playback` 改懒加载（实际播放时才 import），让「离线渲染 / 作曲 / 导出」
     在无音频设备环境中真正零依赖可用。
3. **可用性矩阵**：以上两点建议写进 README「安装」一节（Python 3.13+ / 无音频设备场景）。

## 二、表达力（3dworld 曲目需要的音效原语）

4. **爆炸 / 撞击型低频事件**：原曲的「低频坠落（120→28Hz 滑音）+ 噪声爆闪」在
   MusicPattern 体系中无直接表达（试点降级为 C1 长音，听感差异可见）。
   - 建议：`SweepTone`（起止频率 + 时长 + 曲线形状）与 `NoiseBurst`（衰减/低通）
     两个 pattern 级原语——对游戏演出中的「打击 / 爆炸 / 点火」类是刚需。
5. **Riser / 渐变上升**：136–141.3s 的「噪声上升 + 滤波扫频」是演出的张力构建手段，
   试点未实现。
   - 建议：`build_riser(duration, from_hz, to_hz, shape)` 辅助（automation 模块已有
     `build_up_lowpass` 基础，做 pattern 级封装即可用）。

## 三、工作流（试点手工实现、建议原生化）

6. **「绝对时间锚定」模式**：3dworld 的编排是「时间常量驱动」（事件须精确落在
   118.2s / 141.5s）。试点用「休止符填充到目标拍位」手工对齐——建议提供官方辅助：
   `anchor={"beat": 47, "note": "C1"}` 或 `place(mix, t_sec, pattern)`，
   并补充与 `SongArrangement` 的衔接说明。
7. **A/B 同构对照**：同一「谱面结构」换不同音源/引擎渲染对照（本次试点的核心方法）。
   - 建议：作为官方示例/工具（`compare_render(structure, engines=[...])`）。
8. **与 pyglet / 游戏时间轴的集成示例**：3dworld 用 `engine.audio.TimelineAudio`
   播放 WAV；「bloodmusic 渲染 WAV → 游戏时间轴同步播放」的离线管线用法
   值得一个官方示例（现有 interactive/service 偏实时引擎，离线管线示例缺失）。

## 四、生态（可选）

9. **Cordis 宿主**已有（host-js）；3dworld 也在做 Cordis 试点（`experiments/cordis/`）。
   若两边都稳定，可探索「3dworld 场景服务 + bloodmusic 音频服务」在同一 Cordis
   上下文中的组合示例。

---

记账：本清单由 3dworld 侧（需求方）整理；bloodmusic（供给方）侧采纳后，
建议在 CHANGELOG 中以「3dworld 集成需求」标注来源，形成双向可追溯的迭代记录。
