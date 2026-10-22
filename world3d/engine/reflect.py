# -*- coding: utf-8 -*-
"""反射注册表：一套属性元数据，多处复用（Godot `ClassDB` 思路，自研）。

为什么需要
----------
没有反射时，同一个类的字段要在三处各写一遍：序列化写一份 `to_dict`、
编辑器写一份"这个字段是什么类型/取值范围"、脚本绑定再写一份 getter/setter。
三份各自漂移是常态 —— 加一个字段要改三个地方，漏一处就静默出错。

本模块把**属性元数据注册一次**，然后由它派生出四种用途（O3DE 的
`ReflectContext` 多上下文、Godot `ClassDB` 的同源思路）：

* **序列化** `to_dict()` / `update()` —— 按注册字段读写，未知键不吞掉（返回出来）；
* **编辑器检视** `schema()` —— 类型 / 默认值 / 取值范围 / 选项 / 说明，够画控件；
* **脚本绑定** `bindings()` —— 名字 → (getter, setter)，脚本层不碰类内部；
* **校验** `validate()` —— 类型与取值范围一次查清（顺带服务"数值调错了"这类 bug）。

继承：`register(base=...)` 后 `all_properties()` 会沿继承链展开（基类在前），
与 ClassDB 的"子类看到父类属性"一致。

本模块是**纯数据 + 纯 Python 反射**：不碰 GL、不依赖渲染上下文，headless 可断言。
"""

from __future__ import annotations

#: 支持的属性类型（够覆盖引擎参数；新增类型要同步 `_coerce` 与 `_check`）
TYPES = ('float', 'int', 'bool', 'str', 'vec2', 'vec3', 'vec4',
         'quat', 'color', 'enum', 'resource', 'any')


class ReflectError(ValueError):
    """注册 / 校验 / 反序列化时的错误。"""


def _as_list(v):
    """把 numpy 数组 / 元组统一成普通 list（JSON 友好）。"""
    if v is None:
        return None
    if hasattr(v, 'tolist'):
        return v.tolist()
    if isinstance(v, (list, tuple)):
        return list(v)
    return v


class Property:
    """一个属性的元数据。"""

    __slots__ = ('name', 'type', 'default', 'min', 'max', 'options',
                 'hint', 'doc', 'nullable')

    def __init__(self, name, type='any', default=None, min=None, max=None,
                 options=None, hint='', doc='', nullable=False):
        if not name:
            raise ReflectError('属性必须有名字')
        if type not in TYPES:
            raise ReflectError(f'属性 {name!r} 类型 {type!r} 不在 {TYPES}')
        self.name = name
        self.type = type
        self.default = default
        self.min = min
        self.max = max
        self.options = tuple(options) if options is not None else None
        self.hint = hint
        self.doc = doc
        self.nullable = bool(nullable)

    def schema(self):
        """编辑器检视用的一条元数据。"""
        return {'name': self.name, 'type': self.type,
                'default': _as_list(self.default),
                'min': self.min, 'max': self.max,
                'options': list(self.options) if self.options else None,
                'hint': self.hint, 'doc': self.doc}

    def __repr__(self):
        return f'<Property {self.name}: {self.type}>'


class ClassInfo:
    """一个类的反射信息（不含继承来的属性；用 `ClassDB.all_properties` 展开）。"""

    __slots__ = ('name', 'base', 'props', 'doc')

    def __init__(self, name, base=None, props=(), doc=''):
        self.name = name
        self.base = base
        self.props = {p.name: p for p in props}
        self.doc = doc

    def __repr__(self):
        return f'<ClassInfo {self.name} base={self.base} props={len(self.props)}>'


class ClassDB:
    """反射注册表。

    典型用法::

        db = ClassDB()
        db.register('Light', props=[
            Property('intensity', 'float', 1.0, min=0.0, max=20.0),
            Property('color', 'color', (1, 1, 1)),
            Property('mode', 'enum', 'point', options=('point', 'spot', 'dir')),
        ])
        db.to_dict(light)          # 序列化
        db.update(light, data)     # 反序列化
        db.schema('Light')         # 编辑器控件
        db.bindings('Light')       # 脚本绑定
        db.validate(light)         # 校验
    """

    def __init__(self):
        self._classes = {}

    # ------------------------------------------------------------ 注册 --
    def register(self, name, base=None, props=(), doc=''):
        if not name:
            raise ReflectError('类名不能为空')
        if name in self._classes:
            raise ReflectError(f'类 {name!r} 已注册（重复注册通常是笔误）')
        if base is not None and base not in self._classes and base != name:
            raise ReflectError(f'类 {name!r} 的基类 {base!r} 尚未注册')
        info = ClassInfo(name, base, props, doc)
        self._classes[name] = info
        return info

    def unregister(self, name) -> bool:
        return self._classes.pop(name, None) is not None

    def has(self, name) -> bool:
        return name in self._classes

    def get(self, name) -> ClassInfo:
        try:
            return self._classes[name]
        except KeyError:
            raise ReflectError(f'类 {name!r} 未注册') from None

    def classes(self):
        return sorted(self._classes)

    def ancestors(self, name):
        """继承链：``[基类..., 自身]``（基类在前）。"""
        chain = []
        cur = name
        seen = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = self.get(cur).base
        return chain[::-1]

    def all_properties(self, name):
        """沿继承链展开的属性表：基类属性在前，子类同名属性**覆盖**基类。"""
        out = {}
        for cls_name in self.ancestors(name):
            out.update(self.get(cls_name).props)
        return out

    # -------------------------------------------------------- 用途①序列化 --
    def to_dict(self, obj, name=None):
        """按注册字段把对象读成 JSON 友好的 dict。"""
        cls_name = name or type(obj).__name__
        props = self.all_properties(cls_name)
        out = {}
        for pname, p in props.items():
            if not hasattr(obj, pname):
                continue
            v = getattr(obj, pname)
            if p.type in ('vec2', 'vec3', 'vec4', 'quat', 'color'):
                v = _as_list(v)
            elif p.type == 'int':
                v = int(v) if v is not None else None
            elif p.type == 'float':
                v = float(v) if v is not None else None
            elif p.type == 'bool':
                v = bool(v)
            out[pname] = v
        return out

    def update(self, obj, data, name=None):
        """按注册字段把 dict 写回对象。

        返回 ``(applied, unknown, errors)``：已应用的键、未注册的键（**不静默丢弃**）、
        以及校验错误。未知键返回给调用方，由它决定忽略还是报警。
        """
        cls_name = name or type(obj).__name__
        props = self.all_properties(cls_name)
        applied, unknown, errors = [], [], []
        for k, v in (data or {}).items():
            if k not in props:
                unknown.append(k)
                continue
            p = props[k]
            try:
                setattr(obj, k, self._coerce(p, v))
            except ReflectError as exc:
                errors.append(str(exc))
                continue
            applied.append(k)
        errors += self.validate(obj, cls_name)
        return applied, unknown, errors

    # ------------------------------------------------------ 用途②编辑器 --
    def schema(self, name=None, obj=None):
        """编辑器检视用的元数据列表（够画控件）。"""
        cls_name = name or type(obj).__name__
        return [p.schema() for p in self.all_properties(cls_name).values()]

    # ------------------------------------------------------ 用途③脚本绑定 --
    def bindings(self, name=None, obj=None):
        """名字 → ``(getter, setter)``，脚本层只通过它访问字段。"""
        cls_name = name or type(obj).__name__
        target = obj
        out = {}
        for pname in self.all_properties(cls_name):
            out[pname] = (lambda n=pname: getattr(target, n),
                          lambda v, n=pname: setattr(target, n, v))
        return out if target is not None else {
            pname: None for pname in self.all_properties(cls_name)}

    # -------------------------------------------------------- 用途④校验 --
    def validate(self, obj, name=None):
        """按类型与取值范围校验，返回错误字符串列表（空 = 通过）。"""
        cls_name = name or type(obj).__name__
        errs = []
        for pname, p in self.all_properties(cls_name).items():
            if not hasattr(obj, pname):
                continue
            v = getattr(obj, pname)
            errs += self._check(p, v)
        return errs

    # ------------------------------------------------------------ 内部 --
    def _coerce(self, p, v):
        """把外部值（JSON 出来的）转成属性声明的类型。"""
        t = p.type
        if v is None:
            if not p.nullable and p.default is not None:
                raise ReflectError(f'{p.name} 不可为 None')
            return None
        if t == 'float':
            return float(v)
        if t == 'int':
            return int(v)
        if t == 'bool':
            return bool(v)
        if t == 'str':
            return str(v)
        if t in ('vec2', 'vec3', 'vec4', 'quat', 'color'):
            seq = _as_list(v)
            if not isinstance(seq, (list, tuple)):
                raise ReflectError(f'{p.name} 需要序列，收到 {type(v).__name__}')
            return list(seq)
        if t == 'enum':
            if p.options and v not in p.options:
                raise ReflectError(f'{p.name}={v!r} 不在选项 {p.options}')
            return v
        return v

    def _check(self, p, v):
        """类型 / 范围 / 选项检查，返回错误列表。"""
        errs = []
        if v is None:
            if not p.nullable:
                errs.append(f'{p.name} 为空（声明 nullable=False）')
            return errs
        t = p.type
        if t in ('vec2', 'vec3', 'vec4', 'quat', 'color'):
            seq = _as_list(v)
            want = {'vec2': 2, 'vec3': 3, 'vec4': 4, 'quat': 4, 'color': (3, 4)}[t]
            if not isinstance(seq, (list, tuple)):
                errs.append(f'{p.name} 应为 {t}，实际 {type(v).__name__}')
                return errs
            ok = (len(seq) in want) if isinstance(want, tuple) else (len(seq) == want)
            if not ok:
                errs.append(f'{p.name} 长度应为 {want}，实际 {len(seq)}')
            return errs
        if t == 'enum':
            if p.options and v not in p.options:
                errs.append(f'{p.name}={v!r} 不在选项 {p.options}')
            return errs
        if t == 'float':
            try:
                fv = float(v)
            except (TypeError, ValueError):
                errs.append(f'{p.name} 应为 float，实际 {v!r}')
                return errs
            if p.min is not None and fv < p.min:
                errs.append(f'{p.name}={fv} 小于下限 {p.min}')
            if p.max is not None and fv > p.max:
                errs.append(f'{p.name}={fv} 超过上限 {p.max}')
            return errs
        if t == 'int':
            try:
                iv = int(v)
            except (TypeError, ValueError):
                errs.append(f'{p.name} 应为 int，实际 {v!r}')
                return errs
            if p.min is not None and iv < p.min:
                errs.append(f'{p.name}={iv} 小于下限 {p.min}')
            if p.max is not None and iv > p.max:
                errs.append(f'{p.name}={iv} 超过上限 {p.max}')
        return errs


#: 模块级默认注册表（引擎各子系统可直接往里注册，避免各自建一套）
_default_db = ClassDB()


def default_db():
    return _default_db
