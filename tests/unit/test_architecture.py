"""The layering, enforced: each package may import only from the layers listed for it.

    domain      <- strategies <- services <- container <- cli
       ^            ^              ^
       +---------- storage (contracts in base.py; memory.py and postgres.py implement them)
"""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
MODULES = sorted(p for p in APP.rglob("*.py") if p != APP / "__init__.py")

ALLOWED = {
    "domain": {"domain"},
    "strategies": {"domain", "strategies", "storage.base"},
    "storage": {"domain", "storage"},
    "services": {"domain", "strategies", "services", "storage.base"},
    "config": {"domain", "strategies"},
    "container": {"config", "domain", "strategies", "services", "storage.base", "storage.memory"},
    "cli": {"cli", "config", "container", "domain", "strategies", "storage"},
}


def layer_of(module: str) -> str:
    return module.split(".")[0]


def app_imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            yield node.module[len("app."):]


def allowed(target: str, rules) -> bool:
    return any(target == rule or target.startswith(rule + ".") for rule in rules)


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(APP)))
def test_module_imports_only_from_allowed_layers(path):
    module = ".".join(path.relative_to(APP).with_suffix("").parts)
    rules = ALLOWED[layer_of(module)]
    bad = [target for target in app_imports(path) if not allowed(target, rules)]
    assert not bad, f"app/{path.relative_to(APP)} imports {bad}; allowed: {sorted(rules)}"


def test_the_rules_reject_a_layer_violation():
    # Guards against the check passing vacuously.
    assert not allowed("storage.postgres", ALLOWED["services"])
    assert not allowed("services.rides", ALLOWED["domain"])
    assert allowed("storage.base", ALLOWED["services"])
