"""Annotations must resolve on every Python version we claim to support.

pyproject declares `requires-python = ">=3.12"`. On Python 3.12 and 3.13,
function annotations are evaluated eagerly at definition time, so a name used in
an annotation but never imported raises NameError **at import**. Python 3.14
defers evaluation (PEP 649), which hides the same mistake completely.

That is not hypothetical: `integrations/admin_db.py` annotated
`build_admin_store() -> AdminStore` without importing `AdminStore`. It imported
cleanly on the 3.14 test environment and crashed on a fresh Ubuntu 24.04
server running Python 3.12.3, at boot, before serving a single request.

`typing.get_type_hints()` resolves annotations the way 3.12 would, on any
interpreter, so this catches the whole class without needing a 3.12 box.
"""
import importlib
import inspect
import pkgutil
import typing
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Only our own code. .opencode/ holds vendored third-party tooling that is not
# imported by the game and is not covered by the >=3.12 claim.
PACKAGES = ["common", "games", "integrations", "provider", "staging"]


def _our_modules():
    for pkg in PACKAGES:
        mod = importlib.import_module(pkg)
        yield pkg
        for info in pkgutil.walk_packages(mod.__path__, prefix=pkg + "."):
            yield info.name


class AnnotationCompatibilityTest(unittest.TestCase):
    def test_every_our_module_imports(self):
        failures = {}
        for name in _our_modules():
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001
                failures[name] = f"{type(exc).__name__}: {exc}"
        self.assertEqual(failures, {},
                         f"modules that fail to import: {failures}")

    def test_every_annotation_resolves(self):
        """The check that would have caught the admin_db.py bug.

        get_type_hints forces the same eager resolution 3.12 performs, so an
        unimported name in an annotation surfaces here instead of in
        production.
        """
        problems = {}
        for name in _our_modules():
            try:
                module = importlib.import_module(name)
            except Exception:
                continue  # reported by the test above
            ns = vars(module)
            for attr, value in list(ns.items()):
                targets = []
                if inspect.isfunction(value):
                    targets.append((attr, value))
                elif inspect.isclass(value) and value.__module__ == name:
                    targets.append((attr, value))
                for label, obj in targets:
                    try:
                        typing.get_type_hints(obj, globalns=ns)
                    except Exception as exc:  # noqa: BLE001
                        problems[f"{name}.{label}"] = f"{type(exc).__name__}: {exc}"
        self.assertEqual(problems, {},
                         f"unresolvable annotations (NameError on py3.12): "
                         f"{problems}")

    def test_the_specific_regression_is_pinned(self):
        """If someone drops the import again, fail by name."""
        import integrations.admin_db as mod
        self.assertIn("AdminStore", vars(mod),
                      "admin_db must import AdminStore for its return "
                      "annotation to resolve on Python 3.12")

    def test_declared_floor_is_actually_importable(self):
        # Belt and braces: the annotation check above is the real guard, but a
        # module that only works because of deferred evaluation would still
        # show up as a floor mismatch here on 3.12.
        for name in ("integrations.admin_db", "common.admin_store",
                     "games.teen_patti_pro.api"):
            importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
