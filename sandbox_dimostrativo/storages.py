"""
Storage filesystem consapevole della sessione dimostrativa.

In sessione sandbox le scritture vanno solo sotto ``GESPER_SANDBOX_MEDIA_ROOT``;
lettura ed ``exists`` cercano prima la sandbox, poi ``MEDIA_ROOT`` operativo
(catalogo file condiviso in sola lettura, senza cancellazione dall'operativo).
"""

from __future__ import annotations

import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage

from sandbox_dimostrativo.state import is_sandbox_routing


class SandboxAwareFileSystemStorage(FileSystemStorage):
    """Delega a due radici distinte in base a ``is_sandbox_routing()``."""

    def _op_storage(self) -> FileSystemStorage:
        return FileSystemStorage(
            location=os.path.abspath(str(settings.MEDIA_ROOT)),
            file_permissions_mode=self.file_permissions_mode,
            directory_permissions_mode=self.directory_permissions_mode,
        )

    def _sb_storage(self) -> FileSystemStorage | None:
        root = getattr(settings, "GESPER_SANDBOX_MEDIA_ROOT", None)
        if not root:
            return None
        return FileSystemStorage(
            location=os.path.abspath(str(root)),
            file_permissions_mode=self.file_permissions_mode,
            directory_permissions_mode=self.directory_permissions_mode,
        )

    def _save(self, name, content):
        sb = self._sb_storage()
        if is_sandbox_routing() and sb is not None:
            return sb._save(name, content)
        return super()._save(name, content)

    def delete(self, name):
        if not name:
            raise ValueError("The name must be given to delete().")
        sb = self._sb_storage()
        if is_sandbox_routing() and sb is not None:
            sb.delete(name)
            return
        super().delete(name)

    def exists(self, name):
        if is_sandbox_routing() and self._sb_storage() is not None:
            sb = self._sb_storage()
            assert sb is not None
            if sb.exists(name):
                return True
            return self._op_storage().exists(name)
        return super().exists(name)

    def path(self, name):
        """Percorso assoluto: in demo preferisce file copiati in sandbox, altrimenti operativo."""
        if is_sandbox_routing() and self._sb_storage() is not None:
            sb = self._sb_storage()
            assert sb is not None
            if sb.exists(name):
                return sb.path(name)
            return self._op_storage().path(name)
        return super().path(name)

    def listdir(self, path):
        if not (is_sandbox_routing() and self._sb_storage() is not None):
            return super().listdir(path)
        sb, op = self._sb_storage(), self._op_storage()
        assert sb is not None
        merged_dirs: dict[str, None] = {}
        merged_files: dict[str, None] = {}
        for store in (sb, op):
            try:
                if not store.exists(path):
                    continue
            except OSError:
                continue
            try:
                d, f = store.listdir(path)
            except (FileNotFoundError, NotADirectoryError, OSError):
                continue
            for x in d:
                merged_dirs[x] = None
            for x in f:
                merged_files[x] = None
        return list(merged_dirs.keys()), list(merged_files.keys())
