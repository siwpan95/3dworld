# -*- coding: utf-8 -*-
"""程序化 PBR 纹理素材：fBm 值噪声 → albedo / metal-rough / normal / emissive。

引擎之前的材质全是"纯色 + 常量参数"（1x1 贴图占位），这个模块补上真正
的**贴图资产**——和 .kkrieger 的精神一致：磁盘上不存像素，只存生成规则，
首次调用时现算成 GL 纹理（带缓存）。

两层职责严格分开（无窗口单测只测第一层）::

    make_*()   纯字节生成：size×size 的 RGBA 字节串（确定性，可复现）
    texture()  GL 上传：字节串 → pyglet Texture（REPEAT + mipmap）

约定与 glTF 2.0 对齐：
- MR 贴图：**G=粗糙度，B=金属度**（R 通道空闲，填 AO≈255）；
- 法线贴图：切线空间，由高度场 Sobel 算出（强度可调）；
- 所有噪声按**平铺周期**取模——REPEAT 采样时接缝无跳变。
"""
import math

__all__ = [
    'value_noise', 'fbm', 'height_to_normal',
    'make_marble', 'make_rust_metal', 'make_hex_panel', 'make_circuit',
    'make_brushed', 'texture', 'material_from', 'PATTERN_CACHE',
]


# ----------------------------------------------------------------- 噪声 --
def _hash2(ix, iy, seed):
    """格点哈希 → [0,1)。整数格点 + 固定种子 ⇒ 完全确定。"""
    s = math.sin(ix * 127.1 + iy * 311.7 + seed * 74.7) * 43758.5453
    return s - math.floor(s)


def _fade(u):
    """三次缓动（比 smoothstep 的二次导数更连续，噪声更柔）。"""
    return u * u * u * (u * (u * 6.0 - 15.0) + 10.0)


def value_noise(u, v, cells_u=8, cells_v=None, seed=0):
    """平铺值噪声：纹理宽内恰好 ``cells_u`` 个格点（接缝无跳变）。

    关键坑：cells 必须远小于像素数。第一版把纹理尺寸 256 当 cells 传，
    每像素正好踩一个格点 → 输出的是**白噪声**而非平滑噪声，大理石变
    成了沙砾。cells_v 缺省 = cells_u（各向同性）。
    """
    cells_v = cells_u if cells_v is None else cells_v
    x, y = u * cells_u, v * cells_v
    x0, y0 = math.floor(x) % cells_u, math.floor(y) % cells_v
    x1, y1 = (x0 + 1) % cells_u, (y0 + 1) % cells_v
    fx, fy = _fade(x - math.floor(x)), _fade(y - math.floor(y))
    a = _hash2(x0, y0, seed)
    b = _hash2(x1, y0, seed)
    c = _hash2(x0, y1, seed)
    d = _hash2(x1, y1, seed)
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def fbm(u, v, octaves=4, cells=6, cells_v=None, seed=0,
        gain=0.5, lacunarity=2.0):
    """分形布朗运动：倍频叠加，每层幅度 ×gain、频率 ×lacunarity。"""
    total, amp, freq = 0.0, 1.0, 1.0
    norm = 0.0
    cu = cells
    cv = cells if cells_v is None else cells_v
    for _ in range(octaves):
        # cells 直接给这一层的目标频率（坐标不再额外乘 freq，否则频率
        # 被算成 freq²）；u=1 时 x = cu·freq ≡ 0 (mod cu·freq)，无缝平铺
        total += amp * value_noise(u, v,
                                   cells_u=max(2, int(cu * freq)),
                                   cells_v=max(2, int(cv * freq)), seed=seed)
        norm += amp
        amp *= gain
        freq *= lacunarity
    return total / norm


# ----------------------------------------------------------------- 派生图 --
def height_to_normal(heights, size, strength=2.0):
    """高度场 → 切线空间法线（Sobel，边界按平铺取模）。RGBA，α=255。"""
    out = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            # 平铺 Sobel：dx/y 方向各取一圈邻域差分
            hl = heights[y * size + (x - 1) % size]
            hr = heights[y * size + (x + 1) % size]
            hd = heights[(y - 1) % size * size + x]
            hu = heights[(y + 1) % size * size + x]
            dx = (hl - hr) * strength
            dy = (hd - hu) * strength
            dz = 1.0
            l = math.sqrt(dx * dx + dy * dy + dz * dz)
            i = (y * size + x) * 4
            out[i] = int((dx / l * 0.5 + 0.5) * 255)
            out[i + 1] = int((dy / l * 0.5 + 0.5) * 255)
            out[i + 2] = int((dz / l * 0.5 + 0.5) * 255)
            out[i + 3] = 255
    return bytes(out)


def _blank(size):
    return bytearray(size * size * 4)


def _px(buf, size, x, y, r, g, b, a=255):
    i = ((y % size) * size + (x % size)) * 4
    buf[i] = max(0, min(255, int(r)))
    buf[i + 1] = max(0, min(255, int(g)))
    buf[i + 2] = max(0, min(255, int(b)))
    buf[i + 3] = a


# ----------------------------------------------------------------- 图案 --
def make_marble(size=256, seed=1, vein_scale=4.0):
    """大理石：正弦脉纹被 fBm 扰动。低粗糙度（抛光面）+ 亮脉冲法线。

    ``vein_scale`` 必须是偶整数——``sin(k·u·π)`` 只有 k 为偶数时
    u=0/1 两端相位才一致，REPEAT 采样才无缝。扰动项 n*4π 同理。
    """
    base, mr, h = _blank(size), _blank(size), [0.0] * (size * size)
    for y in range(size):
        for x in range(size):
            u, v = x / size, y / size
            n = fbm(u, v, 4, seed=seed)
            vein = math.sin((u * vein_scale + n * 4.0) * math.pi)
            vein = abs(vein) ** 3                     # 细脉更锐
            gray = 210 + 26 * n - 68 * vein
            _px(base, size, x, y, gray + 8, gray, gray - 6)
            rough = 64 + 100 * vein + 40 * n          # 脉纹更哑
            _px(mr, size, x, y, 255, rough, 6)        # 非金属
            h[y * size + x] = n * 0.6 + vein * 0.4
    return dict(base=bytes(base), mr=bytes(mr),
                normal=height_to_normal(h, size, 2.4), size=size)


def make_rust_metal(size=256, seed=2, rust_scale=5.0):
    """锈蚀钢板：金属灰与铁锈按 fBm 阈值争夺，锈处低金属高粗糙。"""
    base, mr, h = _blank(size), _blank(size), [0.0] * (size * size)
    for y in range(size):
        for x in range(size):
            u, v = x / size, y / size
            n = fbm(u, v, 5, cells=8, seed=seed)
            fine = value_noise(u, v, cells_u=64, cells_v=64, seed=seed + 9)
            r = n * 0.75 + fine * 0.25
            rust = max(0.0, min(1.0, (r - 0.45) * 3.4))
            if rust > 0.01:
                # 锈色分层：中心深褐、边缘橙红，不能像"淡粉"
                cr = 118 + 96 * fine * (0.4 + 0.6 * rust)
                cg = 52 + 48 * fine
                cb = 22 + 18 * fine
                _px(base, size, x, y, cr * rust + 132 * (1 - rust),
                    cg * rust + 136 * (1 - rust),
                    cb * rust + 144 * (1 - rust))
            else:
                _px(base, size, x, y, 132 + 24 * fine, 136 + 20 * fine,
                    144 + 16 * fine)
            metal = 255 * (1.0 - rust)
            rough = 96 + 120 * rust + 24 * fine
            _px(mr, size, x, y, 255, rough, metal)
            h[y * size + x] = r * 0.5 + fine * 0.5
    return dict(base=bytes(base), mr=bytes(mr),
                normal=height_to_normal(h, size, 1.6), size=size)


def make_hex_panel(size=256, seed=3, hex_r=26.0):
    """六边形装甲板：暗色科技面板 + 沟槽缝 + 偶发亮芯（emissive）。"""
    base, mr, em, h = _blank(size), _blank(size), _blank(size), \
        [0.0] * (size * size)
    hr = hex_r * size / 256.0
    # 轴向六边形网格：点 (q, r*sqrt(3)/2)，奇偶行错位半格
    for y in range(size):
        for x in range(size):
            u, v = x / size, y / size
            row = y / (hr * 1.5)
            col = (x / (hr * math.sqrt(3.0))) - (0.5 if int(row) % 2 else 0.0)
            # 到最近六边形中心（近似：把邻域三个候选中心都比一遍）
            best = 1e9
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    cy = (int(row) + dr) * hr * 1.5
                    cx = (int(col) + dc + (0.5 if (int(row) + dr) % 2 else 0.0)) \
                        * hr * math.sqrt(3.0)
                    d = math.hypot(x - cx, y - cy)
                    # 六边形距离近似：欧氏 ×(1+|sin 60°扭曲|)
                    best = min(best, d)
            edge = max(0.0, min(1.0, (best - hr * 0.72) / (hr * 0.28)))
            n = fbm(u, v, 3, seed=seed)
            g = 30 + 26 * n + 14 * edge
            _px(base, size, x, y, g * 0.9, g, g * 1.12)
            _px(mr, size, x, y, 255, 70 + 130 * edge + 30 * n, 40)
            # 每隔几个六边形嵌一颗发光芯
            if _hash2(int(col * 3.7), int(row * 3.7), seed) > 0.87 \
                    and best < hr * 0.35:
                _px(em, size, x, y, 30, 190, 255)
            h[y * size + x] = edge * 0.8 + n * 0.2
    return dict(base=bytes(base), mr=bytes(mr), emissive=bytes(em),
                normal=height_to_normal(h, size, 3.2), size=size)


def make_circuit(size=256, seed=4):
    """电路板：暗底 + 曼哈顿走线（发光）+ 焊点。"""
    base, mr, em, h = _blank(size), _blank(size), _blank(size), \
        [0.0] * (size * size)
    step = max(8, size // 16)
    # 确定性走线：每行/列的哈希决定是否走线与转折
    for y in range(size):
        for x in range(size):
            u, v = x / size, y / size
            n = fbm(u, v, 3, seed=seed)
            on_trace = False
            if x % step < 2 and _hash2(x // step, 0, seed) > 0.45:
                # 竖线，随机一段高度
                seg = y // (step * 3)
                if _hash2(x // step, seg, seed + 5) > 0.4:
                    on_trace = True
            if y % step < 2 and _hash2(0, y // step, seed + 7) > 0.45:
                seg = x // (step * 3)
                if _hash2(seg, y // step, seed + 11) > 0.4:
                    on_trace = True
            if on_trace:
                _px(base, size, x, y, 24, 78, 60)
                _px(mr, size, x, y, 255, 200, 255)     # 铜线：金属低粗糙
                _px(em, size, x, y, 12, 120, 60)
                h[y * size + x] = 0.9
            else:
                g = 14 + 10 * n
                _px(base, size, x, y, g, g * 1.15, g * 1.2)
                _px(mr, size, x, y, 255, 120 + 60 * n, 0)
                h[y * size + x] = 0.1 + 0.15 * n
            # 焊点：走线交点上的圆点
            if x % step < 2 and y % step < 2 and on_trace:
                _px(em, size, x, y, 40, 255, 120)
    return dict(base=bytes(base), mr=bytes(mr), emissive=bytes(em),
                normal=height_to_normal(h, size, 2.0), size=size)


def make_brushed(size=256, seed=5):
    """拉丝金属：u 向高度拉伸的细噪声（各向异性拉丝的近似）。"""
    base, mr, h = _blank(size), _blank(size), [0.0] * (size * size)
    for y in range(size):
        for x in range(size):
            u, v = x / size, y / size
            # 细丝：v 向 48 格、u 向 2 格 → 沿 u 拉出拉丝
            streak = value_noise(u, v, cells_u=2, cells_v=48, seed=seed)
            fine = value_noise(u, v, cells_u=6, cells_v=160, seed=seed + 3)
            n = streak * 0.7 + fine * 0.3
            g = 150 + 70 * n
            _px(base, size, x, y, g, g * 0.99, g * 0.97)
            _px(mr, size, x, y, 255, 60 + 50 * fine, 255)
            h[y * size + x] = fine
    return dict(base=bytes(base), mr=bytes(mr),
                normal=height_to_normal(h, size, 0.9), size=size)


PATTERNS = {
    'marble': make_marble, 'rust_metal': make_rust_metal,
    'hex_panel': make_hex_panel, 'circuit': make_circuit,
    'brushed': make_brushed,
}


# --------------------------------------------------------------- GL 层 --
def texture(pattern, key, cache_id):
    """把 pattern 字典里的某张字节图上传成 GL 纹理（REPEAT + mipmap）。

    ``cache_id`` 必须带上图案名——只按 (槽位, 尺寸) 缓存的话，同尺寸的
    大理石和锈铁会互相串图（第一版就埋了这个雷）。

    与 ``make_solid_texture`` 同样的坑：``ImageData.get_texture()`` 会占用
    当前活跃纹理单元，渲染中途创建会冲掉已有绑定——这里同样保存/恢复。
    """
    import pyglet
    from pyglet.gl import (GL_LINEAR, GL_LINEAR_MIPMAP_LINEAR, GL_REPEAT,
                           GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
                           GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
                           GL_ACTIVE_TEXTURE, GL_TEXTURE_BINDING_2D, GLint,
                           glActiveTexture, glBindTexture, glGenerateMipmap,
                           glGetIntegerv, glTexParameteri)
    import ctypes
    cache = PATTERN_CACHE.setdefault(cache_id, {})
    if key in cache:
        return cache[key]
    active, bound = GLint(), GLint()
    glGetIntegerv(GL_ACTIVE_TEXTURE, ctypes.byref(active))
    glGetIntegerv(GL_TEXTURE_BINDING_2D, ctypes.byref(bound))
    try:
        data = pattern[key]
        img = pyglet.image.ImageData(pattern['size'], pattern['size'],
                                     'RGBA', data)
        tex = img.get_texture()
        glBindTexture(GL_TEXTURE_2D, tex.id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_REPEAT)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
                        GL_LINEAR_MIPMAP_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glGenerateMipmap(GL_TEXTURE_2D)
        cache[key] = tex
        return tex
    finally:
        glActiveTexture(active.value)
        glBindTexture(GL_TEXTURE_2D, bound.value)


PATTERN_CACHE = {}


def material_from(pattern_name, size=256, base_color=(1.0, 1.0, 1.0),
                  emissive=(0.0, 0.0, 0.0), seed=None, pattern_kwargs=None,
                  **mat_kwargs):
    """图案名 → 现成 PBR Material（贴图槽全接好，带缓存）。

    metallic/roughness 的**常量**会被贴图覆盖，但 Material 上仍保留
    标量字段（apply() 里 uniform 用标量、贴图在片元里再乘——双层控制）。
    ``pattern_kwargs`` 透传给图案生成器（如大理石的脉纹频率）。
    """
    from engine.material import Material
    maker = PATTERNS[pattern_name]
    kw = dict(pattern_kwargs or {})
    if seed is not None:
        kw['seed'] = seed
    pat = maker(size=size, **kw)
    key = f'{pattern_name}_{size}_{seed}' if seed is not None \
        else f'{pattern_name}_{size}'
    m = Material(name=mat_kwargs.pop('name', key),
                 base_color=base_color, **mat_kwargs)
    m.base_tex = texture(pat, 'base', key)
    m.mr_tex = texture(pat, 'mr', key)
    m.normal_tex = texture(pat, 'normal', key)
    if 'emissive' in pat:
        m.emissive_tex = texture(pat, 'emissive', key)
        # emissive uniform 是纹理的乘子：默认 0 会把发光贴图整个灭杀
        # （第一版 demo 里电路板一片黑就是因为这个），有图就默认全亮
        m.emissive = tuple(emissive) if any(emissive) else (1.0, 1.0, 1.0)
    else:
        m.emissive = tuple(emissive)
    return m
