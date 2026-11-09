"""CLAUDE.md "Dependency direction", read from the code with `ast`: nothing is imported.

Every `bleep.*` import counts, at module level, inside a function or under `if TYPE_CHECKING:`,
since a type-only import still ties the two layers together.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "bleep"

# What each part of `bleep/` may import besides itself, in CLAUDE.md's order from the top down.
ALLOWED: dict[str, set[str]] = {
    "main": {"api", "wiring", "watcher", "incidents", "graph", "hosts", "config"},
    "api": {
        "wiring",
        "auth",
        "watcher",
        "notify",
        "talk",
        "incidents",
        "plugins",
        "llm",
        "guard",
        "hosts",
        "store",
        "schemas",
        "config",
        "prompts",
    },
    "wiring": {
        "auth",
        "watcher",
        "notify",
        "talk",
        "incidents",
        "graph",
        "plugins",
        "tools",
        "knowledge",
        "memory",
        "llm",
        "guard",
        "hosts",
        "store",
        "schemas",
        "config",
        "prompts",
    },
    "watcher": {
        "incidents",
        "graph",
        "llm",
        "guard",
        "hosts",
        "store",
        "schemas",
        "config",
        "prompts",
    },
    "notify": {"talk", "incidents", "graph", "guard", "store", "schemas", "config", "prompts"},
    "talk": {"incidents", "llm", "guard", "hosts", "store", "schemas", "config", "prompts"},
    "incidents": {"graph", "plugins", "llm", "guard", "hosts", "store", "schemas", "prompts"},
    "graph": {"tools", "memory", "llm", "guard", "hosts", "schemas", "prompts"},
    "plugins": {"tools", "guard", "hosts", "schemas"},
    "tools": {"memory", "guard", "hosts", "schemas"},
    "knowledge": {"memory", "guard", "hosts", "store", "schemas"},
    "memory": {"guard", "store", "schemas"},
    "llm": {"store", "schemas", "config", "prompts"},
    "auth": {"store"},
    "guard": {"hosts", "store", "schemas"},
    "hosts": {"store", "schemas"},
    "schemas": set(),
    "store": set(),
    "prompts": set(),
    "config": set(),
}

# The arrow against the direction, held to one module and named in CLAUDE.md.
NARROW: dict[str, set[str]] = {
    "schemas": {"hosts.config"},
}

# `api` takes only the type of the parts from `wiring`; only `main` builds them.
API_FROM_WIRING = {"AppParts"}


def bleep_imports(source: str) -> list[tuple[int, str, list[str]]]:
    """(line, module, names) of every `bleep.*` import in `source`; `from bleep import x` gives
    the module `bleep.x`."""
    found: list[tuple[int, str, list[str]]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name, []) for a in node.names if a.name.startswith("bleep.")]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                found.append((node.lineno, "." * node.level + (node.module or ""), []))
            elif node.module == "bleep":
                found += [(node.lineno, f"bleep.{a.name}", []) for a in node.names]
            elif node.module and node.module.startswith("bleep."):
                found.append((node.lineno, node.module, [a.name for a in node.names]))
    return found


def _part(path: Path) -> str:
    return path.relative_to(SRC).parts[0].removesuffix(".py")


def _allowed(part: str, module: str) -> bool:
    target = module.removeprefix("bleep.")
    if target.split(".")[0] in ALLOWED[part] | {part}:
        return True
    return any(target == t or target.startswith(t + ".") for t in NARROW.get(part, set()))


def _more_than_app_parts(names: list[str]) -> bool:
    """Whether an import from `wiring` takes more than `AppParts`; `import bleep.wiring` (no
    names) takes all of it."""
    return not names or bool(set(names) - API_FROM_WIRING)


def _modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if p != SRC / "__init__.py")


def test_every_part_of_bleep_has_a_rule():
    parts = {_part(p) for p in _modules()}
    assert parts - ALLOWED.keys() == set()
    assert ALLOWED.keys() - parts == set()


def test_every_import_follows_the_dependency_direction():
    wrong = []
    for path in _modules():
        part = _part(path)
        for line, module, names in bleep_imports(path.read_text()):
            where = f"{path.relative_to(SRC)}:{line} imports {module}"
            if module.startswith("."):
                wrong.append(f"{where}: write imports in full, from bleep.")
            elif not _allowed(part, module):
                wrong.append(f"{where}: {part} may import {sorted(ALLOWED[part])}")
            elif part == "api" and module == "bleep.wiring" and _more_than_app_parts(names):
                wrong.append(f"{where}: api takes only {sorted(API_FROM_WIRING)} from wiring")
    assert wrong == []


def test_each_part_may_import_only_parts_below_it():
    order = list(ALLOWED)
    upward = [
        f"{part} -> {target}"
        for part, targets in ALLOWED.items()
        for target in targets
        if order.index(target) <= order.index(part)
    ]
    assert upward == []


def test_imports_inside_functions_and_type_checking_blocks_count():
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from bleep.incidents.repository import IncidentRepo\n"
        "def later():\n"
        "    import bleep.watcher.manager\n"
        "    from bleep import prompts\n"
    )
    assert bleep_imports(source) == [
        (3, "bleep.incidents.repository", ["IncidentRepo"]),
        (5, "bleep.watcher.manager", []),
        (6, "bleep.prompts", []),
    ]
