# -*- coding: utf-8 -*-
"""批量打包 demos / 演出为独立 exe（PyInstaller 封装），并组装发行版。

前置：当前 Python 环境需安装 PyInstaller（本项目用隔离环境，见 docs/分发指南.md）::

    pip install pyinstaller

用法（项目根目录）::

    python scripts/build_exe.py --list          列出可打包入口
    python scripts/build_exe.py --only tunnel,engine_showcase
                                                打包指定入口（name 见 --list）
    python scripts/build_exe.py --all           打包全部入口
    python scripts/build_exe.py --pack          打包「发行版精选」并组装到
                                                dist/pack-YYYYMMDD/（含 README.txt）

产物：dist/<name>.exe（--onefile）；--pack 另组装 dist/pack-*/ 目录。
"""
from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# name, 模块路径, exe 文件名, 发行版精选, 附加 PyInstaller 参数
ENTRIES = [
    ('main',            'main.py',                      '3dworld_show_48s.exe',   False, []),
    ('kkrieger',        'kkrieger_demo.py',             '3dworld_kkrieger_150s.exe', False, []),
    ('nordic_saga',     'examples/nordic_saga.py',         '3dworld_nordic_60s.exe', False, []),
    ('engine_showcase', 'examples/engine_showcase.py',     '3dworld_engine_showcase.exe', True, []),
    ('material_lab',    'examples/material_lab.py',        '3dworld_material_lab.exe', True, []),
    ('minecraft_style', 'examples/minecraft_style.py',     '3dworld_minecraft.exe',  True, []),
    ('solar_system',    'examples/solar_system.py',        '3dworld_solar_system.exe', True, []),
    ('water_waves',     'examples/water_waves.py',         '3dworld_water_waves.exe', True, []),
    ('demoscene_tunnel', 'examples/demoscene_tunnel.py',   '3dworld_tunnel.exe',     True, []),
    ('raymarch_sdf',    'examples/raymarch_sdf.py',        '3dworld_raymarch.exe',   False, []),
    ('particles_gpu',   'examples/particles_gpu.py',       '3dworld_particles.exe',  False, []),
    ('boids',           'examples/boids.py',               '3dworld_boids.exe',      False, []),
    ('shadow_map',      'examples/shadow_map.py',          '3dworld_shadow_map.exe', False, []),
    ('terrain_flyover', 'examples/terrain_flyover.py',     '3dworld_terrain.exe',    False, []),
    ('voxel_terrain',   'examples/voxel_terrain.py',       '3dworld_voxel.exe',      False, []),
]

PACK_DESC = {
    '3dworld_engine_showcase.exe': '引擎综合巡礼（PBR/IBL/Bloom/SSAO/实例化/物理/拾取）',
    '3dworld_material_lab.exe': '材质实验室（车削/管扫/环面结 × 程序化 PBR 纹理）',
    '3dworld_minecraft.exe': '方块世界 48s 短片（程序化像素材质 + 昼夜光照）',
    '3dworld_solar_system.exe': '太阳系（层级变换 + 轨道力学 + 实例化小行星带）',
    '3dworld_water_waves.exe': '水面（Gerstner 波顶点位移 + 菲涅尔天空）',
    '3dworld_tunnel.exe': '无限隧道（纯片元着色器 demoscene）',
}

README_TXT = """3dworld demo pack —— 程序化 3D 演示合集（Windows 独立版）
================================================================

来源：https://gitee.com/siwpan/3dworld （MIT 许可，零外部素材、全程序化生成）
运行：双击任意 exe 即可（无需安装 Python）。首次启动需数秒解包，属正常现象。

内含：
{desc}

通用操作：
  ESC 退出   SPACE 暂停/继续   鼠标拖动/滚轮 切换/环绕视角
  部分演示支持更多按键（右下角 HUD 有帮助行）

说明：
  - 所有画面与音乐均由代码实时生成，不含任何图片/模型/音频素材文件；
  - 若杀毒软件误报（PyInstaller 常见误报），请加入信任；
  - 视频版（MP4）与源码见仓库主页。

打包日期：{date}
"""


def find_pyinstaller():
    r = subprocess.run([sys.executable, '-m', 'PyInstaller', '--version'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('[build] 当前环境没有 PyInstaller，请先 pip install pyinstaller')
    return r.stdout.strip()


def build_one(name, mod, exe_name, extra):
    print(f'===== building {name} -> dist/{exe_name} =====', flush=True)
    cmd = [sys.executable, '-m', 'PyInstaller', '--onefile', '--console',
           '--name', exe_name.replace('.exe', ''),
           '--paths', '.',
           '--distpath', 'dist', '--workpath', 'build/pyi',
           '--specpath', 'build/pyi'] + list(extra) + [mod]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:], flush=True)
        print(r.stderr[-800:], flush=True)
        return False
    print('  [ok]', flush=True)
    return True


def main(argv=None):
    p = argparse.ArgumentParser(description='batch exe builder (PyInstaller)')
    p.add_argument('--list', action='store_true')
    p.add_argument('--only', default=None, help='comma-separated names')
    p.add_argument('--all', action='store_true')
    p.add_argument('--pack', action='store_true', help='build release pack')
    args = p.parse_args(argv)

    if args.list:
        print(f'{"name":18s} {"module":32s} {"exe":32s} 精选')
        for name, mod, exe, picked, _ in ENTRIES:
            print(f'{name:18s} {mod:32s} {exe:32s} {"*" if picked else ""}')
        return 0

    ver = find_pyinstaller()
    print(f'[build] PyInstaller {ver}', flush=True)

    if args.all:
        targets = ENTRIES
    elif args.only:
        names = {x.strip() for x in args.only.split(',') if x.strip()}
        targets = [e for e in ENTRIES if e[0] in names]
        missing = names - {e[0] for e in targets}
        if missing:
            sys.exit(f'[build] 未知入口: {sorted(missing)}（--list 查看）')
    elif args.pack:
        targets = [e for e in ENTRIES if e[3]]
    else:
        p.print_help()
        return 0

    ok, failed = [], []
    for name, mod, exe, _picked, extra in targets:
        (ok if build_one(name, mod, exe, extra) else failed).append(name)

    print(f'[build] done: {len(ok)} ok, {len(failed)} failed {failed}', flush=True)

    if args.pack and not failed:
        date = datetime.date.today().strftime('%Y%m%d')
        pack_dir = os.path.join(ROOT, 'dist', f'3dworld-demo-pack-{date}')
        os.makedirs(pack_dir, exist_ok=True)
        import shutil
        descs = []
        for name, mod, exe, picked, _ in ENTRIES:
            if not picked:
                continue
            src = os.path.join(ROOT, 'dist', exe.replace('.exe', '') + '.exe')
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(pack_dir, exe))
                descs.append(f'  {exe:36s} {PACK_DESC.get(exe, name)}')
        with open(os.path.join(pack_dir, 'README.txt'), 'w', encoding='utf-8') as f:
            f.write(README_TXT.format(desc='\n'.join(descs),
                                      date=f'{date[:4]}-{date[4:6]}-{date[6:]}'))
        total = sum(os.path.getsize(os.path.join(pack_dir, n))
                    for n in os.listdir(pack_dir))
        print(f'[pack] assembled -> {pack_dir} '
              f'({len(descs)} exe, {total / 1048576:.1f} MB)', flush=True)
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
