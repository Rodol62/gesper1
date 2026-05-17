"""
System check: evita l’errore criptico «no such column» quando il modello è avanti rispetto al DB.

All’avvio (runserver, check) compare un avviso esplicito con il comando da eseguire.
Usa Warning (non Error) così `manage.py migrate` può comunque applicare le migration.
"""

from __future__ import annotations

from django.core.checks import Warning, register, Tags
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError, OperationalError


@register(Tags.database, deploy=True)
def check_pending_migrations(app_configs, **kwargs):
    aliases = kwargs.get("databases")
    if aliases is None:
        aliases = list(connections)

    warnings = []
    for alias in aliases:
        conn = connections[alias]
        try:
            conn.ensure_connection()
        except (DatabaseError, OperationalError):
            continue
        try:
            executor = MigrationExecutor(conn)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        except (DatabaseError, OperationalError, Exception):
            continue
        if not plan:
            continue
        forward = [m for m, backwards in plan if not backwards]
        n_fwd = len(forward)
        if n_fwd == 0:
            continue
        limit = 14
        sample = [f"{m.app_label}.{m.name}" for m in forward[:limit]]
        tail = " …" if n_fwd > len(sample) else ""
        warnings.append(
            Warning(
                f'Il database «{alias}» non è allineato al codice: ci sono migration Django '
                f"non applicate ({n_fwd} passaggi in coda).",
                hint=(
                    "Esegui dalla cartella del progetto (con il venv attivo):\n"
                    "  python manage.py migrate\n"
                    "Altrimenti le query ORM falliscono con OperationalError / «no such column».\n"
                    f"Esempi in coda: {', '.join(sample)}{tail}"
                ),
                id="documenti.W001",
            )
        )
    return warnings
