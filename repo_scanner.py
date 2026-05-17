"""Repository scanner utilities.

This module provides a repository scanner for Python projects with support for
controlled directory exclusion, file caching, error-tolerant loading, and
logging of scan metrics.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from time import perf_counter
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

DEFAULT_ALLOWED_EXTENSIONS: Set[str] = {".py"}
DEFAULT_EXCLUDE_DIRS: Set[str] = {"__pycache__", ".git", ".venv", "node_modules"}


class ScanCacheError(Exception):
    """Exception raised when the scan cache cannot be loaded or saved."""


class FileScanError(Exception):
    """Exception raised when a file cannot be loaded during scanning."""


class ScanCache:
    """Persisted cache that tracks file metadata for incremental scanning."""

    def __init__(self, cache_path: Optional[Path] = None):
        self.cache_path = cache_path
        self.entries: Dict[str, Dict[str, float]] = {}
        if self.cache_path is not None:
            self._load_cache()

    def _load_cache(self) -> None:
        if not self.cache_path.exists():
            return

        try:
            with self.cache_path.open("r", encoding="utf-8") as cache_file:
                data = json.load(cache_file)
            if isinstance(data, dict):
                self.entries = data
            else:
                logger.warning("Cache file has invalid format and will be ignored: %s", self.cache_path)
        except Exception as exc:
            logger.warning("Unable to load scan cache %s: %s", self.cache_path, exc)
            self.entries = {}

    def save(self) -> None:
        if self.cache_path is None:
            return

        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("w", encoding="utf-8") as cache_file:
                json.dump(self.entries, cache_file, indent=2)
            logger.info("Scan cache saved to %s", self.cache_path)
        except Exception as exc:
            logger.warning("Unable to save scan cache %s: %s", self.cache_path, exc)

    def is_fresh(self, file_path: Path, stat_result: os.stat_result) -> bool:
        entry = self.entries.get(str(file_path))
        if not entry:
            return False
        return (
            entry.get("mtime") == stat_result.st_mtime
            and entry.get("size") == stat_result.st_size
        )

    def update(self, file_path: Path, stat_result: os.stat_result, digest: str) -> None:
        self.entries[str(file_path)] = {
            "mtime": stat_result.st_mtime,
            "size": stat_result.st_size,
            "sha256": digest,
        }


class DirectoryFilter:
    """Utility for directory exclusion during repository traversal."""

    def __init__(self, exclude_dirs: Optional[Sequence[str]] = None):
        self.exclude_dirs = DEFAULT_EXCLUDE_DIRS.copy()
        if exclude_dirs:
            self.exclude_dirs.update(exclude_dirs)

    def should_exclude(self, directory_name: str) -> bool:
        return directory_name in self.exclude_dirs

    def filter_dirs(self, directories: List[str]) -> List[str]:
        return [directory for directory in directories if not self.should_exclude(directory)]


class FileLoader:
    """Utility for loading file content with robust error handling."""

    @staticmethod
    def load(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                logger.warning("UTF-8 decode failed, retrying with latin-1 for %s", path)
                return path.read_text(encoding="latin-1")
            except Exception as exc:
                raise FileScanError(f"Cannot decode file {path}: {exc}") from exc
        except FileNotFoundError as exc:
            raise FileScanError(f"File not found: {path}") from exc
        except PermissionError as exc:
            raise FileScanError(f"Permission denied reading file: {path}") from exc
        except OSError as exc:
            raise FileScanError(f"Unable to read file {path}: {exc}") from exc

    @staticmethod
    def digest(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


class RepositoryScanner:
    """Scanner that traverses a repository and returns Python file contents."""

    def __init__(
        self,
        root: str,
        allowed_extensions: Optional[Sequence[str]] = None,
        exclude_dirs: Optional[Sequence[str]] = None,
        cache_path: Optional[str] = None,
    ):
        self.root = Path(root).resolve()
        self.allowed_extensions = {
            extension.lower() for extension in (allowed_extensions or DEFAULT_ALLOWED_EXTENSIONS)
        }
        self.filter = DirectoryFilter(exclude_dirs)
        self.cache = ScanCache(Path(cache_path).resolve() if cache_path else None)
        self.stats: Dict[str, int] = {
            "total_files": 0,
            "matched_files": 0,
            "skipped_cached": 0,
            "errors": 0,
        }
        self._validate_root()

    def _validate_root(self) -> None:
        if not self.root.exists() or not self.root.is_dir():
            raise ValueError(f"Repository root does not exist or is not a directory: {self.root}")

    def scan(self) -> List[Dict[str, str]]:
        start_time = perf_counter()
        files = []

        for file_path in self._iter_source_files():
            self.stats["total_files"] += 1
            try:
                content = self._load_if_changed(file_path)
                if content is None:
                    self.stats["skipped_cached"] += 1
                    continue
                files.append({"path": str(file_path), "content": content})
                self.stats["matched_files"] += 1
            except FileScanError as exc:
                logger.warning("Skipping file %s: %s", file_path, exc)
                self.stats["errors"] += 1

        duration = perf_counter() - start_time
        logger.info(
            "Repository scan completed: %s files found, %s processed, %s skipped by cache, %s errors in %.3f seconds.",
            self.stats["total_files"],
            self.stats["matched_files"],
            self.stats["skipped_cached"],
            self.stats["errors"],
            duration,
        )

        self.cache.save()
        return files

    def _iter_source_files(self) -> Iterator[Path]:
        for base, dirnames, filenames in os.walk(self.root):
            dirnames[:] = self.filter.filter_dirs(dirnames)
            for filename in filenames:
                file_path = Path(base) / filename
                if file_path.suffix.lower() in self.allowed_extensions:
                    yield file_path

    def _load_if_changed(self, file_path: Path) -> Optional[str]:
        stat_result = file_path.stat()
        if self.cache.is_fresh(file_path, stat_result):
            logger.debug("File unchanged, skipping: %s", file_path)
            return None

        content = FileLoader.load(file_path)
        digest = FileLoader.digest(content)
        self.cache.update(file_path, stat_result, digest)
        return content


def scan_repository(
    root: str,
    exclude_dirs: Optional[Sequence[str]] = None,
    cache_path: Optional[str] = None,
    allowed_extensions: Optional[Sequence[str]] = None,
) -> List[Dict[str, str]]:
    """Scan a repository for Python files and return path/content mappings.

    Args:
        root: Root directory of the repository to scan.
        exclude_dirs: Additional directory names to exclude from traversal.
        cache_path: Optional JSON cache file path for incremental scanning.
        allowed_extensions: Optional list of file extensions to include.

    Returns:
        A list of dictionaries with keys 'path' and 'content'.
    """
    scanner = RepositoryScanner(
        root,
        allowed_extensions=allowed_extensions,
        exclude_dirs=exclude_dirs,
        cache_path=cache_path,
    )
    return scanner.scan()
