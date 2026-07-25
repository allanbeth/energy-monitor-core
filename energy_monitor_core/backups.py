from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .logging import get_logger


logger = get_logger(__name__)


class BackupService:
    def __init__(self, config_manager: Any):
        self.config_manager = config_manager

    def backup_core(self) -> Dict[str, Any]:
        return self.config_manager.create_core_config_backup()

    def backup_module(self, module_name: str) -> Dict[str, Any]:
        return self.config_manager.create_module_backup(module_name)

    def backup_all(self) -> Dict[str, Any]:
        created = [self.backup_core()]
        for module_name in self.config_manager.get_module_names():
            created.append(self.backup_module(module_name))
        return {"ok": True, "created": created}

    def list_backups(self, module_name: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.config_manager.list_backups(module_name)

    def restore_core(self, backup_name: str) -> bool:
        return self.config_manager.restore_core_backup(backup_name)

    def restore_module(self, module_name: str, backup_name: str) -> bool:
        return self.config_manager.restore_module_backup(module_name, backup_name)
