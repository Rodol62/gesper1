"""Patch application utilities.

This module provides safe patch application for refactoring plans, including
validation of patch schema, backup management, rollback support, and detailed
logging of applied changes.
"""

import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

DRY_RUN = True  # non sovrascrive i file reali


class PatchFormatError(Exception):
    """Raised when a patch plan is invalid."""


class PatchApplicationError(Exception):
    """Raised when applying a patch to a file fails."""


class PatchValidator:
    """Validates the JSON patch plan structure."""

    @staticmethod
    def validate(plan_json: str) -> List[Dict[str, Any]]:
        try:
            plan = json.loads(plan_json)
        except json.JSONDecodeError as exc:
            logger.error("Patch plan JSON is invalid: %s", exc)
            raise PatchFormatError("Invalid JSON in patch plan.") from exc

        if not isinstance(plan, list):
            raise PatchFormatError("Patch plan must be a list of patch items.")

        for index, item in enumerate(plan):
            if not isinstance(item, dict):
                raise PatchFormatError(f"Patch item {index} must be an object.")
            if "path" not in item or "new_content" not in item:
                raise PatchFormatError(f"Patch item {index} must include 'path' and 'new_content'.")
            if not isinstance(item["path"], str) or not isinstance(item["new_content"], str):
                raise PatchFormatError(f"Patch item {index} fields must be strings.")

        return plan


class PathSanitizer:
    """Sanitizes and validates filesystem paths used in patch application."""

    @staticmethod
    def sanitize(path: str) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            logger.error("Absolute paths are not allowed: %s", path)
            raise PatchFormatError("Absolute paths are not allowed in patch plan.")
        normalized = candidate.resolve()
        if ".." in candidate.parts:
            logger.error("Path traversal detected: %s", path)
            raise PatchFormatError("Path traversal is not allowed in patch plan.")
        return normalized


class BackupManager:
    """Handles backup and restore operations for files before patching."""

    def __init__(self, backup_dir: Optional[str] = None):
        self.backup_dir = Path(backup_dir) if backup_dir else Path(tempfile.gettempdir()) / "patcher_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.backup_map: Dict[Path, Path] = {}

    def create_backup(self, target_path: Path) -> Path:
        if not target_path.exists():
            return target_path
        if target_path in self.backup_map:
            return self.backup_map[target_path]

        backup_path = self.backup_dir / f"{target_path.name}.{int(target_path.stat().st_mtime)}.bak"
        shutil.copy2(target_path, backup_path)
        self.backup_map[target_path] = backup_path
        logger.info("Backup created for %s -> %s", target_path, backup_path)
        return backup_path

    def restore_all(self) -> None:
        for target_path, backup_path in self.backup_map.items():
            try:
                shutil.copy2(backup_path, target_path)
                logger.warning("Restored %s from backup %s", target_path, backup_path)
            except Exception as exc:
                logger.error("Failed to restore backup %s to %s: %s", backup_path, target_path, exc)

    def cleanup(self) -> None:
        for backup_path in self.backup_map.values():
            try:
                backup_path.unlink()
                logger.debug("Removed backup %s", backup_path)
            except Exception as exc:
                logger.warning("Unable to remove backup %s: %s", backup_path, exc)
        self.backup_map.clear()


class PatchLoader:
    """Loads and writes file content with robust error handling."""

    @staticmethod
    def write(path: Path, content: str, dry_run: bool = False) -> None:
        target_path = Path(str(path) + ".refactored") if dry_run else path
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as file_handle:
                file_handle.write(content)
            if dry_run:
                logger.info("Dry-run write created %s", target_path)
        except PermissionError as exc:
            logger.error("Permission denied writing file %s: %s", target_path, exc)
            raise PatchApplicationError(f"Permission denied writing file {target_path}") from exc
        except OSError as exc:
            logger.error("Failed to write file %s: %s", target_path, exc)
            raise PatchApplicationError(f"Unable to write file {target_path}") from exc


class PatchApplier:
    """Applies patch items to files, with rollback support."""

    def __init__(self, backup_manager: BackupManager):
        self.backup_manager = backup_manager
        self.modified_files: List[Path] = []

    def apply(self, plan: List[Dict[str, Any]], dry_run: bool = False) -> None:
        for index, item in enumerate(plan):
            path = item["path"]
            new_content = item["new_content"]
            target_path = self._sanitize_path(path)

            if not dry_run:
                self.backup_manager.create_backup(target_path)
            else:
                logger.info("Dry run enabled, no backup for %s", target_path)

            try:
                PatchLoader.write(target_path, new_content, dry_run=dry_run)
                self.modified_files.append(target_path)
                logger.info("Patch applied to %s (item %s)", target_path, index)
            except PatchApplicationError:
                logger.error("Patch failed for %s, performing rollback.", target_path)
                self.backup_manager.restore_all()
                raise
            except Exception as exc:
                logger.error("Unexpected error applying patch %s: %s", target_path, exc)
                self.backup_manager.restore_all()
                raise PatchApplicationError(f"Failed to apply patch to {target_path}") from exc

    @staticmethod
    def _sanitize_path(path: str) -> Path:
        return PathSanitizer.sanitize(path)


def apply_patches(plan_json: str) -> None:
    """Apply a sequence of patches described by a JSON patch plan.

    Args:
        plan_json: JSON-serialized patch plan.

    Raises:
        PatchFormatError: If the patch plan is invalid.
        PatchApplicationError: If applying any patch fails.
    """
    start_time = time.time()
    plan = PatchValidator.validate(plan_json)
    backup_manager = BackupManager()
    applier = PatchApplier(backup_manager)

    try:
        applier.apply(plan, dry_run=DRY_RUN)
        elapsed = time.time() - start_time
        logger.info("Applied %s patch(es) in %.3f seconds.", len(plan), elapsed)
    except Exception:
        logger.error("Patch application failed after %s seconds.", time.time() - start_time)
        raise
    finally:
        backup_manager.cleanup()
