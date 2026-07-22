from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render_job_execution_template.py"
SPEC = importlib.util.spec_from_file_location("render_job_execution_template", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class JobExecutionTemplateTests(unittest.TestCase):
    def template(self) -> dict:
        return {
            "containers": [
                {
                    "name": "recipient-reconciler",
                    "image": "registry.example/airco:old",
                    "imageType": "ContainerImage",
                    "command": ["airco-tracker"],
                    "args": ["reconcile-alert-recipients"],
                    "env": [
                        {"name": "PLAIN", "value": "value with spaces"},
                        {"name": "SECRET", "secretRef": "secret-name"},
                    ],
                    "resources": {
                        "cpu": 0.5,
                        "memory": "1Gi",
                        "ephemeralStorage": "2Gi",
                    },
                }
            ],
            "initContainers": None,
            "volumes": None,
        }

    def test_replaces_only_image_and_preserves_runtime_contract(self) -> None:
        rendered = MODULE.render_execution_template(
            self.template(), "registry.example/airco:candidate"
        )
        container = rendered["containers"][0]
        self.assertEqual(container["image"], "registry.example/airco:candidate")
        self.assertEqual(container["name"], "recipient-reconciler")
        self.assertEqual(container["command"], ["airco-tracker"])
        self.assertEqual(container["args"], ["reconcile-alert-recipients"])
        self.assertEqual(len(container["env"]), 2)
        self.assertEqual(container["resources"], {"cpu": 0.5, "memory": "1Gi"})
        self.assertNotIn("imageType", container)
        self.assertNotIn("ephemeralStorage", container["resources"])
        self.assertNotIn("volumes", rendered)

    def test_rejects_ambiguous_or_volume_backed_templates(self) -> None:
        multiple = self.template()
        multiple["containers"].append(dict(multiple["containers"][0]))
        with self.assertRaisesRegex(MODULE.TemplateError, "exactly one container"):
            MODULE.render_execution_template(multiple, "registry.example/airco:new")

        volume_backed = self.template()
        volume_backed["volumes"] = [{"name": "data", "storageType": "EmptyDir"}]
        with self.assertRaisesRegex(MODULE.TemplateError, "job volumes"):
            MODULE.render_execution_template(volume_backed, "registry.example/airco:new")

    def test_rejects_invalid_environment_sources(self) -> None:
        invalid = self.template()
        invalid["containers"][0]["env"] = [
            {"name": "AMBIGUOUS", "value": "x", "secretRef": "secret"}
        ]
        with self.assertRaisesRegex(MODULE.TemplateError, "exactly one value source"):
            MODULE.render_execution_template(invalid, "registry.example/airco:new")


if __name__ == "__main__":
    unittest.main()
