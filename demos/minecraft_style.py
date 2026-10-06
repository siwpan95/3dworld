# -*- coding: utf-8 -*-
"""方块世界 · Minecraft 风格动画短片（五幕 / 48s）。

**致敬声明**：本 demo 是"方块世界"这一视觉风格的致敬实现 —— **零外部素材**：
所有贴图由 `logic/blocks.py` 的哈希噪声与图案规则在启动时**现算**（16×16 像素、
GL_NEAREST 采样），世界、树木、村庄、生物、云也都是程序化生成的。
未复制 Minecraft 或任何第三方的贴图、模型、名称与代码。

五幕（48 秒循环，t=0 与 t=48 同帧 ⇒ 无缝回绕）::

    一 日出 0-10s   高空掠过方块大地，云在脚下飘
    二 村庄 10-20s  下降进场：小屋、火把、水塘
    三 建造 20-30s  方块从天而降，逐块拼出一栋小屋
    四 生物 30-40s  方块生物沿小路走过村庄
    五 日落 40-48s  天色转暖再入夜，窗户与火把次第点亮

技术看点（都是本项目此前没有的）
--------------------------------
1. **程序化像素贴图图集**：23 种 16×16 贴图横向拼成图集，NEAREST 采样出像素感；
   UV 取半像素内缩防渗色；火把贴图带透明像素，走 alpha 裁剪（cutout）。
2. **昼夜不重建网格**：网格只烘"相对面亮度"（顶 1.0 / 侧 0.8 / 底 0.5，MC 同款），
   天光由 uniform 调制；**自发光方块**（火把/窗户）写在顶点色 alpha 上，
   夜里不随天光变暗 —— 于是"日落点灯"是零成本的。
3. **实例化下落方块**：一个基础立方体 + 每实例（世界坐标、图集格号、缩放、转角），
   按时间表依次落位拼成小屋；云层共用同一条实例化管线。
4. **方块生物**：猪与爬行者 = 六个盒体（身/头/四腿）各自的模型矩阵，
   步态由 `logic/blocks.mob_pose()` 的纯数学给出（前后腿反相，看起来才是"走"）。

按键：SPACE 暂停　←/→ 快退快进 5s　N/P 切幕　C 相机模式　R 重播　ESC 退出
"""
import array
import math

import pyglet
from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST, GL_NEAREST, GL_ONE_MINUS_SRC_ALPHA,
                       GL_SRC_ALPHA, GL_TRIANGLES, glBlendFunc, glClearColor,
                       glDepthMask, glDisable, glEnable)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3

from _harness import DemoApp, run
from logic import blocks as B
from logic.camera import look_at_matrix

# ---------------------------------------------------------------- 着色器 --
WORLD_VS = '''#version 330 core
in vec3 position;
in vec4 colors;
in vec2 tex_coords;
out vec4 v_col;
out vec2 v_uv;
out float v_dist;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 u_model;
void main() {
    vec4 wp = u_model * vec4(position, 1.0);
    vec4 vp = window.view * wp;
    v_dist = length(vp.xyz);
    v_col = colors;
    v_uv = tex_coords;
    gl_Position = window.projection * vp;
}'''

# 顶点色 rgba 的含义：rgb = 面亮度×色调（顶 1.0 / 侧 0.8 / 底 0.5），a = 自发光强度。
# 于是"白天亮、夜里暗"只是一次 uniform 乘法，而火把/窗户夜里照旧亮着。
WORLD_FS = '''#version 330 core
in vec4 v_col;
in vec2 v_uv;
in float v_dist;
out vec4 final_colors;
uniform sampler2D u_atlas;
uniform float u_sky;          // 天光强度（昼夜由它调制，网格不重建）
uniform vec3  u_fog_color;
uniform float u_fog_near;
uniform float u_fog_far;
uniform float u_scroll;       // 水面 UV 滚动（MC 的水是动画贴图）
uniform int   u_cutout;       // 1 = 透明像素直接丢弃（火把这类镂空贴图）
void main() {
    vec2 uv = v_uv;
    if (u_scroll > 0.0) { uv.y += u_scroll; uv.x += u_scroll * 0.35; }
    vec4 tex = texture(u_atlas, uv);
    if (u_cutout == 1 && tex.a < 0.5) { discard; }
    vec3 lit = tex.rgb * (v_col.rgb * u_sky + v_col.a);
    lit = mix(lit, u_fog_color, smoothstep(u_fog_near, u_fog_far, v_dist));
    final_colors = vec4(lit, tex.a);
}'''

INST_VS = '''#version 330 core
in vec3 position;
in vec4 colors;
in vec2 tex_coords;
in vec4 i_pos;        // xyz = 世界坐标，w = 图集格号
in vec3 i_anim;       // x = 缩放，y = 绕 Y 转角
out vec4 v_col;
out vec2 v_uv;
out float v_dist;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform float u_tiles;        // 图集格数
void main() {
    float ang = i_anim.y;
    float c = cos(ang), s = sin(ang);
    vec3 local = position * i_anim.x;
    vec3 rot = vec3(local.x * c - local.z * s, local.y, local.x * s + local.z * c);
    vec3 wp = i_pos.xyz + rot;
    vec4 vp = window.view * vec4(wp, 1.0);
    v_dist = length(vp.xyz);
    v_col = colors;
    // 面内 UV [0,1] → 图集内该格；半像素内缩，免得采到相邻格的像素
    float e = 0.5 / 16.0;
    v_uv = vec2((i_pos.w + clamp(tex_coords.x, e, 1.0 - e)) / u_tiles,
                clamp(tex_coords.y, e, 1.0 - e));
    gl_Position = window.projection * vp;
}'''

INST_FS = '''#version 330 core
in vec4 v_col;
in vec2 v_uv;
in float v_dist;
out vec4 final_colors;
uniform sampler2D u_atlas;
uniform float u_sky;
uniform vec3  u_fog_color;
uniform float u_fog_near;
uniform float u_fog_far;
void main() {
    vec4 tex = texture(u_atlas, v_uv);
    vec3 lit = tex.rgb * (v_col.rgb * u_sky + v_col.a);
    lit = mix(lit, u_fog_color, smoothstep(u_fog_near, u_fog_far, v_dist));
    final_colors = vec4(lit, tex.a);
}'''

SKY_VS = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec2 v_uv;
void main() {
    v_uv = position.xy * 0.5 + 0.5;
    gl_Position = vec4(position.xy, 0.0, 1.0);
}'''

SKY_FS = '''#version 330 core
in vec2 v_uv;
out vec4 final_colors;
uniform vec3  u_sky;
uniform vec3  u_horizon;
uniform float u_stars;
float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
void main() {
    float t = pow(clamp(v_uv.y, 0.0, 1.0), 0.65);
    vec3 col = mix(u_horizon, u_sky, t);
    if (u_stars > 0.01) {                       // 星空：稀疏点，越靠天顶越明显
        vec2 g = floor(v_uv * vec2(220.0, 130.0));
        float h = hash(g);
        float star = step(0.9955, h) * (0.55 + 0.45 * hash(g + 7.7));
        col += vec3(star) * u_stars * (1.0 - t);
    }
    final_colors = vec4(col, 1.0);
}'''

class BlockWorldDemo(DemoApp):
    TITLE = '方块世界 · Minecraft 风格动画短片（五幕 48s / 程序化像素贴图）'
    HELP = ('SPACE 暂停   ←/→ 快退快进   N/P 切幕   C 相机   R 重播   '
            '鼠标拖动/滚轮 自由视角   ESC 退出')
    DURATION = B.DURATION
    FOV = 60.0
    Z_NEAR, Z_FAR = 0.15, 420.0
    SHOTS = ((3.0, 'docs/demo_mc_dawn.png'),
             (24.0, 'docs/demo_mc_build.png'),
             (35.0, 'docs/demo_mc_mobs.png'),
             (41.5, 'docs/demo_mc_dusk.png'))

    # ------------------------------------------------------------ 资源 --
    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        self.world_prog = ShaderProgram(Shader(WORLD_VS, 'vertex'),
                                        Shader(WORLD_FS, 'fragment'))
        self.inst_prog = ShaderProgram(Shader(INST_VS, 'vertex'),
                                       Shader(INST_FS, 'fragment'))
        self.sky_prog = ShaderProgram(Shader(SKY_VS, 'vertex'),
                                      Shader(SKY_FS, 'fragment'))

        # --- 贴图图集：像素在 logic 层现算，这里只负责上传 ---
        w, h, px, self.tile_index = B.atlas_pixels()
        img = pyglet.image.ImageData(w, h, 'RGBA', px)
        self.atlas = img.get_texture()
        self.atlas.min_filter = GL_NEAREST          # 像素感的关键：不要线性过滤
        self.atlas.mag_filter = GL_NEAREST
        self.tile_count = float(len(self.tile_index))

        # --- 世界 → 网格（实测 0.2s，不需要渐进重建）---
        self.blocks, self.heights, self.trees, self.houses = B.generate_world()
        pos, col, uv = array.array('f'), array.array('f'), array.array('f')
        self.faces, self.verts = B.mesh_world(self.blocks, self.tile_index,
                                              pos=pos, col=col, uv=uv)
        self.terrain = self.world_prog.vertex_list(
            len(pos) // 3, GL_TRIANGLES, position=('f', pos),
            colors=('f', col), tex_coords=('f', uv))
        wpos, wcol, wuv = array.array('f'), array.array('f'), array.array('f')
        self.water_faces, _ = B.mesh_world(self.blocks, self.tile_index,
                                           want_water=True, pos=wpos,
                                           col=wcol, uv=wuv)
        self.water = self.world_prog.vertex_list(
            len(wpos) // 3, GL_TRIANGLES, position=('f', wpos),
            colors=('f', wcol), tex_coords=('f', wuv))
        print(f'[mc] atlas {w}x{h} tiles={len(self.tile_index)} | '
              f'terrain faces={self.faces} water={self.water_faces}')

        # --- 天空 / 太阳月亮 ---
        quad = (-1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0, 1.0, 0.0,
                -1.0, -1.0, 0.0, 1.0, 1.0, 0.0, -1.0, 1.0, 0.0)
        self.sky_quad = self.sky_prog.vertex_list(
            6, GL_TRIANGLES, position=('f', quad), colors=('f', (1.0,) * 24))
        self.sun = self._mesh_list('cloud', (8.0, 8.0, 0.6), (1.0, 0.93, 0.60))
        self.moon = self._mesh_list('cloud', (5.5, 5.5, 0.6), (0.88, 0.91, 1.0))

        # --- 实例化：云层 + 第三幕的下落方块 ---
        self.cloud_vl, self.cloud_inst = self._instanced(40)
        self.cloud_base = []
        for i in range(40):
            ang = 2 * math.pi * i / 40
            r = 16 + 30 * ((i * 37 % 100) / 100.0)
            self.cloud_base.append((36 + r * math.cos(ang),
                                    34 + 2.5 * ((i * 53 % 100) / 100.0 - 0.5),
                                    36 + r * math.sin(ang)))
        self.build_blocks = B.build_house_shape()
        self.build_times = [tt for (tt, _i) in B.build_schedule(len(self.build_blocks))]
        self.block_vl, self.block_inst = self._instanced(len(self.build_blocks))

        # --- 生物：猪 ×2 + 爬行者 ×1，沿村庄小路巡游 ---
        self.mobs = []
        # 巡游线收在路面（x=36 宽 3）正中，别蹭到东侧那栋房子
        for kind, lane, speed, phase in (('pig', -1.1, 0.9, 0.0),
                                         ('pig', 1.2, 0.75, 2.1),
                                         ('creeper', 0.0, 0.6, 4.2)):
            self.mobs.append({'kind': kind, 'lane': lane, 'speed': speed,
                              'phase': phase, 'parts': self._mob_lists(kind)})

        self.sky_state = B.sky_state(0.0)
        self.camera_mode = 'cine'
        self.free_yaw, self.free_pitch, self.free_dist = 40.0, 22.0, 52.0

    def _mesh_list(self, tile, size, tint, offset=(0.0, 0.0, 0.0)):
        p, c, u = B.box_mesh(self.tile_index[tile], len(self.tile_index),
                             size, offset, tint)
        return self.world_prog.vertex_list(len(p) // 3, GL_TRIANGLES,
                                           position=('f', p), colors=('f', c),
                                           tex_coords=('f', u))

    def _instanced(self, count):
        """建一个实例化立方体批次。

        两个 pyglet 2.1.16 的坑（都在这里一次性绕开）：

        1. 实例属性的**初始数组长度按"基础顶点数 × 分量数"校验**（36×4 / 36×3），
           不是实例数 —— 按实例数给会报 `Invalid data size for 'position'`；
        2. 实例属性**只能挂在 `add_instance()` 返回的 VertexInstance 上**
           （VertexList 本身没有 `i_pos`），所以实例对象要一并返回、逐帧改它们。
        """
        p, c, u = B.box_mesh(None, None, (1.0, 1.0, 1.0))
        vl = self.inst_prog.vertex_list_instanced(
            36, GL_TRIANGLES, instance_attributes=['i_pos', 'i_anim'],
            position=('f', p), colors=('f', c), tex_coords=('f', u),
            i_pos=('f', [0.0, 0.0, 0.0, 0.0] * 36),
            # 初始 i_anim 的缩放给 0：0 号槽是"初始数据占位"，拿不到 VertexInstance
            # 也就逐帧改不了它 —— 直接缩到 0 让它不可见，比在世界原点留一个孤立方块干净。
            i_anim=('f', [0.0, 0.0, 0.0] * 36))
        inst = [vl.add_instance(i_pos=(0.0, 0.0, 0.0, 0.0), i_anim=(1.0, 0.0, 0.0))
                for _ in range(count)]
        return vl, inst

    def _mob_lists(self, kind):
        """方块生物的六个盒体。

        坐标约定：**所有 offset 以"脚底 = y 0"为基准**（不是身体中心）。
        第一版按身体中心给，结果腿的上半截整个埋在身体盒体里、下半截又埋进地面
        —— 六个部位只看得见一个头，排查了很久（详见 docstring 的坑位）。
        每项还带一个 `drop`：腿是"从髋部垂下"的，`drop = -腿高/2`，
        这样旋转发生在髋部而不是腿中间（否则腿像跷跷板一样绕自己中点在摆）。
        """
        if kind == 'pig':
            spec = (('body', 'pig_body', (1.00, 0.55, 1.25), (0.0, 0.675, 0.0), 0.0),
                    ('head', 'pig_face', (0.50, 0.50, 0.50), (0.0, 0.72, -0.85), 0.0),
                    ('leg_fl', 'pig_body', (0.24, 0.40, 0.24), (-0.30, 0.20, -0.40), -0.20),
                    ('leg_fr', 'pig_body', (0.24, 0.40, 0.24), (0.30, 0.20, -0.40), -0.20),
                    ('leg_bl', 'pig_body', (0.24, 0.40, 0.24), (-0.30, 0.20, 0.40), -0.20),
                    ('leg_br', 'pig_body', (0.24, 0.40, 0.24), (0.30, 0.20, 0.40), -0.20))
        else:                                        # creeper
            spec = (('body', 'creeper_skin', (0.60, 1.00, 0.35), (0.0, 0.75, 0.0), 0.0),
                    ('head', 'creeper_face', (0.55, 0.55, 0.55), (0.0, 1.52, 0.0), 0.0),
                    ('leg_fl', 'creeper_skin', (0.24, 0.30, 0.24), (-0.17, 0.15, -0.22), -0.15),
                    ('leg_fr', 'creeper_skin', (0.24, 0.30, 0.24), (0.17, 0.15, -0.22), -0.15),
                    ('leg_bl', 'creeper_skin', (0.24, 0.30, 0.24), (-0.17, 0.15, 0.22), -0.15),
                    ('leg_br', 'creeper_skin', (0.24, 0.30, 0.24), (0.17, 0.15, 0.22), -0.15))
        return [(n, o, self._mesh_list(tl, sz, (1.0, 1.0, 1.0)), dr)
                for n, tl, sz, o, dr in spec]

    # ------------------------------------------------------------ 帧 --
    def update(self, dt):
        # 昼夜相位由 day_phase() 按幕编排 —— 不是 t/DURATION（见 logic 里的说明）
        self.sky_state = B.sky_state(B.day_phase(self.time, duration=self.DURATION))

    def _mob_state(self, mob, t):
        """生物沿小路来回走：位置取那一列的地形高度，朝向随行进方向。"""
        span = 26.0
        u = (t * mob['speed'] * 0.08 + mob['phase']) % 2.0
        tri = u if u < 1.0 else 2.0 - u
        x = 36.0 + mob['lane']
        z = 36.0 + 4.0 + tri * span - span * 0.5
        col, row = int(round(x)), int(round(z))
        h = B.WATER_LEVEL + 2
        if 0 <= col < B.WORLD_W and 0 <= row < B.WORLD_W:
            h = self.heights[row * B.WORLD_W + col]
        # 模型头朝 -Z；u<1 时段向 +Z 行进 ⇒ 转 180°（加一点斜角避免正对镜头太呆）
        yaw = math.pi + 0.25 if u < 1.0 else 0.25
        # h 是地表方块的中心高度、顶面在 h+0.5；生物各部位以"脚底=0"为基准
        # ⇒ pos.y = h+0.5 正好脚踩地面。第一版给 h+0.05，整只猪沉在地里。
        return Vec3(x, h + 0.5, z), yaw, (t if u < 1.0 else -t)

    def render(self):
        w = self.window
        st = self.sky_state
        t = self.time

        # --- 相机：五幕脚本 / 自由视角 ---
        if self.camera_mode == 'cine':
            eye, tgt = B.camera_pose(t, B.CAM_KEYS, self.DURATION)
        else:
            eye = (36.0 + self.free_dist * 0.6, 12.0 + self.free_dist * 0.5,
                   36.0 + self.free_dist * 0.8)
            tgt = (36.0, 14.0, 36.0)
        w.view = look_at_matrix(Vec3(*eye), Vec3(*tgt), Vec3(0, 1, 0))

        # --- 1) 清屏 + 天空（全屏，不写深度）---
        glClearColor(st['horizon'][0], st['horizon'][1], st['horizon'][2], 1.0)
        w.clear()
        glDisable(GL_DEPTH_TEST)
        glDepthMask(False)
        self.sky_prog.use()
        self.sky_prog['u_sky'] = st['sky']
        self.sky_prog['u_horizon'] = st['horizon']
        self.sky_prog['u_stars'] = st['stars']
        self.sky_quad.draw(GL_TRIANGLES)

        # --- 2) 太阳 / 月亮：薄盒沿太阳方向摆到远处 ---
        prog = self.world_prog
        prog.use()
        prog['u_atlas'] = 0
        self.atlas.bind()
        prog['u_sky'] = 1.0
        prog['u_fog_near'] = 200.0
        prog['u_fog_far'] = 400.0
        prog['u_fog_color'] = st['horizon']
        prog['u_scroll'] = 0.0
        prog['u_cutout'] = 0
        d = 160.0
        if st['sun_visible']:
            prog['u_model'] = Mat4.from_translation(
                Vec3(st['sun'][0] * d, st['sun'][1] * d, st['sun'][2] * d))
            self.sun.draw(GL_TRIANGLES)
        if st['moon_visible']:
            prog['u_model'] = Mat4.from_translation(
                Vec3(-st['sun'][0] * d, -st['sun'][1] * d, -st['sun'][2] * d))
            self.moon.draw(GL_TRIANGLES)

        # --- 3) 不透明：地形 / 云 / 生物 / 下落方块 ---
        glEnable(GL_DEPTH_TEST)
        glDepthMask(True)
        prog.use()
        prog['u_sky'] = st['ambient']
        prog['u_fog_near'] = 60.0
        prog['u_fog_far'] = 220.0
        prog['u_fog_color'] = st['horizon']
        prog['u_scroll'] = 0.0
        prog['u_cutout'] = 1                      # 火把贴图有透明像素
        prog['u_model'] = Mat4()
        self.terrain.draw(GL_TRIANGLES)

        prog['u_fog_near'] = 90.0                 # 云更高更远，单独一档雾
        prog['u_fog_far'] = 320.0
        for i, (bx, by, bz) in enumerate(self.cloud_base):
            self.cloud_inst[i].i_pos = (bx + (t * 0.9) % 70.0 - 35.0, by, bz,
                                        float(self.tile_index['cloud']))
        self.cloud_vl.draw(GL_TRIANGLES)

        prog['u_fog_near'] = 60.0
        prog['u_fog_far'] = 220.0
        for mob in self.mobs:
            pos, yaw, walk_t = self._mob_state(mob, t)
            pose = B.mob_pose(mob['kind'], walk_t, speed=mob['speed'])
            # 起伏作用在整只生物上（不只是身体），否则腿会脱离身体
            base = (Mat4.from_translation(pos + Vec3(0, pose['body_y'], 0))
                    @ Mat4.from_rotation(yaw, Vec3(0, 1, 0)))
            for name, off, vl, drop in mob['parts']:
                m = base @ Mat4.from_translation(Vec3(*off))
                if name.startswith('leg'):
                    side = name[-1]
                    if name.startswith('leg_b'):
                        ang = pose['leg_back_l'] if side == 'l' else pose['leg_back_r']
                    else:
                        ang = pose['leg_front_l'] if side == 'l' else pose['leg_front_r']
                    # 顺序：先下垂 drop，再绕髋部转 —— 反过来就成了绕腿中点摆（跷跷板）
                    m = (m @ Mat4.from_rotation(math.radians(ang), Vec3(1, 0, 0))
                         @ Mat4.from_translation(Vec3(0, drop, 0)))
                prog['u_model'] = m
                vl.draw(GL_TRIANGLES)

        # 第三幕：方块按时间表落下拼成小屋（每实例：世界坐标 + 格号 + 缩放 + 转角）
        self.inst_prog.use()
        self.inst_prog['u_atlas'] = 0
        self.inst_prog['u_tiles'] = self.tile_count
        self.inst_prog['u_sky'] = st['ambient']
        self.inst_prog['u_fog_color'] = st['horizon']
        self.inst_prog['u_fog_near'] = 60.0
        self.inst_prog['u_fog_far'] = 220.0
        self.atlas.bind()
        if t >= 19.0:
            for i, (bx, by, bz, block) in enumerate(self.build_blocks):
                e = t - self.build_times[i]
                if e < 0.0:
                    self.block_inst[i].i_anim = (0.0, 0.0, 0.0)   # 还没轮到：缩到 0
                    continue
                k = min(1.0, e / 0.55)
                ease = k * k * (3 - 2 * k)
                tile = float(self.tile_index[B.BLOCK_FACES[block][1]])
                self.block_inst[i].i_pos = (bx, by + (1.0 - ease) * 16.0, bz, tile)
                self.block_inst[i].i_anim = (0.12 + 0.88 * ease, (1.0 - ease) * 2.4, 1.0)
            self.block_vl.draw(GL_TRIANGLES)

        # --- 4) 水：半透明，不写深度（否则会挡住它下面的地形）---
        glDepthMask(False)
        prog.use()
        prog['u_sky'] = st['ambient']
        prog['u_scroll'] = 0.05 * t
        prog['u_cutout'] = 0
        prog['u_model'] = Mat4()
        prog['u_fog_near'] = 50.0
        prog['u_fog_far'] = 190.0
        self.water.draw(GL_TRIANGLES)
        glDepthMask(True)

    # ---------------------------------------------------------- 交互 --
    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.camera_mode = 'free'
        self.free_yaw += dx * 0.3
        self.free_pitch = max(4.0, min(88.0, self.free_pitch + dy * 0.3))
        self.free_dist = max(10.0, min(110.0, self.free_dist))

    def on_mouse_scroll(self, x, y, sx, sy):
        self.camera_mode = 'free'
        self.free_dist = max(12.0, min(110.0, self.free_dist - sy * 4))

    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.N:
            self._jump_act(1)
        elif symbol == key.P:
            self._jump_act(-1)
        elif symbol == key.R:
            self.time = 0.0
        elif symbol == key.LEFT:
            self.time = max(0.0, self.time - 5.0)
        elif symbol == key.RIGHT:
            self.time = min(self.DURATION - 0.01, self.time + 5.0)
        elif symbol == key.C:
            self.camera_mode = 'free' if self.camera_mode == 'cine' else 'cine'

    def _jump_act(self, step):
        times = [a[0] for a in B.ACTS]
        idx = 0
        for i, t0 in enumerate(times):
            if self.time >= t0:
                idx = i
        if step < 0:
            nxt = idx - 1 if self.time - times[idx] <= 0.2 else idx
            nxt = max(0, nxt)
        else:
            nxt = (idx + 1) % len(times)
        self.time = times[nxt]

    def status_text(self):
        act = B.act_at(self.time)
        st = self.sky_state
        return (f'{act[1]}  {act[2]}   t={self.time:5.1f}/{self.DURATION:.0f}s   '
                f'天光 {st["ambient"]:.2f}  星 {st["stars"]:.2f}  '
                f'面 {self.faces}+{self.water_faces}水  {self.fps:5.1f} fps'
                f'{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(BlockWorldDemo)
