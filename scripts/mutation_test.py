"""Lightweight mutation testing for the pure core logic.

mutmut does not run natively on Windows, so this does the same job in ~100
lines: parse a module, generate one mutant per arithmetic/comparison operator,
numeric constant and boolean operator, write it in place, run the relevant
tests, and restore the original. A mutant is "killed" when the tests fail.

Usage:
    python scripts/mutation_test.py                       # default targets
    python scripts/mutation_test.py app/services/tdee.py tests/test_tdee.py
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TARGETS = [
    ("app/services/tdee.py", ["tests/test_tdee.py"]),
    ("app/services/nutrition.py", ["tests/test_nutrition.py"]),
]

BINOP_SWAPS = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}
CMP_SWAPS = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
             ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.In: ast.NotIn, ast.NotIn: ast.In}
BOOL_SWAPS = {ast.And: ast.Or, ast.Or: ast.And}


def _nodes(tree: ast.AST):
    """Nodes inside function bodies only; seed-data tables are not logic."""
    for top in ast.walk(tree):
        if isinstance(top, ast.FunctionDef):
            for stmt in top.body:
                yield from ast.walk(stmt)


def _mutation_points(tree: ast.AST) -> list[tuple[str, int]]:
    """(kind, index-within-kind) for every mutable node, in walk order."""
    points: list[tuple[str, int]] = []
    counters = {"bin": 0, "cmp": 0, "bool": 0, "num": 0}
    for node in _nodes(tree):
        kind = None
        if isinstance(node, ast.BinOp) and type(node.op) in BINOP_SWAPS:
            kind = "bin"
        elif isinstance(node, ast.Compare) and type(node.ops[0]) in CMP_SWAPS:
            kind = "cmp"
        elif isinstance(node, ast.BoolOp) and type(node.op) in BOOL_SWAPS:
            kind = "bool"
        elif (isinstance(node, ast.Constant) and type(node.value) in (int, float)
              and not isinstance(node.value, bool)):
            kind = "num"
        if kind:
            points.append((kind, counters[kind]))
            counters[kind] += 1
    return points


def _apply(tree: ast.AST, kind: str, index: int) -> str:
    seen = 0
    for node in _nodes(tree):
        match = (
            (kind == "bin" and isinstance(node, ast.BinOp) and type(node.op) in BINOP_SWAPS)
            or (kind == "cmp" and isinstance(node, ast.Compare) and type(node.ops[0]) in CMP_SWAPS)
            or (kind == "bool" and isinstance(node, ast.BoolOp) and type(node.op) in BOOL_SWAPS)
            or (kind == "num" and isinstance(node, ast.Constant)
                and type(node.value) in (int, float) and not isinstance(node.value, bool))
        )
        if not match:
            continue
        if seen == index:
            if kind == "bin":
                node.op = BINOP_SWAPS[type(node.op)]()
            elif kind == "cmp":
                node.ops[0] = CMP_SWAPS[type(node.ops[0])]()
            elif kind == "bool":
                node.op = BOOL_SWAPS[type(node.op)]()
            else:
                node.value = node.value + 1
            return ast.unparse(tree)
        seen += 1
    raise IndexError(index)


def run(target: str, tests: list[str]) -> tuple[int, int, list[str]]:
    path = ROOT / target
    original = path.read_text(encoding="utf-8")
    points = _mutation_points(ast.parse(original))
    killed, survivors = 0, []
    try:
        for kind, index in points:
            path.write_text(_apply(ast.parse(original), kind, index), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests],
                cwd=ROOT, capture_output=True,
            )
            if result.returncode != 0:
                killed += 1
            else:
                survivors.append(f"{kind}#{index}")
    finally:
        path.write_text(original, encoding="utf-8")
    return killed, len(points), survivors


def main(argv: list[str]) -> int:
    targets = [(argv[0], argv[1:])] if argv else DEFAULT_TARGETS
    total_killed = total = 0
    for target, tests in targets:
        killed, count, survivors = run(target, tests)
        total_killed += killed
        total += count
        score = 100 * killed / count if count else 100
        print(f"{target}: {killed}/{count} killed ({score:.1f}%)  survivors: {', '.join(survivors) or '-'}")
    if total:
        print(f"TOTAL: {total_killed}/{total} killed ({100 * total_killed / total:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
