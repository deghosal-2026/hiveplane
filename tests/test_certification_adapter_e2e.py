"""Adapter-backed certification of the three example workloads (M23, #109).

Exercises the real path end to end: a corpus task is submitted as a normal run
through the dispatching adapter (raw-worker or langgraph), the policy/budget/
sandbox gates, the fixture tool executor, and the replayed model provider; the
agent's actual output is checked. A deliberately regressed agent must fail,
proving the benchmark is not theater (D19/D20).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from hiveplane.adapters.base import Adapter, AdapterRunExecutor
from hiveplane.adapters.dispatch import DispatchingAdapter
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.executor import AdapterTaskExecutor
from hiveplane.certification.models import (
    CertificationPolicy,
    CertificationStatus,
    Environment,
    TargetContext,
    Thresholds,
)
from hiveplane.certification.runner import UnconfiguredTaskExecutor
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.core.manifest import load_manifest
from hiveplane.core.run import Run
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.gates import (
    ManifestSandboxGate,
    NullRunExecutor,
    RegistryCertificationGate,
)
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.tool_executor import FixtureToolExecutor
from hiveplane.execution.tools import ToolGateway
from hiveplane.execution.wiring import build_langgraph, build_raw_worker
from hiveplane.llm.fake import FakeProvider
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.policy.store import InMemoryApprovalStore
from hiveplane.registry.seeding import derive_tool_registrations, seed_tools
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.sandbox.manager import InMemorySandboxManager
from hiveplane.shaping.injection import InjectionScanner
from hiveplane.shaping.pipeline import ShapingPipeline

ROOT = Path(__file__).resolve().parents[1]
WORKLOADS_DIR = ROOT / "examples" / "workloads"
CORPORA_DIR = ROOT / "examples"
TOOLS_DIR = ROOT / "deploy" / "testdata" / "tools"
REPLAY_FILE = ROOT / "deploy" / "testdata" / "llm" / "replay.json"

_WORKLOADS = ("repo-agent", "docs-agent", "incident-agent")
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="control-plane",
    control_plane_version="0.1.0",
)
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


class _Harness:
    """A real control plane wired for in-process, deterministic certification."""

    def __init__(self, *, entrypoints_root: Path = ROOT) -> None:
        private_key, public_key = generate_keypair()
        budget = BudgetService(InMemoryBudgetStore(), CostTable(), clock=lambda: _NOW)
        self.registry = RegistryService(
            InMemoryRegistryStore(),
            clock=lambda: _NOW,
            attestation_public_key=public_key,
        )
        self.approvals = ApprovalService(InMemoryApprovalStore(), clock=lambda: _NOW)
        self.provider = FakeProvider(
            replay=json.loads(REPLAY_FILE.read_text(encoding="utf-8"))
        )
        self.service = RunService(
            InMemoryRunStore(),
            self.registry,
            admission=AdmissionPipeline(
                RegistryCertificationGate(self.registry),
                PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _NOW),
                budget,
                ManifestSandboxGate(),
                clock=lambda: _NOW,
            ),
            executor=NullRunExecutor(),
            fanout=_FanOut(),
            approvals=self.approvals,
            budget=budget,
            sandbox_runtime=InMemorySandboxManager(clock=lambda: _NOW),
        )
        self.gateway = ToolGateway(
            self.registry,
            PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _NOW),
            self.service,
            shaping=ShapingPipeline(InjectionScanner()),
            approvals=self.approvals,
            executor=FixtureToolExecutor(TOOLS_DIR),
            clock=lambda: _NOW,
        )
        self.adapters: dict[RuntimeAdapter, Adapter] = {
            RuntimeAdapter.RAW_WORKER: build_raw_worker(
                self.service,
                self.gateway,
                root=entrypoints_root,
                spawner=lambda work: work(),
                provider=self.provider,
            ),
            RuntimeAdapter.LANGGRAPH: build_langgraph(
                self.service,
                self.gateway,
                root=entrypoints_root,
                spawner=lambda work: work(),
                provider=self.provider,
            ),
        }
        self.service.attach_executor(AdapterRunExecutor(DispatchingAdapter(self.adapters)))
        self.coordinator = CertificationCoordinator(
            self.registry,
            CertificationService(
                CertificationEngine(_policy(), clock=lambda: _NOW),
                self.registry,
                private_key=private_key,
                environment=_ENV,
                clock=lambda: _NOW,
            ),
            InMemoryCertificationStore(),
            executor=UnconfiguredTaskExecutor(),
            executor_factory=self._executor_for,
            corpora_dir=CORPORA_DIR,
            environment=_ENV,
            clock=lambda: _NOW,
        )

    def _executor_for(self, workload: str, model_identity: str | None) -> AdapterTaskExecutor:
        return AdapterTaskExecutor(
            self.service,
            self.registry,
            workload=workload,
            model_identity=model_identity,
            approvals=self.approvals,
            sleep=lambda seconds: None,
        )

    def register_examples(self) -> None:
        manifests = [load_manifest(WORKLOADS_DIR / f"{name}.yaml") for name in _WORKLOADS]
        seed_tools(
            self.registry,
            [
                registration
                for manifest in manifests
                for registration in derive_tool_registrations(manifest)
            ],
            clock=lambda: _NOW,
        )
        for manifest in manifests:
            self.registry.create(manifest)


def _policy() -> CertificationPolicy:
    return CertificationPolicy(
        staging=Thresholds(
            min_pass_rate=0.80, max_critical_failures=2, max_p95_latency_ms=60000
        ),
        production=Thresholds(
            min_pass_rate=0.85, max_critical_failures=0, max_p95_latency_ms=30000
        ),
    )


def test_all_three_example_workloads_certify_at_production() -> None:
    harness = _Harness()
    harness.register_examples()

    for workload in _WORKLOADS:
        harness.coordinator.certify(workload, target_context=TargetContext.STAGING)
        record = harness.coordinator.certify(
            workload, target_context=TargetContext.PRODUCTION
        )

        aggregate = record.benchmark_result.aggregate
        failed = [
            task.task_id
            for task in record.benchmark_result.tasks
            if task.status.value == "fail"
        ]
        assert aggregate.pass_rate == 1.0, f"{workload}: failures={failed}"
        assert aggregate.critical_failures == 0, f"{workload}: critical failure"
        assert record.certification.status is CertificationStatus.CERTIFIED


def test_regressed_agent_fails_certification(tmp_path: Path) -> None:
    (tmp_path / "broken_agent.py").write_text(
        "def run(task, ctx):\n"
        "    return {'risk': 'low', 'summary': 'looks fine'}\n",
        encoding="utf-8",
    )
    harness = _Harness(entrypoints_root=tmp_path)
    repo = load_manifest(WORKLOADS_DIR / "repo-agent.yaml")
    certification = repo.spec.certification
    assert certification is not None
    broken = repo.model_copy(
        update={
            "metadata": repo.metadata.model_copy(update={"name": "broken-agent"}),
            "spec": repo.spec.model_copy(
                update={
                    "runtime": repo.spec.runtime.model_copy(
                        update={"entrypoint": "broken_agent:run"}
                    ),
                    "certification": certification.model_copy(
                        update={"benchmark_corpus": "corpora/repo-agent/v2"}
                    ),
                }
            ),
        }
    )
    seed_tools(harness.registry, derive_tool_registrations(broken), clock=lambda: _NOW)
    harness.registry.create(broken)

    record = harness.coordinator.certify(
        "broken-agent", target_context=TargetContext.PRODUCTION
    )

    aggregate = record.benchmark_result.aggregate
    assert aggregate.critical_failures >= 1, "regressed agent must fail a critical task"
    assert aggregate.pass_rate < 0.85
    assert record.certification.status is CertificationStatus.UNCERTIFIED
