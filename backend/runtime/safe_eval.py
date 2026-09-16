# Model-authored conditions are interpreted over a closed AST grammar - never eval(), no attribute access
from __future__ import annotations

import ast
from typing import Any, Optional

class UnsafeExpression(Exception):
    pass

_MAX_POW = 128

_ALLOWED_METHODS = frozenset({
    "startswith", "endswith", "lower", "upper", "strip", "lstrip", "rstrip",
    "split", "rsplit", "join", "get", "keys", "values", "items", "count",
    "index", "find", "replace", "title", "capitalize", "isdigit", "isalpha",
    "isnumeric", "zfill",
})

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
    ast.USub, ast.UAdd, ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.FloorDiv, ast.Mod, ast.Pow, ast.Compare, ast.Eq, ast.NotEq, ast.Lt,
    ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.Name, ast.Load, ast.Store, ast.Constant, ast.Subscript, ast.Slice,
    ast.Index if hasattr(ast, "Index") else ast.Slice,
    ast.List, ast.Tuple, ast.Dict, ast.Set, ast.IfExp, ast.Call,
    ast.Attribute,
    ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp,
    ast.comprehension,
)

def _validate(tree: ast.AST, functions: Optional[set] = None) -> Optional[str]:
    method_attrs = {id(n.func) for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in _ALLOWED_METHODS
                    and not n.func.attr.startswith("_")}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and id(node) not in method_attrs:
            return (f"attribute access ({node.attr!r}) is not allowed in "
                    "expressions - only whitelisted method calls like .startswith(...)")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if id(node.func) not in method_attrs:
                    return f"method {node.func.attr!r} is not allowed"
            elif not isinstance(node.func, ast.Name):
                return "only plain named functions may be called"
            elif functions is not None and node.func.id not in functions:
                return (f"unknown function {node.func.id!r} - a check may call "
                        f"only: {', '.join(sorted(functions))}")
            if node.keywords:
                return "keyword arguments are not allowed"
        if isinstance(node, ast.comprehension):
            targets = [node.target] if isinstance(node.target, ast.Name) \
                else list(getattr(node.target, "elts", []))
            if not all(isinstance(t, ast.Name) for t in targets):
                return "comprehension targets must be plain names"
        if not isinstance(node, _ALLOWED_NODES):
            return f"{type(node).__name__} is not allowed in expressions"
    return None

# Says whether an expression fits the allowed grammar and calls only known functions, before it is ever stored
def check(expr: str, functions: set) -> Optional[str]:
    if not str(expr or "").strip():
        return "empty expression"
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return f"not a valid python expression: {e.msg}"
    return _validate(tree, functions)

# Reads which output fields an expression pins to known values, used to prefill certain form fields
def known_equalities(expr: str) -> dict:
    out: dict = {}
    try:
        tree = ast.parse(str(expr or ""), mode="eval").body
    except SyntaxError:
        return out
    parts = []
    while isinstance(tree, ast.BoolOp) and isinstance(tree.op, ast.And):
        parts.extend(tree.values[1:])
        tree = tree.values[0]
    parts.append(tree)
    for part in parts:
        if not (isinstance(part, ast.Compare) and len(part.ops) == 1
                and isinstance(part.ops[0], ast.Eq)
                and isinstance(part.left, ast.Name)):
            continue
        rhs = part.comparators[0]
        if isinstance(rhs, ast.Constant):
            out[part.left.id] = {"value": rhs.value}
        elif isinstance(rhs, ast.Name):
            out[part.left.id] = {"input": rhs.id}
    return out

# The names an expression reads, knowable at save time - so a bad check is refused before it ever runs
def free_names(expr: str) -> set:
    try:
        tree = ast.parse(str(expr or ""), mode="eval")
    except SyntaxError:
        return set()
    called, bound = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
        if isinstance(node, ast.comprehension):
            targets = [node.target] if isinstance(node.target, ast.Name) \
                else list(getattr(node.target, "elts", []))
            bound |= {t.id for t in targets if isinstance(t, ast.Name)}
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} \
        - called - bound

# Interprets the expression over its syntax tree with the given names and functions - eval() is never used
def evaluate(expr: str, scope: dict, functions: dict) -> Any:
    try:
        tree = ast.parse(str(expr or ""), mode="eval")
    except SyntaxError as e:
        raise UnsafeExpression(f"not a valid python expression: {e.msg}") from e
    problem = _validate(tree, set(functions))
    if problem:
        raise UnsafeExpression(problem)
    return _eval(tree.body, [dict(scope)], functions)

def _eval(node: ast.AST, scopes: list, functions: dict) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        for s in reversed(scopes):
            if node.id in s:
                return s[node.id]
        raise NameError(f"name {node.id!r} is not defined")
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            v: Any = True
            for x in node.values:
                v = _eval(x, scopes, functions)
                if not v:
                    return v
            return v
        for x in node.values:
            v = _eval(x, scopes, functions)
            if v:
                return v
        return v
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, scopes, functions)
        if isinstance(node.op, ast.Not):
            return not v
        return -v if isinstance(node.op, ast.USub) else +v
    if isinstance(node, ast.BinOp):
        a = _eval(node.left, scopes, functions)
        b = _eval(node.right, scopes, functions)
        if isinstance(node.op, ast.Pow):
            if isinstance(b, (int, float)) and abs(b) > _MAX_POW:
                raise UnsafeExpression(f"exponent above {_MAX_POW}")
            return a ** b
        ops = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b,
               ast.Mult: lambda: a * b, ast.Div: lambda: a / b,
               ast.FloorDiv: lambda: a // b, ast.Mod: lambda: a % b}
        return ops[type(node.op)]()
    if isinstance(node, ast.Compare):
        left = _eval(node.left, scopes, functions)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, scopes, functions)
            ok = {ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right,
                  ast.Lt: lambda: left < right, ast.LtE: lambda: left <= right,
                  ast.Gt: lambda: left > right, ast.GtE: lambda: left >= right,
                  ast.In: lambda: left in right, ast.NotIn: lambda: left not in right,
                  ast.Is: lambda: left is right, ast.IsNot: lambda: left is not right,
                  }[type(op)]()
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.Subscript):
        container = _eval(node.value, scopes, functions)
        if isinstance(node.slice, ast.Slice):
            lo = _eval(node.slice.lower, scopes, functions) if node.slice.lower else None
            hi = _eval(node.slice.upper, scopes, functions) if node.slice.upper else None
            st = _eval(node.slice.step, scopes, functions) if node.slice.step else None
            return container[slice(lo, hi, st)]
        inner = node.slice
        if inner.__class__.__name__ == "Index":
            inner = inner.value
        return container[_eval(inner, scopes, functions)]
    if isinstance(node, ast.List):
        return [_eval(x, scopes, functions) for x in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(x, scopes, functions) for x in node.elts)
    if isinstance(node, ast.Set):
        return {_eval(x, scopes, functions) for x in node.elts}
    if isinstance(node, ast.Dict):
        return {_eval(k, scopes, functions): _eval(v, scopes, functions)
                for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.IfExp):
        return (_eval(node.body, scopes, functions)
                if _eval(node.test, scopes, functions)
                else _eval(node.orelse, scopes, functions))
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr.startswith("_") or attr not in _ALLOWED_METHODS:
                raise UnsafeExpression(f"method {attr!r} is not allowed")
            obj = _eval(node.func.value, scopes, functions)
            fn = getattr(obj, attr)
        else:
            fn = functions[node.func.id]
        return fn(*[_eval(a, scopes, functions) for a in node.args])
    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        out = list(_comp(node.generators, node.elt, scopes, functions))
        if isinstance(node, ast.SetComp):
            return set(out)
        return out
    if isinstance(node, ast.DictComp):
        return {k: v for k, v in _comp(node.generators, (node.key, node.value),
                                       scopes, functions, pair=True)}
    raise UnsafeExpression(f"{type(node).__name__} is not allowed in expressions")

def _comp(generators, elt, scopes: list, functions: dict, pair: bool = False):
    def rec(gi: int):
        if gi == len(generators):
            if pair:
                yield (_eval(elt[0], scopes, functions),
                       _eval(elt[1], scopes, functions))
            else:
                yield _eval(elt, scopes, functions)
            return
        gen = generators[gi]
        for item in _eval(gen.iter, scopes, functions):
            local: dict = {}
            if isinstance(gen.target, ast.Name):
                local[gen.target.id] = item
            else:
                for t, v in zip(gen.target.elts, item):
                    local[t.id] = v
            scopes.append(local)
            try:
                if all(_eval(cond, scopes, functions) for cond in gen.ifs):
                    yield from rec(gi + 1)
            finally:
                scopes.pop()
    yield from rec(0)
