"""Generic TOML-shaped dict → dataclass decoder.

:func:`decode` recursively builds a dataclass instance from a plain
``Mapping`` (the shape :func:`tomllib.load` returns): a TOML table maps 1:1
onto a dataclass, so the dataclass *is* the schema and its docstring the
documentation — no separate hand-written parser per config section. Supports
primitives (``str``/``int``/``bool``/``float``), ``tuple[X, ...]`` (from a
TOML array), ``Optional[X]``, and nested dataclasses (from a TOML sub-table).

Fail-closed: an unknown key, a missing required field, or a type mismatch
(including ``bool`` where ``int`` is declared — ``isinstance(True, int)`` is
``True`` in Python, so that mismatch needs an explicit check) all raise
:class:`~warden.core.config.ConfigError` with a ``path``-prefixed message.
"""

from __future__ import annotations

import dataclasses
from typing import Mapping, TypeVar, get_args, get_origin, get_type_hints

from .config import ConfigError

T = TypeVar("T")


def _field_path(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


def decode(cls: type[T], mapping: Mapping[str, object], *, path: str = "") -> T:
    """Build a ``cls`` instance from *mapping*, a plain dict shaped like a
    TOML table. Raises :class:`ConfigError` on any unknown key, missing
    required field, or type mismatch, with *path* prefixing every message
    (empty at the top level, ``"outer.inner"`` for a nested dataclass field).
    """
    if not isinstance(mapping, Mapping):
        raise ConfigError(f"{path or cls.__name__}: expected a table, got {mapping!r}")

    fields = {f.name: f for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    hints = get_type_hints(cls)

    unknown = sorted(set(mapping) - set(fields))
    if unknown:
        where = path or cls.__name__
        raise ConfigError(f"{where}: unknown key(s): {', '.join(unknown)}")

    kwargs: dict[str, object] = {}
    for name, f in fields.items():
        field_path = _field_path(path, name)
        if name not in mapping:
            has_default = (
                f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING
            )
            if not has_default:
                raise ConfigError(f"{field_path}: missing required field")
            continue
        kwargs[name] = _decode_value(hints[name], mapping[name], field_path)

    return cls(**kwargs)


def _decode_value(hint: object, value: object, path: str) -> object:
    origin = get_origin(hint)

    if origin is tuple:
        args = get_args(hint)
        if len(args) != 2 or args[1] is not Ellipsis:
            raise ConfigError(f"{path}: unsupported tuple shape {hint!r}")
        if not isinstance(value, list):
            raise ConfigError(f"{path}: expected a list, got {value!r}")
        elem_type = args[0]
        return tuple(_decode_value(elem_type, v, f"{path}[{i}]") for i, v in enumerate(value))

    if origin is not None:  # only Optional[X] (Union[X, None]) is supported
        args = get_args(hint)
        if type(None) in args and len(args) == 2:
            if value is None:
                return None
            inner = next(a for a in args if a is not type(None))
            return _decode_value(inner, value, path)
        raise ConfigError(f"{path}: unsupported type {hint!r}")

    if dataclasses.is_dataclass(hint):
        return decode(hint, value, path=path)  # type: ignore[arg-type]

    if hint is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{path}: expected a bool, got {value!r}")
        return value

    if hint is int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConfigError(f"{path}: expected an int, got {value!r}")
        return value

    if hint is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"{path}: expected a float, got {value!r}")
        return float(value)

    if hint is str:
        if not isinstance(value, str):
            raise ConfigError(f"{path}: expected a string, got {value!r}")
        return value

    raise ConfigError(f"{path}: unsupported field type {hint!r}")
