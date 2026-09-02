"""CQ -> JavaScript compiler for frontend."""
from __future__ import annotations
import json, os

def js_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)

def compile_expr(node, env="_e") -> str:
    k = node[0]
    if k == "num": return str(node[1])
    if k == "str":
        if "{" in node[1]:
            return f"cqRt.interpolate({js_str(node[1])}, {env})"
        return js_str(node[1])
    if k == "bool": return "true" if node[1] else "false"
    if k == "none": return "null"
    if k == "ok": return f"cqRt.ok({compile_expr(node[1], env)})"
    if k == "err": return f"cqRt.err({compile_expr(node[1], env)})"
    if k == "var": return f"{env}[{js_str(node[1])}]"
    if k == "list": return "[" + ", ".join(compile_expr(x, env) for x in node[1]) + "]"
    if k == "binop":
        a, b = compile_expr(node[2], env), compile_expr(node[3], env)
        op = node[1]
        if op == "==": return f"({a} === {b})"
        if op == "!=": return f"({a} !== {b})"
        return f"({a} {op} {b})"
    if k == "unary":
        inner = compile_expr(node[2], env)
        return f"(-({inner}))" if node[1] == "-" else f"(!({inner}))"
    if k == "call":
        if node[1][0] == "dot":
            obj = compile_expr(node[1][1], env)
            args = ", ".join(compile_expr(a, env) for a in node[2])
            return f"cqRt.callMethod({obj}, {js_str(node[1][2])}, [{args}])"
        fn = compile_expr(node[1], env)
        args = ", ".join(compile_expr(a, env) for a in node[2])
        return f"cqRt.call({fn}, [{args}])"
    if k == "dot": return f"cqRt.get({compile_expr(node[1], env)}, {js_str(node[2])})"
    if k == "print": return f"cqRt.print({compile_expr(node[1], env)})"
    if k == "pipe":
        left = compile_expr(node[1], env)
        right = node[2]
        if right[0] == "call":
            fn = compile_expr(right[1], env)
            extra = ", ".join(compile_expr(a, env) for a in right[2])
            args = left if not extra else f"{left}, {extra}"
            return f"cqRt.call({fn}, [{args}])"
        if right[0] == "print": return f"cqRt.print({left})"
        return f"cqRt.call({compile_expr(right, env)}, [{left}])"
    if k == "lambda":
        params = [p[0] for p in node[1]]
        plist = ", ".join(params)
        assigns = ", ".join(f"{js_str(p)}: {p}" for p in params)
        body = compile_block(node[3], env)
        return (f"(function(parent){{ return function({plist}){{ "
                f"const {env} = Object.assign(Object.create(parent), {{{assigns}}}); return {body}; }}; }})({env})")
    if k == "struct":
        fields = ", ".join(f"{js_str(k_)}: {compile_expr(v, env)}" for k_, v in node[2])
        return f"cqRt.struct({js_str(node[1])}, {{{fields}}})"
    if k == "expr": return compile_expr(node[1], env)
    return "null"

def compile_block(node, env="_e") -> str:
    if node[0] != "block": return compile_stmt(node, env)
    stmts = node[1]
    if not stmts: return "null"
    parts = [compile_stmt(s, env) for s in stmts]
    return "([" + ", ".join(parts) + "]).at(-1)"

def compile_stmt(node, env="_e") -> str:
    k = node[0]
    if k == "block": return compile_block(node, env)
    if k == "let": return f"({env}[{js_str(node[1])}] = {compile_expr(node[4], env)})"
    if k == "assign": return f"cqRt.set({env}, {js_str(node[1])}, {compile_expr(node[2], env)})"
    if k == "print": return f"cqRt.print({compile_expr(node[1], env)})"
    if k == "expr": return compile_expr(node[1], env)
    if k == "return": return compile_expr(node[1], env)
    if k == "import": return f"({env}[{js_str(node[1])}] = cqRt.modules[{js_str(node[1])}])"
    if k == "type": return f"({env}[{js_str(node[1])}] = {js_str(node[1])})"
    if k == "iface": return "null"
    if k == "def":
        params = [p[0] for p in node[2]]
        plist = ", ".join(params)
        assigns = ", ".join(f"{js_str(p)}: {p}" for p in params)
        body = compile_block(node[4], env)
        return (f"({env}[{js_str(node[1])}] = (function(parent){{ return function({plist}){{ "
                f"const {env} = Object.assign(Object.create(parent), {{{assigns}}}); return {body}; }}; }})({env}))")
    if k == "if":
        cond = compile_expr(node[1], env)
        then = compile_block(node[2], env)
        els = compile_block(node[3], env) if node[3] else "null"
        return f"({cond} ? {then} : {els})"
    if k == "for":
        item = node[1]
        xs = compile_expr(node[2], env)
        body = compile_block(node[3], env)
        return (f"({xs}).map((_it)=>{{ const {env} = Object.assign({{}}, {env}, "
                f"{{{js_str(item)}: _it}}); return {body}; }})")
    if k == "match":
        val = compile_expr(node[1], env)
        arms = []
        for pat, body in node[2]:
            b = compile_expr(body, env) if body[0] != "block" else compile_block(body, env)
            if pat[0] == "ctor" and pat[1] == "Ok":
                arms.append(f"(_r.ok ? (function(){{ const {env} = Object.assign({{}}, {env}, {{{js_str(pat[2])}: _r.value}}); return {b}; }})() : undefined)")
            elif pat[0] == "ctor" and pat[1] == "Err":
                arms.append(f"(!_r.ok ? (function(){{ const {env} = Object.assign({{}}, {env}, {{{js_str(pat[2])}: _r.value}}); return {b}; }})() : undefined)")
            else:
                arms.append(b)
        chain = " ?? ".join(arms) if arms else "null"
        return f"(function(_r){{ return {chain}; }})({val})"
    return "null"

def compile_to_js(tree, src_name="app.cq") -> str:
    lines = [f"// Generated by CQ from {src_name}", "const _e = cqRt.env();"]
    if tree[0] == "block":
        for stmt in tree[1]:
            lines.append(compile_stmt(stmt, "_e") + ";")
    else:
        lines.append(compile_stmt(tree, "_e") + ";")
    return "\n".join(lines) + "\n"

def wrap_html(title: str, js_name: str = "app.js") -> str:
    return "<!doctype html><html lang=zh-CN><head><meta charset=utf-8><title>"+title+"</title></head><body><div id=app></div><pre id=log></pre><script src=cq_rt.js></script><script src="+js_name+"></script></body></html>\n"

def build_web(tree, out_dir: str, src_name="app.cq", title="CQ"):
    os.makedirs(out_dir, exist_ok=True)
    here = os.path.dirname(os.path.abspath(__file__))
    rt_src = os.path.join(here, "web", "cq_rt.js")
    with open(rt_src, "r", encoding="utf-8") as f:
        rt = f.read()
    with open(os.path.join(out_dir, "cq_rt.js"), "w", encoding="utf-8") as f:
        f.write(rt)
    with open(os.path.join(out_dir, "app.js"), "w", encoding="utf-8") as f:
        f.write(compile_to_js(tree, src_name))
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(wrap_html(title))
    return os.path.join(out_dir, "index.html")
