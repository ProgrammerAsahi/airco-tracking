#!/usr/bin/env python3
"""Render a one-off Container Apps Job execution template safely."""

from __future__ import annotations

import json
import sys
from typing import Any


MAX_TEMPLATE_BYTES = 1_000_000


class TemplateError(ValueError):
    """The deployed template cannot be represented as a safe one-off run."""


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TemplateError(f"Container {field} must be a non-empty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise TemplateError(f"Container {field} contains control characters")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TemplateError(f"Container {field} must be a string list")
    return list(value)


def _environment(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise TemplateError("Container env must be a list")
    rendered: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise TemplateError("Container env entries must be objects")
        item = {"name": _nonempty_string(entry.get("name"), "env name")}
        has_value = isinstance(entry.get("value"), str)
        has_secret = isinstance(entry.get("secretRef"), str) and bool(entry["secretRef"].strip())
        if has_value == has_secret:
            raise TemplateError("Each container env entry must use exactly one value source")
        if has_value:
            item["value"] = entry["value"]
        else:
            item["secretRef"] = entry["secretRef"]
        rendered.append(item)
    return rendered


def _container(value: Any, *, image: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TemplateError("Container entries must be objects")
    if value.get("volumeMounts") not in (None, []):
        raise TemplateError("Candidate verification does not support mounted volumes")
    rendered: dict[str, Any] = {
        "name": _nonempty_string(value.get("name"), "name"),
        "image": _nonempty_string(image if image is not None else value.get("image"), "image"),
    }
    for field in ("command", "args"):
        if field in value and value[field] is not None:
            rendered[field] = _string_list(value[field], field)
    if "env" in value and value["env"] is not None:
        rendered["env"] = _environment(value["env"])
    resources = value.get("resources")
    if not isinstance(resources, dict):
        raise TemplateError("Container resources must be an object")
    cpu = resources.get("cpu")
    memory = resources.get("memory")
    if not isinstance(cpu, (int, float, str)) or not isinstance(memory, str) or not memory.strip():
        raise TemplateError("Container cpu and memory resources are required")
    rendered["resources"] = {"cpu": cpu, "memory": memory}
    return rendered


def render_execution_template(template: Any, candidate_image: str) -> dict[str, Any]:
    _nonempty_string(candidate_image, "image")
    if not isinstance(template, dict):
        raise TemplateError("Job template must be an object")
    if template.get("volumes") not in (None, []):
        raise TemplateError("Candidate verification does not support job volumes")
    containers = template.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise TemplateError("Candidate verification requires exactly one container")
    result: dict[str, Any] = {
        "containers": [_container(containers[0], image=candidate_image)],
    }
    init_containers = template.get("initContainers")
    if init_containers not in (None, []):
        if not isinstance(init_containers, list):
            raise TemplateError("Job initContainers must be a list")
        result["initContainers"] = [_container(item) for item in init_containers]
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: render_job_execution_template.py IMAGE", file=sys.stderr)
        return 2
    raw = sys.stdin.read(MAX_TEMPLATE_BYTES + 1)
    if len(raw) > MAX_TEMPLATE_BYTES:
        print("Job template exceeded the size limit", file=sys.stderr)
        return 2
    try:
        source = json.loads(raw)
        rendered = render_execution_template(source, sys.argv[1])
    except (json.JSONDecodeError, TemplateError) as exc:
        print(f"Invalid job template: {exc}", file=sys.stderr)
        return 2
    json.dump(rendered, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
