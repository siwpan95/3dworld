# Godot 4.x 与 Unreal Engine 5 源码架构调研笔记

> 调研时间：2026-10-21 ｜ 版本基准：Godot `master`（version.py: major=4, minor=8, patch=0, status="beta"，commit `679066724352c12673001d692d41dc2dab9314c0`）；UE5 参考 5.3 / 5.6 / 5.8 公开文档
> 所有目录路径与类名均来自真实抓取（Godot 侧通过 `git ls-tree -r HEAD` 全量文件清单 + `git show HEAD:<path>` 逐文件核对；UE5 侧通过官方文档 / 论坛 / 技术博客核对）。
> 标注「（推测）」者为未能从一手来源确认、但由上下文高度推断的条目。

---

## 0. 元信息（信息来源 URL 清单）

本节列出实际抓取过、且为本笔记结论提供直接依据的链接（≥12 条）：

| # | URL | 获取方式 | 提供的关键信息 |
|---|---|---|---|
| 1 | https://raw.githubusercontent.com/godotengine/godot/master/README.md | WebFetch | 引擎定位、MIT 许可、跨平台列表、模块化开源社区 |
| 2 | https://github.com/godotengine/godot/tree/master/servers | WebFetch | `servers/` 下 12 个子目录：audio / camera / debugger / display / movie_writer / navigation_2d / navigation_3d / physics_2d / physics_3d / rendering / text / xr |
| 3 | https://github.com/godotengine/godot/tree/master/drivers | WebFetch | `drivers/` 下 24 个驱动目录：vulkan / gles3 / d3d12 / metal / egl / gl_context / alsa / coreaudio / wasapi / xaudio2 等 |
| 4 | https://github.com/godotengine/godot/tree/master/scene | WebFetch | `scene/` 子目录：2d / 3d / animation / audio / debugger / gui / main / resources / theme |
| 5 | https://docs.godotengine.org/en/stable/engine_details/architecture/godot_architecture_diagram.html | curl + HTML 抽取 | **官方五层架构定义**：Scene Layer / Server Layer / Drivers & Platform Interface / Core / Main，及各层对应源码目录 |
| 6 | https://docs.godotengine.org/en/stable/tutorials/scripting/gdscript/gdscript_basics.html | curl + HTML 抽取 | GDScript 语法：`class_name`、`extends`、`@icon`、内嵌类、枚举 |
| 7 | https://docs.godotengine.org/pt-br/4.x/engine_details/engine_api/gdextension/gdextension_interface_json_file.html | WebSearch 命中官方文档镜像 | GDExtension C ABI：`gdextension_interface.json` 三段结构（header / types / interface）、`since` 版本标记、不向后兼容承诺 |
| 8 | https://github.com/EpicGames | WebFetch | EpicGames 组织：UnrealEngine 仓库 EULA 门控；公开开源仓库列表 |
| 9 | https://docs.unrealengine.com/5.3/en-US/render-dependency-graph-in-unreal-engine | WebSearch 命中官方文档 | **RDG 官方说明**：`FRDGBuilder` 生命周期、setup/execute 双时间线、pass culling、RDG 资源与 RHI 资源分离、静态 uniform buffer 绑定 |
| 10 | https://docs.unrealengine.com/5.1/en-US/API/Runtime/RenderCore/FRDGBuilder | WebSearch 命中官方 API | `FRDGBuilder` 头文件路径 `/Engine/Source/Runtime/RenderCore/Public/RenderGraphBuilder.h`、模块 RenderCore、`AddPass`/`Execute`/`AddPassDependency` 签名 |
| 11 | https://docs.unrealengine.com/latest/INT/API/Runtime/RHI/index.html | WebSearch 命中官方 API | RHI 模块位置 `/Engine/Source/Runtime/RHI/`、模块名 `RHI`、RHI 类清单（`FDynamicRHI`、`FRHICommandListImmediate`、`FRHIResource` 等 60+ 类） |
| 12 | https://www.unrealengine.com/tech-blog/chaos-scene-queries-and-rigid-body-engine-in-ue5 | WebFetch | **Chaos 取代 PhysX 官方确认**：UE5.0 起 Chaos 为默认物理引擎、性能对比数据、加速结构优化方向 |
| 13 | https://docs.unrealengine.com/5.2/zh-CN/unreal-engine-modules | WebSearch 命中官方文档 | **Build.cs 机制**：`ModuleRules` 类、`PublicDependencyModuleNames` vs `PrivateDependencyModuleNames`、Public/Private 子目录约定 |
| 14 | https://dev.epicgames.com/documentation/unreal-engine/world-partition-in-unreal-engine?application_version=5.0 | WebSearch 命中官方文档 | **World Partition 官方说明**：单一持久关卡 + 网格单元（Grid Cell）自动流送、Data Layer、HLOD、One File Per Actor、转换 Commandlet 与 ini 配置 |
| 15 | https://forums.unrealengine.com/t/knowledge-base-garbage-collector-internals/501800/2 | WebSearch 命中官方论坛 | **UE GC 官方内部机制**：Mark & Sweep 三阶段、Cluster 优化、UE5.4 增量可达性分析、`CreateCluster()` / `FUObjectClusters.AllocateCluster` 用法 |
| 16 | https://dev.epicgames.com/documentation/zh-cn/unreal-engine/incremental-garbage-collection-in-unreal-engine | WebSearch 命中官方文档 | 增量可达性分析、`gc.AllowIncrementalReachability` / `gc.AllowIncrementalGather`、`TObjectPtr` 写屏障要求 |
| 17 | https://forums.unrealengine.com/t/external-pak-files-and-use-io-store-option/2137290/4 | WebSearch 命中官方论坛（UE_Seb.Tho 回复） | **IoStore 官方解释**：`.utoc`/`.ucas` 分离头与数据、虚拟文件系统按包路径寻址、`AssetRegistry.bin` 独立存放 |
| 18 | https://book.bl4.dev/02-unreal-architecture.html | WebFetch | **UE5 运行时内存布局**：UObject 40 字节头字段表、`FName` 8 字节编码、`UStruct`/`FProperty` 链、`GUObjectArray` 分块结构、Zen Package 五段式、usmap schema 格式 |
| 19 | https://raw.githubusercontent.com/Lynricsy/HyperSkills/main/skills/unreal/references/unreal-scripting.md | WebFetch | UHT 反射宏语义、`.generated.h` 规则、CDO 传播、GC 可达性契约、指针类型选型表、Subsystem 生命周期 |
| 20 | https://github.com/godotengine/godot-docs/blob/8c843e8d/engine_details/engine_api/gdextension/gdextension_interface_json_file.rst | WebSearch 命中官方文档仓库 | GDExtension JSON 格式规范（header / types / interface 三段、kind 分类、since 字段） |
| 21 | https://zread.ai/JasonHe78/UE5-1-1/15-nanite-virtualized-geometry | WebSearch 命中 | Nanite 三处代码区分布：`Renderer/Private/Nanite/`、`Shaders/Private/Nanite/`、`Runtime/Rendering/NaniteResources.h`；`EPipeline` 四种管线类型 |
| 22 | https://www.medien.ifi.lmu.de/lehre/ws2122/gp/slides/gp-ws2122-extra-nanite.pdf | WebSearch 命中（LMU 课程讲义） | Nanite 源码检索策略与 `Engine/Source/Runtime/Renderer/Private/Nanite` 路径、Visibility Buffer 流程 |
| 23 | https://d1ap1mz92jnks1.cloudfront.net/t/rhi-switching-dx11-dx12-vulkan-during-runtime/571030/6 | WebSearch 命中官方论坛 | `LoadDynamicRHIModule` 位于 `RHI/Windows/WindowsDynamicRHI.cpp`、`DefaultGraphicsRHI` 配置项、启动链 `FEngineLoop::PreInit → RHIInit → PlatformCreateDynamicRHI` |
| 24 | https://natsuneko3.github.io/2023/04/16/ue源码/RHI解析/ | WebSearch 命中 | RHI 继承体系：`FDynamicRHI` / `IRHICommandContext` / `IRHICommandContextPSOFallback`、`FRHICommandListExecutor` 与 `GRHICommandList` |
| 25 | https://www.hqwc.cn/a/650725.html | WebFetch | UE5 `Engine/Source` 顶层与 `Runtime/` 子目录清单、Public/Private 依赖语义、胖核心 + 卫星模块 |
| 26 | https://www.deepwiki.com/godotengine/godot/7.1-gdscript-implementation | WebSearch 命中 | Godot `RenderingServerDefault` 线程模型、`FUNCRIDTEX` 宏派发机制（其中"RSG 是命名空间"的说法与源码不符，结论已按一手源码改写，过程见附录 B） |
| 27 | https://www.deepwiki.com/godotengine/godot/7.2-renderingdevice-and-drivers | WebSearch 命中 | Godot `RenderingDevice` / `RenderingDeviceDriver` / `RenderingContextDriver` / `RenderingDeviceGraph` 四层结构、staging buffer 池 |
| 28 | https://factory.ai/open-source-wikis/godot?page=drivers/graphics.md | WebSearch 命中 | Godot 各图形驱动文件布局（`rendering_context_driver_*` / `rendering_device_driver_*`）、VMA/MoltenVK 依赖 |
| 29 | https://modding.wiki/en/UnrealEngineModdingGuides/UnderstandingPakVsIoStore | WebSearch 命中 | Pak vs IoStore 对比：`.pak`/`.sig` vs `.utoc`/`.ucas`、Zen 资产序列化差异 |
| 30 | https://blog.bl4.dev / https://fffeiya.github.io/... （UE5 源码阅读笔记） | WebSearch 命中 | `Engine/Source` Runtime 模块分组（Core / CoreUObject / Engine / RenderCore / RHI / Slate / SlateCore / Chaos / PhysicsCore / NetCore / TraceLog / Projects …） |
| 31 | https://docs.godotengine.org/zh-cn/4.3/about/faq.html | WebSearch 命中官方 FAQ | **Godot 官方 FAQ 三条关键结论**：「Godot 不使用 ECS，而是依赖于继承」；「不强制用户实现 DOD」（DOD 只是缓存一致性优化，需每帧数万对象才有意义）；「不使用异常」的理由（游戏不应崩溃 + 异常增大二进制体积与编译时间） |
| 32 | https://godotengine.org/article/why-isnt-godot-ecs-based-game-engine/ | WebSearch 命中官方博客 | Godot 官方对「为何不是 ECS」的完整解释：性能需求极端时用 C++/GDExtension 处理热点，其余用 GDScript/C# |
| 33 | https://fastly.jsdelivr.net/gh/godotengine/godot-docs@master/tutorials/performance/using_servers.rst | curl（经 arch-classic 提示的 jsdelivr 通道） | **官方 using_servers 文档原文**：RID 定义（"opaque handles to the server implementation"）、**RID 引用不参与引用计数的重要警告**、"整套场景系统是可选的"、World3D/World2D 取 scenario/space/canvas RID 的官方表述 |
| 34 | https://fastly.jsdelivr.net/gh/godotengine/godot-docs@master/engine_details/architecture/godot_architecture_diagram.rst | curl（jsdelivr 通道） | 官方五层架构的 **rst 原文**（比 HTML 抽取更可靠的一手来源） |
| 35 | https://www.thecandidstartup.org/2023/04/03/nanite-graphics-pipeline.html | WebSearch 命中 | Nanite visibility buffer 逐位分配（30 depth / 27 cluster / 7 triangle）、软件光栅器 3× 加速、**材质深度缓冲 + G-Buffer 生成流程**（含 SIGGRAPH 2021 幻灯片编号引用） |
| 36 | https://onlinelibrary.wiley.com/doi/10.1111/cgf.70538 （CuRast, EG STAR 2025） | WebSearch 命中 | 学术侧对 Nanite visibility buffer 位分配的独立印证（"30 bit for depth values and a payload of 34 bit for the triangle ID… 27 bit for the cluster index and 7 bit to address the triangle"），并给出 1.34 亿 cluster × 128 三角形的寻址能力推导 |
| 37 | https://vrarwiki.com/index.php?title=Nanite | WebSearch 命中 | Nanite 参数汇总表（cluster ≤128 三角、每组 8~32、软硬光栅阈值 ~32 像素、14.4 bytes/三角、1600 万实例上限、Opaque+Masked、DX12 SM6），与 arch-classic 数据一致 |

---

## 1. Godot 4.x

### 1.1 目录结构

Godot 是**单仓库（monorepo）+ SCons 构建**。实测 `git ls-tree -r HEAD` 统计 14,434 个文件，顶层分布：

```
godot/
├── core/          472 文件   基础类型、对象模型、Variant、OS 抽象、IO
├── scene/         852 文件   场景树、节点、GUI、资源、动画
├── servers/       544 文件   渲染/物理/音频/导航/文本/XR 等"服务器"
├── editor/       1821 文件   编辑器 UI 与工具（TOOLS_ENABLED 才编译）
├── drivers/       240 文件   图形 API / 音频后端 / 输入硬件驱动
├── platform/      869 文件   各 OS 平台实现
├── modules/      3190 文件   可选功能模块（60 个）
├── thirdparty/   4891 文件   捆绑的第三方库
├── doc/           849 文件   XML 类参考（生成用）
├── tests/         238 文件   单元/集成测试
├── main/           13 文件   引擎入口与生命周期
├── misc/          405 文件   模板、脚本、发布工具
└── SConstruct / methods.py / platform_methods.py / version.py
```

**各目录职责边界**（官方文档第 5 条链接给出的五层划分，与实测目录一一对应）：

| 目录 | 职责 | 关键约束 |
|---|---|---|
| `core/` | Object / ClassDB / Variant / 容器 / 内存 / 文件 IO / OS 抽象 | 不依赖上层任何模块；是依赖树根 |
| `servers/` | 各子系统的**单例服务器**，引擎启动时初始化 | 只暴露 `RID` 句柄，不暴露内部对象指针 |
| `scene/` | 场景图与节点体系（面向游戏逻辑的最高层） | 通过 servers 的 RID 接口通信，不直接调驱动 |
| `drivers/` | 图形 API / 音频后端 / OS 接口的具体实现 | 对应 `RenderingDeviceDriver`、`RenderingContextDriver` 等抽象 |
| `platform/` | 平台专属 OS / DisplayServer 实现（android / ios / linuxbsd / macos / visionos / web / windows） | 每平台有 `detect.py` 供 SCons 探测 |
| `editor/` | 编辑器 UI、Inspector、导入管线、脚本编辑器 | 整目录被 `#ifdef TOOLS_ENABLED` 包裹，导出模板不含 |
| `modules/` | 可选特性（gdscript / mono / jolt_physics / openxr / gltf …） | 通过 `config.py` 决定编译期启用 |
| `main/` | `main.cpp` / `main.h` / `performance.cpp` | 负责启动、关闭、主循环分发 |
| `tests/` | `core/ data/ python_build/ scene/ servers/ compatibility_test/` | 每个子目录对应被测子系统 |
| `thirdparty/` | 捆绑依赖 | 与系统版本解耦 |

**`servers/` 的 12 个子目录**（实测）：`audio/` `camera/` `debugger/` `display/` `movie_writer/` `navigation_2d/` `navigation_3d/` `physics_2d/` `physics_3d/` `rendering/` `text/` `xr/`

**`drivers/` 的 24 个子目录**（实测）：`accesskit/ alsa/ alsamidi/ apple/ apple_embedded/ backtrace/ coreaudio/ coremidi/ d3d12/ egl/ gl_context/ gles3/ metal/ png/ pulseaudio/ sdl/ unix/ vulkan/ wasapi/ windows/ winmidi/ xaudio2/`

**`servers/rendering/` 内部**（实测）：`dummy/` `environment/` `renderer_rd/` `storage/`（`storage/ltc/`）；顶层是 `rendering_server.h/cpp`、`rendering_server_default.h/cpp`、`rendering_device.h/cpp`、`rendering_device_driver.h`、`rendering_device_graph.h`、`rendering_context_driver.h`、`renderer_viewport.*`、`renderer_scene_cull.*`、`renderer_canvas_cull.*`、`renderer_compositor.*`、`renderer_geometry_instance.*`、`renderer_light_culler.*`、`cluster_builder_rd.*` 等。

**`renderer_rd/` 内部**（实测）：`effects/`（bokeh_dof / fsr / fsr2 / metal_fx / smaa / taa / tone_mapper / vrs / resolve …）、`environment/`（fog / gi / sky）、`forward_clustered/`（Forward+ 管线）、`forward_mobile/`（Mobile 管线）、`shaders/`、`spirv-reflect/`、`storage_rd/`。

**`scene/` 内部**（实测）：`2d/` `3d/` `animation/` `audio/` `debugger/` `gui/` `main/` `resources/` `theme/` + `register_scene_types.cpp` `property_utils.cpp` `scene_string_names.h`。

**平台/后端抽象方式**：Godot 不用「一套接口 + 平台 ifdef」，而是**接口继承链 + 编译期选型**：

- `RenderingDevice`（`servers/rendering/rendering_device.h`）→ 继承 `RenderingDeviceCommons`，提供 `texture_create()` / `texture_get_format()` / `draw_command_insert_label()` / `submit()` 等**跨 API 的统一 GPU 接口**；
- `RenderingDeviceDriver`（`rendering_device_driver.h`）是**驱动抽象基类**，具体实现为 `RenderingDeviceDriverVulkan`（`drivers/vulkan/rendering_device_driver_vulkan.h`）、`RenderingDeviceDriverD3D12`（`drivers/d3d12/rendering_device_driver_d3d12.h`）、`RenderingDeviceDriverMetal`（`drivers/metal/`）；
- `RenderingContextDriver`（`rendering_context_driver.h`）负责 surface / swapchain / 设备枚举，与资源创建解耦（`drivers/vulkan/rendering_context_driver_vulkan.h` 等）；
- `RenderingDeviceGraph`（`rendering_device_graph.h`）是**命令录制与屏障插入层**，在 API 调用之间自动推导资源状态转换；
- 旧版 GLES3 路径**不经过 RenderingDevice**，直接实现 `RendererCompositor` / `RendererCanvasRender` / `RendererSceneRender`（`drivers/gles3/rasterizer_canvas_gles3.h`），对应引擎的 "Compatibility" 渲染方法。

### 1.2 核心类清单

| 类名 | 头文件路径（实测） | 职责 |
|---|---|---|
| `Object` | `core/object/object.h` | 一切引擎对象的基类；`ClassDB` 注册、通知（notification）机制、`ObjectID` 标识 |
| `RefCounted` | `core/object/ref_counted.h` | 引用计数基类（`Resource` 的父类） |
| `Resource` | `core/io/resource.h` | 可加载/可保存的数据对象；`set_path()` / `get_path()` / `emit_changed()` / `take_over_path()` |
| `ClassDB` | `core/object/class_db.h` | **全局类注册表**：`HashMap<StringName, ClassInfo> classes`；`register_class<T>()`、`get_property_list()`、`get_method_list()` |
| `PropertyInfo` | `core/object/property_info.h` | 属性元数据（类型、hint、usage） |
| `MethodInfo` | `core/object/method_info.h` | 方法元数据 |
| `Variant` | `core/variant/variant.h` | 引擎通用动态类型；`variant_parser` / `variant_op` / `variant_construct` 等配套 |
| `Node` | `scene/main/node.h` | 场景节点基类，`GDCLASS(Node, Object)`；`_process` / `_physics_process` / `_enter_tree` / `_ready` 虚函数 |
| `SceneTree` | `scene/main/scene_tree.h` | 场景树与主循环；`edited_scene_root`、分组（group）、`ProcessGroup` 分批调度 |
| `Viewport` | `scene/main/viewport.h` | 视口；串联 2D 画布与 3D 世界 |
| `World3D` | `scene/resources/3d/world_3d.h:46-47` | 3D 世界资源（`GDCLASS(World3D, Resource)`）；`get_scenario()`（:76）取渲染 scenario RID、`get_space()`（:72）取物理 space RID、`get_direct_space_state()`（:93） |
| `World2D` | `scene/resources/world_2d.h:44-45` | 2D 世界资源；`get_canvas()`（:62）取画布 RID、`get_space()`（:68）取 2D 物理 space RID |
| `Window` | `scene/main/window.h` | 窗口节点 |
| `CanvasItem` | `scene/main/canvas_item.h` | 2D 绘制基类 |
| `RenderingServer` | `servers/rendering/rendering_server.h` | 渲染系统**公开 API**（纯虚接口），全部资源以 `RID` 句柄表达 |
| `RenderingServerDefault` | `servers/rendering/rendering_server_default.h` | RenderingServer 的默认实现 + 线程调度 |
| `RenderingServerGlobals` | `servers/rendering/rendering_server_globals.h:52-70` | 渲染子系统的全局单例集合（`utilities` / `light_storage` / `material_storage` / `mesh_storage` / `particles_storage` / `texture_storage` / `gi` / `fog` / `canvas` / `viewport` / `scene` / `rasterizer` / `canvas_render`），以宏 `#define RSG RenderingServerGlobals` 访问 |
| `RenderingDevice` | `servers/rendering/rendering_device.h` | 统一 GPU 接口层（Forward+/Mobile 后端） |
| `RenderingDeviceDriver` | `servers/rendering/rendering_device_driver.h` | 驱动抽象基类（Vulkan / D3D12 / Metal 各自继承） |
| `RenderingDeviceGraph` | `servers/rendering/rendering_device_graph.h` | 命令录制、资源状态与屏障推导 |
| `RenderingContextDriver` | `servers/rendering/rendering_context_driver.h` | 平台 surface / swapchain 抽象 |
| `PhysicsServer3D` | `servers/physics_3d/physics_server_3d.h` | 3D 物理服务器抽象（`shape_create()` / `space_create()` 等纯虚） |
| `PhysicsServer2D` | `servers/physics_2d/physics_server_2d.h` | 2D 物理服务器抽象 |
| `JoltPhysicsServer3D` | `modules/jolt_physics/jolt_physics_server_3d.h` | Jolt 后端实现 |
| `GodotPhysicsServer3D` | `modules/godot_physics_3d/godot_physics_server_3d.h` | GodotPhysics 内置后端实现 |
| `GDExtensionManager` | `core/extension/gdextension_manager.h` | GDExtension 动态库加载与管理 |
| `GDExtensionInterface` | `core/extension/gdextension_interface.h` / `.json` | GDExtension C ABI 定义（JSON 为唯一真相源） |
| `ResourceLoader` / `ResourceSaver` | `core/io/resource_loader.h` / `resource_saver.h` | 资源加载/保存（`.tres` / `.tscn` / `.res`） |
| `ResourceFormatText` | `scene/resources/resource_format_text.cpp` | 文本资源格式（`.tres` / `.tscn`）读写 |
| `ResourceFormatBinary` | `core/io/resource_format_binary.cpp` | 二进制资源格式（`.res` / `.scn`）读写 |
| `WorkerThreadPool` | `core/object/worker_thread_pool.h` | 通用线程池 |
| `MessageQueue` | `core/object/message_queue.h` | 延迟调用队列（`call_deferred`） |
| `ScriptInstance` / `ScriptLanguage` | `core/object/script_instance.h` / `script_language.h` | 脚本实例与脚本语言抽象 |
| `CommandQueueMT` | `core/templates/command_queue_mt.h` | 多线程命令队列（服务器线程模型的基础） |

### 1.3 核心机制实现说明

#### (1) 反射与类注册：ClassDB + GDCLASS

Godot 用**运行期自注册**而非编译期代码生成。核心在 `core/object/object.h:245` 的宏：

```cpp
#define GDCLASS(m_class, m_inherits) \
    GDSOFTCLASS(m_class, m_inherits) \
private: \
    void operator=(const m_class &p_rval) = delete; \
    friend class ::ClassDB; \
static GDType &get_gdtype_static_mutable() { ... memnew(GDType(&super_type::get_gdtype_static(), StringName(#m_class))); ... } \
    virtual const GDType &_get_typev() const override { return get_gdtype_static(); } \
    static const StringName &get_class_static() { return get_gdtype_static().get_name(); } \
    static void initialize_class() { ... m_inherits::initialize_class(); ... }
```

要点：
- `GDCLASS` 同时完成三件事：① 生成 `get_class_static()` 供 RTTI/类型比较用；② `friend class ::ClassDB` 授权 ClassDB 访问私有 `_bind_methods()`；③ `initialize_class()` 沿继承链**自底向上**初始化，保证父类先注册。
- 每个类在自己的 `_bind_methods()` 里用 `ClassDB::bind_method()` / `ADD_PROPERTY()` / `ADD_GROUP()` / `ADD_SIGNAL()` 宏把方法与属性登记进 ClassDB。宏定义见 `core/object/object.h:47-64`。
- `ClassDB` 内部用 `static HashMap<StringName, ClassInfo> classes`（`class_db.h:185`），`ClassInfo` 持有 `inherits_ptr` 指针形成继承链。
- 运行时查询接口：`get_class_list()`（:314）、`get_property_list()`（:460）、`get_method_list()`（:474）——编辑器 Inspector、GDScript 反射、`.tres` 序列化全部依赖这套数据。

**与 UE 的本质差异**：Godot 的反射是**运行期哈希表**（类自己注册自己），UE 的反射是**编译期生成 C++ 代码**（UHT 生成 `.generated.h`/`.gen.cpp`）。Godot 零构建期成本但无法做编译期优化；UE 有构建期成本但能生成类型安全的静态调用。

#### (2) 渲染：四层解耦 + RID 句柄 + 渲染线程

**RID 句柄模型**：`RenderingServer` 里所有资源都是 `RID`（`servers/rendering/rendering_server.h`），如 `virtual RID texture_2d_create(const Ref<Image> &p_image) = 0;`、`virtual RID shape_create() = 0;`。**服务器从不返回内部对象指针**，只给不透明句柄。这是 Godot 能把渲染/物理跑在独立线程的根本原因——调用方拿着 RID 完全无法触碰服务器内部状态。

官方文档对 RID 的定义（`tutorials/performance/using_servers.rst` 原文）："The key to using servers is understanding Resource ID (RID) objects. These are **opaque handles to the server implementation**. They are allocated and freed manually." 且 "anything that inherits `Resource` can be directly casted to an RID"（但并非所有 Resource 都含 RID，此时 RID 为空）。

官方同时给出一条**关键警告**（解释了 RID 与引用计数的边界）：
> "Resources are reference-counted (see `RefCounted`), and **references to a resource's RID are *not* counted** when determining whether the resource is still in use. Make sure to **keep a reference** to the resource outside the server. Otherwise, both the resource and its RID will be erased."

即：RID 本身不延长生命周期，跨服务器边界操作时必须由调用方额外持有 `Resource` 引用。文档还强调"**整套场景系统是可选的**"（"the whole scene system is *optional*"）——虽然编不掉，但可以完全绕过，这正是服务器架构的设计意图。

`World3D` / `World2D` 正是渲染与物理两个服务器的**交汇点**（实测源码印证官方描述）：
- `World3D`（`scene/resources/3d/world_3d.h:72,76,93`）：`get_scenario()` → 渲染 scenario RID；`get_space()` → 物理 space RID；`get_direct_space_state()` → 物理直接空间状态
- `World2D`（`scene/resources/world_2d.h:62,68`）：`get_canvas()` → 画布 RID；`get_space()` → 2D 物理 space RID

**线程模型**（`servers/rendering/rendering_server_default.h`）：
```cpp
class RenderingServerDefault : public RenderingServer {
    mutable CommandQueueMT command_queue;          // :80
    Thread::ID server_thread = Thread::MAIN_ID;    // :82
    bool create_thread = false;                    // :85
    void _thread_loop();                           // :89
#define ASYNC_COND_PUSH (Thread::get_caller_id() != server_thread)   // :117
```
派发通过 `server_wrap_mt_common.h` 里的宏族实现：
```cpp
#define FUNC0R(m_r, m_type) \
    virtual m_r m_type() override { \
        if (ASYNC_COND_PUSH_AND_RET) { \
            m_r ret; \
            command_queue.push_and_ret(server_name, &ServerName::m_type, &ret); ...
```
即：**若当前线程就是服务器线程则内联执行，否则压入 `CommandQueueMT` 队列**。`CommandQueueMT` 定义在 `core/templates/command_queue_mt.h`（标准实现是 `core/templates/command_queue.h`）。

**RenderingDevice 的线程守卫**（`servers/rendering/rendering_device.cpp:57`）：
```cpp
#define ERR_RENDER_THREAD_GUARD() ERR_FAIL_COND_MSG(render_thread_id != Thread::get_caller_id(), ERR_RENDER_THREAD_MSG);
```
所有 GPU 录制操作必须只在渲染线程调用。

**分层**（据 DeepWiki 与实测文件）：
```
场景层    SceneTree / Viewport / Node3D
   ↓ 只认 RID
公开 API  RenderingServer (纯虚接口)
   ↓
场景后端  RenderingServerDefault → RendererViewport → RendererSceneCull(3D) / RendererCanvasCull(2D)
   ↓
管线      RendererSceneRender (抽象) → RenderForwardClustered / RenderForwardMobile
   ↓
GPU 抽象  RenderingDevice → RenderingDeviceGraph(命令+屏障) → RenderingDeviceDriver
   ↓
具体 API  Vulkan / D3D12 / Metal
```
`renderer_rd/forward_clustered/` 即 Forward+（簇状前向 + SDFGI/SSAO/SSR），`renderer_rd/forward_mobile/` 即 Mobile 管线。

#### (3) 场景管理：SceneTree + Viewport 树 + World3D

- `SceneTree`（`scene/main/scene_tree.h`）继承 `MainLoop`，持有 `edited_scene_root`（:172）、`group_map`（:150）、`ProcessGroup` 机制。
- **分组调度优化**：`ProcessGroup` 把需要 `_process` / `_physics_process` 的节点分桶（`physics_nodes` / `nodes` 两个数组，:105-106），用 `PagedAllocator<ProcessGroup, true>`（:118）分页分配以提升缓存命中率；`process_groups_dirty` 标记需重建分组。`scene_tree_fti.h/cpp` 负责「Fast Type Instantiation」——按类型批量实例化节点以跳过逐节点实例化。
- `Viewport`（`scene/main/viewport.h`）持有 `World3D` / `World2D` 资源引用，是 2D 画布与 3D 场景的汇合点。
- 可见性剔除在 `RendererSceneCull`（`servers/rendering/renderer_scene_cull.h`）内，用 `AABB` 做视锥剔除；`renderer_scene_occlusion_cull.*` 提供遮挡剔除。

#### (4) 物理：服务器抽象 + 可插拔后端

`PhysicsServer3D`（`servers/physics_3d/physics_server_3d.h`）是**纯虚接口**：
```cpp
class PhysicsServer3D : public Object {
    virtual RID world_boundary_shape_create() = 0;
    virtual RID sphere_shape_create() = 0;
    virtual RID box_shape_create() = 0;
    virtual RID space_create() = 0;
    virtual void space_set_active(RID p_space, bool p_active) = 0;
    virtual PhysicsDirectSpaceState3D *space_get_direct_state(RID p_space) = 0;
    ...
};
```
**两个真实后端并存**（实测模块）：
- `modules/godot_physics_3d/` — Godot 自研物理（`godot_body_3d.*`、`godot_area_3d.*`、`godot_broad_phase_3d_bvh.*`、`gjk_epa.*`、`godot_collision_solver_3d.*`）
- `modules/jolt_physics/` — Jolt 物理（`jolt_physics_server_3d.*`、`jolt_body_3d.*`、`joints/jolt_hinge_joint_3d.*` 等 6 种 joint）

服务器侧还有 `physics_server_3d_manager.h`（后端选择）、`physics_server_3d_wrap_mt.h`（多线程包装，与渲染同构）、`physics_server_3d_extension.h`（GDExtension 扩展点）。2D 侧对称：`servers/physics_2d/physics_server_2d.h` + `modules/godot_physics_2d/`。

#### (5) 资源与序列化

- **格式**：`ResourceFormatText`（`scene/resources/resource_format_text.cpp`）处理 `.tres`（资源）/`.tscn`（场景）；`ResourceFormatBinary`（`core/io/resource_format_binary.cpp`）处理 `.res`/`.scn`。
- **导入管线**：源文件（png / gltf / fbx / obj / wav…）→ `editor/import/3d/` 下的 `ResourceImporter`（如 `resource_importer_scene.cpp`、`editor_import_collada.cpp`）→ 生成中间产物到项目 `.godot/imported/` → 运行时由 `ResourceLoader` 加载。`editor/import/3d/post_import_plugin_*.cpp` 提供导入后处理钩子。
- `Resource` 的 `set_path()` / `_take_over_path()`（`core/io/resource.h:113-114`）与 `ResourceCache` 配合实现「路径唯一性 + 引用缓存」。
- `Resource` 继承 `RefCounted`（:52-53），`emit_changed()` 通知依赖者重载。

#### (6) 脚本与扩展

- **GDScript**（`modules/gdscript/`，含 `editor/` `language_server/` `doc_classes/` `tests/`）与 C#（`modules/mono/`）都实现 `ScriptLanguage`（`core/object/script_language.h`），通过 `ScriptInstance` 挂到对象上。
- **GDExtension** 是 C ABI 扩展机制。`core/extension/gdextension_interface.json` 是**唯一真相源**（官方文档第 7/20 条），三段结构：`header`（`_copyright` / `$schema` / `format_version`）、`types`（每个类型有 `kind`：enum / handle / alias / struct / function）、`interface`（所有通过 `GDExtensionInterfaceGetProcAddress` 加载的函数指针，每项带 `since` 版本号）。
  - 导出命令：`godot --headless --dump-gdextension-interface-json` / `--dump-gdextension-interface` / `--dump-extension-api`。
  - 引擎侧实现：`gdextension_manager.h`（加载）、`gdextension_library_loader.h`（动态库）、`gdextension_interface_header_generator.h`（生成 C 头）、`make_interface_dumper.py`。
  - 初始化分 4 级：`MODULE_INITIALIZATION_LEVEL_CORE` → `SERVERS` → `SCENE` → `EDITOR`。

---

## 2. Unreal Engine 5

### 2.1 目录结构

UE5 源码 EULA 门控（GitHub 第 8 条链接确认），无法直读；但官方文档与社区分析一致给出如下结构：

```
UnrealEngine/
├── Engine/
│   ├── Source/
│   │   ├── Runtime/     游戏运行时必需模块
│   │   │   ├── Core/            TArray/TMap/FString/FName、日志、平台抽象（几乎零依赖，是依赖树根）
│   │   │   ├── CoreUObject/      UObject/UClass/反射/序列化/GC
│   │   │   ├── Engine/           UWorld、AActor、UActorComponent、ULevel、GameFramework/*
│   │   │   ├── RenderCore/       渲染线程基础设施 + RDG（Public/RenderGraphBuilder.h）
│   │   │   ├── RHI/              图形 API 抽象（FDynamicRHI、FRHICommandList*）
│   │   │   ├── Renderer/         具体渲染通路；Private/Nanite/、Private/Lumen/
│   │   │   ├── Rendering/        NaniteResources.h 等共享渲染资源定义
│   │   │   ├── Slate/ SlateCore/  跨平台 UI 框架
│   │   │   ├── PhysicsCore/      物理抽象与基础类型
│   │   │   ├── Chaos/ ChaosCore/ Chaos 物理求解器
│   │   │   ├── NetCore/          网络底层公共件
│   │   │   ├── TraceLog/         追踪与事件记录
│   │   │   ├── Projects/         .uproject / 插件描述文件与加载
│   │   │   ├── InputCore/        输入核心抽象
│   │   │   └── AssetRegistry/    资产注册表
│   │   ├── Editor/       编辑器专用（UnrealEd、各资产编辑器、蓝图编辑器）
│   │   ├── Developer/    开发期工具（DDC、性能分析、源控、Cook 辅助）
│   │   ├── Programs/     独立可执行工具：UnrealBuildTool / UnrealHeaderTool / ShaderCompileWorker / UnrealPak / Insights
│   │   └── ThirdParty/   第三方库接入
│   ├── Shaders/         .usf/.ush；Private/Nanite/ 存放 Nanite 着色器
│   ├── Plugins/         插件（.uplugin + Source/ 模块）
│   ├── Config/          BaseEngine.ini 等
│   └── Build/           Build.version、BatchFiles
```

**关键组织原则**（据第 25、30 条链接）：目录**不等于**模块——一个目录下可能有多个 `.Build.cs`，一个模块也可能横跨多目录。**权威的模块定义永远是 `.Build.cs` 文件**。

**模块内部标准结构**（官方第 13 条链接）：
```
[ModuleName]/
├── [ModuleName].Build.cs      # 必须位于模块根目录
├── Public/                    # 对外接口；其他模块可 #include
├── Private/                   # 内部实现；仅本模块可 include
│   └── [ModuleName]Module.cpp # StartupModule / ShutdownModule
└── Resources/                 # 资源
```

**`.Build.cs` 机制**：
```csharp
using UnrealBuildTool;
public class ModuleTest : ModuleRules {
    public ModuleTest(ReadOnlyTargetRules Target) : base(Target) {
        PrivateDependencyModuleNames.AddRange(new string[] { "Core" });
    }
}
```
- `PublicDependencyModuleNames` —— **传递性**依赖；依赖你的模块会自动获得对该模块 Public 头的访问权。
- `PrivateDependencyModuleNames` —— **实现细节**依赖，不外传。
- 官方建议：「尽可能首选私有依赖性，因为可以缩短项目的编译时间」；可用前向声明把很多依赖降为私有。

**渲染器组织**：据第 21、22 条链接，Nanite 的代码物理分布在三处：
- `Engine/Source/Runtime/Renderer/Private/Nanite/` — C++ 编排（render graph 集成、缓冲分配、draw list 构建、可见性查询）
- `Engine/Shaders/Private/Nanite/` — GPU 着色器（剔除、光栅化、层级遍历、数据转码）
- `Engine/Source/Runtime/Rendering/NaniteResources.h` — 持久 GPU 缓冲布局（cluster 数据、层级页、impostor 结构）

Lumen 对应 `Renderer/Private/Lumen/`。这与 Godot 把渲染后端集中在 `servers/rendering/renderer_rd/` 形成鲜明对比——UE5 的渲染是「模块化拆分 + 跨目录共享资源头」。

### 2.2 核心类清单

| 类名 | 头文件路径 / 模块 | 职责 |
|---|---|---|
| `UObject` | `CoreUObject` — `Runtime/CoreUObject/Public/UObject/Object.h` | 一切可反射对象的基类；40 字节头（VTable / ObjectFlags / InternalIndex / ClassPrivate / NamePrivate / OuterPrivate） |
| `UClass` | `CoreUObject` | 运行时类型描述符，继承 `UStruct`；持有 CDO |
| `UStruct` | `CoreUObject` | 含 `Super` / `ChildProperties`（FProperty 链表）/ `PropertiesSize` |
| `FProperty` | `CoreUObject` | 反射属性节点：`NamePrivate` / `Offset_Internal` / `ElementSize` / 类型数据 |
| `AActor` | `Engine` — `Runtime/Engine/Classes/GameFramework/Actor.h` | 场景中所有对象的基类；世界位置、组件挂载 |
| `APawn` / `ACharacter` | `Engine` | 可被控制器占有 / 人形骨架与移动 |
| `UActorComponent` | `Engine` | 组件模式核心（`CreateDefaultSubobject`） |
| `UWorld` | `Engine` | 世界实例；持有 `PersistentLevel`、流送状态、Subsystem 集合 |
| `ULevel` | `Engine` | 关卡数据（Actor 集合） |
| `FWorldContext` | `Engine` | 引擎级世界上下文（编辑器/PIE/游戏多世界并存时的区分） |
| `FWorldPartition` | `Engine` | 世界分区系统；网格单元（Grid Cell）流送、Data Layer、HLOD |
| `UWorldPartitionSubsystem` | `Engine` | World Partition 的 Subsystem 入口 |
| `FRDGBuilder` | `RenderCore` — `Public/RenderGraphBuilder.h` | 渲染依赖图构建器；`AddPass()` / `CreateTexture()` / `CreateBuffer()` / `Execute()` |
| `FRDGPass` / `FRDGPassRef` | `RenderCore` | 图中的 pass 节点（`FRDGPassRef` 为引用计数句柄） |
| `FRDGBlackboard` | `RenderCore` | 图的全局黑板（跨 pass 共享数据） |
| `FDynamicRHI` | `RHI` — `Runtime/RHI/` | **动态绑定**的 RHI 实现接口（D3D12 / Vulkan / Metal 各自实现） |
| `FRHICommandList` / `FRHICommandListImmediate` | `RHI` | RHI 命令列表；Immediate 版立即执行、不可并行 |
| `FRHICommandListExecutor` / `GRHICommandList` | `RHI` | 命令列表执行器，把 Renderer 的中间指令派发到 RHI 线程 / 工作线程 |
| `FRHIResource` / `FRHITexture` / `FRHIBuffer` | `RHI` | RHI 资源基类与具体类型 |
| `FShader` / `FComputeShader` / `FPixelShader` | `RHI` | 着色器对象（`FGraphicsShader` 等） |
| `FGraphicsPipelineState` | `RHI` | 图形管线状态 |
| `FDefaultModuleImpl` | `Projects` | 通用模块实现（`IMPLEMENT_MODULE` 的默认形态） |
| `FModuleManager` | `Projects` | 动态模块加载（`FModuleManager::LoadModule`） |
| `IAssetRegistry` | `AssetRegistry` | 资产查询接口（`ScanPathsSynchronous` 等） |

**UObject 运行时布局**（第 18 条链接，BL4 逆向指南，字段偏移为实测）：

```
Offset  Size  Field          Purpose
0x00    8     VTable
0x08    4     ObjectFlags    RF_Transient / RF_Public 等
0x0C    4     InternalIndex  在全局对象数组中的位置
0x10    8     ClassPrivate   指向本对象的 UClass
0x18    8     NamePrivate    FName（8 字节：低 32 位 ComparisonIndex + 高 32 位 Number）
0x20    8     OuterPrivate   父对象（package / owner）
```
`FName` 是 8 字节：低 32 位是全局名字池 `GNames` 索引，高 32 位是实例后缀（如 `Actor_5`）。所有 UObject 存在分块的 `GUObjectArray` 中，每块约 65536 项，每项 `FUObjectItem` 24 字节（对象指针 8 + Flags 4 + ClusterRootIndex 4 + SerialNumber 4）。

### 2.3 核心机制实现说明

#### (1) 渲染：RHI → RenderCore(RDG) → Renderer 三层

**第三层 RHI**（`Runtime/RHI/`）：`FDynamicRHI` 是动态绑定的 RHI 实现接口。UE5 的 RHI 继承体系（据第 24 条链接）：

```
FDynamicRHI
├── FD3D12DynamicRHI / FVulkanDynamicRHI / FMetalDynamicRHI   （现代 API）
└── 传统 API（D3D11 / OpenGL）
    └── 额外继承 IRHICommandContextPSOFallback
        （用于统一 PSO 数据的处理，保证新旧 API 行为一致）
```
现代 API（DX12/Vulkan/Metal）只需继承 `FDynamicRHI`；传统 API 因不支持并行绘制，需额外继承 `IRHICommandContextPSOFallback`。

**RHI 选择时机**（第 23 条链接，官方论坛技术答复）：启动链为
```
FEngineLoop::PreInit → RHIInit → PlatformCreateDynamicRHI → LoadDynamicRHIModule
```
`LoadDynamicRHIModule` 位于 `RHI/Windows/WindowsDynamicRHI.cpp`。选择顺序：项目设置 `DefaultGraphicsRHI` → `TargetedRHIs` → 默认映射到 D3D12；命令行参数可强制覆盖；不支持则拒绝启动。

**命令执行**：顶层是 `FRHICommandListExecutor`，其单例 `GRHICommandList` 负责把 Renderer 层的 RHI 中间指令**转译或直接调用**到目标图形 API。派发可能在专用 **RHI 线程**执行，也可能在渲染线程或工作线程执行。现代多线程 API（D3D12/Metal/Vulkan）的命令列表会在多个工作线程上执行以降低驱动开销。

**第二层 RenderCore / RDG**（第 9、10 条链接，官方文档）：
`FRDGBuilder` 是渲染线程上的**命令列表 + 资源生命周期管理器**。官方明确的核心设计：

> "Instantiate an `FRDGBuilder` instance, create resources and add passes to set up the graph. Then, call `FRDGBuilder::Execute` to compile and execute the graph."
> "These methods only allocate descriptors. The underlying RHI resources are allocated later during execution."

**双时间线**：
- **Setup 时间线** — 构图。创建 RDG 资源（只分配描述符）、`AddPass()` 添加 pass lambda、配置渲染管线分支。**所有 RHI 命令被延迟进 pass lambda**。
- **Execute 时间线** — RDG 先做一次**编译**（推导 pass 间依赖、裁剪无用 pass、计算瞬态资源生命周期、插入资源屏障与转换、处理异步计算依赖、合并相同渲染目标的 pass），然后**并行**执行各 pass 的 lambda。

```cpp
FRDGBuilder GraphBuilder(RHICmdList);
FMyShaderCS::FParameters* PassParameters = GraphBuilder.AllocParameters();
PassParameters->SceneColorTexture = SceneColor;
PassParameters->SceneColorOutput = GraphBuilder.CreateUAV(NewSceneColor);
GraphBuilder.AddPass(
    RDG_EVENT_NAME("MyShader %d%d", View.ViewRect.Width(), View.ViewRect.Height()),
    PassParameters,
    ERDGPassFlags::Compute,
    [PassParameters, ComputeShader, GroupCount](FRHIComputeCommandList& RHICmdList) {
        FComputeShaderUtils::Dispatch(RHICmdList, ComputeShader, PassParameters, GroupCount);
    });
GraphBuilder.Execute();
```

关键约束：官方明确 "Avoid using `FRHICommandListImmediate` unless strictly necessary, as it disqualifies the pass from executing in parallel." 且 "The graph builder API is single-threaded and only one instance may be instantiated at a time."

**第一层 Renderer**（`Runtime/Renderer/`）：具体的渲染通路。Nanite 用 GPU 驱动管线，四种 `EPipeline`（据第 21 条链接）：`Primary`（主场景）、`Shadows`（深度）、`Lumen`（深度，供 surface cache / 光追）、`HitProxy`（编辑器拾取）。`FCullingContext`（`NaniteCullRaster.h`）持有 20+ 个 RDG buffer 引用，构成完整剔除数据流；`FConfiguration` 位域含 `bTwoPassOcclusion`（HZB 两阶段遮挡剔除）、`bUpdateStreaming`、`bProgrammableRaster`（软件光栅）等 12 个标志位。

Nanite 的几何管线要点（据 SIGGRAPH 2021《A Deep Dive into Nanite Virtualized Geometry》与多源交叉验证）：
- **Cluster 划分**：导入时切成 ≤128 三角形的 cluster；每 8~32 个 cluster 成组，简化到约一半三角形后重新切成 4~16 个新 cluster，逐层重复。
- **DAG 而非树**：decimation 保留每组共享边界不变，且分组在每层**重新洗牌**，使每条边最终都会在某层被简化 → 产生有向无环图，避免相邻 cluster 不同 LOD 间的裂缝，实现连续 LOD 而非离散跳变。
- **两 pass HZB 遮挡剔除**：pass1 用**上一帧** Hi-Z 测（画出上帧可见的）并构建本帧 Hi-Z；pass2 用新 Hi-Z 重测上帧被遮挡的。
- **软/硬光栅分流**：三角形 >约 32 像素走 GPU 硬件光栅器；小的走 **compute shader 软件光栅器**（一个 cluster 的 128 个三角形并行处理，实测平均快约 3 倍）。两个光栅器遵循同一套光栅化规范，故跨光栅器无裂缝。
- **Visibility Buffer 布局**：每像素一个 **64-bit** 值，用 **64-bit atomic** 保证最近面获胜，位分配为
  ```
  30 bits  depth
  27 bits  visible cluster index   → 可寻址 1.34 亿个 cluster
   7 bits  triangle index         → 每 cluster 128 个三角形
  ```
  **位宽由寻址空间倒推，而非经验取值**：30/34 的切分使 cluster 索引空间恰为 2^27 ≈ 1.34 亿，同时 7 位恰好覆盖 2^7 = 128 个三角形（与每 cluster 的 128 三角形上限一致）；学术侧（CuRast, EG STAR 2025）给出了同一推导。三源独立交叉验证（SIGGRAPH 2021 原文、逐位分析、参数汇总表）结论一致。
- **材质延后求值**：几何全部完成后单独一趟读 visibility buffer 重建属性，只对可见像素算材质 → **着色成本与三角形复杂度解耦**。
- **体积**：Epic "Valley of the Ancients" 样本 **14.4 bytes/输入三角形**，比带 4 级 LOD 的标准 static mesh 小约 **7.6 倍**。
- **限制**：不支持半透明/体积材质（仅 Opaque 与 Masked 混合模式）；单场景上限 1600 万 Nanite 实例；需 DX12 SM6；UE5.5 起实验性支持骨骼网格。

#### (2) 场景管理：UWorld / ULevel / World Partition / GC

- **UWorld / ULevel**：UE5 仍保留 `ULevel` 概念，但 World Partition 下大量 Actor 存储为**独立外部包**（One File Per Actor），运行时动态加载进 Level。
- **World Partition**（第 14 条链接，官方文档）：把世界存为「**单一持久关卡 + 网格单元（grid cells）**」，引擎按与流送源的距离自动加载/卸载单元。配套三大特性：**One File Per Actor**（每 Actor 一文件，消解多人协作冲突）、**Data Layers**（按逻辑开关分组内容）、**Hierarchical LOD**。网格参数写在 ini：
  ```ini
  [/Script/Engine.WorldPartitionRuntimeSpatialHash]
  Grids=(GridName="MainGrid",CellSize=3200,LoadingRange=25600.000000,...)
  ```
  转换既有关卡用 `WorldPartitionConvertCommandlet`（`-run=WorldPartitionConvertCommandlet`）。
  **流送源优先级**：多源相交时取最高优先级；**Target State 取最高数值**（Activated > Loaded）。
  **四类必须成为流送源的 actor**（易漏）：过场动画相机 / 快速旅行预加载器（传送前 2~3 秒激活）/ AI streamer / 多人队友（服务器上**所有玩家**都要成为流送源）。
  **Grid Promotion**：比一个 cell 大、或与其它 cell 有**硬引用**的对象会被提升到更粗的网格层，对作者不可见但会**静默膨胀加载集** → 跨 cell 引用务必用软引用（`TSoftObjectPtr` / `TSoftClassPtr`）。
- **GC**（第 15、16 条链接，官方知识库 + 官方文档）：标准 **Mark & Sweep**。
  1. **Mark unreachable** — 所有 UObject 先标记为不可达。
  2. **Reachability analysis** — 从根集合（`UEngine`、已加载 World/Package、`AddToRoot()` 的对象、`FGCObject` / `TStrongObjectPtr` 报告者）出发，沿**反射可见的 UPROPERTY 引用**递归标记可达。
  3. **Destroy garbage** — 调 `BeginDestroy` → `FinishDestroy` → 回收内存。
  - **Cluster 优化**：把生命周期一致的对象打包成簇，遍历到簇根即判定整簇可达/不可达。手动创建示例（官方论坛给出）：
    ```cpp
    const int32 ClusterIndex = GUObjectClusters.AllocateCluster(InternalIndex);
    FUObjectCluster& Cluster = GUObjectClusters[ClusterIndex];
    this->SetClusterIndex(ClusterIndex);
    this->SetFlags(EInternalObjectFlags::ClusterRoot);
    ```
    或直接 `ParentObject->CreateCluster();`（要求子类 `CanBeInCluster()` 返回 true）。
  - **增量可达性分析**（UE5.4+ 实验特性）：把标记阶段分散到多帧，启用 `gc.AllowIncrementalReachability=1` 与 `gc.AllowIncrementalGather=1`。**前提是把所有 UPROPERTY 换成 `TObjectPtr`**，因为增量标记依赖写屏障。

#### (3) 物理：Chaos 取代 PhysX

- 官方确认（第 12 条链接，Epic 技术博客，Michael Lentine）：**"With Unreal Engine 5.0, our internal physics engine Chaos ships as the default physics engine."**
- **所有权结构的变化**（据社区分析）：PhysX 时代是 NVIDIA 闭源库深度集成在 `FPhysScene` / `UBodyInstance` 里；Chaos 是 Epic 自研求解器，几何、刚体、约束、求解器都收进引擎自己的模块（`Chaos/`、`ChaosCore/`），可调试、可扩展。`FPhysScene_Chaos` 是其场景实现。
- **线程模型变化**：UE5 引入 `Tick Async Physics`（项目设置），物理在**独立线程**上以**固定频率**运行以提高确定性，网络物理同步更容易；代价是输入到物理响应之间有延迟回调。
- **能力差异**（官方论坛 Epic 员工回复）：Chaos **未实现** `FContactModifyCallback`（源码中以 `#if !WITH_CHAOS` 标注），也缺少 PhysX 的部分高级查询 flag（`ePOSITION`/`eNORMAL`/`eDISTANCE`/`eUV`/`eMTD`/`eFACE_INDEX`、多命中、backface 查询等）。
- **性能数据**（官方博客实测）：Tumbler 测试 sweep 从 11.64ms（PhysX）降到 4.42ms（Chaos）；Landscape 测试 sweep 从 0.55ms 降到 0.13ms。但刚体求解 Landscape 测试从 10.82ms 降到 5.34ms，Ragdoll 从 5.30ms 升到 8.18ms。
- 官方文档指出：**PhysX 在 UE5 中仍保留但将在后续版本删除**；`PhysXVehicles` 等旧模块通过切换模块依赖保留。

#### (4) 资源与序列化：.uasset / .umap → AssetRegistry → Pak / IoStore

- **内存格式**：UObject 序列化后进入 **Zen Package**（第 18 条链接实测结构）：
  ```
  Zen Package
  ├── Summary      包元数据
  ├── Name Map     本包内局部 FName
  ├── Import Map   本包引用的外部资产
  ├── Export Map   本包定义的对象
  └── Export Data  序列化的属性数据
  ```
  **关键机制**：UE5 使用「**unversioned 序列化**」——属性只写裸值不写名字和类型，按顺序排列。因此解析需要外部 schema，即 `.usmap` 文件（反射系统的 dump，含类名/属性名/类型/偏移）。这是 UE5 资产格式最重要的架构选择。
- **容器格式**（第 17、29 条链接，官方论坛 UE_Seb.Tho 说明 + modding.wiki）：
  - 旧格式 `.pak`（+可选 `.sig`）：单一归档，含 `.uasset`/`.uexp` 对 + 索引；用 AES 加密。
  - **IoStore（UE5 默认）**：拆成 **`.utoc`（Table Of Contents，头/索引）** + **`.ucas`（Container Archive，实际数据）**。官方原话："It was specifically built for the new loading code and stores the asset information/headers separately from the asset data."
  - **虚拟文件系统**按**包路径**（如 `/Game/MyFolder/MyAsset`）寻址，而非原始文件名；`.uasset` 文件在 IoStore 下**不再存在**。
  - **AssetRegistry.bin** 独立文件：IoStore 容器没有目录结构可扫描，资产清单写入单独的 `AssetRegistry.bin` 并打进 pak；`ScanPathsSynchronous` 对 IoStore **无效**（且可能清空已从 AssetRegistry.bin 加载的资产）。
  - 官方建议："storing assets in pak files is not recommended anymore"。
- **AssetRegistry**：`IAssetRegistry` 提供运行时资产查询，是与容器格式解耦的查询层。

#### (5) 脚本与扩展

- **Blueprint VM**：蓝图编译为字节码，由 VM 在运行时/编译期执行；C++ 通过 `UFUNCTION(BlueprintCallable/BlueprintPure/BlueprintImplementableEvent/BlueprintNativeEvent)` 暴露接口。`BlueprintNativeEvent` 有 C++ `_Implementation` 默认实现，蓝图可覆写。
- **C++ 插件**：`Engine/Plugins/<Name>/` 下含 `<Name>.uplugin`（描述文件：模块列表、加载阶段、平台兼容性）+ `Source/<Name>/<Name>.Build.cs` + `Private/Public/`。
- **Python**：官方提供 Unreal Engine Python API（`dev.epicgames.com/documentation/unreal-engine/PythonAPI`），通过 `unreal.MyActorComponent()` 等访问反射 API，`set_editor_property()` 是推荐写法（会触发属性变更事件与脏标记）。
- **模块加载**：`IMPLEMENT_MODULE` 宏 + `FDefaultModuleImpl`；`FModuleManager::LoadModule("ShaderPlugin")` 动态加载。模块在 `.uproject` / `.uplugin` 中声明加载阶段与类型（`Runtime` / `Editor` / `Developer` / `External`）。

#### (6) 反射与序列化：UHT 代码生成

UHT 在 C++ 编译前扫描头文件，生成 `.generated.h`（声明）与 `.gen.cpp`（实现反射数据表）。规则（据第 19 条链接，官方文档核对）：

- `.generated.h` **必须放在头文件最后 include**；`GENERATED_BODY()` **必须是类体的第一行**。违反会产生 UHT 错误且**没有编译器诊断**。
- 命名前缀由工具强制：`A`=可生成 Actor，`U`=其他 UObject 树，`F`=普通结构体，`E`=枚举，`I`=接口，`T`=模板，`S`=Slate 控件。把 Actor 命名为 `UFoo` 是 UHT 失败，不是 lint 警告。
- 头文件名约定为类名去前缀（`AActor` → `Actor.h`），但引擎不强制。
- **CDO（Class Default Object）**：每个类在启动时创建一个实例持有默认属性值；构造函数**先在 CDO 上跑**，所以构造函数里赋的值就是所有实例和蓝图子类的默认。非蓝图类的默认值来自构造函数，蓝图类从 `.uasset` 读取。
  ```cpp
  UMyActorComponent* CDO = GetMutableDefault<UMyActorComponent>();
  // 或
  UMyActorComponent* CDO = UMyActorComponent::StaticClass()->GetDefaultObject();
  ```
  **推论**：构造函数里不要访问 Engine / World（它们可能还没初始化），应放到 `BeginPlay()`。
- 反射提供的自动能力：零初始化、**引用自动置空**（Actor/Component 销毁时，反射可见的 UPROPERTY 指针与容器内指针被置空）、自动序列化、CDO 默认值传播、编辑器/蓝图暴露、RTTI（`Cast<T>` / `IsA`）、网络复制。
  **关键推论**：「data the reflection system cannot see does not participate in any of this, and nothing warns you.」——非 UPROPERTY 的裸 `UObject*` 成员既不保活也不置空。
- 指针类型选型（第 19 条链接表格）：
  | 类型 | GC 保活 | 序列化 | 用途 |
  |---|---|---|---|
  | `UPROPERTY() TObjectPtr<T>` | 是 | 是 | UObject 拥有的成员（UE5 默认） |
  | `TWeakObjectPtr<T>` | 否（自动置空） | 可选 | 可能独立消失的引用 |
  | `TSoftObjectPtr<T>` | 否（存路径） | 是 | 按需加载的资产 |
  | `TStrongObjectPtr<T>` | 是（引用计数） | 否 | 非 UObject 类的强引用 |
  | 裸 `T*` | 否 | 否 | 局部变量 / 参数 |

---

## 3. 两者架构对比

| 维度 | Godot 4.x | Unreal Engine 5 | 差异原因 |
|---|---|---|---|
| **对象基类** | `Object`（`core/object/object.h`），手动引用计数由 `RefCounted` 提供 | `UObject`，GC 托管，40 字节固定头 | Godot 追求轻量与确定性；UE 需要蓝图/编辑器/Python 统一反射 + 弱引用 + 网络复制 |
| **反射机制** | **运行期自注册**：`GDCLASS` 宏 + `ClassDB` 哈希表 | **编译期代码生成**：UHT → `.generated.h` / `.gen.cpp` | Godot 用运行期哈希换取零构建期成本与动态扩展；UE 用构建期生成换取类型安全与静态调用 |
| **属性声明** | `_bind_methods()` 里用 `ADD_PROPERTY` 宏登记 | 头文件里用 `UPROPERTY(...)` 标注，UHT 解析 | UE 的标注式更贴近声明处；Godot 的集中式便于运行期动态注册（GDExtension） |
| **资源/场景句柄** | **`RID` 不透明句柄**（服务器不返回内部指针） | **真实 UObject 指针** + `FName` + `Outer` 链 | RID 让 Godot 渲染/物理能安全跑在独立线程；UE 靠 GC 与线程断言管理并发 |
| **线程模型（渲染）** | `RenderingServerDefault` + `CommandQueueMT`，非服务器线程则入队；`ERR_RENDER_THREAD_GUARD` 强制 | Game Thread → Render Thread → RHI Thread；`FRHICommandListExecutor` + `GRHICommandList` | UE 有 RDG 可并行执行 pass，需要独立 RHI 线程；Godot 单渲染线程模型更简单 |
| **渲染分层** | `RenderingServer` → `RenderingServerDefault` → `RendererSceneRender` → `RenderingDevice` → `RenderingDeviceDriver` → Vulkan/D3D12/Metal | `RHI`（`FDynamicRHI`）→ `RenderCore`（`FRDGBuilder`）→ `Renderer` | Godot 用「类继承 + 接口纯虚」；UE 用「模块依赖 + RDG 资源图」 |
| **渲染命令组织** | `RenderingDeviceGraph` 自动插屏障、资源状态转换 | **RDG**：声明式 pass 图，编译期裁剪 pass、推导依赖、合并同目标 pass、并行执行 | RDG 是 UE5 的核心创新：把「写渲染代码」变成「描述图」 |
| **光栅化策略** | 前向（Forward+ 簇状 / Mobile），不用延迟渲染 | 延迟渲染为主（Deferred Shading），Nanite 走 Visibility Buffer + 延迟材质求值 | Godot 内存友好、VR 友好、MSAA 便宜；UE 目标 AAA 画质与全局光照 |
| **Nanite / Lumen** | 无对等物（Godot 无虚拟化几何） | `Renderer/Private/Nanite/` + `Shaders/Private/Nanite/` + `Rendering/NaniteResources.h`；Lumen 走 `Renderer/Private/Lumen/` | UE5 主打 AAA 级几何与全局光照 |
| **物理** | `PhysicsServer3D` 抽象 + **GodotPhysics / Jolt 两个可切换后端**（模块级） | **Chaos 自研取代 PhysX**（`Chaos/` `ChaosCore/`），独立物理线程 + 固定步长 | Godot 让用户选后端（GPL 顾虑）；UE 需要 LWC 精度、网络化物理、破坏系统，必须自研 |
| **物理抽象位置** | 服务器层（`servers/physics_3d/`），与渲染同构 | `PhysicsCore`（抽象） + `Chaos`（实现），在 Runtime 层 | 结构相同：抽象层 + 可替换后端 |
| **世界/大地图** | `SceneTree` + `Viewport` + `World3D`；流送靠开发者手动 | `UWorld` / `ULevel` / `FWorldContext` + **World Partition**（网格单元自动流送 + Data Layer + HLOD + OFPA） | UE5 主打开放世界；Godot 4 无内置大世界分区系统 |
| **GC** | 引用计数（`RefCounted`），无追踪式 GC | **Mark & Sweep**（可达性分析 → 销毁），**Cluster** 优化，UE5.4+ 增量可达性分析 | UE 的 UObject 图太动态，纯引用计数无法处理环 |
| **序列化格式** | `.tres`（文本）/`.res`（二进制）/`.tscn`（场景）；`ResourceFormatText` / `ResourceFormatBinary` | `.uasset` / `.umap` → **Zen Package**（unversioned 序列化）+ `.usmap` schema | UE 的 unversioned 序列化支持任意热重载与跨版本兼容，但需外部 schema |
| **打包容器** | 导出为可执行文件 / PCK（`platform/*/export/`） | **IoStore**（`.utoc` + `.ucas`）为 UE5 默认，旧 `.pak` 保留但"不再推荐" | UE 需要按包路径虚拟寻址 + 流式加载 + 独立 AssetRegistry.bin |
| **模块化单位** | `modules/<name>/`（SCons `config.py` 决定启用） | `.Build.cs`（UBT 编译）+ `.uplugin`/`.uproject`（运行时加载描述） | UE 的模块需支持动态加载与平台裁剪，UBT 提供依赖图与 IWYU |
| **依赖声明** | 目录间无显式依赖声明，SCons 按目录推断 | **显式**：`PublicDependencyModuleNames` / `PrivateDependencyModuleNames` | UE 需在编译期做链接裁剪和 PCH 优化 |
| **脚本** | GDScript（`modules/gdscript/`）、C#（`modules/mono/`）、`@tool` 注解 | Blueprint VM + C++ + Python（官方 API） | Godot 走「一门语言 + 嵌入脚本」；UE 走「可视化 + C++ 双极」 |
| **原生扩展 ABI** | **GDExtension**（C ABI，`gdextension_interface.json` 为唯一真相源，4 级初始化） | 插件走 C++ ABI（引擎与插件同编译器版本） | GDExtension 追求跨版本稳定与多语言绑定；UE 插件与引擎同构建，允许直接用 C++ |
| **编辑器形态** | `editor/` 目录（`#ifdef TOOLS_ENABLED`），与运行时同进程同语言 | `Editor/` + `Slate`/`SlateCore` + UMG，UMG 构建在 Slate 之上 | 两者都把编辑器与运行时分离，但 UE 用 Slate 独立支撑编辑器 UI |
| **平台抽象** | `platform/<os>/`（含 `detect.py`）+ `drivers/<api>/` 两级 | `Runtime/<Platform>/` + `Runtime/RHI/` + `Core` 平台抽象层 | Godot 把 OS 与 API 分两目录；UE 平台层在 Runtime 内并有专属 RHI 实现 |
| **第三人称视角** | 轻量、开源、可读性强、扩展点集中在 `modules/` | 体量巨大（Runtime 数十模块）、工业化程度高、生态成熟 | 目标用户不同：Godot 面向独立开发者与定制引擎，UE 面向 AAA 团队 |
| **API 稳定性** | GDExtension JSON 带 `since` 字段，承诺不向后不兼容变更 | UE 每次大版本有大量 C++ API 破坏性变更（需源码合并） | GDExtension 面向第三方二进制分发，UE 主要面向源码集成 |

---

## 4. 设计模式提炼

| 模式 | Godot 4.x 的实现 | UE5 的实现 | 说明 |
|---|---|---|---|
| **组件化 / Component 树** | `Node` 树（`scene/main/node.h`），`Node3D` / `Node2D` / `Control` 各自成树；功能拆成子节点而非继承 | `AActor` + `UActorComponent` 组合；`Actor` 只做位置与生命周期，行为全在 Component | 两者都是「组合优于继承」；UE 把 Transform 提到 Actor，Godot 用 `Node3D` 单独表达 |
| **ECS 混合** | 官方 FAQ 明确「**Godot 不使用 ECS，而是依赖于继承**」，也不强制 DOD；组合通过"带独立脚本的子节点"实现，可在运行时增删 | 同样非 ECS 为主，但 `Chaos` 内部用 `SpatialAccelerationStructure` 做宽相位；`MassEntity` 提供了 ECS 化的实验路径 | 二者都把 ECS 留给专用子系统，不作为主架构 |
| **抽象工厂 / 平台后端工厂** | `RenderingDeviceDriver` 抽象基类 + Vulkan/D3D12/Metal 各自实现；`PhysicsServer3D` + GodotPhysics/Jolt 双后端 | `FDynamicRHI` + `LoadDynamicRHIModule` 按配置动态挑选；`FDefaultModuleImpl` 工厂 | Godot 用「纯虚基类 + 编译期选型」；UE 用「动态加载 + 运行期选型」 |
| **Visitor** | `Object::accept_notification` / `_notification` 沿继承链下传（`object.h:228-236` 的 `_notification_forwardv` / `_notification_backwardv` 双向遍历） | 反射系统里的 `FProperty` 链表遍历；`Cast<T>` 的类型匹配遍历 | Godot 的通知双向遍历是 Visitor 的双向变体（既向下也向上） |
| **观察者 / 事件** | **信号（Signal）**：`Object::connect()` / `emit_signal()`，GDScript 原生 `signal` 关键字 | **委托（Delegate）**：`DECLARE_DYNAMIC_MULTICAST_DELEGATE`、`FDelegate` / `TFunction` | Godot 信号是语言级一等公民；UE 委托是 C++ 宏 + 反射类型 |
| **单例 / Service Locator** | `Engine::get_singleton()`、`RenderingServer::get_singleton()`、`ClassDB` 静态方法；`RSG::` 渲染子系统全局指针集合（实测为 `class RenderingServerGlobals` 的静态成员，宏 `#define RSG RenderingServerGlobals`，见 `servers/rendering/rendering_server_globals.h:52-70`） | `GEngine`、`GWorld`、`GUObjectArray`、`GRHICommandList`、`GConfig` 全局单例；`UWorldSubsystem` 体系提供受管的服务定位 | 两者都重度依赖全局单例；UE 额外用 Subsystem 提供生命周期受管的替代方案 |
| **对象池** | `PagedAllocator<ProcessGroup, true>`（按页分配 ProcessGroup）；`RenderingDevice` 的 staging buffer 池 | `FUObjectArray` 的 `FUObjectItem` 槽位复用；`FSceneInterface` 的渲染线程资源池 | UE 的 `GUObjectArray` 是分块 + 槽位复用的典型对象池 |
| **RAII** | Godot **刻意不用异常**（官方 FAQ："如果发生意外情况，Godot 将打印一条错误…但随后它将尝试尽可能优雅地恢复并继续运行"，且异常会显著增加二进制体积与编译时间），用 `memnew`/`memdelete` + `Ref<T>` 引用计数 + `MutexLock`/`SafeRefcount` 锁守卫 | `TSharedPtr` / `TUniquePtr` / `TWeakObjectPtr` / `FScopeLock` / `ON_SCOPE_EXIT`；`TStrongObjectPtr` 明确为 RAII 引用计数持有者 | Godot 的 RAII 主要用于锁与引用计数；UE 的 RAII 覆盖所有权语义 |
| **CRTP** | 不常用；Godot 更依赖 `Object::cast_to<T>` 与 `is_class_ptr` 虚函数 | `TTypeCompatibleBytes`、`TIsDerivedFrom` 等模板约束大量使用 | Godot 运行时 RTTI 走 `ObjectID` + `ClassDB`；UE 用编译期模板 + 运行时 `UClass` 双轨 |
| **命令模式 / 队列** | `CommandQueueMT`（`core/templates/command_queue_mt.h`）+ `MessageQueue`（`call_deferred`）+ `FUNC0R` 宏族 | `FRHICommandList` 系列（命令列表 = 延迟执行的命令录制）、`ENQUEUE_RENDER_COMMAND` | 两者的「命令列表」都是把主线程意图延迟到渲染线程执行 |
| **解释器 / VM** | GDScript 是**树遍历解释器**（`modules/gdscript/`）；`ScriptInstance` 接口让任意语言挂载 | Blueprint 编译为**字节码** + VM；Python 通过官方绑定 | Godot 解释执行 vs UE 编译执行 |
| **资源所有权 / 引用语义** | `Ref<T>` 智能引用 + `RefCounted`；资源按 `res://` 路径全局唯一（`ResourceCache`） | `UPROPERTY` 标记 + GC 图；`Outer` 链定义归属；`TStrongObjectPtr` 补齐非 UObject 场景 | Godot 的「路径即身份」很轻；UE 的「Outer 链 + GC 图」支持复杂生命周期 |
| **门面 / 外观** | `RenderingServer` 作为渲染子系统统一门面，屏蔽 RD / 兼容两种后端 | `FRDGBuilder` 作为渲染命令门面；`IAssetRegistry` 作为资产查询门面 | |
| **享元 / 池化资源** | `RID` 资源由服务器集中管理，主机侧只留轻量引用 | `FRHIResource` 引用计数 + RDG transient 资源帧内分配帧末释放 | |

**总结性观察**：
1. **两者的"单点真相源"不同**：Godot 是 `ClassDB`（运行期哈希表），UE 是 `UClass`（UHT 生成的运行时类型对象 + `FProperty` 链表）。这决定了后续一切：Godot 的类可热增删，UE 的类在编译期定型。
2. **并发策略不同**：Godot 用「RID 句柄 + 单服务器线程 + 命令队列」简单可靠地隔离并发；UE 用「多线程分层（Game / Render / RHI / Worker）+ RDG 声明式并行」追求极致并行度。
3. **性能取向不同**：Godot 的 RID 与服务器模式牺牲了一部分直接访问灵活性，换来极低的耦合与易读性；UE 的 `FProperty` 偏移式反射牺牲了可读性，换来零成本反射查询与热重载。
4. **可扩展性的载体不同**：Godot 是 GDExtension（稳定 C ABI，跨语言）；UE 是插件（`.uplugin` + C++ 模块，引擎同构建）。前者更适合第三方发行二进制插件，后者更适合深度定制引擎。

---

## 5. 参考链接清单

（已在第 0 节按「URL / 获取方式 / 关键信息」表格化列出，此处按主题归类索引）

**Godot 官方**
1. https://raw.githubusercontent.com/godotengine/godot/master/README.md
2. https://github.com/godotengine/godot/tree/master/servers
3. https://github.com/godotengine/godot/tree/master/drivers
4. https://github.com/godotengine/godot/tree/master/scene
5. https://docs.godotengine.org/en/stable/engine_details/architecture/godot_architecture_diagram.html
6. https://docs.godotengine.org/en/stable/tutorials/scripting/gdscript/gdscript_basics.html
7. https://docs.godotengine.org/pt-br/4.x/engine_details/engine_api/gdextension/gdextension_interface_json_file.html
8. https://github.com/godotengine/godot-docs/blob/8c843e8d/engine_details/engine_api/gdextension/gdextension_interface_json_file.rst
8b. https://docs.godotengine.org/zh-cn/4.3/about/faq.html
8c. https://godotengine.org/article/why-isnt-godot-ecs-based-game-engine/
8d. https://fastly.jsdelivr.net/gh/godotengine/godot-docs@master/tutorials/performance/using_servers.rst
8e. https://fastly.jsdelivr.net/gh/godotengine/godot-docs@master/engine_details/architecture/godot_architecture_diagram.rst

**Unreal Engine 官方**
9. https://github.com/EpicGames
10. https://docs.unrealengine.com/5.3/en-US/render-dependency-graph-in-unreal-engine
11. https://docs.unrealengine.com/5.1/en-US/API/Runtime/RenderCore/FRDGBuilder
12. https://docs.unrealengine.com/latest/INT/API/Runtime/RHI/index.html
13. https://www.unrealengine.com/tech-blog/chaos-scene-queries-and-rigid-body-engine-in-ue5
14. https://docs.unrealengine.com/5.2/zh-CN/unreal-engine-modules
15. https://dev.epicgames.com/documentation/unreal-engine/world-partition-in-unreal-engine?application_version=5.0
16. https://forums.unrealengine.com/t/knowledge-base-garbage-collector-internals/501800/2
17. https://dev.epicgames.com/documentation/zh-cn/unreal-engine/incremental-garbage-collection-in-unreal-engine
18. https://forums.unrealengine.com/t/external-pak-files-and-use-io-store-option/2137290/4
19. https://d1ap1mz92jnks1.cloudfront.net/t/rhi-switching-dx11-dx12-vulkan-during-runtime/571030/6

**第三方深度分析**
20. https://book.bl4.dev/02-unreal-architecture.html
21. https://raw.githubusercontent.com/Lynricsy/HyperSkills/main/skills/unreal/references/unreal-scripting.md
22. https://zread.ai/JasonHe78/UE5-1-1/15-nanite-virtualized-geometry
23. https://www.medien.ifi.lmu.de/lehre/ws2122/gp/slides/gp-ws2122-extra-nanite.pdf
24. https://natsuneko3.github.io/2023/04/16/ue源码/RHI解析/
25. https://www.hqwc.cn/a/650725.html
26. https://www.deepwiki.com/godotengine/godot/7.1-gdscript-implementation
27. https://www.deepwiki.com/godotengine/godot/7.2-renderingdevice-and-drivers
28. https://factory.ai/open-source-wikis/godot?page=drivers/graphics.md
29. https://modding.wiki/en/UnrealEngineModdingGuides/UnderstandingPakVsIoStore
30. https://fffeiya.github.io/2026/01/24/UE5.6_GAS_Enemy_AI_Stage_Summary%20-%20%E5%89%AF%E6%9C%AC
31. https://www.thecandidstartup.org/2023/04/03/nanite-graphics-pipeline.html
32. https://onlinelibrary.wiley.com/doi/10.1111/cgf.70538
33. https://vrarwiki.com/index.php?title=Nanite

---

### 附：方法论说明

- **Godot 侧**采用「本地稀疏克隆 + `git ls-tree -r HEAD` 全量文件清单 + `git show HEAD:<path>` 逐文件读源码」的方式，所有类名、宏、字段、行号均直接来自 `master` 分支 commit `679066724352c12673001d692d41dc2dab9314c0`。
- **UE5 侧**源码 EULA 门控无法直读，因此类路径与 API 签名通过「官方 API 文档页（docs.unrealengine.com）+ 官方论坛技术帖 + Epic 技术博客 + 运行时内存布局逆向分析」四路交叉验证。
- 标注「（推测）」的条目为无法从一手来源直接确认的内容。
