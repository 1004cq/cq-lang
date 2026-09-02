#!/usr/bin/env python3
"""CQ 0.2 — typecheck + compiler + stdlib."""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from dataclasses import dataclass
from typing import Any

import cq_rt
from cq_rt import Module, Result, Struct, std_modules


class CQError(Exception):
    pass


@dataclass(frozen=True)
class SourceFile:
    name: str
    text: str

    def line_text(self, line: int) -> str:
        lines = self.text.splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1]
        return ""


@dataclass(frozen=True)
class SourceLocation:
    source: SourceFile
    line: int
    column: int


class CQDiagnostic(CQError):
    def __init__(self, kind: str, message: str, location: SourceLocation):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.location = location

    def __str__(self):
        loc = self.location
        source_line = loc.source.line_text(loc.line)
        expanded_line, _ = render_source_text(source_line)
        _, prefix_width = render_source_text(source_line[: max(0, loc.column - 1)])
        width = len(str(loc.line))
        return "\n".join(
            [
                f"{loc.source.name}:{loc.line}:{loc.column}: {self.kind}：{self.message}",
                f"{' ' * width} |",
                f"{loc.line:>{width}} | {expanded_line}",
                f"{' ' * width} | {' ' * prefix_width}^",
            ]
        )


def render_source_text(text: str) -> tuple[str, int]:
    """Expand tabs and measure wide characters so carets align for Chinese code."""
    out = []
    width = 0
    for char in text:
        if char == "\t":
            spaces = 4 - (width % 4)
            out.append(" " * spaces)
            width += spaces
            continue
        out.append(char)
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return "".join(out), width


class Node(tuple):
    """Tuple-compatible AST node carrying source location for diagnostics."""

    def __new__(cls, values, location: SourceLocation):
        node = super().__new__(cls, values)
        node.location = location
        return node


@dataclass
class Tok:
    kind: str
    value: Any
    line: int
    column: int
    source: SourceFile

    @property
    def location(self) -> SourceLocation:
        return SourceLocation(self.source, self.line, self.column)


KEYWORDS = {
    "let",
    "mut",
    "fn",
    "if",
    "else",
    "return",
    "true",
    "false",
    "match",
    "type",
    "print",
    "Ok",
    "Err",
    "Some",
    "None",
    "import",
    "for",
    "iface",
    "impl",
    "while",
    "and",
    "or",
    "not",
}

TOKEN_NAMES = {
    "ID": "名称",
    "NUM": "数字",
    "STR": "字符串",
    "EQ": "`=`",
    "ARROW": "`->`",
    "FAT": "`=>`",
    "OP": "运算符",
    "EOF": "文件结尾",
    "(": "`(`",
    ")": "`)`",
    "{": "`{`",
    "}": "`}`",
    "[": "`[`",
    "]": "`]`",
    ",": "`,`",
    ":": "`:`",
    ".": "`.`",
}


def expected_token(kind: str) -> str:
    return TOKEN_NAMES.get(kind, f"`{kind.lower()}`")


def describe_token(tok: Tok) -> str:
    if tok.kind == "EOF":
        return "文件结尾"
    if tok.kind == "ID":
        return f"名称 {tok.value!r}"
    if tok.kind == "STR":
        return "字符串"
    if tok.kind == "NUM":
        return f"数字 {tok.value}"
    if tok.value is not None:
        return f"`{tok.value}`"
    return TOKEN_NAMES.get(tok.kind, tok.kind)

CTX = {"dir": os.path.dirname(os.path.abspath(__file__))}


def tokenize(src: str, filename: str = "<input>") -> list[Tok]:
    source = SourceFile(filename, src)
    tokens: list[Tok] = []
    i = 0
    line = 1
    line_start = 0
    n = len(src)

    def token(kind: str, value: Any, token_line: int, column: int):
        tokens.append(Tok(kind, value, token_line, column, source))

    def peek(k=0):
        j = i + k
        return src[j] if j < n else ""

    while i < n:
        c = src[i]
        start_line = line
        start_column = i - line_start + 1
        if c == "\n":
            line += 1
            i += 1
            line_start = i
            continue
        if c.isspace():
            i += 1
            continue
        if c == "#" or (c == "/" and peek(1) == "/"):
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "?" and peek(1) == "?":
            token("COALESCE", "??", start_line, start_column)
            i += 2
            continue
        if c == "?":
            token("TRY", "?", start_line, start_column)
            i += 1
            continue
        if c == "|" and peek(1) == ">":
            token("PIPE", "|>", start_line, start_column)
            i += 2
            continue
        if c == "-" and peek(1) == ">":
            token("ARROW", "->", start_line, start_column)
            i += 2
            continue
        if c == "=" and peek(1) == ">":
            token("FAT", "=>", start_line, start_column)
            i += 2
            continue
        if c == "=" and peek(1) == "=":
            token("OP", "==", start_line, start_column)
            i += 2
            continue
        if c == "!" and peek(1) == "=":
            token("OP", "!=", start_line, start_column)
            i += 2
            continue
        if c == ">" and peek(1) == "=":
            token("OP", ">=", start_line, start_column)
            i += 2
            continue
        if c == "<" and peek(1) == "=":
            token("OP", "<=", start_line, start_column)
            i += 2
            continue
        if c in "(){},[]:.":
            token(c, c, start_line, start_column)
            i += 1
            continue
        if c == "=":
            token("EQ", "=", start_line, start_column)
            i += 1
            continue
        if c in "+-*/%<>":
            token("OP", c, start_line, start_column)
            i += 1
            continue
        if c in "\"'":
            quote = c
            i += 1
            buf = []
            while i < n and src[i] != quote:
                if src[i] == "\\" and i + 1 < n:
                    nxt = src[i + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", quote: quote}.get(nxt, nxt))
                    i += 2
                    continue
                if src[i] == "\n":
                    line += 1
                    line_start = i + 1
                buf.append(src[i])
                i += 1
            if i >= n:
                raise CQDiagnostic(
                    "语法错误",
                    f"字符串缺少结束引号 {quote}",
                    SourceLocation(source, start_line, start_column),
                )
            i += 1
            token("STR", "".join(buf), start_line, start_column)
            continue
        if c.isdigit() or (c == "." and peek(1).isdigit()):
            j = i
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            raw = src[i:j]
            i = j
            try:
                value = float(raw) if "." in raw else int(raw)
            except ValueError as e:
                raise CQDiagnostic(
                    "语法错误",
                    f"{raw!r} 不是有效数字",
                    SourceLocation(source, start_line, start_column),
                ) from e
            token("NUM", value, start_line, start_column)
            continue
        if c.isalpha() or c == "_" or ord(c) > 127:
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_" or ord(src[j]) > 127):
                j += 1
            word = src[i:j]
            i = j
            if word in KEYWORDS:
                token(word.upper(), word, start_line, start_column)
            else:
                token("ID", word, start_line, start_column)
            continue
        raise CQDiagnostic(
            "语法错误",
            f"无法识别字符 {c!r}",
            SourceLocation(source, start_line, start_column),
        )
    token("EOF", None, line, i - line_start + 1)
    return tokens


class ReturnSignal(Exception):
    def __init__(self, value):
        self.value = value


class Env:
    def __init__(self, parent: Env | None = None):
        self.parent = parent
        self.data: dict[str, Any] = {}
        self.mut: set[str] = set()

    def get(self, name: str):
        if name in self.data:
            return self.data[name]
        if self.parent:
            return self.parent.get(name)
        raise CQError(f"找不到名称：{name}")

    def define(self, name: str, value: Any, mutable: bool = False):
        self.data[name] = value
        if mutable:
            self.mut.add(name)

    def assign(self, name: str, value: Any):
        if name in self.data:
            if name not in self.mut:
                raise CQError(f"{name} 不可变。请用 mut 声明")
            self.data[name] = value
            return
        if self.parent:
            self.parent.assign(name, value)
            return
        raise CQError(f"找不到可赋值变量：{name}")


@dataclass
class Function:
    params: list[tuple[str, str | None]]
    ret: str | None
    body: Any
    env: Env
    location: SourceLocation | None = None


@dataclass
class TypeDef:
    name: str
    fields: list[tuple[str, str | None]]


# Struct is cq_rt.Struct(name, values) — TypeDef stays local for interpreter types


# Result / Struct / Module come from cq_rt


class Parser:
    def __init__(self, tokens: list[Tok]):
        self.tokens = tokens
        self.i = 0

    def cur(self) -> Tok:
        return self.tokens[self.i]

    def eat(self, kind=None) -> Tok:
        tok = self.cur()
        if kind and tok.kind != kind:
            raise CQDiagnostic(
                "语法错误",
                f"这里需要 {expected_token(kind)}，但遇到了{describe_token(tok)}",
                tok.location,
            )
        self.i += 1
        return tok

    @staticmethod
    def located(values, tok: Tok) -> Node:
        return Node(values, tok.location)

    def parse(self):
        start = self.cur()
        stmts = []
        while self.cur().kind != "EOF":
            stmts.append(self.stmt())
        return self.located(("block", stmts), start)

    def stmt(self):
        start = self.cur()
        return self.located(self._stmt(), start)

    def _stmt(self):
        k = self.cur().kind
        if k == "LET":
            return self.let_stmt(False)
        if k == "MUT":
            return self.let_stmt(True)
        if k == "FN":
            return self.fn_stmt()
        if k == "IF":
            return self.if_stmt()
        if k == "WHILE":
            self.eat("WHILE")
            cond = self.pipeline()
            body = self.block()
            return ("while", cond, body)
        if k == "MATCH":
            return self.match_stmt()
        if k == "RETURN":
            self.eat("RETURN")
            if self.cur().kind in {"}", "EOF"}:
                return ("return", ("null", None))
            return ("return", self.pipeline())
        if k == "TYPE":
            return self.type_stmt()
        if k == "IMPORT":
            self.eat("IMPORT")
            name = self.eat("ID").value
            return ("import", name)
        if k == "FOR":
            self.eat("FOR")
            name = self.eat("ID").value
            if self.cur().kind != "ID" or self.cur().value != "in":
                raise CQDiagnostic(
                    "语法错误",
                    "`for` 的写法是 `for 名称 in 列表 { ... }`；这里缺少 `in`",
                    self.cur().location,
                )
            self.eat("ID")
            expr = self.pipeline()
            body = self.block()
            return ("for", name, expr, body)
        if k == "IFACE":
            return self.iface_stmt()
        if k == "IMPL":
            return self.impl_stmt()
        if k == "PRINT":
            self.eat("PRINT")
            if self.cur().kind == "(":
                args = self.args()
                node = ("print", args[0] if args else ("str", ""))
            else:
                node = ("print", self.pipeline())
            return node
        if k == "ID" and self.tokens[self.i + 1].kind == "EQ":
            name = self.eat("ID").value
            self.eat("EQ")
            return ("assign", name, self.pipeline())
        return ("expr", self.pipeline())

    def let_stmt(self, mutable: bool):
        self.eat("MUT" if mutable else "LET")
        name = self.eat("ID").value
        typ = None
        if self.cur().kind == ":":
            self.eat(":")
            typ = self.type_name()
        self.eat("EQ")
        return ("let", name, mutable, typ, self.pipeline())

    def type_name(self):
        if self.cur().kind in {"ID", "OK", "ERR"}:
            name = self.eat().value
        else:
            name = self.eat("ID").value
        if self.cur().kind == "<":
            self.eat("OP") if self.cur().value == "<" else self.eat()
            # consume generic params loosely
            depth = 1
            while depth and self.cur().kind != "EOF":
                tok = self.eat()
                if tok.kind == "OP" and tok.value == "<":
                    depth += 1
                elif tok.kind == "OP" and tok.value == ">":
                    depth -= 1
                elif tok.value == ">":
                    depth -= 1
        return name

    def fn_stmt(self):
        self.eat("FN")
        name = self.eat("ID").value
        params = self.param_list()
        ret = None
        if self.cur().kind == "ARROW":
            self.eat("ARROW")
            ret = self.type_name()
        body = self.block()
        return ("def", name, params, ret, body)

    def param_list(self):
        self.eat("(")
        params = []
        while self.cur().kind != ")":
            pname = self.eat("ID").value
            ptyp = None
            if self.cur().kind == ":":
                self.eat(":")
                ptyp = self.type_name()
            params.append((pname, ptyp))
            if self.cur().kind == ",":
                self.eat(",")
        self.eat(")")
        return params

    def type_stmt(self):
        self.eat("TYPE")
        name = self.eat("ID").value
        self.eat("{")
        fields = []
        while self.cur().kind != "}":
            fname = self.eat("ID").value
            ftyp = None
            if self.cur().kind == ":":
                self.eat(":")
                ftyp = self.type_name()
            fields.append((fname, ftyp))
        self.eat("}")
        return ("type", name, fields)

    def iface_stmt(self):
        self.eat("IFACE")
        name = self.eat("ID").value
        self.eat("{")
        methods = []
        while self.cur().kind != "}":
            self.eat("FN")
            mname = self.eat("ID").value
            params = self.param_list()
            ret = None
            if self.cur().kind == "ARROW":
                self.eat("ARROW")
                ret = self.type_name()
            methods.append((mname, params, ret))
        self.eat("}")
        return ("iface", name, methods)

    def impl_stmt(self):
        self.eat("IMPL")
        iface = self.eat("ID").value
        if self.cur().kind == "FOR":
            self.eat("FOR")
        elif self.cur().kind == "ID" and self.cur().value == "for":
            self.eat("ID")
        else:
            raise CQDiagnostic(
                "语法错误",
                "`impl` 的写法是 `impl 接口 for 类型 { ... }`；这里缺少 `for`",
                self.cur().location,
            )
        typ = self.eat("ID").value
        self.eat("{")
        methods = []
        while self.cur().kind != "}":
            methods.append(self.fn_stmt())
        self.eat("}")
        return ("impl", iface, typ, methods)

    def if_stmt(self):
        self.eat("IF")
        cond = self.pipeline()
        then = self.block()
        els = None
        if self.cur().kind == "ELSE":
            self.eat("ELSE")
            els = self.block() if self.cur().kind == "{" else ("block", [self.stmt()])
        return ("if", cond, then, els)

    def match_stmt(self):
        self.eat("MATCH")
        expr = self.pipeline()
        self.eat("{")
        arms = []
        while self.cur().kind != "}":
            pat = self.pattern()
            self.eat("FAT")
            body = self.pipeline() if self.cur().kind != "{" else self.block()
            arms.append((pat, body))
        self.eat("}")
        return ("match", expr, arms)

    def pattern(self):
        tok = self.cur()
        if tok.kind in {"OK", "ERR", "SOME"}:
            tag = self.eat().value
            self.eat("(")
            inner = self.eat("ID").value
            self.eat(")")
            return ("ctor", tag, inner)
        if tok.kind == "NONE":
            self.eat("NONE")
            return ("none",)
        if tok.kind == "ID":
            return ("bind", self.eat("ID").value)
        if tok.kind == "NUM":
            return ("num", self.eat("NUM").value)
        if tok.kind == "STR":
            return ("str", self.eat("STR").value)
        raise CQDiagnostic(
            "语法错误",
            "无法识别这个 `match` 模式；可使用 `Ok(x)`、`Err(e)`、`Some(x)`、`None`、字面量或名称",
            tok.location,
        )

    def block(self):
        self.eat("{")
        stmts = []
        while self.cur().kind not in {"}", "EOF"}:
            stmts.append(self.stmt())
        self.eat("}")
        return ("block", stmts)

    def pipeline(self):
        start = self.cur()
        node = self.expr()
        while self.cur().kind == "PIPE":
            self.eat("PIPE")
            right = self.expr()
            node = ("pipe", node, right)
        return self.located(node, start)

    def expr(self):
        return self.or_expr()

    def or_expr(self):
        node = self.and_expr()
        while self.cur().kind == "OR":
            self.eat("OR")
            node = ("or", node, self.and_expr())
        return node

    def and_expr(self):
        node = self.cmp()
        while self.cur().kind == "AND":
            self.eat("AND")
            node = ("and", node, self.cmp())
        return node

    def cmp(self):
        node = self.add()
        while self.cur().kind == "OP" and self.cur().value in {"==", "!=", "<", ">", "<=", ">="}:
            op = self.eat("OP").value
            node = ("binop", op, node, self.add())
        return node

    def add(self):
        node = self.mul()
        while self.cur().kind == "OP" and self.cur().value in {"+", "-"}:
            op = self.eat("OP").value
            node = ("binop", op, node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.cur().kind == "OP" and self.cur().value in {"*", "/", "%"}:
            op = self.eat("OP").value
            node = ("binop", op, node, self.unary())
        return node

    def unary(self):
        if self.cur().kind == "NOT" or (self.cur().kind == "OP" and self.cur().value in {"-", "!"}):
            op = self.eat().value
            return ("unary", op, self.unary())
        return self.call()

    def call(self):
        node = self.atom()
        while True:
            if self.cur().kind == "(":
                node = ("call", node, self.args())
            elif self.cur().kind == ".":
                self.eat(".")
                field = self.eat("ID").value
                node = ("dot", node, field)
            elif self.cur().kind == "TRY":
                self.eat("TRY")
                node = ("try", node)
            elif self.cur().kind == "COALESCE":
                self.eat("COALESCE")
                node = ("coalesce", node, self.unary())
            else:
                break
        return node

    def args(self):
        self.eat("(")
        out = []
        while self.cur().kind != ")":
            out.append(self.pipeline())
            if self.cur().kind == ",":
                self.eat(",")
        self.eat(")")
        return out

    def atom(self):
        tok = self.cur()
        if tok.kind == "NUM":
            self.eat("NUM")
            return ("num", tok.value)
        if tok.kind == "STR":
            self.eat("STR")
            return ("str", tok.value)
        if tok.kind == "TRUE":
            self.eat("TRUE")
            return ("bool", True)
        if tok.kind == "FALSE":
            self.eat("FALSE")
            return ("bool", False)
        if tok.kind == "NONE":
            self.eat("NONE")
            return ("none",)
        if tok.kind == "OK":
            self.eat("OK")
            self.eat("(")
            v = self.pipeline()
            self.eat(")")
            return ("ok", v)
        if tok.kind == "ERR":
            self.eat("ERR")
            self.eat("(")
            v = self.pipeline()
            self.eat(")")
            return ("err", v)
        if tok.kind == "FN":
            self.eat("FN")
            params = self.param_list()
            ret = None
            if self.cur().kind == "ARROW":
                self.eat("ARROW")
                ret = self.type_name()
            body = self.block()
            return ("lambda", params, ret, body)
        if tok.kind == "PRINT":
            self.eat("PRINT")
            if self.cur().kind == "(":
                args = self.args()
                return ("print", args[0] if args else ("str", ""))
            return ("print", ("str", ""))
        if tok.kind == "ID":
            name = self.eat("ID").value
            if self.cur().kind == "{" and self._looks_like_struct():
                return self.struct_lit(name)
            return ("var", name)
        if tok.kind == "[":
            return self.list_lit()
        if tok.kind == "(":
            self.eat("(")
            node = self.pipeline()
            self.eat(")")
            return node
        raise CQDiagnostic(
            "语法错误",
            f"这里需要一个表达式，但遇到了{describe_token(tok)}",
            tok.location,
        )

    def _looks_like_struct(self):
        # ID { name: ...  or ID { name =
        j = self.i + 1
        if j >= len(self.tokens):
            return False
        if self.tokens[j].kind != "ID":
            return False
        k = j + 1
        if k >= len(self.tokens):
            return False
        return self.tokens[k].kind in {":", "EQ", ",", "}"}

    def struct_lit(self, name):
        self.eat("{")
        fields = []
        while self.cur().kind != "}":
            fname = self.eat("ID").value
            if self.cur().kind == ":":
                self.eat(":")
            elif self.cur().kind == "EQ":
                self.eat("EQ")
            fields.append((fname, self.pipeline()))
            if self.cur().kind == ",":
                self.eat(",")
        self.eat("}")
        return ("struct", name, fields)

    def list_lit(self):
        self.eat("[")
        items = []
        while self.cur().kind != "]":
            items.append(self.pipeline())
            if self.cur().kind == ",":
                self.eat(",")
        self.eat("]")
        return ("list", items)


def truthy(v):
    if isinstance(v, Result):
        return v.ok
    return not (v is None or v is False or v == 0 or v == "" or v == [])


def stringify(v):
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Function):
        return "<fn>"
    if isinstance(v, TypeDef):
        return f"<type {v.name}>"
    if isinstance(v, Struct):
        inner = ", ".join(f"{k}: {stringify(val)}" for k, val in v.values.items())
        return f"{v.name} {{ {inner} }}"
    if isinstance(v, Module):
        return f"<mod {v.name}>"
    if isinstance(v, Result):
        return f"{'Ok' if v.ok else 'Err'}({stringify(v.value)})"
    if isinstance(v, list):
        return "[" + ", ".join(stringify(x) for x in v) + "]"
    return str(v)


def interpolate(s: str, env: Env):
    out = []
    i = 0
    while i < len(s):
        if s[i] == "{":
            j = s.find("}", i)
            if j < 0:
                out.append(s[i:])
                break
            expr = s[i + 1 : j].strip()
            root, *rest = expr.split(".")
            val = env.get(root)
            for part in rest:
                val = eval_node(("dot", ("raw", val), part), env)
            out.append(stringify(val))
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def runtime_type_name(val) -> str:
    if isinstance(val, bool):
        return "Bool"
    if isinstance(val, int):
        return "Int"
    if isinstance(val, float):
        return "Float"
    if isinstance(val, str):
        return "Str"
    if isinstance(val, list):
        return "List"
    if isinstance(val, Result):
        return "Result"
    if isinstance(val, Struct):
        return val.name
    if val is None:
        return "None"
    return type(val).__name__


def check_type(val, typ: str | None, env: Env, node=None, subject: str = "值"):
    if typ is None:
        return
    mapping = {
        "Int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "Float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "Num": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "Str": lambda v: isinstance(v, str),
        "Bool": lambda v: isinstance(v, bool),
        "List": lambda v: isinstance(v, list),
        "Result": lambda v: isinstance(v, Result),
    }
    pred = mapping.get(typ)
    if pred and not pred(val):
        message = f"{subject}需要 {typ}，但得到 {runtime_type_name(val)}"
        location = getattr(node, "location", None)
        if location:
            raise CQDiagnostic("类型错误", message, location)
        raise CQError(f"类型错误：{message}")


def eval_node(node, env: Env):
    kind = node[0]
    if kind == "raw":
        return node[1]
    if kind == "block":
        val = None
        for s in node[1]:
            val = eval_node(s, env)
        return val
    if kind == "let":
        _, name, mutable, typ, expr = node
        val = eval_node(expr, env)
        check_type(val, typ, env, expr, f"变量 {name} ")
        env.define(name, val, mutable)
        return val
    if kind == "assign":
        val = eval_node(node[2], env)
        env.assign(node[1], val)
        return val
    if kind == "def":
        _, name, params, ret, body = node
        fn = Function(params, ret, body, env, getattr(node, "location", None))
        env.define(name, fn)
        return fn
    if kind == "lambda":
        return Function(node[1], node[2], node[3], env, getattr(node, "location", None))
    if kind == "type":
        td = TypeDef(node[1], node[2])
        env.define(node[1], td)
        return td
    if kind == "iface":
        env.define(node[1], ("iface", node[1], node[2]))
        return node[1]
    if kind == "impl":
        _, iface, typ, methods = node
        table = {}
        for m in methods:
            table[m[1]] = Function(m[2], m[3], m[4], env, getattr(m, "location", None))
        try:
            impls = env.get("__impls__")
        except CQError:
            impls = {}
            env.define("__impls__", impls)
        impls[(iface, typ)] = table
        return typ
    if kind == "import":
        env.define(node[1], load_module(node[1], env))
        return env.get(node[1])
    if kind == "for":
        xs = eval_node(node[2], env)
        if not isinstance(xs, list):
            raise CQError("for 只能遍历列表")
        val = None
        for item in xs:
            local = Env(env)
            local.define(node[1], item)
            val = eval_node(node[3], local)
        return val
    if kind == "if":
        if truthy(eval_node(node[1], env)):
            return eval_node(node[2], env)
        if node[3] is not None:
            return eval_node(node[3], env)
        return None
    if kind == "while":
        val = None
        n = 0
        while truthy(eval_node(node[1], env)):
            val = eval_node(node[2], env)
            n += 1
            if n > 100000:
                raise CQError("while 超过 100000 次，已打断")
        return val
    if kind == "and":
        a = eval_node(node[1], env)
        return eval_node(node[2], env) if truthy(a) else a
    if kind == "or":
        a = eval_node(node[1], env)
        return a if truthy(a) else eval_node(node[2], env)
    if kind == "try":
        val = eval_node(node[1], env)
        if isinstance(val, Result):
            if val.ok:
                return val.value
            raise ReturnSignal(val)
        if val is None:
            raise ReturnSignal(Result(False, "None"))
        return val
    if kind == "coalesce":
        val = eval_node(node[1], env)
        if val is None:
            return eval_node(node[2], env)
        if isinstance(val, Result):
            return val.value if val.ok else eval_node(node[2], env)
        return val
    if kind == "match":
        val = eval_node(node[1], env)
        for pat, body in node[2]:
            local = Env(env)
            if bind_pat(pat, val, local):
                return eval_node(body, local)
        raise CQError(f"match 没有匹配到：{stringify(val)}")
    if kind == "return":
        raise ReturnSignal(eval_node(node[1], env))
    if kind == "print":
        val = eval_node(node[1], env)
        text = stringify(val)
        print(text)
        return val
    if kind == "expr":
        return eval_node(node[1], env)
    if kind == "num":
        return node[1]
    if kind == "str":
        if "{" in node[1]:
            return interpolate(node[1], env)
        return node[1]
    if kind == "bool":
        return node[1]
    if kind == "none":
        return None
    if kind == "ok":
        return Result(True, eval_node(node[1], env))
    if kind == "err":
        return Result(False, eval_node(node[1], env))
    if kind == "var":
        return env.get(node[1])
    if kind == "list":
        return [eval_node(x, env) for x in node[1]]
    if kind == "struct":
        td = env.get(node[1])
        if not isinstance(td, TypeDef):
            raise CQError(f"{node[1]} 不是类型")
        values = {k: eval_node(v, env) for k, v in node[2]}
        field_nodes = dict(node[2])
        for fname, ftyp in td.fields:
            if fname not in values:
                raise CQError(f"{td.name} 缺少字段 {fname}")
            check_type(values[fname], ftyp, env, field_nodes[fname], f"字段 {td.name}.{fname} ")
        return Struct(td.name, values)
    if kind == "dot":
        obj = eval_node(node[1], env)
        field = node[2]
        if isinstance(obj, Struct):
            if field not in obj.values:
                raise CQError(f"{obj.name} 没有字段 {field}")
            return obj.values[field]
        if isinstance(obj, Module):
            if field not in obj.values:
                raise CQError(f"模块 {obj.name} 没有 {field}")
            return obj.values[field]
        raise CQError(f"不能取字段 {field}")
    if kind == "unary":
        v = eval_node(node[2], env)
        if node[1] == "-":
            return -v
        return not truthy(v)
    if kind == "binop":
        a = eval_node(node[2], env)
        b = eval_node(node[3], env)
        op = node[1]
        if op == "+" and (isinstance(a, str) or isinstance(b, str)):
            return stringify(a) + stringify(b)
        table = {
            "+": lambda x, y: x + y,
            "-": lambda x, y: x - y,
            "*": lambda x, y: x * y,
            "/": lambda x, y: x / y if not isinstance(x, int) or not isinstance(y, int) else (x // y if y != 0 and x % y == 0 else x / y),
            "%": lambda x, y: x % y,
            "==": lambda x, y: x == y,
            "!=": lambda x, y: x != y,
            "<": lambda x, y: x < y,
            ">": lambda x, y: x > y,
            "<=": lambda x, y: x <= y,
            ">=": lambda x, y: x >= y,
        }
        try:
            return table[op](a, b)
        except TypeError as e:
            raise CQError(f"运算 {op} 失败：{e}") from e
    if kind == "call":
        target = node[1]
        args = [eval_node(a, env) for a in node[2]]
        if target[0] == "dot":
            obj = eval_node(target[1], env)
            method = target[2]
            if isinstance(obj, Struct):
                try:
                    impls = env.get("__impls__")
                except CQError:
                    impls = {}
                for (_iface, typ), table in list(impls.items()):
                    if typ == obj.name and method in table:
                        return call_fn(table[method], [obj] + args, node, [target[1]] + node[2])
            if isinstance(obj, Module):
                return call_fn(obj.values[method], args, node, node[2])
            raise CQError(f"没有方法 {method}")
        fn = eval_node(target, env)
        return call_fn(fn, args, node, node[2])
    if kind == "pipe":
        left = eval_node(node[1], env)
        right = node[2]
        if right[0] == "call":
            fn = eval_node(right[1], env)
            args = [eval_node(a, env) for a in right[2]]
            return call_fn(fn, [left] + args, node, [node[1]] + right[2])
        if right[0] == "print":
            val = left
            print(stringify(val))
            return val
        fn = eval_node(right, env)
        return call_fn(fn, [left], node, [node[1]])
    raise CQError(f"未知节点 {kind}")


def bind_pat(pat, val, env: Env) -> bool:
    k = pat[0]
    if k == "ctor":
        tag, name = pat[1], pat[2]
        if not isinstance(val, Result):
            return False
        if tag == "Ok" and val.ok:
            env.define(name, val.value)
            return True
        if tag == "Err" and not val.ok:
            env.define(name, val.value)
            return True
        return False
    if k == "none":
        return val is None
    if k == "bind":
        env.define(pat[1], val)
        return True
    if k == "num":
        return val == pat[1]
    if k == "str":
        return val == pat[1]
    return False


def call_fn(fn, args, call_node=None, arg_nodes=None):
    if callable(fn) and not isinstance(fn, Function):
        return fn(*args)
    if isinstance(fn, TypeDef):
        raise CQError(f"请用 {fn.name} {{ 字段: 值 }} 创建结构体")
    if not isinstance(fn, Function):
        raise CQError(f"不能调用：{stringify(fn)}")
    if len(args) != len(fn.params):
        raise CQError(f"参数个数不对：需要 {len(fn.params)} 个，给了 {len(args)} 个")
    local = Env(fn.env)
    arg_nodes = arg_nodes or []
    for index, ((name, typ), val) in enumerate(zip(fn.params, args)):
        value_node = arg_nodes[index] if index < len(arg_nodes) else call_node or fn.body
        check_type(val, typ, local, value_node, f"参数 {name} ")
        local.define(name, val)
    try:
        result = eval_node(fn.body, local)
    except ReturnSignal as r:
        result = r.value
    result_node = fn.body
    if result_node[0] == "block" and result_node[1]:
        result_node = result_node[1][-1]
    check_type(result, fn.ret, local, result_node, "函数返回值 ")
    return result


def builtins(env: Env):
    env.define("map", lambda xs, f: [call_fn(f, [x]) for x in xs])
    env.define("filter", lambda xs, f: [x for x in xs if truthy(call_fn(f, [x]))])
    env.define("len", lambda xs: len(xs))
    env.define("range", lambda n: list(range(int(n))))
    env.define("push", lambda xs, v: xs + [v])
    env.define("first", lambda xs: xs[0] if xs else None)
    env.define("last", lambda xs: xs[-1] if xs else None)
    env.define("str", lambda v: stringify(v))
    env.define("int", lambda v: int(float(v)))
    env.define("float", lambda v: float(v))
    env.define("ok", lambda v: Result(True, v))
    env.define("err", lambda v: Result(False, v))
    env.define("unwrap", lambda r: r.value if isinstance(r, Result) and r.ok else (_ for _ in ()).throw(CQError(f"unwrap 失败：{stringify(r)}")))


def parse_source(src: str, filename: str = "<input>"):
    return Parser(tokenize(src, filename)).parse()


_BUILTIN_SKIP = {
    "map", "filter", "len", "range", "push", "first", "last", "str", "int", "float",
    "ok", "err", "unwrap", "__impls__",
}


def load_module(name: str, env: Env):
    here = CTX.get("dir") or os.getcwd()
    root = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, f"{name}.cq"),
        os.path.join(here, "std", f"{name}.cq"),
        os.path.join(here, "vendor", name, f"{name}.cq"),
        os.path.join(root, "std", f"{name}.cq"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                src = f.read()
            tree = parse_source(src, path)
            local = Env()
            builtins(local)
            prev = CTX.get("dir")
            CTX["dir"] = os.path.dirname(path)
            try:
                eval_node(tree, local)
            finally:
                CTX["dir"] = prev
            exported = {k: v for k, v in local.data.items() if k not in _BUILTIN_SKIP}
            return Module(name, exported)
    mods = std_modules()
    if name not in mods:
        raise CQError(f"找不到模块 {name}。试过：{candidates}，以及内置 {list(mods)}")
    mod = mods[name]
    if name == "task":
        raw_spawn = mod.values["spawn"]

        def spawn(fn, _raw=raw_spawn):
            if isinstance(fn, Function):
                snap = Env(fn.env.parent)
                snap.data = dict(fn.env.data)
                frozen = Function(fn.params, fn.ret, fn.body, snap, fn.location)
                return _raw(lambda: call_fn(frozen, []))
            return _raw(fn)

        mod.values["spawn"] = spawn
    return mod


def run_source(src: str, check: bool = True, filename: str = "<input>"):
    tree = parse_source(src, filename)
    errors = typecheck(tree)
    if check and errors:
        raise CQError("类型检查失败：\n" + "\n\n".join(str(error) for error in errors))
    env = Env()
    builtins(env)
    return eval_node(tree, env)


class TypeInfo:
    def __init__(self, name="Any", args=None):
        self.name = name
        self.args = args or []

    def __str__(self):
        if not self.args:
            return self.name
        return f"{self.name}<{', '.join(str(a) for a in self.args)}>"


def parse_typ(name: str | None) -> TypeInfo:
    if not name:
        return TypeInfo("Any")
    return TypeInfo(name)


class Checker:
    def __init__(self):
        self.errors: list[CQDiagnostic] = []
        self.env: dict[str, TypeInfo] = {
            "map": TypeInfo("Fn"),
            "filter": TypeInfo("Fn"),
            "len": TypeInfo("Fn"),
            "range": TypeInfo("Fn"),
            "print": TypeInfo("Fn"),
            "fs": TypeInfo("Module"),
            "json": TypeInfo("Module"),
            "http": TypeInfo("Module"),
            "task": TypeInfo("Module"),
        }
        self.ifaces: dict[str, list] = {}
        self.structs: dict[str, list] = {}
        self.mut: set[str] = set()
        self.impls: dict[tuple[str, str], list] = {}

    def err(self, msg, node):
        location = getattr(
            node,
            "location",
            SourceLocation(SourceFile("<input>", ""), 1, 1),
        )
        self.errors.append(CQDiagnostic("类型错误", msg, location))

    def vars_in(self, node, acc):
        if not isinstance(node, tuple):
            return
        if node[0] == "var":
            acc.add(node[1])
            return
        for x in node[1:]:
            if isinstance(x, tuple):
                self.vars_in(x, acc)
            elif isinstance(x, list):
                for y in x:
                    if isinstance(y, tuple):
                        self.vars_in(y, acc)

    def free_check(self, node, owner=None):
        if not isinstance(node, tuple):
            return
        owner = node if hasattr(node, "location") else owner
        if node[0] == "call":
            tgt, args = node[1], node[2]
            spawnish = (tgt[0] == "dot" and tgt[2] == "spawn") or (tgt[0] == "var" and tgt[1] == "spawn")
            if spawnish:
                for a in args:
                    used: set[str] = set()
                    self.vars_in(a, used)
                    bad = used & self.mut
                    if bad:
                        names = ", ".join(sorted(bad))
                        self.err(f"任务不能捕获可变变量 {names}；请先复制为不可变值", owner or node)
        for x in node[1:]:
            if isinstance(x, tuple):
                self.free_check(x, owner)
            elif isinstance(x, list):
                for y in x:
                    if isinstance(y, tuple):
                        self.free_check(y, owner)

    def infer(self, node) -> TypeInfo:
        k = node[0]
        if k == "num":
            return TypeInfo("Float" if isinstance(node[1], float) else "Int")
        if k == "str":
            return TypeInfo("Str")
        if k == "bool":
            return TypeInfo("Bool")
        if k == "none":
            return TypeInfo("Option", [TypeInfo("Any")])
        if k == "ok":
            return TypeInfo("Result", [self.infer(node[1]), TypeInfo("Any")])
        if k == "err":
            return TypeInfo("Result", [TypeInfo("Any"), self.infer(node[1])])
        if k == "list":
            inner = self.infer(node[1][0]) if node[1] else TypeInfo("Any")
            return TypeInfo("List", [inner])
        if k == "var":
            return self.env.get(node[1], TypeInfo("Any"))
        if k == "lambda":
            return TypeInfo("Fn")
        if k == "call":
            return TypeInfo("Any")
        if k == "pipe":
            return self.infer(node[2]) if node[2][0] != "print" else TypeInfo("Any")
        if k == "binop":
            op, a, b = node[1], self.infer(node[2]), self.infer(node[3])
            if op in {"==", "!=", "<", ">", "<=", ">="}:
                return TypeInfo("Bool")
            if a.name == "Str" or b.name == "Str":
                return TypeInfo("Str")
            if a.name == "Float" or b.name == "Float":
                return TypeInfo("Float")
            if a.name == "Int" and b.name == "Int":
                return TypeInfo("Int")
            return TypeInfo("Any")
        if k == "struct":
            return TypeInfo(node[1])
        if k == "dot":
            obj = self.infer(node[1])
            if obj.name in self.structs:
                fields = dict(self.structs[obj.name])
                if node[2] in fields and fields[node[2]]:
                    return parse_typ(fields[node[2]])
            if obj.name == "Module":
                return TypeInfo("Fn")
            return TypeInfo("Any")
        if k in {"print", "expr"}:
            return self.infer(node[1])
        return TypeInfo("Any")

    def stmt(self, node):
        k = node[0]
        if k == "block":
            for s in node[1]:
                self.stmt(s)
            return
        if k == "let":
            _, name, mutable, typ, expr = node
            got = self.infer(expr)
            want = parse_typ(typ)
            if typ and want.name not in {"Any", "Result"} and got.name not in {"Any", want.name}:
                self.err(f"变量 {name} 声明为 {want}，但右侧表达式是 {got}", expr)
            self.env[name] = want if typ else got
            if mutable:
                self.mut.add(name)
            self.free_check(expr)
            return
        if k == "assign":
            if node[1] not in self.env:
                self.err(f"变量 {node[1]} 尚未声明，不能赋值", node)
            return
        if k == "def":
            _, name, params, ret, body = node
            self.env[name] = TypeInfo("Fn", [parse_typ(ret)])
            saved = dict(self.env)
            for pname, ptyp in params:
                self.env[pname] = parse_typ(ptyp)
            self.stmt(body)
            self.env = saved
            self.env[name] = TypeInfo("Fn", [parse_typ(ret)])
            return
        if k == "type":
            self.structs[node[1]] = node[2]
            self.env[node[1]] = TypeInfo("Type")
            return
        if k == "iface":
            self.ifaces[node[1]] = node[2]
            self.env[node[1]] = TypeInfo("Iface")
            return
        if k == "impl":
            iface, typ, methods = node[1], node[2], node[3]
            if iface not in self.ifaces:
                self.err(f"接口 {iface} 尚未声明；请先写 `iface {iface} {{ ... }}`", node)
            else:
                need = {m[0] for m in self.ifaces[iface]}
                got = {m[1] for m in methods}
                missing = need - got
                extra = got - need
                if missing:
                    self.err(f"{typ} 实现 {iface} 时缺少方法：{', '.join(sorted(missing))}", node)
                if extra:
                    self.err(f"{typ} 实现了 {iface} 中未声明的方法：{', '.join(sorted(extra))}", node)
            self.impls[(iface, typ)] = methods
            return
        if k == "import":
            if node[1] not in {"fs", "json", "http", "task", "math", "web"}:
                # 文件模块在运行时解析；check 不报未知
                pass
            self.env[node[1]] = TypeInfo("Module")
            return
        if k == "match":
            val_t = self.infer(node[1])
            tags = []
            for pat, body in node[2]:
                if pat[0] == "ctor":
                    tags.append(pat[1])
                self.stmt(("block", [body]) if body[0] != "block" else body)
            if val_t.name == "Result" or True:
                if "Ok" in tags or "Err" in tags:
                    if "Ok" not in tags or "Err" not in tags:
                        missing = "Ok" if "Ok" not in tags else "Err"
                        self.err(f"Result 的 `match` 缺少 {missing} 分支；必须同时处理 Ok 和 Err", node)
            return
        if k == "if":
            condition_type = self.infer(node[1])
            if condition_type.name not in {"Bool", "Any", "Result"}:
                self.err(f"`if` 条件需要 Bool，但得到 {condition_type}", node[1])
            self.stmt(node[2])
            if node[3]:
                self.stmt(node[3])
            return
        if k == "for":
            self.infer(node[2])
            saved = dict(self.env)
            self.env[node[1]] = TypeInfo("Any")
            self.stmt(node[3])
            self.env = saved
            return
        if k in {"print", "expr", "return"}:
            self.infer(node[1])
            self.free_check(node[1])


def typecheck(tree) -> list[CQDiagnostic]:
    c = Checker()
    c.stmt(tree)
    return c.errors


def print_diagnostics(errors: list[CQDiagnostic], stream=None):
    stream = stream or sys.stdout
    for index, error in enumerate(errors):
        if index:
            print(file=stream)
        print(error, file=stream)


def py_quote(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def compile_expr(node, env_name="_e") -> str:
    k = node[0]
    if k == "num":
        return repr(node[1])
    if k == "str":
        if "{" in node[1]:
            return f"cq_rt.interpolate({py_quote(node[1])}, {env_name})"
        return py_quote(node[1])
    if k == "bool":
        return "True" if node[1] else "False"
    if k == "none":
        return "None"
    if k == "ok":
        return f"cq_rt.Result(True, {compile_expr(node[1], env_name)})"
    if k == "err":
        return f"cq_rt.Result(False, {compile_expr(node[1], env_name)})"
    if k == "var":
        return f"{env_name}[{py_quote(node[1])}]"
    if k == "list":
        items = ", ".join(compile_expr(x, env_name) for x in node[1])
        return f"[{items}]"
    if k == "binop":
        a = compile_expr(node[2], env_name)
        b = compile_expr(node[3], env_name)
        op = node[1]
        if op == "/":
            return f"({a} / {b})"
        return f"({a} {op} {b})"
    if k == "unary":
        return f"(-{compile_expr(node[2], env_name)})" if node[1] == "-" else f"(not {compile_expr(node[2], env_name)})"
    if k == "call":
        fn = compile_expr(node[1], env_name)
        args = ", ".join(compile_expr(a, env_name) for a in node[2])
        return f"({fn})({args})"
    if k == "dot":
        obj = compile_expr(node[1], env_name)
        return f"({obj}).{node[2]}"
    if k == "print":
        return f"cq_rt.cq_print({compile_expr(node[1], env_name)})"
    if k == "pipe":
        left = compile_expr(node[1], env_name)
        right = node[2]
        if right[0] == "call":
            fn = compile_expr(right[1], env_name)
            args = ", ".join([left] + [compile_expr(a, env_name) for a in right[2]])
            return f"({fn})({args})"
        if right[0] == "print":
            return f"cq_rt.cq_print({left})"
        return f"({compile_expr(right, env_name)})({left})"
    if k == "lambda":
        params = ", ".join(p[0] for p in node[1])
        body_py = compile_block(node[3], env_name, params.split(", ") if params else [])
        extra = ", ".join(py_quote(p[0]) + ": " + p[0] for p in node[1])
        return "(lambda %s: (lambda %s: %s)({**%s, %s}))" % (params, env_name, body_py, env_name, extra)
    if k == "struct":
        fields = ", ".join(f"{py_quote(k)}: {compile_expr(v, env_name)}" for k, v in node[2])
        return f"cq_rt.Struct({py_quote(node[1])}, {{{fields}}})"
    if k == "expr":
        return compile_expr(node[1], env_name)
    return "None"


def compile_block(node, env_name="_e", extra_params=None) -> str:
    if node[0] != "block":
        return compile_stmt(node, env_name)
    stmts = node[1]
    if not stmts:
        return "None"
    parts = [compile_stmt(s, env_name) for s in stmts]
    if len(parts) == 1:
        return parts[0]
    return "(" + ", ".join(parts) + ")[-1]"


def compile_stmt(node, env_name="_e") -> str:
    k = node[0]
    if k == "block":
        return compile_block(node, env_name)
    if k == "let":
        return f"{env_name}.__setitem__({py_quote(node[1])}, {compile_expr(node[4], env_name)})"
    if k == "assign":
        return f"{env_name}.__setitem__({py_quote(node[1])}, {compile_expr(node[2], env_name)})"
    if k == "print":
        return f"cq_rt.cq_print({compile_expr(node[1], env_name)})"
    if k == "expr":
        return compile_expr(node[1], env_name)
    if k == "return":
        return compile_expr(node[1], env_name)
    if k == "import":
        return f"{env_name}[{py_quote(node[1])}] = cq_rt.std_modules()[{py_quote(node[1])}]"
    if k == "type":
        return f"{env_name}[{py_quote(node[1])}] = {py_quote(node[1])}"
    if k == "iface":
        return f"{env_name}[{py_quote(node[1])}] = {py_quote('iface:' + node[1])}"
    if k == "def":
        fname = node[1]
        params = [p[0] for p in node[2]]
        plist = ", ".join(params)
        assigns = ", ".join(py_quote(p) + ": " + p for p in params)
        body = compile_block(node[4], env_name)
        return "%s.__setitem__(%s, (lambda %s: (lambda %s: %s)({**%s, %s})))" % (
            env_name, py_quote(fname), plist, env_name, body, env_name, assigns
        )
    if k == "if":
        cond = compile_expr(node[1], env_name)
        then = compile_block(node[2], env_name)
        els = compile_block(node[3], env_name) if node[3] else "None"
        return f"({then} if {cond} else {els})"
    if k == "for":
        item = node[1]
        xs = compile_expr(node[2], env_name)
        body = compile_block(node[3], env_name)
        return "[((lambda %s: %s)({**%s, %s: _it})) for _it in %s]" % (
            env_name, body, env_name, py_quote(item), xs
        )
    if k == "match":
        val = compile_expr(node[1], env_name)
        chunks = []
        for pat, body in node[2]:
            b = compile_expr(body, env_name) if body[0] != "block" else compile_block(body, env_name)
            if pat[0] == "ctor" and pat[1] == "Ok":
                chunks.append(
                    "(lambda _r: (lambda %s: %s)({**%s, %s: _r.value}) if _r.ok else _MISS)(_r)"
                    % (env_name, b, env_name, py_quote(pat[2]))
                )
            elif pat[0] == "ctor" and pat[1] == "Err":
                chunks.append(
                    "(lambda _r: (lambda %s: %s)({**%s, %s: _r.value}) if (not _r.ok) else _MISS)(_r)"
                    % (env_name, b, env_name, py_quote(pat[2]))
                )
            else:
                chunks.append(b)
        joined = " if _x is not _MISS else ".join(chunks) if chunks else "None"
        return f"(lambda _r, _MISS, _x: {chunks[0] if chunks else 'None'})({val}, object(), None)"
    return "None"


def compile_to_py(tree, src_name="program.cq") -> str:
    lines = [
        "#!/usr/bin/env python3",
        f"# Generated by CQ 0.2 compiler from {src_name}",
        "import sys",
        "sys.path.insert(0, sys.path[0])",
        "import cq_rt",
        "_e = {",
        "  'map': cq_rt.cq_map,",
        "  'filter': cq_rt.cq_filter,",
        "  'len': len,",
        "  'range': lambda n: list(range(int(n))),",
        "  'str': cq_rt.stringify,",
        "}",
        "_e.update({k: v for k, v in cq_rt.std_modules().items()})",
        "",
    ]
    if tree[0] == "block":
        for stmt in tree[1]:
            lines.append(compile_stmt(stmt, "_e"))
    else:
        lines.append(compile_stmt(tree, "_e"))
    return "\n".join(lines) + "\n"


def pkg_init(root: str, name: str | None = None):
    name = name or os.path.basename(os.path.abspath(root)) or "app"
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    os.makedirs(os.path.join(root, "vendor"), exist_ok=True)
    mod = os.path.join(root, "cq.mod")
    if not os.path.exists(mod):
        with open(mod, "w", encoding="utf-8") as f:
            f.write(f"module {name}\ncq 0.3\n\nrequire (\n)\n")
    main_cq = os.path.join(root, "src", "main.cq")
    if not os.path.exists(main_cq):
        with open(main_cq, "w", encoding="utf-8") as f:
            f.write('print("hello from CQ pkg")\n')
    print(f"已初始化包 {name}：{mod}")
    return 0


def pkg_add(root: str, dep: str):
    vendor = os.path.join(root, "vendor", dep)
    os.makedirs(vendor, exist_ok=True)
    lib = os.path.join(vendor, f"{dep}.cq")
    if not os.path.exists(lib):
        with open(lib, "w", encoding="utf-8") as f:
            f.write(f'// vendor/{dep}\nfn hello() -> Str {{\n  "{dep}"\n}}\n')
    mod = os.path.join(root, "cq.mod")
    if os.path.exists(mod):
        text = open(mod, encoding="utf-8").read()
        if dep not in text:
            text = text.replace("require (", f"require (\n  {dep} latest")
            open(mod, "w", encoding="utf-8").write(text)
    print(f"已添加依赖 {dep} -> {lib}")
    return 0


def repl():
    print("CQ 0.3  ·  输入 :q 退出")
    env = Env()
    builtins(env)
    buf = ""
    while True:
        try:
            line = input(".. " if buf else "cq> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not buf and line.strip() in {":q", ":quit", "exit"}:
            return 0
        buf += line + "\n"
        if line.strip().endswith("{") or (buf.count("{") > buf.count("}")):
            continue
        src = buf
        buf = ""
        try:
            tree = parse_source(src, "<repl>")
            errors = typecheck(tree)
            print_diagnostics(errors)
            val = eval_node(tree, env)
            if val is not None and not isinstance(val, Function):
                print(stringify(val))
        except CQDiagnostic as e:
            print(e)
        except CQError as e:
            print(f"CQ 错误：{e}")
        except Exception as e:
            print(f"内部错误：{e}")


def main_entry():
    raise SystemExit(main(sys.argv))


def main(argv):
    usage = (
        "用法:\n"
        "  cq                 交互 REPL\n"
        "  cq 文件.cq         运行\n"
        "  cq run    文件.cq\n"
        "  cq check  文件.cq\n"
        "  cq build  文件.cq\n"
        "  cq native 文件.cq [输出二进制]\n"
        "  cq web    文件.cq [输出目录]\n"
        "  cq pkg init [名字]\n"
        "  cq pkg add 名字\n"
    )
    if len(argv) < 2:
        return repl()
    cmd = argv[1]
    if cmd == "pkg":
        sub = argv[2] if len(argv) > 2 else ""
        if sub == "init":
            return pkg_init(os.getcwd(), argv[3] if len(argv) > 3 else None)
        if sub == "add":
            if len(argv) < 4:
                print(usage)
                return 1
            return pkg_add(os.getcwd(), argv[3])
        print(usage)
        return 1
    if cmd in {"run", "check", "build", "native", "web"}:
        if len(argv) < 3:
            print(usage)
            return 1
        path = argv[2]
    else:
        cmd = "run"
        path = argv[1]
    source_name = path
    path = os.path.abspath(path)
    CTX["dir"] = os.path.dirname(path)
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    try:
        tree = parse_source(src, source_name)
        errors = typecheck(tree)
        if cmd == "web":
            from cq_js import build_web
            if errors:
                print("CQ check 失败：")
                print_diagnostics(errors)
                return 1
            out_dir = argv[3] if len(argv) > 3 else os.path.join(os.path.dirname(path), "dist")
            html = build_web(tree, out_dir, os.path.basename(path), "CQ")
            print(f"已生成前端：{html}")
            return 0
        if cmd == "native":
            from cq_cgen import build_native, CGenError
            if errors:
                print("CQ check 失败：")
                print_diagnostics(errors)
                return 1
            out_bin = argv[3] if len(argv) > 3 else os.path.splitext(path)[0]
            cpath = build_native(tree, out_bin, keep_c=out_bin + ".c")
            print(f"已生成本地二进制：{out_bin}")
            print(f"C 源码：{cpath}")
            return 0
        if cmd == "check":
            if errors:
                print("CQ check 失败：")
                print_diagnostics(errors)
                return 1
            print("CQ check 通过")
            return 0
        if cmd == "build":
            if errors:
                print("CQ check 失败：")
                print_diagnostics(errors)
                return 1
            out = os.path.splitext(path)[0] + ".cq.py"
            py = compile_to_py(tree, os.path.basename(path))
            with open(out, "w", encoding="utf-8") as w:
                w.write(py)
            print(f"已编译：{out}")
            return 0
        if errors:
            print("CQ 类型警告：")
            print_diagnostics(errors)
        env = Env()
        builtins(env)
        eval_node(tree, env)
    except CQDiagnostic as e:
        print(e, file=sys.stderr)
        return 1
    except CQError as e:
        print(f"CQ 错误：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
