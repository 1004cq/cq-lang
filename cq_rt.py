"""CQ runtime — used by the CQ compiler output."""

from __future__ import annotations

import json as _json
import os
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen


class CQError(Exception):
    pass


@dataclass
class Result:
    ok: bool
    value: Any

    def __repr__(self):
        tag = "Ok" if self.ok else "Err"
        return f"{tag}({self.value!r})"


@dataclass
class Struct:
    name: str
    values: dict

    def __getattr__(self, item):
        if item in self.values:
            return self.values[item]
        raise CQError(f"{self.name} missing field {item}")


@dataclass
class Module:
    name: str
    values: dict

    def __getattr__(self, item):
        if item in self.values:
            return self.values[item]
        raise CQError(f"module {self.name} missing {item}")


def stringify(v):
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Result):
        return f"{'Ok' if v.ok else 'Err'}({stringify(v.value)})"
    if isinstance(v, Struct):
        inner = ", ".join(f"{k}: {stringify(val)}" for k, val in v.values.items())
        return f"{v.name} {{ {inner} }}"
    if isinstance(v, list):
        return "[" + ", ".join(stringify(x) for x in v) + "]"
    return str(v)


def cq_print(v):
    print(stringify(v))
    return v


def cq_map(xs, f):
    return [f(x) for x in xs]


def cq_filter(xs, f):
    return [x for x in xs if f(x)]


def interpolate(s: str, env: dict):
    out = []
    i = 0
    while i < len(s):
        if s[i] == "{":
            j = s.find("}", i)
            if j < 0:
                out.append(s[i:])
                break
            expr = s[i + 1 : j].strip()
            cur = env
            first = True
            for part in expr.split("."):
                if first and isinstance(cur, dict):
                    cur = cur[part]
                    first = False
                elif isinstance(cur, Struct):
                    cur = cur.values[part]
                elif isinstance(cur, Module):
                    cur = cur.values[part]
                else:
                    cur = getattr(cur, part)
            out.append(stringify(cur))
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def fs_read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return Result(True, f.read())
    except Exception as e:
        return Result(False, str(e))


def fs_write(path, text):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(text))
        return Result(True, path)
    except Exception as e:
        return Result(False, str(e))


def fs_exists(path):
    return os.path.exists(path)


def json_encode(v):
    def conv(x):
        if isinstance(x, Result):
            return {"ok": x.ok, "value": conv(x.value)}
        if isinstance(x, Struct):
            return {k: conv(val) for k, val in x.values.items()}
        if isinstance(x, list):
            return [conv(i) for i in x]
        return x

    return _json.dumps(conv(v), ensure_ascii=False)


def json_decode(s):
    try:
        return Result(True, _json.loads(s))
    except Exception as e:
        return Result(False, str(e))


def http_get(url, timeout=15):
    try:
        req = Request(url, headers={"User-Agent": "CQ/0.3"})
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            return Result(True, data)
    except Exception as e:
        return Result(False, str(e))


_pool = ThreadPoolExecutor(max_workers=8)


def task_spawn(fn):
    return _pool.submit(fn)


def task_await(handle):
    if isinstance(handle, Future):
        return handle.result()
    return handle


def task_await_all(handles):
    return [task_await(h) for h in handles]


def std_modules():
    return {
        "fs": Module("fs", {"read": fs_read, "write": fs_write, "exists": fs_exists}),
        "json": Module("json", {"encode": json_encode, "decode": json_decode}),
        "http": Module("http", {"get": http_get}),
        "task": Module("task", {"spawn": task_spawn, "await": task_await, "await_all": task_await_all}),
    }
