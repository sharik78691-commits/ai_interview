"""Architecture (layering) tests — fail when a module imports across a forbidden boundary.

Layers, outermost first:

    main ─▶ api (routers) ─▶ services ─▶ repositories ─▶ models / db
                 │               └──▶ providers (LLM / STT adapters)
                 └──▶ auth, core, schemas

Rules enforced here (all imports, including function-local ones):

* routers never import other routers (shared state belongs in a service);
* nothing below the router layer imports ``app.api``;
* the DB layer stays schema-only (no auth / services / api);
* repositories hold queries only (no services / api / auth / providers);
* providers are adapters (no services / api); their DB access is limited to
  the two model-override readers listed in ``PROVIDER_DB_ALLOWLIST``;
* core is the foundation (imports nothing from higher layers).
"""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# Documented exceptions: admin overrides for model enable/default flags are
# read straight from the DB by the provider registries (cached, read-only).
PROVIDER_DB_ALLOWLIST = {
    "app/providers/llm/registry.py",
    "app/providers/stt/provider.py",
}


def _modules() -> list[Path]:
    return [p for p in APP.rglob("*.py") if "__pycache__" not in p.parts]


def _rel(path: Path) -> str:
    return path.relative_to(APP.parent).as_posix()


def _app_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.startswith("app."):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    found.add(alias.name)
    return found


def _layer(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def _violations(source_layer: str, forbidden: set[str], allow: set[str] = frozenset()) -> list[str]:
    bad = []
    for path in _modules():
        rel = _rel(path)
        if not rel.startswith(f"app/{source_layer}/") or rel in allow:
            continue
        for mod in _app_imports(path):
            if _layer(mod) in forbidden:
                bad.append(f"{rel} imports {mod}")
    return sorted(bad)


def test_routers_do_not_import_each_other():
    bad = []
    for path in _modules():
        rel = _rel(path)
        if not rel.startswith("app/api/"):
            continue
        bad += [f"{rel} imports {m}" for m in _app_imports(path) if _layer(m) == "api"]
    assert not bad, "Move shared state into app/services:\n" + "\n".join(bad)


@pytest.mark.parametrize(
    "layer", ["services", "repositories", "providers", "models", "schemas", "core", "db", "auth"]
)
def test_lower_layers_never_import_routers(layer):
    assert not _violations(layer, {"api", "main"})


def test_db_layer_is_schema_only():
    assert not _violations("db", {"auth", "services", "repositories", "providers"})


def test_repositories_hold_queries_only():
    assert not _violations("repositories", {"services", "auth", "providers"})


def test_providers_are_adapters():
    assert not _violations("providers", {"services", "auth", "repositories"})
    assert not _violations("providers", {"db"}, allow=PROVIDER_DB_ALLOWLIST)


def test_core_is_the_foundation():
    assert not _violations("core", {"services", "repositories", "providers", "auth", "db", "models"})


def test_services_do_not_depend_on_http_framework():
    """Business services stay transport-agnostic (no FastAPI request objects)."""
    bad = []
    for path in _modules():
        rel = _rel(path)
        if not rel.startswith("app/services/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                ("fastapi", "starlette")
            ):
                bad.append(f"{rel} imports {node.module}")
    assert not bad, "\n".join(bad)
