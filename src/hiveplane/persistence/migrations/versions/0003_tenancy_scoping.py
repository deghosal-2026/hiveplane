"""tenancy scoping

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import context, op
from sqlalchemy import (
    Column,
    Connection,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    inspect,
)

from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TENANT_TABLES = ("tenants", "teams", "memberships")
_LEGACY_TABLES = (
    "workloads",
    "workload_versions",
    "runs",
    "run_events",
    "usage_events",
    "run_admissions",
    "audit_log",
    "approvals",
    "certifications",
    "attestations",
    "tools",
    "trigger_rules",
    "drift_schedules",
    "fan_out_deliveries",
    "health_signals",
    "cost_attributions",
    "budget_run_spend",
    "budget_day_spend",
    "budget_team_spend",
)
_ATTRIBUTED_TABLES = (
    "runs",
    "usage_events",
    "cost_attributions",
    "budget_run_spend",
    "budget_day_spend",
    "budget_team_spend",
)
_TENANT_COLUMN = "tenant_id"
_ATTRIBUTED_COLUMNS = ("team_id", "attribution_key")
_WORKLOAD_REF_TABLES = (
    "certifications",
    "attestations",
    "trigger_rules",
    "drift_schedules",
    "health_signals",
    "cost_attributions",
)
_LEGACY_UNIQUES: dict[str, dict[str, tuple[str, ...]]] = {
    "workload_versions": {"uq_workload_versions": ("workload", "version")},
}
_TENANT_INDEX_COLUMNS = frozenset({"tenant_id", "team_id"})


def _table_names(bind: Connection) -> set[str]:
    return set(inspect(bind).get_table_names())


def _column_names(bind: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(bind).get_columns(table)}


def _index_names(bind: Connection, table: str) -> set[str]:
    return {str(index["name"]) for index in inspect(bind).get_indexes(table)}


def _unique_signatures(bind: Connection, table: str) -> set[tuple[str, tuple[str, ...]]]:
    return {
        (str(constraint["name"]), tuple(constraint["column_names"]))
        for constraint in inspect(bind).get_unique_constraints(table)
    }


def _foreign_key_signatures(
    bind: Connection, table: str
) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        (
            tuple(constraint["constrained_columns"]),
            constraint["referred_table"],
            tuple(constraint["referred_columns"]),
        )
        for constraint in inspect(bind).get_foreign_keys(table)
    }


def _foreign_key_name(table: str, columns: Iterable[str]) -> str:
    return f"{table}_{'_'.join(columns)}_fkey"


def _metadata_primary_key(table: str) -> list[str]:
    return [column.name for column in Base.metadata.tables[table].primary_key.columns]


def _create_tenancy_tables(bind: Connection | None) -> None:
    for name in _TENANT_TABLES:
        if bind is None or name not in _table_names(bind):
            Base.metadata.tables[name].create(op.get_bind(), checkfirst=True)


def _drop_tenancy_tables(bind: Connection | None) -> None:
    for name in reversed(_TENANT_TABLES):
        if bind is None or name in _table_names(bind):
            Base.metadata.tables[name].drop(op.get_bind(), checkfirst=True)


def _seed_tenancy() -> None:
    op.execute(
        "INSERT INTO tenants (tenant_id, name, created_at, payload) VALUES "
        "('default', 'Default Tenant', now(), '{}'::jsonb), "
        "('system', 'System Tenant', now(), '{}'::jsonb) "
        "ON CONFLICT (tenant_id) DO NOTHING"
    )
    op.execute(
        "INSERT INTO teams "
        "(tenant_id, team_id, name, attribution_key, created_at, payload) VALUES "
        "('default', 'default', 'Default Team', 'default', now(), '{}'::jsonb) "
        "ON CONFLICT (tenant_id, team_id) DO NOTHING"
    )


def _delete_orphan_runs() -> None:
    op.execute("DELETE FROM fan_out_deliveries WHERE run_id NOT IN (SELECT id FROM runs)")
    op.execute(
        "DELETE FROM fan_out_deliveries WHERE run_id IN "
        "(SELECT id FROM runs WHERE workload_id NOT IN (SELECT name FROM workloads))"
    )
    op.execute("DELETE FROM runs WHERE workload_id NOT IN (SELECT name FROM workloads)")


def _delete_orphan_workload_refs(bind: Connection | None) -> None:
    if bind is None:
        return
    for table in _WORKLOAD_REF_TABLES:
        op.execute(f"DELETE FROM {table} WHERE workload NOT IN (SELECT name FROM workloads)")


def _add_tenant_columns(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        existing = set() if bind is None else _column_names(bind, table)
        if _TENANT_COLUMN not in existing:
            op.add_column(
                table,
                Column(
                    _TENANT_COLUMN,
                    String(64),
                    nullable=False,
                    server_default="default",
                ),
            )
        if table not in _ATTRIBUTED_TABLES:
            continue
        for column, length in (("team_id", 64), ("attribution_key", 253)):
            if column not in existing:
                op.add_column(table, Column(column, String(length), nullable=True))


def _drop_tenant_columns(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        existing = set() if bind is None else _column_names(bind, table)
        columns = (
            (*_ATTRIBUTED_COLUMNS, _TENANT_COLUMN)
            if table in _ATTRIBUTED_TABLES
            else (_TENANT_COLUMN,)
        )
        for column in columns:
            if bind is None or column in existing:
                op.drop_column(table, column)


def _upgrade_primary_keys(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        target = _metadata_primary_key(table)
        if _TENANT_COLUMN not in target:
            continue
        if bind is not None:
            current = inspect(bind).get_pk_constraint(table)
            if list(current["constrained_columns"]) == target:
                continue
            name = str(current["name"] or f"{table}_pkey")
            op.drop_constraint(name, table, type_="primary")
        else:
            op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, target)


def _downgrade_primary_keys(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        target = _metadata_primary_key(table)
        if _TENANT_COLUMN not in target:
            continue
        legacy = [column for column in target if column != _TENANT_COLUMN]
        if bind is not None:
            current = inspect(bind).get_pk_constraint(table)
            if list(current["constrained_columns"]) == legacy:
                continue
            name = str(current["name"] or f"{table}_pkey")
            op.drop_constraint(name, table, type_="primary")
        else:
            op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, legacy)


def _upgrade_unique_constraints(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        for constraint in Base.metadata.tables[table].constraints:
            if not isinstance(constraint, UniqueConstraint) or constraint.name is None:
                continue
            name = str(constraint.name)
            columns = [column.name for column in constraint.columns]
            if bind is not None:
                existing = _unique_signatures(bind, table)
                if (name, tuple(columns)) in existing:
                    continue
                if name in {signature[0] for signature in existing}:
                    op.drop_constraint(name, table, type_="unique")
            else:
                if _TENANT_COLUMN not in columns:
                    continue
                if name in _LEGACY_UNIQUES.get(table, {}):
                    op.drop_constraint(name, table, type_="unique")
            op.create_unique_constraint(name, table, columns)


def _downgrade_unique_constraints(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        existing = (
            None
            if bind is None
            else {name for name, _ in _unique_signatures(bind, table)}
        )
        for constraint in Base.metadata.tables[table].constraints:
            if not isinstance(constraint, UniqueConstraint) or constraint.name is None:
                continue
            name = str(constraint.name)
            if _TENANT_COLUMN not in {column.name for column in constraint.columns}:
                continue
            if existing is not None and name not in existing:
                continue
            op.drop_constraint(name, table, type_="unique")
    for table, constraints in _LEGACY_UNIQUES.items():
        existing_legacy = set() if bind is None else _unique_signatures(bind, table)
        for name, columns in constraints.items():
            if bind is not None and (name, columns) in existing_legacy:
                continue
            op.create_unique_constraint(name, table, list(columns))


def _upgrade_indexes(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        existing = set() if bind is None else _index_names(bind, table)
        for index in Base.metadata.tables[table].indexes:
            if index.name is None or index.name in existing:
                continue
            columns = [column.name for column in index.columns]
            if bind is None and not _TENANT_INDEX_COLUMNS.intersection(columns):
                continue
            op.create_index(index.name, table, columns, unique=index.unique)


def _downgrade_indexes(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        existing = None if bind is None else _index_names(bind, table)
        for index in Base.metadata.tables[table].indexes:
            if index.name is None or (existing is not None and index.name not in existing):
                continue
            if not _TENANT_INDEX_COLUMNS.intersection(column.name for column in index.columns):
                continue
            op.drop_index(index.name, table_name=table)


def _upgrade_foreign_keys(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        for constraint in Base.metadata.tables[table].constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            local = [column.name for column in constraint.columns]
            if bind is not None and (
                tuple(local),
                constraint.elements[0].column.table.name,
                tuple(element.column.name for element in constraint.elements),
            ) in _foreign_key_signatures(bind, table):
                continue
            if bind is None and _TENANT_COLUMN not in local:
                continue
            op.create_foreign_key(
                _foreign_key_name(table, local),
                table,
                constraint.elements[0].column.table.name,
                local,
                [element.column.name for element in constraint.elements],
                ondelete=constraint.ondelete,
            )


def _downgrade_foreign_keys(bind: Connection | None) -> None:
    for table in _LEGACY_TABLES:
        existing = (
            None
            if bind is None
            else {constraint["name"] for constraint in inspect(bind).get_foreign_keys(table)}
        )
        for constraint in Base.metadata.tables[table].constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            local = [column.name for column in constraint.columns]
            if _TENANT_COLUMN not in local:
                continue
            name = _foreign_key_name(table, local)
            if existing is not None and name not in existing:
                continue
            op.drop_constraint(name, table, type_="foreignkey")


def upgrade() -> None:
    """Scope the system of record to tenants with a default backfill (#149)."""
    bind = None if context.is_offline_mode() else op.get_bind()
    _create_tenancy_tables(bind)
    _seed_tenancy()
    _delete_orphan_runs()
    _add_tenant_columns(bind)
    _upgrade_primary_keys(bind)
    _upgrade_unique_constraints(bind)
    _upgrade_indexes(bind)
    _delete_orphan_workload_refs(bind)
    _upgrade_foreign_keys(bind)


def downgrade() -> None:
    """Drop tenant scoping and the tenancy tables (orphan deletions are permanent)."""
    bind = None if context.is_offline_mode() else op.get_bind()
    _downgrade_foreign_keys(bind)
    _downgrade_unique_constraints(bind)
    _downgrade_primary_keys(bind)
    _downgrade_indexes(bind)
    _drop_tenant_columns(bind)
    _drop_tenancy_tables(bind)
