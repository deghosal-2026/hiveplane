"""PostgreSQL-backed RunStore (M18, DD-05)."""

from __future__ import annotations

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from hiveplane.core.event import RunEvent
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.models import AdmissionResult, DeliveryRecord
from hiveplane.persistence.base import session_factory
from hiveplane.persistence.models import (
    FanOutDeliveryRow,
    RunAdmissionRow,
    RunEventRow,
    RunRow,
    UsageEventRow,
)


class PostgresRunStore:
    """A durable run store backed by PostgreSQL."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_run(self, run: Run) -> None:
        """Insert or update a run."""
        with self._session.begin() as session:
            row = session.get(RunRow, run.id)
            if row is None:
                session.add(
                    RunRow(
                        id=run.id,
                        workload_id=run.workload_id,
                        caller=run.caller,
                        state=run.state.value,
                        context=run.context.value if run.context else None,
                        model_identity=run.model_identity,
                        sandbox=run.sandbox,
                        sandbox_id=run.sandbox_id,
                        manifest_version=run.manifest_version,
                        cost_usd=run.cost_usd,
                        created_at=run.created_at,
                        updated_at=run.updated_at,
                        payload=run.model_dump(mode="json"),
                    )
                )
            else:
                row.workload_id = run.workload_id
                row.state = run.state.value
                row.context = run.context.value if run.context else None
                row.model_identity = run.model_identity
                row.sandbox = run.sandbox
                row.sandbox_id = run.sandbox_id
                row.manifest_version = run.manifest_version
                row.cost_usd = run.cost_usd
                row.updated_at = run.updated_at
                row.payload = run.model_dump(mode="json")

    def get_run(self, run_id: str) -> Run | None:
        """Return a run by id, or None."""
        with self._session() as session:
            row = session.get(RunRow, run_id)
            return Run.model_validate(row.payload) if row is not None else None

    def list_runs(
        self, *, workload: str | None = None, state: RunState | None = None
    ) -> list[Run]:
        """List runs filtered by workload and/or state, oldest first."""
        statement = select(RunRow).order_by(RunRow.created_at)
        if workload is not None:
            statement = statement.where(RunRow.workload_id == workload)
        if state is not None:
            statement = statement.where(RunRow.state == state.value)
        with self._session() as session:
            return [Run.model_validate(row.payload) for row in session.scalars(statement)]

    def add_event(self, event: RunEvent) -> None:
        """Append one run event with an atomically-assigned sequence.

        The sequence is computed inside the write transaction (not from a
        separate read) so concurrent appends for the same run cannot collide on
        the composite primary key.
        """
        with self._session.begin() as session:
            self._require(session, event.run_id)
            max_seq = session.scalar(
                select(func.max(RunEventRow.sequence)).where(
                    RunEventRow.run_id == event.run_id
                )
            )
            sequence = 0 if max_seq is None else max_seq + 1
            session.add(
                RunEventRow(
                    run_id=event.run_id,
                    sequence=sequence,
                    type=event.type.value,
                    actor=event.actor,
                    timestamp=event.timestamp,
                    payload=event.model_dump(mode="json"),
                )
            )

    def list_events(self, run_id: str) -> list[RunEvent]:
        """Return events ordered by sequence."""
        with self._session() as session:
            self._require(session, run_id)
            rows = session.scalars(
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id)
                .order_by(RunEventRow.sequence)
            )
            return [RunEvent.model_validate(row.payload) for row in rows]

    def add_usage(self, report: UsageReport) -> None:
        """Append a usage report."""
        with self._session.begin() as session:
            self._require(session, report.run_id)
            session.add(
                UsageEventRow(
                    run_id=report.run_id,
                    model_identity=report.model_identity,
                    cost_usd=report.cost_usd,
                    timestamp=report.timestamp,
                    payload=report.model_dump(mode="json"),
                )
            )

    def list_usage(self, run_id: str) -> list[UsageReport]:
        """Return usage reports for a run."""
        with self._session() as session:
            self._require(session, run_id)
            rows = session.scalars(
                select(UsageEventRow)
                .where(UsageEventRow.run_id == run_id)
                .order_by(UsageEventRow.id)
            )
            return [UsageReport.model_validate(row.payload) for row in rows]

    def save_admission(self, result: AdmissionResult) -> None:
        """Insert or replace a run's admission decision."""
        with self._session.begin() as session:
            row = session.get(RunAdmissionRow, result.run_id)
            if row is None:
                session.add(
                    RunAdmissionRow(
                        run_id=result.run_id,
                        outcome=result.outcome.value,
                        context=result.context.value,
                        payload=result.model_dump(mode="json"),
                    )
                )
            else:
                row.outcome = result.outcome.value
                row.context = result.context.value
                row.payload = result.model_dump(mode="json")

    def get_admission(self, run_id: str) -> AdmissionResult | None:
        """Return a run's admission decision, or None."""
        with self._session() as session:
            row = session.get(RunAdmissionRow, run_id)
            return AdmissionResult.model_validate(row.payload) if row is not None else None

    def add_delivery(self, record: DeliveryRecord) -> None:
        """Append a fan-out delivery record."""
        with self._session.begin() as session:
            self._require(session, record.run_id)
            session.add(
                FanOutDeliveryRow(
                    delivery_id=(
                        f"{record.run_id}:{record.destination_type.value}:"
                        f"{record.timestamp.isoformat()}"
                    ),
                    run_id=record.run_id,
                    destination_type=record.destination_type.value,
                    status=record.status.value,
                    timestamp=record.timestamp,
                    payload=record.model_dump(mode="json"),
                )
            )

    def list_deliveries(self, run_id: str) -> list[DeliveryRecord]:
        """Return delivery records for a run."""
        with self._session() as session:
            self._require(session, run_id)
            rows = session.scalars(
                select(FanOutDeliveryRow)
                .where(FanOutDeliveryRow.run_id == run_id)
                .order_by(FanOutDeliveryRow.timestamp)
            )
            return [DeliveryRecord.model_validate(row.payload) for row in rows]

    @staticmethod
    def _require(session: Session, run_id: str) -> None:
        if session.get(RunRow, run_id) is None:
            raise RunNotFoundError(run_id)

    def clear(self) -> None:
        """Delete all run data; used by tests and destructive operations."""
        with self._session.begin() as session:
            for table in (
                FanOutDeliveryRow,
                UsageEventRow,
                RunEventRow,
                RunAdmissionRow,
                RunRow,
            ):
                session.execute(delete(table))
