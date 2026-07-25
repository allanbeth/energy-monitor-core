from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .logging import get_logger


logger = get_logger(__name__)


CORE_DEPENDENCIES = [
    {"pip": "Flask", "import": "flask", "optional": False},
    {"pip": "paho-mqtt", "import": "paho.mqtt.client", "optional": False},
]


def _import_dependency(import_name: str) -> None:
    importlib.import_module(import_name)


def _is_missing_dependency(import_name: str, error: Exception) -> bool:
    if not isinstance(error, ModuleNotFoundError):
        return False
    missing_name = (error.name or "").strip()
    if not missing_name:
        return False
    return missing_name.split(".")[0] == import_name.split(".")[0]


def _install_missing(packages: Iterable[str]) -> Tuple[bool, str]:
    package_list = [package for package in packages if package]
    if not package_list:
        return True, ""

    command = [sys.executable, "-m", "pip", "install", *sorted(set(package_list))]
    result = subprocess.run(command, capture_output=True, text=True)
    output = "\n".join(filter(None, [result.stdout.strip(), result.stderr.strip()]))
    return result.returncode == 0, output


def _check_dependency_group(requirements: List[Dict[str, Any]]) -> Dict[str, Any]:
    missing: List[Dict[str, Any]] = []
    installable: List[str] = []
    steps: List[str] = []

    for requirement in requirements:
        pip_name = requirement.get("pip")
        import_name = requirement.get("import")
        optional = bool(requirement.get("optional", False))
        if not pip_name or not import_name:
            continue

        try:
            _import_dependency(import_name)
            steps.append(f"✓ {pip_name} available")
        except Exception as error:
            missing.append({"pip": pip_name, "import": import_name, "optional": optional})
            if _is_missing_dependency(import_name, error):
                installable.append(pip_name)
            steps.append(f"✗ {pip_name} unavailable: {error}")

    install_ok, output = _install_missing(installable)
    if output:
        steps.append(output)

    return {
        "ok": not missing or all(item.get("optional") for item in missing),
        "install_ok": install_ok,
        "missing": missing,
        "steps": steps,
    }


def ensure_core_dependencies() -> Dict[str, Any]:
    result = _check_dependency_group(CORE_DEPENDENCIES)
    logger.info("Core dependency check: %s", result)
    return result


def _load_dependency_manifest(manifest_path: Path) -> Dict[str, Any]:
    if not manifest_path.exists():
        raise FileNotFoundError(f"Dependency manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid dependency manifest: {manifest_path}")
    return payload


def ensure_module_dependencies(config_manager: Any, module_names: Iterable[str]) -> Dict[str, Any]:
    overall_ok = True
    install_ok = True
    checked: Dict[str, Any] = {}

    for module_name in module_names:
        paths = config_manager.get_module_paths(module_name)
        manifest = _load_dependency_manifest(paths["dependencies"])
        python_requirements = manifest.get("python_dependencies", [])
        system_requirements = manifest.get("system_dependencies", [])

        python_result = _check_dependency_group(python_requirements if isinstance(python_requirements, list) else [])
        system_missing = []
        system_steps: List[str] = []
        for requirement in system_requirements if isinstance(system_requirements, list) else []:
            apt_name = requirement.get("apt")
            command_name = requirement.get("command")
            optional = bool(requirement.get("optional", False))
            if not apt_name:
                continue
            if command_name and shutil.which(str(command_name)):
                system_steps.append(f"✓ {apt_name} available via {command_name}")
            else:
                system_missing.append({"apt": apt_name, "command": command_name, "optional": optional})
                system_steps.append(f"✗ {apt_name} missing")

        checked[module_name] = {
            "python": python_result,
            "system": {"missing": system_missing, "steps": system_steps},
            "manifest_path": str(paths["dependencies"]),
        }
        overall_ok = overall_ok and bool(python_result.get("ok", False)) and (not system_missing or all(item.get("optional") for item in system_missing))
        install_ok = install_ok and bool(python_result.get("install_ok", False))

    result = {"ok": overall_ok, "install_ok": install_ok, "modules": checked}
    logger.info("Module dependency check: %s", result)
    return result
