from __future__ import annotations

import ast
import unittest
from pathlib import Path


ADAPTER_ROOT = Path(__file__).resolve().parents[1] / "airco_tracker" / "adapters"


class AdapterTransportBoundaryTests(unittest.TestCase):
    def test_adapters_cannot_bypass_the_hardened_fetcher(self) -> None:
        violations: list[str] = []
        forbidden_modules = {"requests", "httpx", "aiohttp", "urllib.request"}
        forbidden_calls = {"urlopen"}

        for path in sorted(ADAPTER_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in forbidden_modules:
                            violations.append(f"{path}:{node.lineno}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module in forbidden_modules:
                        violations.append(f"{path}:{node.lineno}: from {node.module}")
                elif isinstance(node, ast.Attribute) and node.attr == "session":
                    violations.append(f"{path}:{node.lineno}: direct .session access")
                elif isinstance(node, ast.Attribute) and node.attr == "post":
                    violations.append(f"{path}:{node.lineno}: direct .post access")
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in forbidden_calls
                ):
                    violations.append(f"{path}:{node.lineno}: direct {node.func.id} call")

        self.assertEqual(
            violations,
            [],
            "All adapter HTTP must use airco_tracker.fetch.Fetcher:\n"
            + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
