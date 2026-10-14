# -*- coding: utf-8 -*-
"""机械门禁（P4）—— 把"同一事实写在两处必须相等"从散文变成 <1 秒可跑的检查。

## 为什么这个项目**特别**需要它

本轮改造里，**同一件事被手改过 5~6 次**：示例数（`37 个` 在 README 里出现 6 处）、
测试模块清单与逐模块用例数、配置树/内核/共同核计数、文档间相对链接（P2-4 搬了 205 个文件）、
`cli` 的用法 docstring（P2-4 改了命令名）。**这些手改每一次都可能漏一处。**

门禁上线**当天就抓到一个真漂移**：`tests/test_timeline_ui` 在 `run_all_tests.py` 里
（14 个模块）却没进 README 的测试清单（只列了 13 个）。

## 设计约束（照抄 `repo-consistency-gates` 的踩坑表）

* **只校验"现状"，豁免"快照"**：README 里"当前有 N 个示例"是现状（必须同步）；
  `CHANGELOG` 里的历史计数是快照（**不校验**）。
* **失败信息要点名哪两处不一致、附两边实际值**；通过时也要**给数字**。
* **跳过 ≠ 通过**：前提不满足记 SKIP 并说明原因，不计入通过数。
* **不跑构建、不联网、不改文件**（`--fix` 只重新生成 `docs/README.md`）。
* **每条门禁都要能回答"我把它防的那件事做出来，它会不会红"** —— 见 `--selfcheck`。

用法::

    python scripts/verify.py                 # 全部门禁
    python scripts/verify.py --list          # 只列名字
    python scripts/verify.py --only docs-index-fresh,readme-demo-count
    python scripts/verify.py --verbose       # 打印每条的证据
    python scripts/verify.py --json          # 机器可读
    python scripts/verify.py --fix           # 只重新生成生成物（docs/README.md）
    python scripts/verify.py --selfcheck     # 在沙箱里逐条注入破坏，证明它拦得住
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================================== 框架 ==


class Skip(Exception):
    """前提不满足（缺文件/缺工具）—— 记 SKIP，不计入通过数。"""


class Fail(Exception):
    """门禁不通过。消息里必须点名**哪两处不一致 + 两边实际值**。"""


GATES = []

#: `python -m venv` / `python -m PyInstaller …` 这类是**工具**，不是本项目的模块。
#: （写门禁时先把文档里真实出现过的命令列一遍 —— 凭想象会把这些记成"死命令"。）
TOOLING_MODULES = {'venv', 'pip', 'pytest', 'unittest', 'json', 'http', 'compileall',
                   'ensurepip', 'PyInstaller', 'pyinstaller', 'pydoc'}


def gate(name, desc):
    def deco(fn):
        GATES.append((name, desc, fn))
        return fn
    return deco


# ====================================================== 权威源（不 import 项目模块）==
# 刻意**不用 import**：import 会拖进 pyglet / 开窗口 / 几十个 demo 模块，
# 而门禁要能在 <1 秒、无 GL 的环境（含 pre-commit）里跑。

def _read(root, rel):
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        raise Skip(f'{rel} 不存在')
    return open(path, encoding='utf-8').read()


def _ast_value(root, rel, name):
    """AST 取某个模块级赋值（比正则稳；`WANT_CORES` / `MODULES` / `SKIP` 都用它）。"""
    tree = ast.parse(_read(root, rel))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == name:
            return ast.literal_eval(node.value)
    raise Skip(f'{rel} 里没有 {name}（可能改成动态构造了）')


def real_demos(root):
    """真实可发现的 demo：与 `verify_demos.py` 同规则（examples/*.py 减 SKIP）。"""
    skip = set(_ast_value(root, 'scripts/verify_demos.py', 'SKIP'))
    ed = os.path.join(root, 'examples')
    return sorted(f[:-3] for f in os.listdir(ed)
                  if f.endswith('.py') and f not in skip)


def real_test_modules(root):
    mods = _ast_value(root, 'scripts/run_all_tests.py', 'MODULES')
    return [(m.split('.')[-1], flag) for m, flag, _label in mods]


def test_case_count(root, module_name):
    """测试模块**声明**的用例数。两种真实形态都要认（写之前先 grep 过实际写法）：

    * **新式**（`tests = [...]` 显式运行清单）—— brickward / csm / volumetrics / dof /
      motionblur / framegraph / compat 都是这种；
    * **旧式**（没有清单，靠 `def test_*` 函数计数）—— run_tests / test_cores / test_skin /
      test_gpu_cull / test_p2_render 是这种。

    我第一版只认新式，于是那 5 个模块全被记成 SKIP —— **而它们的 README 数字其实是对的**
    （62/17/8/7/7 逐个吻合）。**门禁的启发式必须来自真实措辞，不能凭想象。**

    两种形态同时存在时还断言它们相等 —— 那能抓"定义了测试函数却没注册进 runner"
    （= 定义了但永远不会跑）这种更隐蔽的问题。
    """
    src = _read(root, f'tests/{module_name}.py')
    tree = ast.parse(src)
    listed = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == 'tests' \
                        and isinstance(node.value, (ast.List, ast.Tuple)):
                    listed = len(node.value.elts)
    defined = sum(1 for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name.startswith('test_'))
    if listed is not None and defined and listed != defined:
        raise Fail(f'tests/{module_name}.py: 定义了 {defined} 个 `test_*`，'
                   f'但 runner 里只注册了 {listed} 个（有测试永远不会跑）')
    if listed is not None:
        return listed, 'runner 清单'
    if defined:
        return defined, 'def test_* 计数'
    raise Skip(f'tests/{module_name}.py 里既没有 runner 清单也没有 test_* 函数')


def _fenced(text, heading):
    """取某个 `## 标题` 之下的第一个围栏代码块。"""
    m = re.search(re.escape(heading) + r'\n+```[a-z]*\n(.*?)```', text, re.S)
    if not m:
        raise Skip(f'找不到 `{heading}` 下的代码块')
    return m.group(1)


# ============================================================== 门禁 ==


@gate('readme-demo-count', 'README 里每一处示例数 == 真实可发现 demo 数')
def g_demo_count(root, verbose):
    n = len(real_demos(root))
    text = _read(root, 'README.md')
    hits = re.findall(r'(\d+)\s*个\s*(?:独立示例与工具|单文件示例与工具|单文件\s*3D\s*示例与工具|demo)',
                      text)
    if not hits:
        raise Fail('README 里一处示例数都没匹配到（措辞变了？先 grep 真实措辞再改正则）')
    bad = [(i + 1, v) for i, v in enumerate(hits) if int(v) != n]
    if bad:
        raise Fail(f'真实 demo 数 = {n}，但 README 有 {len(bad)} 处不一致：'
                   + '；'.join(f'第 {i} 处写 {v}' for i, v in bad))
    return f'{len(hits)} 处断言 == {n} 个 demo'


@gate('readme-test-modules', 'README 的测试命令表 ↔ run_all_tests 的模块表（双向）')
def g_test_modules(root, verbose):
    have = {m for m, _f in real_test_modules(root)}
    text = _read(root, 'README.md')
    listed = set(re.findall(r'python -m tests\.(\w+)', text))
    missing = sorted(have - listed)
    extra = sorted(listed - have)
    if missing or extra:
        msg = []
        if missing:
            msg.append(f'注册了但 README 没列：{missing}')
        if extra:
            msg.append(f'README 列了但没注册：{extra}')
        raise Fail('；'.join(msg))
    return f'{len(have)} 个模块双向一致'


@gate('readme-test-counts', 'README 每行的「N 项」== 该模块声明用例数')
def g_test_counts(root, verbose):
    text = _read(root, 'README.md')
    pairs = re.findall(r'python -m tests\.(\w+)\s+[^\n（]*（(\d+)\s*项', text)
    if not pairs:
        raise Fail('README 里没匹配到「N 项」的写法')
    bad, checked, how = [], 0, {}
    for name, claimed in pairs:
        try:
            real, rule = test_case_count(root, name)
        except Skip as e:
            bad.append(f'{name}: {e}')
            continue
        checked += 1
        how[rule] = how.get(rule, 0) + 1
        if real != int(claimed):
            bad.append(f'{name}: README 写 {claimed} 项，实际声明 {real} 项')
    if bad:
        raise Fail('；'.join(bad))
    tail = '、'.join(f'{v} 个用{k}' for k, v in sorted(how.items()))
    return f'{checked} 个模块用例数与声明一致（{tail}）'


@gate('readme-config-count', 'README 的配置树数 == configs/cordis.*.yml 数')
def g_config_count(root, verbose):
    n = len([f for f in os.listdir(os.path.join(root, 'configs'))
             if f.startswith('cordis.') and f.endswith('.yml')])
    text = _read(root, 'README.md')
    hits = re.findall(r'(\d+)\s*棵配置树', text)
    if not hits:
        raise Fail('README 里没匹配到「N 棵配置树」')
    bad = [v for v in hits if int(v) != n]
    if bad:
        raise Fail(f'真实 {n} 棵，README 里不一致的写法：{bad}')
    return f'{len(hits)} 处断言 == {n} 棵配置树'


@gate('readme-kernel-count', 'README 的内核数 == games/*/kernel.py 数')
def g_kernel_count(root, verbose):
    gd = os.path.join(root, 'world3d', 'games')
    n = len([d for d in os.listdir(gd)
             if os.path.exists(os.path.join(gd, d, 'kernel.py'))])
    text = _read(root, 'README.md')
    hits = re.findall(r'(\d+)\s*个\s*(?:内核|Cordis 内核)', text)
    if not hits:
        raise Fail('README 里没匹配到「N 个内核」')
    bad = [v for v in hits if int(v) != n]
    if bad:
        raise Fail(f'真实 {n} 个内核，README 里不一致的写法：{bad}')
    return f'{len(hits)} 处断言 == {n} 个内核'


@gate('readme-core-count', 'README 的共同核数 == host/tree.WANT_CORES 长度')
def g_core_count(root, verbose):
    n = len(_ast_value(root, 'world3d/host/tree.py', 'WANT_CORES'))
    text = _read(root, 'README.md')
    hits = re.findall(r'(\d+)\s*(?:个)?\s*共同核', text)
    if not hits:
        raise Fail('README 里没匹配到「N 共同核」')
    bad = [v for v in hits if int(v) != n]
    if bad:
        raise Fail(f'真实 {n} 个共同核，README 里不一致的写法：{bad}')
    return f'{len(hits)} 处断言 == {n} 个共同核'


@gate('examples-index-pairs', 'examples/README.md 的表格 ↔ examples/*.py（双向）')
def g_examples_index(root, verbose):
    text = _read(root, 'examples/README.md')
    listed = set(re.findall(r'^\|\s*`([\w.]+\.py)`', text, re.M))
    have = {f + '.py' for f in real_demos(root)}
    missing = sorted(have - listed)
    ghosts = sorted(listed - set(
        f for f in os.listdir(os.path.join(root, 'examples')) if f.endswith('.py')))
    if missing or ghosts:
        msg = []
        if missing:
            msg.append(f'有 demo 但表里没行：{missing}')
        if ghosts:
            msg.append(f'表里有行但文件不存在：{ghosts}')
        raise Fail('；'.join(msg))
    return f'{len(have)} 个 demo 与索引双向一致（表内 {len(listed)} 行）'


@gate('doc-links', 'README/docs/examples 里的相对链接都指得到真实文件')
def g_doc_links(root, verbose):
    targets = ['README.md', 'examples/README.md']
    dd = os.path.join(root, 'docs')
    targets += [f'docs/{f}' for f in sorted(os.listdir(dd)) if f.endswith('.md')]
    dead, checked = [], 0
    link_re = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)')
    for rel in targets:
        base = os.path.dirname(os.path.join(root, rel))
        for url in link_re.findall(_read(root, rel)):
            if url.startswith(('http://', 'https://', 'mailto:', '#')):
                continue
            path = url.split('#')[0]
            if not path:
                continue
            checked += 1
            if not os.path.exists(os.path.normpath(os.path.join(base, path))):
                dead.append(f'{rel} → {url}')
    if dead:
        raise Fail(f'{len(dead)} 条死链：' + '；'.join(dead[:8])
                   + ('…' if len(dead) > 8 else ''))
    return f'{checked} 条相对链接全部有效（{len(targets)} 个文件）'


@gate('docs-index-fresh', 'docs/README.md == scripts/docs_index.py 重新生成的结果')
def g_docs_index(root, verbose):
    sys.path.insert(0, os.path.join(root, 'scripts'))
    try:
        import docs_index
        importlib = None
        expected = docs_index.build_index()
    finally:
        sys.path.pop(0)
    del importlib
    path = os.path.join(root, 'docs', 'README.md')
    if not os.path.exists(path):
        raise Fail('docs/README.md 不存在 —— 跑 `python scripts/docs_index.py --write`')
    cur = open(path, encoding='utf-8').read()
    if cur != expected:
        raise Fail('docs/README.md 不是最新的 —— 跑 '
                   '`python scripts/docs_index.py --write`（或 `verify.py --fix`）')
    from_index = len(re.findall(r'^\|\s*\[', expected, re.M))
    return f'索引最新（{from_index} 篇登记）'


@gate('commands-exist', 'README/docs 里写的每条命令，目标都真的存在')
def g_commands(root, verbose):
    files = ['README.md', 'examples/README.md', 'docs/分发指南.md', 'docs/引擎设计.md']
    bad, n = [], 0
    for rel in files:
        if not os.path.exists(os.path.join(root, rel)):
            continue
        text = _read(root, rel)
        for mod in set(re.findall(r'python -m ([\w.]+)', text)):
            if mod.split('.')[0] in TOOLING_MODULES:
                continue            # `python -m venv` 这类是工具，不是本项目模块
            n += 1
            if not _module_exists(root, mod):
                bad.append(f'{rel}: python -m {mod}')
        # `python scripts/x.py` 与 `python x.py` **两种写法 README 里都有**：
        # 后者是"先 cd 进 examples/ 再跑"的简写 ⇒ 要在 scripts/ examples/ 根 三处找
        for script in set(re.findall(r'python ([\w./\\-]+\.py)', text)):
            rel2 = script.replace('\\', '/')
            if rel2.split('/')[0] in ('scripts', 'examples'):
                cands = [rel2]
            else:
                cands = [rel2, f'scripts/{rel2}', f'examples/{rel2}']
            n += 1
            if not any(os.path.exists(os.path.join(root, c)) for c in cands):
                bad.append(f'{rel}: python {script}')
    if bad:
        raise Fail(f'{len(bad)} 条命令指向不存在的目标：' + '；'.join(bad[:8]))
    return f'{n} 条命令全部可达'


def _module_exists(root, mod):
    """`a.b.c` → 存在 a/b/c.py 或 a/b/c/__init__.py（**只按仓库根**）。

    ⚠ **不要加 `world3d/` 兜底**：那会让"忘了改前缀"的旧引用（`python -m cli.x`、
    `from core import contracts`）照样通过 —— P2-4 之前它们合法，之后已经失效。
    我第一版就留了这个兜底，结果 `docs/P0-1` 里那条 `from core import contracts`
    明明已经跑不通、门禁却报"全部可达"（**假绿**）。
    **门禁的容忍度要跟着"现状"走，不能留着历史包袱。**
    """
    parts = mod.split('.')
    p = os.path.join(root, *parts)
    return os.path.exists(p + '.py') or os.path.exists(os.path.join(p, '__init__.py'))


@gate('pyrefs-exist', '文档/YAML 里 `python -c "import <模块>"` 的模块真的存在')
def g_pyrefs(root, verbose):
    """只扫 **`python -c "…"` 一行式命令**里的 `import`（.md / .yml / .yaml 全部）。

    **这条是 P4-b 实踩出来的**：P2-4 把 `_cordis_locate` 搬进了 `world3d/`，
    而 `.github/workflows/ci.yml` 里那行 `python -c "import _cordis_locate; …"`
    没跟着改 —— 迁移脚本只覆盖**行首** `import X`，没覆盖**命令行字符串里的** import。
    症状是那条 CI 步骤直接 `ModuleNotFoundError`，而**没有任何测试或门禁会碰到它**
    （CI 在 GitHub 上跑，本机验收根本不执行 workflow）。

    ⚠ 范围必须收紧到"一行式命令"：我第一版扫全文任意 `import \\w+`，
    结果把散文里的 `import 与`、`import 模块`、占位符 `import X` 全算成了模块（45 处假阳性）。
    **门禁太松和太紧一样有害 —— 前者让人无视它。**

    ⚠ 还有第 4 条范围约束：**它连"引用这个模式"的散文也一起抓。**
    写 P4 设计说明时我原样引用了这个模式，当场 6 处变红 ⇒ 定下全仓文档约定：
    **引用模式时写占位符 `python -c "import <模块>"`**（`<` 不在 `[A-Za-z_]` 里，天然排除）。
    与 `commands-exist` 的 `python <文件>.py` 同源 —— **能被机械校验的字面必须是"真命令"；
    只想说明形状，就用占位符。**
    """
    bad, n = [], 0
    cmd_re = re.compile(r'python\s+-c\s+["\'](.+?)["\']', re.S)
    # 两种形态都要认，而且**只认语句开头**（`^` 或 `;` 之后）：
    #   * `import a.b.c`     → 模块必须存在
    #   * `from a.b import C` → **包**必须存在（C 可能是类，也可能是子模块，不校验名字）
    # 不限定语句开头就会把 `from core import contracts` 里的 `contracts` 当成顶层模块
    # （假阳性）—— 而"from 形态"本身恰恰是最常见的写法，必须覆盖。
    imp_re = re.compile(r'(?:^|;)\s*import\s+([A-Za-z_][\w.]*)')
    from_re = re.compile(r'(?:^|;)\s*from\s+([A-Za-z_][\w.]*)\s+import')
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ('.git', '.venv', 'build', '__pycache__', 'vendor',
                                    'recordings', 'shots', 'node_modules')]
        for fn in filenames:
            if not fn.endswith(('.md', '.yml', '.yaml')):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace('\\', '/')
            text = _read(root, rel)
            for payload in cmd_re.findall(text):
                refs = ([(m, 'import') for m in imp_re.findall(payload)]
                        + [(m, 'from') for m in from_re.findall(payload)])
                for mod, kind in refs:
                    head = mod.split('.')[0]
                    if (head in TOOLING_MODULES or head in _STDLIB_HINTS
                            or head in sys.stdlib_module_names):
                        continue
                    n += 1
                    if not _module_exists(root, mod):
                        bad.append(f'{rel}: python -c "{kind} {mod} …"')
    if bad:
        raise Fail(f'{len(bad)} 处一行式命令引用了不存在的模块：' + '；'.join(bad[:8]))
    return f'{n} 处 `python -c` 里的 import/from 全部可达（含 .github/workflows）'


def _load_script(path, tag):
    """按**路径 + 唯一模块名**加载一个仓库内脚本。

    ⚠ 不能 `import`：`sys.modules` 会缓存第一次的导入，selfcheck 在沙箱里改过的副本
    就**永远不会被重新加载**（P4-b 实踩：破坏用例照样 PASS）。
    """
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader(f'_{tag}_{abs(hash(path))}', path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _ensure_import_roots(root):
    """把**真实仓库根**放到 `sys.path` 前面、把被检的 `root`（selfcheck 里是沙箱）放到后面。

    **门禁之间不能互相污染 import 状态**：selfcheck 的沙箱不拷 `vendor/`（50MB→2MB 的
    取舍），所以一旦沙箱里的 `world3d` 抢先被 import 进 `sys.modules`，后面**依赖 cordis**
    的门禁（内核模板：内核就是 cordis Service）就会连带报「未找到 cordis 包」——
    那是沙箱的局限，不是被检对象的缺陷。P5-a 加内核模板门禁时正是这么被 selfcheck
    抓出来的（"沙箱基线就是 FAIL"），根因却在**上一条**门禁把沙箱插到了 sys.path[0]。
    """
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    if root not in sys.path:
        sys.path.append(root)


@gate('scaffold-templates-valid', '脚手架模板仍能渲染 / 可解析 / 关键属性齐全')
def g_scaffold(root, verbose):
    """模板会**随项目演进腐掉**（导入路径改名、`DemoApp` 钩子改名、返回类型变了）——
    而它腐掉时**没人会发现**，直到下一个用它的人踩坑。这条门禁把模板钉在"现状"上。

    检查四项：渲染 → `ast.parse` → **真 import**（抓"导入了不存在的符号"，
    `ast` 抓不到）→ 关键属性齐全（`TITLE/HELP/SHOTS`、`setup/render`、runner 标记）。

    ⚠ "模板能跑"（`--smoke` 真开窗口）由本批**手测**过（`examples/zzprobe.py --smoke 3
    → EXIT_OK`）；门禁只做静态 + import 校验 —— 真跑要 GL 且会破坏 <1s 预算。
    """
    scaffold = os.path.join(root, 'scripts', 'new_demo.py')
    if not os.path.exists(scaffold):
        raise Skip('scripts/new_demo.py 不存在')
    N = _load_script(scaffold, 'scaffold')
    demo = N.render_demo('zzprobe')
    test = N.render_test('zzprobe')

    for label, text in (('demo', demo), ('test', test)):
        try:
            ast.parse(text)
        except SyntaxError as e:
            raise Fail(f'{label} 模板渲染后不是合法 Python：{e}')

    # ⚠ 断言必须**带括号**：只写 `'def render'` 的话 `def render_broken` 也算"出现过"
    # —— selfcheck 正是用这条破坏用例指出我的断言太松（宽松的"出现过"是假绿高发区）。
    need_demo = ('TITLE', 'HELP', 'SHOTS', 'def setup(self):', 'def update(self, dt):',
                 'def render(self):', 'DemoApp', 'world3d.platform.app',
                 'sys.path.insert', '__main__')
    miss = [w for w in need_demo if w not in demo]
    if miss:
        raise Fail(f'demo 模板缺关键片段：{miss}')
    need_test = ('def main() -> int:', 'sys.exit(main())', 'ALL_ZZPROBE_TESTS_PASSED',
                 'ALL_ZZPROBE_TESTS_FAILED')
    miss = [w for w in need_test if w not in test]
    if miss:
        raise Fail(f'test 模板缺关键片段：{miss}')

    # 真 import 一次（`ast` 抓不到"import 了不存在的名字"）
    with tempfile.TemporaryDirectory() as td:
        _ensure_import_roots(root)
        for fn, text, label in (('zzprobe_demo.py', demo, 'demo'),
                                ('test_zzprobe.py', test, 'test')):
            path = os.path.join(td, fn)
            open(path, 'w', encoding='utf-8', newline='\n').write(text)
            spec = importlib.util.spec_from_file_location(f'zz_scaffold_{label}', path)
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception as e:                      # noqa: BLE001
                raise Fail(f'{label} 模板真 import 失败（{type(e).__name__}: {e}）'
                           f' —— 模板引用了已不存在/已改名的符号')
    return f'两套模板渲染 + ast + 真 import + {len(need_demo) + len(need_test)} 项关键片段全通过'


@gate('scaffold-kernel-valid', '内核模板：渲染 → ast → 真 import → 跑通 run_generic 的契约')
def g_kernel_scaffold(root, verbose):
    """内核模板比 demo 模板**更容易腐**，因为它在**别的文件里**被三处引用：

    * `cli/config_boot.run_generic` 按 `inject / status / auto_step` 三条约定验收它；
    * `host/tree.py` 的 `WANT_<名>` 决定就绪判定要等哪些服务；
    * `host/registry.py` 的 `SERVICES` 决定服务短名解析成哪个类。

    所以这条门禁不止"片段对得上"，而是**把模板真跑一遍**，四段：
    渲染 → `ast.parse` → 真 import → **按 `run_generic` 的断言跑一次**（跑出 `won`）。

    ⚠ 真 import 用 `exec` + 自造模块，而不是落盘到 `world3d/games/<名>/`：
    模板是靠 `__file__` 算自举根的，所以把 `__file__` 设成"将要去的位置"
    （`<root>/world3d/games/zzkern/kernel.py`）就能让自举块算出**真实的仓库根** ——
    顺带把"自举块层数对不对"也验了（层数错了 `_ROOT` 就不是仓库根）。
    测试模板则靠**把渲染出的内核先塞进 `sys.modules`** 才 import 得动
    （它 `from world3d.games.<名>.kernel import …`，而那个包此刻并不存在）。
    """
    import types

    scaffold = os.path.join(root, 'scripts', 'new_kernel.py')
    if not os.path.exists(scaffold):
        raise Skip('scripts/new_kernel.py 不存在')
    N = _load_script(scaffold, 'kernelscaffold')
    name, up = 'zzkern', 'ZZKERN'
    cls = N._cls_name(name)
    src = {
        'init': N.render_games_init(name),
        'kernel': N.render_kernel(name),
        'test': N.render_test(name),
        'config': N.render_config(name),
    }
    for label in ('init', 'kernel', 'test'):
        try:
            ast.parse(src[label])
        except SyntaxError as e:
            raise Fail(f'{label} 模板渲染后不是合法 Python：{e}')

    # ---- 关键片段（**带冒号/括号**：'def status' 会被 'def status_broken' 匹配到）----
    need_kernel = (f'class {cls}(Service):', '    inject = []',
                   '    def status(self) -> str:', '    def auto_step(self, dt: float):',
                   'def make_offline(', '__all__', f"super().__init__(ctx, '{name}')")
    miss = [w for w in need_kernel if w not in src['kernel']]
    if miss:
        raise Fail(f'内核模板缺关键片段：{miss}')
    need_cfg = (f'- name: {name}', '- name: render', '  config:', f'    seed: {N.SEED}')
    miss = [w for w in need_cfg if w not in src['config']]
    if miss:
        raise Fail(f'配置树模板缺关键片段：{miss}')
    need_test = ('def main() -> int:', 'sys.exit(main())', f'ALL_{up}_TESTS_PASSED',
                 f'ALL_{up}_TESTS_FAILED', 'make_offline')
    miss = [w for w in need_test if w not in src['test']]
    if miss:
        raise Fail(f'测试模板缺关键片段：{miss}')

    # ---- 真 import（`exec` + 自造模块：见 docstring）----
    #
    # ⚠ 用 `_ensure_import_roots`：**真实仓库根在前、被检 root 在后**。内核是本仓库里
    # 唯一"必须依赖 cordis"的模板，而沙箱不拷 `vendor/` —— 若沙箱的 `world3d` 抢先被
    # import，`ensure()` 就会报「未找到 cordis 包」（沙箱的局限，不是模板的问题）。
    # 模板自身要验的是**自举根算得对不对**，那条仍按被检 root 断言（见下面的 `_ROOT` 检查）。
    _ensure_import_roots(root)
    import world3d._cordis_locate as _loc                      # noqa: PLC0415
    _loc.ensure()

    pkg = f'world3d.games.{name}'
    kpath = os.path.join(root, 'world3d', 'games', name, 'kernel.py')
    saved = {k: sys.modules.get(k) for k in (pkg, pkg + '.kernel')}
    try:
        pkgmod = types.ModuleType(pkg)
        pkgmod.__path__ = []
        sys.modules[pkg] = pkgmod
        kmod = types.ModuleType(pkg + '.kernel')
        kmod.__file__ = kpath
        sys.modules[pkg + '.kernel'] = kmod
        exec(compile(src['kernel'], kpath, 'exec'), kmod.__dict__)     # noqa: S102
        tmod = types.ModuleType(f'zz_test_{name}')
        tmod.__file__ = os.path.join(root, 'tests', f'test_{name}.py')  # 自举块要它
        exec(compile(src['test'], tmod.__file__, 'exec'), tmod.__dict__)  # noqa: S102
    except Exception as e:                                             # noqa: BLE001
        raise Fail(f'模板真 import 失败（{type(e).__name__}: {e}）'
                   f' —— 模板引用了已不存在/已改名的符号')
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    # ---- 自举块层数：`_ROOT` 必须等于仓库根（少一层就会让 world3d/ 遮蔽标准库）----
    if os.path.abspath(getattr(kmod, '_ROOT', '')) != os.path.abspath(root):
        raise Fail(f'内核模板的自举根算错了：_ROOT={getattr(kmod, "_ROOT", None)!r}，'
                   f'应为仓库根 {root!r}（层数错会让 world3d/ 上 sys.path ⇒ 遮蔽 platform）')

    # ---- 按 `run_generic` 的断言跑一次（模板必须真的能满足那份契约）----
    K = getattr(kmod, cls)
    if list(getattr(K, 'inject', None) or []) != []:
        raise Fail(f'内核模板的 inject={getattr(K, "inject", None)!r} 不是 []，'
                   f'与 run_generic 的"纯逻辑内核"契约不符')
    k = kmod.make_offline(kmod.DEFAULT_SEED)
    st0 = k.status()
    if st0 not in ('playing', 'prep'):
        raise Fail(f'内核模板 status() 初值 {st0!r} 不在 (playing, prep) —— '
                   f'run_generic 会直接断言失败')
    t = 0.0
    while k.status() not in ('won', 'lost') and t < 120.0:
        k.auto_step(1.0 / 60.0)
        t += 1.0 / 60.0
    if k.status() != 'won':
        raise Fail(f'内核模板 auto_step 未在 120s 内收尾（state={k.status()!r}）'
                   f' —— run_generic 会判它"只是能构造"')
    return (f'内核/配置树/测试三套模板渲染 + ast + 真 import + '
            f'{len(need_kernel) + len(need_cfg) + len(need_test)} 项关键片段，'
            f'并按 run_generic 契约跑到 won（{t:.1f}s / {k.steps} 步）')


#: 允许在任何 `python -c` 里出现的**非标准库**模块（标准库由 `sys.stdlib_module_names` 判定，
#: 不必在这里一个个列 —— 早先手维护这份名单，结果文档里写一句
#: `python -c "import zipfile, hashlib; …"` 就被判成"模块不存在"）
_STDLIB_HINTS = {'pyglet', 'yaml', 'cordis'}


@gate('workflows-valid', 'CI workflow 可解析 / 结构完整 / 里面的命令都可达')
def g_workflows(root, verbose):
    """`.github/workflows/*.yml` 是**本机跑不到的那一半**（只有 push 到 GitHub 才会执行），
    所以它坏了也**没人会在本地发现** —— 正好是"机械门禁该管的事"。

    三层检查：YAML 可解析 → 结构完整（`on` + 每个 job 有 `runs-on` 与非空 `steps`，
    每个 step 有 `run` 或 `uses`，`uses` 必须带版本）→ **命令可达**
    （`run` 里的 `python -m X` / `python <文件>.py` 目标真实存在）。

    ⚠ YAML 1.1 会把裸 `on:` 解析成**布尔 True**（`{True: [...]}`），所以检查"有没有
    `on`"必须同时认 `'on'` 与 `True` 两个键 —— 只查字符串会永远报"缺 on"。
    """
    import yaml
    wf = os.path.join(root, '.github', 'workflows')
    if not os.path.isdir(wf):
        raise Skip('.github/workflows 不存在')
    files = sorted(f for f in os.listdir(wf) if f.endswith(('.yml', '.yaml')))
    if not files:
        raise Skip('.github/workflows 下没有 workflow')
    bad, n_jobs, n_cmds = [], 0, 0
    for fn in files:
        rel = f'.github/workflows/{fn}'
        try:
            data = yaml.safe_load(_read(root, rel))
        except yaml.YAMLError as e:
            raise Fail(f'{rel} 不是合法 YAML：{e}')
        if not isinstance(data, dict):
            raise Fail(f'{rel} 顶层不是映射')
        if 'on' not in data and True not in data:
            bad.append(f'{rel}: 缺 `on`（触发条件）')
        jobs = data.get('jobs')
        if not isinstance(jobs, dict) or not jobs:
            bad.append(f'{rel}: 缺 `jobs`')
            continue
        for jname, job in jobs.items():
            n_jobs += 1
            if not isinstance(job, dict):
                bad.append(f'{rel}:{jname} 不是映射')
                continue
            if 'runs-on' not in job:
                bad.append(f'{rel}:{jname} 缺 `runs-on`')
            steps = job.get('steps')
            if not isinstance(steps, list) or not steps:
                bad.append(f'{rel}:{jname} 缺 `steps`')
                continue
            for i, st in enumerate(steps):
                if not isinstance(st, dict) or not (st.get('run') or st.get('uses')):
                    bad.append(f'{rel}:{jname} 第 {i + 1} 步既没有 run 也没有 uses')
                    continue
                uses = st.get('uses')
                if uses and '@' not in str(uses):
                    bad.append(f'{rel}:{jname} 第 {i + 1} 步的 uses={uses!r} 没钉版本')
                run = st.get('run')
                if not isinstance(run, str):
                    continue
                for mod in set(re.findall(r'python -m ([\w.]+)', run)):
                    n_cmds += 1
                    if mod.split('.')[0] in TOOLING_MODULES:
                        continue
                    if not _module_exists(root, mod):
                        bad.append(f'{rel}:{jname} `python -m {mod}` 目标不存在')
                for script in set(re.findall(r'python ([\w./-]+\.py)', run)):
                    n_cmds += 1
                    cands = ([script] if script.split('/')[0] in ('scripts', 'examples')
                             else [script, f'scripts/{script}', f'examples/{script}'])
                    if not any(os.path.exists(os.path.join(root, c)) for c in cands):
                        bad.append(f'{rel}:{jname} `python {script}` 文件不存在')
    if bad:
        raise Fail(f'{len(bad)} 处 workflow 问题：' + '；'.join(bad[:6]))
    return f'{len(files)} 个 workflow / {n_jobs} 个 job 结构完整，{n_cmds} 条命令可达'


@gate('cli-usage-names', 'cli/*.py 的用法 docstring 里的模块名 == 真实模块名')
def g_cli_usage(root, verbose):
    cd = os.path.join(root, 'world3d', 'cli')
    bad, n = [], 0
    for fn in sorted(os.listdir(cd)):
        if not fn.endswith('.py') or fn == '__init__.py':
            continue
        text = _read(root, f'world3d/cli/{fn}')
        names = set(re.findall(r'python -m world3d\.cli\.(\w+)', text))
        if not names:
            continue
        n += 1
        want = fn[:-3]
        wrong = sorted(names - {want})
        if wrong:
            bad.append(f'{fn}: docstring 写了 {wrong}，模块是 {want}')
    if bad:
        raise Fail('；'.join(bad))
    return f'{n} 个 cli 模块的用法行都指向自己'


@gate('readme-tree-real', 'README 项目结构树里列出的路径都真实存在')
def g_readme_tree(root, verbose):
    text = _read(root, 'README.md')
    block = _fenced(text, '## 项目结构')
    stack, checked, bad = [], 0, []
    for raw in block.split('\n'):
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        m = re.match(r'^([\w./-]+)', raw.strip())
        if not m:
            continue
        token = m.group(1)
        if not re.match(r'^[\w./-]+(\.py|/)$|^[\w.-]+\.(py|toml|yml|cfg|txt)$', token):
            continue          # 非路径行（说明文字/分隔）
        depth = indent // 2
        stack = stack[:depth]
        rel = '/'.join(stack + [token.rstrip('/')])
        stack = stack + [token.rstrip('/')] if token.endswith('/') else stack
        checked += 1
        if not os.path.exists(os.path.join(root, rel)):
            bad.append(rel)
    if bad:
        raise Fail(f'{len(bad)} 个路径不存在：' + '；'.join(bad[:8]))
    return f'树里 {checked} 个路径全部存在'


# ============================================================== runner ==


def run(gates, root, verbose):
    rows = []
    for name, desc, fn in gates:
        try:
            detail = fn(root, verbose)
            rows.append({'name': name, 'status': 'PASS', 'detail': detail, 'desc': desc})
        except Skip as e:
            rows.append({'name': name, 'status': 'SKIP', 'detail': str(e), 'desc': desc})
        except Fail as e:
            rows.append({'name': name, 'status': 'FAIL', 'detail': str(e), 'desc': desc})
        except Exception as e:                              # noqa: BLE001
            rows.append({'name': name, 'status': 'FAIL',
                         'detail': f'{type(e).__name__}: {e}', 'desc': desc})
    return rows


def report(rows, as_json=False, verbose=False):
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            mark = {'PASS': 'ok  ', 'SKIP': 'skip', 'FAIL': 'FAIL'}[r['status']]
            print(f'  [{mark}] {r["name"]:<24} {r["detail"]}')
        n_pass = sum(1 for r in rows if r['status'] == 'PASS')
        n_skip = sum(1 for r in rows if r['status'] == 'SKIP')
        n_fail = sum(1 for r in rows if r['status'] == 'FAIL')
        print('-' * 74)
        print(f'[verify] {len(rows)} 条门禁：通过 {n_pass}，跳过 {n_skip}，失败 {n_fail}')
    return sum(1 for r in rows if r['status'] == 'FAIL')


# ============================================================== selfcheck ==
SANDBOX_SKIP = {'.git', '.venv', 'build', 'recordings', 'shots', '__pycache__',
                'assets', 'vendor', 'node_modules'}
SANDBOX_SKIP_EXT = {'.png', '.jpg', '.mp4', '.wav', '.gif', '.zip', '.exe', '.raw'}


def _sandbox(root):
    """只拷**文本**（门禁只读文本）—— 50MB 的仓库因此缩到 ~2MB。"""
    dst = tempfile.mkdtemp(prefix='verify_selfcheck_')
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SANDBOX_SKIP and not d.endswith('.egg-info')]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in SANDBOX_SKIP_EXT:
                continue
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, root)
            out = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(src, out)
    return dst


def _subst(rel, old, new):
    def fn(root):
        p = os.path.join(root, rel)
        text = open(p, encoding='utf-8').read()
        if old not in text:
            raise AssertionError(f'沙箱里找不到要替换的文本：{rel} :: {old[:40]!r}')
        open(p, 'w', encoding='utf-8', newline='\n').write(text.replace(old, new, 1))
    return fn


def _append_line(rel, line):
    def fn(root):
        p = os.path.join(root, rel)
        with open(p, 'a', encoding='utf-8', newline='\n') as fh:
            fh.write(line + '\n')
    return fn


def _delete(rel):
    def fn(root):
        os.remove(os.path.join(root, rel))
    return fn


#: 每条门禁一到两个**针对性破坏**（"我把它防的那件事做出来，它会不会红"）
BREAKS = [
    ('readme-demo-count', '把 README 里第一处示例数改成 99',
     _subst('README.md', '**37 个独立示例与工具**', '**99 个独立示例与工具**')),
    ('readme-test-modules', '从 README 删掉一行测试命令',
     _subst('README.md', 'python -m tests.test_compat', 'python -m tests.test_removed_thing')),
    ('readme-test-counts', '把某个模块的用例数写错',
     _subst('README.md', '（65 项，零 GL）', '（999 项，零 GL）')),
    ('readme-config-count', '把配置树数改成 99',
     _subst('README.md', '10 棵配置树', '99 棵配置树')),
    ('readme-kernel-count', '把内核数改成 99',
     _subst('README.md', '9 个内核 + 13 共同核', '99 个内核 + 13 共同核')),
    ('readme-core-count', '把共同核数改成 99',
     _subst('README.md', '9 个内核 + 13 共同核', '9 个内核 + 99 共同核')),
    ('examples-index-pairs', '从 examples/README 删掉一个 demo 的行',
     _subst('examples/README.md', '| `frame_graph.py` |', '| `removed.py` |')),
    ('doc-links', '把一个文档链接指到不存在的文件',
     _subst('README.md', '](examples/README.md)', '](examples/NOPE.md)')),
    ('docs-index-fresh', '往索引里塞一行假的',
     _append_line('docs/README.md', '| [`假的`](nope.md) | 假 |')),
    ('commands-exist', '把一条命令的模块名改坏',
     _subst('README.md', 'python -m world3d.cli.frame_plan --all-toggles',
            'python -m world3d.cli.no_such_cli')),
    ('cli-usage-names', '把 cli docstring 的用法行改成别的模块',
     _subst('world3d/cli/frame_plan.py', 'python -m world3d.cli.frame_plan',
            'python -m world3d.cli.config_boot')),
    ('readme-tree-real', '把目录树里的一个文件名改坏',
     _subst('README.md', '    app.py            DemoApp · run',
            '    app_broken.py     DemoApp · run')),
    ('pyrefs-exist', '把 CI 里的 import 改成不存在的模块',
     _subst('.github/workflows/ci.yml', 'import world3d._cordis_locate as L',
            'import no_such_module as L')),
    ('scaffold-templates-valid', '把模板里的关键钩子改名',
     _subst('scripts/new_demo.py', '    def render(self):',
            '    def render_broken(self):')),
    ('scaffold-kernel-valid', '把内核模板的 status() 改名（破坏 run_generic 的契约）',
     _subst('scripts/new_kernel.py', '    def status(self) -> str:',
            '    def status_broken(self) -> str:')),
    ('workflows-valid', '把 workflow 里的一条命令指到不存在的模块',
     _subst('.github/workflows/ci.yml', 'python scripts/verify.py',
            'python scripts/verify_broken.py')),
]


def selfcheck(root):
    print('[selfcheck] 建沙箱（只拷文本）…')
    sandbox = _sandbox(root)
    print(f'[selfcheck] 沙箱 {sandbox}')
    base = {r['name']: r['status'] for r in run(GATES, sandbox, False)}
    bad_base = [n for n, s in base.items() if s == 'FAIL']
    rows = []
    for name, desc, breaker in BREAKS:
        if base.get(name) == 'FAIL':
            rows.append({'name': name, 'status': 'FAIL',
                         'detail': f'沙箱基线就是 FAIL（{base[name]}）', 'desc': desc})
            continue
        work = tempfile.mkdtemp(prefix='verify_break_')
        shutil.copytree(sandbox, work, dirs_exist_ok=True)
        try:
            breaker(work)
            gates = [g for g in GATES if g[0] == name]
            after = run(gates, work, False)
            st = after[0]['status']
            rows.append({'name': name, 'status': 'PASS' if st == 'FAIL' else 'FAIL',
                         'detail': (f'注入「{desc}」→ 门禁 {st}'
                                    + (f'：{after[0]["detail"][:50]}…' if st == 'FAIL'
                                       else '（**没拦住**）')),
                         'desc': desc})
        except AssertionError as e:
            rows.append({'name': name, 'status': 'FAIL', 'detail': str(e), 'desc': desc})
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if bad_base:
        print(f'[selfcheck] ⚠ 沙箱基线有 {len(bad_base)} 条 FAIL：{bad_base}')
    report(rows)
    shutil.rmtree(sandbox, ignore_errors=True)
    print('[selfcheck] 每条门禁都被自己的破坏用例验证过'
          if all(r['status'] == 'PASS' for r in rows) else
          '[selfcheck] 有门禁没拦住破坏 —— 它是装饰品')
    return sum(1 for r in rows if r['status'] == 'FAIL')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='机械门禁（P4）')
    ap.add_argument('--list', action='store_true', help='只列门禁名字与说明')
    ap.add_argument('--only', default='', help='逗号分隔，只跑这些')
    ap.add_argument('--json', action='store_true', help='机器可读输出')
    ap.add_argument('--verbose', action='store_true', help='打印证据细节')
    ap.add_argument('--fix', action='store_true', help='只重新生成生成物')
    ap.add_argument('--selfcheck', action='store_true', help='沙箱注入破坏，证明门禁拦得住')
    args = ap.parse_args(argv)

    if args.list:
        for name, desc, _fn in GATES:
            print(f'  {name:<24} {desc}')
        return 0
    if args.fix:
        sys.path.insert(0, os.path.join(ROOT, 'scripts'))
        import docs_index
        docs_index.main(['--write'])
        print('[verify] 生成物已刷新；重跑 `python scripts/verify.py` 复核')
        return 0
    if args.selfcheck:
        return 1 if selfcheck(ROOT) else 0

    gates = GATES
    if args.only:
        want = {x.strip() for x in args.only.split(',') if x.strip()}
        gates = [g for g in GATES if g[0] in want]
        unknown = want - {g[0] for g in GATES}
        if unknown:
            print(f'[verify] 未知门禁：{sorted(unknown)}')
            return 2
    rows = run(gates, ROOT, args.verbose)
    n_fail = report(rows, args.json, args.verbose)
    if not args.json:
        print('== VERIFY_OK ==' if not n_fail else 'VERIFY_FAILED')
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main())
