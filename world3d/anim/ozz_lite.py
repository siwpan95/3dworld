# -*- coding: utf-8 -*-
"""ozz-lite：轻量骨架动画层（纯数学 / 无 GL）。

按 3dworld engine 层「纯数学 / 无 GL / headless 可编译校验」定位实现。
借鉴 **ozz-animation**（MIT）的若干核心算法思路自研：

* 骨架层级（joint 父子链）与局部/世界变换的前向运动学；
* clip 的多轨采样（translation / rotation / scale 关键帧，线性或 slerp 插值）；
* 多 pose 的**惰性混合**（按权重归一化，旋转用 nlerp/slerp）；
* **加法动画**（相对参考 pose 求 delta，再按权重叠加到基 pose）；
* **分层**（带关节掩码把一层叠加到基 pose 上，含权重混合）；
* **root motion** 提取（根关节在一段时间内的局部变换增量）。

**不搬 ozz 的 C++ 源码、不照搬命名/结构**，仅按公开算法思路自研。
本模块刻意只做 headless 的数学子集（采样/混合/分层/root motion），不做压缩
（ACL 属另一许可体系，按纪律不引入）。
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
        """给定每关节局部 (t,r,s) 列表，返回世界 4x4 矩阵列表。"""
        loc = self.bind if local is None else local
        world = []
        for i, (t, r, s) in enumerate(loc):
            m = _trs_to_4x4(t, r, s)
            p = self.parents[i]
            if p >= 0:
                m = world[p] @ m
            world.append(m)
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


def sample_clip(clip, t, loop=False):
    """采样 clip 在时刻 t 的局部 pose：返回 list[(t,r,s)]（与骨架关节同序）。"""
    if loop and clip.duration > _EPS:
        t = t % clip.duration
    # 用 tracks 的 key 范围确定关节数
    n_joints = max(list(clip.tracks.keys()) + [0]) + 1
    pose = [None] * n_joints
    for j, track in clip.tracks.items():
        tloc = _sample_track(track['translation'][0], track['translation'][1], t) \
            if 'translation' in track else np.zeros(3)
        rloc = _sample_track(track['rotation'][0], track['rotation'][1], t, slerp=True) \
            if 'rotation' in track else q_identity()
        sloc = _sample_track(track['scale'][0], track['scale'][1], t) \
            if 'scale' in track else np.ones(3)
        pose[j] = (np.asarray(tloc, float), np.asarray(rloc, float), np.asarray(sloc, float))
    # 未提供轨道的关节用单位局部变换
    for j in range(n_joints):
        if pose[j] is None:
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
