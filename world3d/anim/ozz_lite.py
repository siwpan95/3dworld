# -*- coding: utf-8 -*-
"""ozz-lite：轻量骨架动画层（纯数学 / 无 GL，headless 可断言）。

按 3dworld engine 层「纯数学核心 + 可注入 GLSL，CI 无 GPU 可跑断言」定位实现。
借鉴 **ozz-animation**（MIT）的若干核心算法思路自研：

* 骨架层级（joint 父子链）与局部/世界变换的前向运动学（FK）；
* clip 的多轨采样（translation / rotation / scale 关键帧，线性或 slerp 插值）；
* 多 pose 的**惰性混合**（按权重归一化，旋转用 nlerp/slerp）；
* **加法动画**（相对参考 pose 求 delta，再按权重叠加到基 pose）；
* **分层**（带关节掩码把一层叠加到基 pose 上，含权重混合）；
* **root motion** 提取（根关节在一段时间内的局部变换增量）；
* **真实蒙皮网格回放**（消费 `gltf_lite` 的 skin / animation，做骨架构建 +
  clip 构建 + 蒙皮矩阵计算 + 线性蒙皮顶点变换——纯 numpy、零 GL、headless）。

**不搬 ozz 的 C++ 源码、不照搬命名/结构**，仅按公开算法思路自研。
本模块刻意只做 headless 的数学子集，不做压缩（ACL 属另一许可体系，按纪律不引入）；
GL/渲染是消费方（如 `matcache_lite` 的 GLSL）的事。
"""
from __future__ import annotations

import numpy as np

_EPS = 1e-9


# ------------------------------------------------------- 四元数工具 --
def q_identity():
    return np.array([1.0, 0.0, 0.0, 0.0], dtype=float)


def q_normalize(q):
    q = np.asarray(q, dtype=float)
    n = np.linalg.norm(q)
    return q / n if n > _EPS else q_identity()


def q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ], dtype=float)


def q_conj(q):
    w, x, y, z = q
    return np.array([w, -x, -y, -z], dtype=float)


def q_slerp(a, b, t):
    a = q_normalize(a)
    b = q_normalize(b)
    dot = float(np.dot(a, b))
    if dot < 0.0:
        b = -b
        dot = -dot
    if dot > 0.9995:
        return q_normalize(a + t * (b - a))
    theta = np.arccos(np.clip(dot, -1.0, 1.0))
    sin = np.sin(theta)
    if sin < _EPS:
        return q_normalize(a + t * (b - a))
    wa = np.sin((1 - t) * theta) / sin
    wb = np.sin(t * theta) / sin
    return q_normalize(wa * a + wb * b)


def q_to_matrix(q):
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=float)


def _trs_to_4x4(t, r, s):
    m = q_to_matrix(r)
    s = np.asarray(s, dtype=float)
    rot = m * s[None, :]
    mat = np.eye(4)
    mat[:3, :3] = rot
    mat[:3, 3] = np.asarray(t, dtype=float)
    return mat


def _matrix_to_quat(m):
    """旋转矩阵（列向量约定，行主序 4x4 的 3x3 部分）→ (w,x,y,z)。"""
    m = np.asarray(m, dtype=float)
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    return np.array([w, x, y, z], dtype=float)


def _matrix_to_trs(m):
    """把 4x4（行主序、列向量约定）分解为 (translation, quat(wxyz), scale)。"""
    m = np.asarray(m, dtype=float)
    t = m[:3, 3].copy()
    col0 = m[:3, 0]
    col1 = m[:3, 1]
    col2 = m[:3, 2]
    sx = float(np.linalg.norm(col0))
    sy = float(np.linalg.norm(col1))
    sz = float(np.linalg.norm(col2))
    s = np.array([sx, sy, sz], dtype=float)
    rmat = np.column_stack([col0 / max(sx, _EPS),
                            col1 / max(sy, _EPS),
                            col2 / max(sz, _EPS)])
    q = _matrix_to_quat(rmat)
    return t, q, s


def _gltf_quat_to_ozz(q):
    """glTF 四元数序 (x,y,z,w) → ozz 序 (w,x,y,z)。"""
    x, y, z, w = q
    return np.array([w, x, y, z], dtype=float)


# ------------------------------------------------------------ 骨架 --
class Skeleton:
    """关节层级。joints[i] = (name, parent_index, bind_local(t,r,s))。"""

    def __init__(self, joints):
        self.names = [j[0] for j in joints]
        self.parents = [j[1] for j in joints]
        self.bind = [(np.asarray(j[2][0], float), q_normalize(j[2][1]),
                      np.asarray(j[2][2], float)) for j in joints]
        self.index = {name: i for i, name in enumerate(self.names)}

    def world_matrices(self, local=None):
        """给定每关节局部 (t,r,s) 列表，返回世界 4x4 矩阵列表。

        用定点迭代，支持关节索引未按拓扑序排列的情形（glTF skin.joints
        顺序不保证父先于子）。
        """
        loc = self.bind if local is None else local
        n = len(loc)
        world = [None] * n
        for _ in range(n):
            progress = False
            for i in range(n):
                if world[i] is not None:
                    continue
                p = self.parents[i]
                if p < 0 or world[p] is not None:
                    m = _trs_to_4x4(*loc[i])
                    if p >= 0:
                        m = world[p] @ m
                    world[i] = m
                    progress = True
            if not progress:
                break
        return world


# ------------------------------------------------------------ clip --
class Clip:
    """动画片段。tracks[joint_index] = dict with optional
    'translation'=(times, vals[N,3]), 'rotation'=(times, quats[N,4]),
    'scale'=(times, vals[N,3])。"""

    def __init__(self, name, duration, tracks):
        self.name = name
        self.duration = float(duration)
        self.tracks = tracks


def _sample_track(times, vals, t, slerp=False):
    times = np.asarray(times, dtype=float)
    n = len(times)
    if n == 0:
        return None
    if t <= times[0]:
        return vals[0]
    if t >= times[-1]:
        return vals[-1]
    i = int(np.searchsorted(times, t, side='right')) - 1
    i = min(max(i, 0), n - 2)
    t0, t1 = times[i], times[i + 1]
    a = 0.0 if abs(t1 - t0) < _EPS else (t - t0) / (t1 - t0)
    if slerp:
        return q_slerp(vals[i], vals[i + 1], a)
    return vals[i] + a * (vals[i + 1] - vals[i])


def sample_clip(clip, t, loop=False, bind=None):
    """采样 clip 在时刻 t 的局部 pose：返回 list[(t,r,s)]（与骨架关节同序）。

    bind 可选：list[(t,r,s)]，作为未提供轨道的关节的默认局部变换。用于
    glTF 回放时保留关节节点的静态 TRS（动画只驱动部分通道时，其余分量
    仍取节点静态值而非单位变换）。缺省用单位变换。
    """
    if loop and clip.duration > _EPS:
        t = t % clip.duration
    keys = list(clip.tracks.keys())
    if keys:
        max_key = max(keys)
        max_bind = (len(bind) - 1) if bind is not None else -1
        n_joints = max(max_key, max_bind) + 1
    else:
        n_joints = len(bind) if bind is not None else 1
    pose = [None] * n_joints
    for j, track in clip.tracks.items():
        tloc = _sample_track(track['translation'][0], track['translation'][1], t) \
            if 'translation' in track else (
                np.asarray(bind[j][0], float) if bind is not None else np.zeros(3))
        rloc = _sample_track(track['rotation'][0], track['rotation'][1], t, slerp=True) \
            if 'rotation' in track else (
                np.asarray(bind[j][1], float) if bind is not None else q_identity())
        sloc = _sample_track(track['scale'][0], track['scale'][1], t) \
            if 'scale' in track else (
                np.asarray(bind[j][2], float) if bind is not None else np.ones(3))
        pose[j] = (np.asarray(tloc, float), np.asarray(rloc, float), np.asarray(sloc, float))
    # 未提供轨道的关节用缺省（bind 或单位变换）
    for j in range(n_joints):
        if pose[j] is None:
            if bind is not None:
                pose[j] = (np.asarray(bind[j][0], float), np.asarray(bind[j][1], float),
                           np.asarray(bind[j][2], float))
            else:
                pose[j] = (np.zeros(3), q_identity(), np.ones(3))
    return pose


# --------------------------------------------------------- 混合 / 分层 --
def blend(poses, weights):
    """惰性混合 N 个 pose（list[(t,r,s)]），按权重归一化。"""
    weights = np.asarray(weights, dtype=float)
    wsum = weights.sum()
    if wsum < _EPS:
        return poses[0]
    weights = weights / wsum
    n = len(poses[0])
    out = []
    for k in range(n):
        tw = np.zeros(3)
        rw = q_identity()
        sw = np.zeros(3)
        for pose, w in zip(poses, weights):
            t, r, s = pose[k]
            tw += w * t
            sw += w * s
            rw = q_slerp(rw, r, w)  # 加权 nlerp/slerp
        out.append((tw, rw, sw))
    return out


def make_additive(clip, reference_pose):
    """把 clip 转为加法片段：每个关节记录相对 reference_pose 的 delta。"""
    n_joints = len(reference_pose)
    tracks = {}
    for j in range(n_joints):
        rt, rr, rs = reference_pose[j]
        if j in clip.tracks:
            track = clip.tracks[j]
            dt = track.get('translation')
            dr = track.get('rotation')
            ds = track.get('scale')
            nt = None
            if dt is not None:
                times, vals = dt
                dvals = np.asarray([
                    vals[i] - rt for i in range(len(vals))
                ], dtype=float)
                nt = (times, dvals)
            nr = None
            if dr is not None:
                times, quats = dr
                nquats = np.asarray([
                    q_normalize(q_mul(q_conj(q_normalize(rr)), q_normalize(quats[i])))
                    for i in range(len(quats))
                ], dtype=float)
                nr = (times, nquats)
            ns = None
            if ds is not None:
                times, vals = ds
                dvals = np.asarray([
                    vals[i] / rs for i in range(len(vals))
                ], dtype=float)
                ns = (times, dvals)
            tracks[j] = {}
            if nt is not None:
                tracks[j]['translation'] = nt
            if nr is not None:
                tracks[j]['rotation'] = nr
            if ns is not None:
                tracks[j]['scale'] = ns
    return Clip(clip.name + "_add", clip.duration, tracks)


def apply_additive(base_pose, additive_pose, weight):
    """把加法片段按 weight 叠加到基 pose 上（局部空间）。"""
    n = len(base_pose)
    out = []
    for k in range(n):
        bt, br, bs = base_pose[k]
        at, ar, as_ = additive_pose[k]
        # 平移/缩放：基 + weight*delta
        t = bt + weight * at
        s = bs * (np.ones(3) + weight * (as_ - 1.0))
        # 旋转：基 * slerp(identity, delta, weight)
        r = q_mul(br, q_slerp(q_identity(), ar, weight))
        out.append((t, q_normalize(r), s))
    return out


def apply_layer(base_pose, layer_pose, mask, weight=1.0):
    """分层：对 mask（关节索引集合）内的关节用 layer_pose 按 weight 混合覆盖。"""
    mask = set(mask)
    n = len(base_pose)
    out = []
    for k in range(n):
        if k in mask:
            bt, br, bs = base_pose[k]
            lt, lr, ls = layer_pose[k]
            t = bt + weight * (lt - bt)
            s = bs + weight * (ls - bs)
            r = q_slerp(br, lr, weight)
            out.append((t, r, s))
        else:
            out.append(base_pose[k])
    return out


# ----------------------------------------------------- root motion --
def extract_root_motion(clip, t0, t1, loop=False):
    """提取根关节（clip 第 0 关节）在 [t0,t1] 内的局部变换增量。"""
    p0 = sample_clip(clip, t0, loop)[0]
    p1 = sample_clip(clip, t1, loop)[0]
    dt = p1[0] - p0[0]                                    # 平移增量
    dr = q_normalize(q_mul(q_conj(q_normalize(p0[1])), q_normalize(p1[1])))  # 旋转增量
    return dt, dr


# ---------------------------- 真实蒙皮网格回放（消费 gltf_lite） --
def skeleton_from_gltf(model, skin):
    """从 GltfModel + Skin 构造 ozz Skeleton（关节序 = skin.joints 序）。

    每个关节的父关节取对应节点在 glTF 节点树中的父节点（映射到关节序，
    不在 skin 内则为 -1）；bind-local 由节点 local 矩阵分解为 TRS。
    """
    joint_nodes = list(skin.joints)
    node_to_joint = {n: j for j, n in enumerate(joint_nodes)}
    names, parents, bind = [], [], []
    for n in joint_nodes:
        node = model.node(n)
        names.append(node.name)
        pnode = node.raw.get("parent")
        pj = node_to_joint.get(pnode, -1) if pnode is not None else -1
        parents.append(pj)
        t, r, s = _matrix_to_trs(node.local)
        bind.append((t, r, s))
    return Skeleton(list(zip(names, parents, bind))), node_to_joint


def _anim_duration(anim):
    d = 0.0
    for ch in anim.channels:
        tmax = float(np.asarray(ch.sampler.times, dtype=float).max())
        if tmax > d:
            d = tmax
    return d


def clip_from_gltf_animation(model, anim, node_to_joint):
    """从 GltfModel + Animation 构造 ozz Clip（轨道键 = 关节序）。

    仅取 target 节点落在 skin 内的通道；rotation 的 glTF 四元数
    (x,y,z,w) 转为 ozz (w,x,y,z)。weights（morph target）通道暂不支持，
    按需忽略；CUBIC/STEP 插值回退为 LINEAR。
    """
    tracks = {}
    for ch in anim.channels:
        ji = node_to_joint.get(ch.node)
        if ji is None:
            continue
        sampler = ch.sampler
        times = np.asarray(sampler.times, dtype=float).reshape(-1)
        vals = np.asarray(sampler.values, dtype=float)
        if ch.path == "translation":
            tracks.setdefault(ji, {})["translation"] = \
                (times, vals.reshape(vals.shape[0], -1))
        elif ch.path == "rotation":
            qozz = np.array([_gltf_quat_to_ozz(v)
                             for v in vals.reshape(vals.shape[0], -1)])
            tracks.setdefault(ji, {})["rotation"] = (times, qozz)
        elif ch.path == "scale":
            tracks.setdefault(ji, {})["scale"] = \
                (times, vals.reshape(vals.shape[0], -1))
        # weights（morph）忽略
    return Clip(anim.name, _anim_duration(anim), tracks)


def compute_skin_matrices(skeleton, local_pose, inverse_bind):
    """skin_matrix[j] = jointWorld[j] @ inverseBind[j] → list[(4,4)]。"""
    inv = np.asarray(inverse_bind, dtype=float)
    joint_world = skeleton.world_matrices(local_pose)
    return [joint_world[j] @ inv[j] for j in range(len(joint_world))]


def skin_vertices(positions, joints, weights, skin_matrices):
    """线性蒙皮：对 V 顶点，v_skin = Σ_{k<4} w_k · skinMat[j_k] · (x,y,z,1)。

    positions: (V,3) float；joints: (V,4) int（索引 skin_matrices）；
    weights: (V,4) float（每顶点四元权重）。返回 (V,3)。
    """
    positions = np.asarray(positions, dtype=float)
    joints = np.asarray(joints, dtype=int)
    weights = np.asarray(weights, dtype=float)
    V = positions.shape[0]
    hom = np.concatenate([positions, np.ones((V, 1), dtype=float)], axis=1)  # (V,4)
    out = np.zeros((V, 3), dtype=float)
    for k in range(4):
        ji = joints[:, k]
        w = weights[:, k]
        M = np.stack([skin_matrices[j] for j in ji])          # (V,4,4)
        tp = (M @ hom[:, :, None])[:, :, 0]                   # (V,4)
        out += w[:, None] * tp[:, :3]
    return out


def skinned_mesh_playback(model, skin_index=0, animation_index=0,
                          mesh_index=0, prim_index=0, t=0.0, loop=False):
    """端到端真实蒙皮网格回放（headless / 零 GL）。

    从 GltfModel 抽 skin + animation + mesh primitive，构造骨架与 clip，
    在时刻 t 采样局部 pose，计算蒙皮矩阵并线性蒙皮顶点。返回 dict：
    positions（蒙皮后 (V,3)）、bind_positions（原始 (V,3)）、
    skin_matrices（list[(4,4)]）、local_pose、skeleton、node_to_joint。

    注：顶点按 bind space 蒙皮输出，mesh 节点自身的世界变换由消费方
    （渲染端）叠加；本回放直接输出 bind-space 蒙皮坐标。无 inverseBind
    或无 JOINTS_0/WEIGHTS_0 时退化为静态坐标。
    """
    skins = model.skins()
    anims = model.animations()
    skin = skins[skin_index]
    anim = anims[animation_index]
    sk, node_to_joint = skeleton_from_gltf(model, skin)
    clip = clip_from_gltf_animation(model, anim, node_to_joint)
    local_pose = sample_clip(clip, t, loop, bind=sk.bind)
    prim = model.meshes[mesh_index].primitives[prim_index]
    arr = model.primitive_arrays(prim)
    positions = np.asarray(arr["POSITION"], dtype=float)
    if skin.inverse_bind is None:
        return {
            "positions": positions, "bind_positions": positions,
            "skin_matrices": None, "local_pose": local_pose,
            "skeleton": sk, "node_to_joint": node_to_joint,
        }
    skin_mats = compute_skin_matrices(sk, local_pose, skin.inverse_bind)
    joints = arr.get("JOINTS_0")
    weights = arr.get("WEIGHTS_0")
    if joints is None or weights is None:
        return {
            "positions": positions, "bind_positions": positions,
            "skin_matrices": skin_mats, "local_pose": local_pose,
            "skeleton": sk, "node_to_joint": node_to_joint,
        }
    skinned = skin_vertices(positions, np.asarray(joints, dtype=int),
                            np.asarray(weights, dtype=float), skin_mats)
    return {
        "positions": skinned,
        "bind_positions": positions,
        "skin_matrices": skin_mats, "local_pose": local_pose,
        "skeleton": sk, "node_to_joint": node_to_joint,
    }
