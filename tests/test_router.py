"""Unit tests for the smart task router (M30-01..M30-03, #199-#201)."""

from __future__ import annotations

from typing import Any

import pytest

from hiveplane.core.run import AdmissionContext
from hiveplane.llm.models import (
    CompletionRequest,
    CompletionResponse,
    TokenUsage,
)
from hiveplane.router.catalog import CatalogCandidate, StaticCatalog
from hiveplane.router.classifier import (
    ClassificationResult,
    ClassifierError,
    LLMTaskClassifier,
)
from hiveplane.router.engine import RouterEngine, hash_task
from hiveplane.router.models import RefusalReason, RouteOutcome
from hiveplane.router.store import InMemoryRouterStore


class StaticClassifier:
    def __init__(self, scores: dict[str, float], model: str = "classifier-v1") -> None:
        self.scores = scores
        self.model = model
        self.calls: list[list[str]] = []

    def classify(
        self, task: str, candidates: list[CatalogCandidate]
    ) -> ClassificationResult:
        self.calls.append([candidate.workload for candidate in candidates])
        return ClassificationResult(model_identity=self.model, scores=dict(self.scores))


class StubProvider:
    def __init__(self, content: str, model_identity: str = "cheap-model") -> None:
        self._content = content
        self._model_identity = model_identity
        self.requests: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(
            content=self._content,
            model_identity=self._model_identity,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


def _engine(scores: dict[str, float], **kwargs: Any) -> tuple[RouterEngine, StaticClassifier]:
    classifier = StaticClassifier(scores)
    catalog = StaticCatalog(["triage", "remediate", "notify"])
    engine = RouterEngine(
        catalog,
        classifier,
        confidence_threshold=kwargs.pop("confidence_threshold", 0.6),
        margin=kwargs.pop("margin", 0.15),
        **kwargs,
    )
    return engine, classifier


def test_routes_to_highest_scoring_certified_workload() -> None:
    engine, _ = _engine({"triage": 0.9, "remediate": 0.2, "notify": 0.1})
    decision = engine.route("the database is down")
    assert decision.outcome is RouteOutcome.ROUTED
    assert decision.chosen == "triage"
    assert decision.routed is True
    assert [c.workload for c in decision.candidates] == ["triage", "remediate", "notify"]
    assert [c.rank for c in decision.candidates] == [1, 2, 3]
    assert decision.classifier_model == "classifier-v1"


def test_low_confidence_is_refused_not_guessed() -> None:
    engine, _ = _engine({"triage": 0.4, "remediate": 0.3, "notify": 0.1})
    decision = engine.route("something unclear")
    assert decision.outcome is RouteOutcome.REFUSED
    assert decision.reason is RefusalReason.LOW_CONFIDENCE
    assert decision.chosen is None


def test_ambiguous_top_two_is_refused() -> None:
    engine, _ = _engine({"triage": 0.9, "remediate": 0.85, "notify": 0.1})
    decision = engine.route("triage or remediate?")
    assert decision.outcome is RouteOutcome.REFUSED
    assert decision.reason is RefusalReason.AMBIGUOUS


def test_no_candidates_is_refused() -> None:
    engine = RouterEngine(StaticCatalog([]), StaticClassifier({}))
    decision = engine.route("anything")
    assert decision.outcome is RouteOutcome.REFUSED
    assert decision.reason is RefusalReason.NO_CANDIDATES
    assert decision.candidates == []


def test_classifier_only_sees_candidates() -> None:
    engine, classifier = _engine({"triage": 0.9})
    engine.route("task")
    assert classifier.calls == [["triage", "remediate", "notify"]]


def test_decision_is_recorded_and_explainable() -> None:
    store = InMemoryRouterStore()
    engine, _ = _engine({"triage": 0.9, "remediate": 0.1}, store=store)
    decision = engine.route("incident")
    stored = store.get_decision(decision.decision_id)
    assert stored is not None
    assert stored.chosen == "triage"
    assert stored.task_hash == hash_task("incident")
    assert "incident" not in stored.model_dump_json()


def test_routing_is_deterministic() -> None:
    engine, _ = _engine({"triage": 0.9, "remediate": 0.1})
    first = engine.route("same task")
    second = engine.route("same task")
    assert first.chosen == second.chosen
    assert [c.workload for c in first.candidates] == [
        c.workload for c in second.candidates
    ]


def test_llm_classifier_parses_json_scores() -> None:
    provider = StubProvider('{"scores": {"triage": 0.8, "remediate": 0.1}}')
    classifier = LLMTaskClassifier(provider, model="cheap")
    result = classifier.classify(
        "db down", [CatalogCandidate(workload="triage"), CatalogCandidate(workload="remediate")]
    )
    assert result.scores == {"triage": 0.8, "remediate": 0.1}
    assert result.model_identity == "cheap-model"
    assert provider.requests[0].model == "cheap"


def test_llm_classifier_clamps_and_ignores_unknown() -> None:
    provider = StubProvider('{"scores": {"triage": 5, "ghost": 0.9}}')
    classifier = LLMTaskClassifier(provider, model="cheap")
    result = classifier.classify("x", [CatalogCandidate(workload="triage")])
    assert result.scores == {"triage": 1.0}


def test_llm_classifier_parses_fenced_json() -> None:
    provider = StubProvider('```json\n{"scores": {"triage": 0.7}}\n```')
    classifier = LLMTaskClassifier(provider, model="cheap")
    result = classifier.classify("x", [CatalogCandidate(workload="triage")])
    assert result.scores == {"triage": 0.7}


def test_llm_classifier_rejects_unparseable_response() -> None:
    classifier = LLMTaskClassifier(StubProvider("not json"), model="cheap")
    with pytest.raises(ClassifierError):
        classifier.classify("x", [CatalogCandidate(workload="triage")])


def test_llm_classifier_rejects_non_object_json() -> None:
    classifier = LLMTaskClassifier(StubProvider("[1, 2]"), model="cheap")
    with pytest.raises(ClassifierError):
        classifier.classify("x", [CatalogCandidate(workload="triage")])


def test_llm_classifier_rejects_malformed_json() -> None:
    classifier = LLMTaskClassifier(StubProvider("{not valid json}"), model="cheap")
    with pytest.raises(ClassifierError):
        classifier.classify("x", [CatalogCandidate(workload="triage")])


def test_llm_classifier_rejects_scores_not_object() -> None:
    classifier = LLMTaskClassifier(StubProvider('{"scores": 5}'), model="cheap")
    with pytest.raises(ClassifierError):
        classifier.classify("x", [CatalogCandidate(workload="triage")])


def test_llm_classifier_rejects_non_numeric_scores() -> None:
    classifier = LLMTaskClassifier(
        StubProvider('{"scores": {"triage": true}}'), model="cheap"
    )
    with pytest.raises(ClassifierError):
        classifier.classify("x", [CatalogCandidate(workload="triage")])


def test_llm_classifier_rejects_no_matching_scores() -> None:
    classifier = LLMTaskClassifier(
        StubProvider('{"scores": {"ghost": 0.9}}'), model="cheap"
    )
    with pytest.raises(ClassifierError):
        classifier.classify("x", [CatalogCandidate(workload="triage")])


def test_llm_classifier_rejects_empty_candidates() -> None:
    classifier = LLMTaskClassifier(StubProvider("{}"), model="cheap")
    with pytest.raises(ClassifierError):
        classifier.classify("x", [])


def test_registry_catalog_excludes_uncertified(monkeypatch: pytest.MonkeyPatch) -> None:
    from hiveplane.registry.service import RegistryService
    from hiveplane.router.catalog import RegistryCatalog

    class _Decision:
        def __init__(self, admitted: bool) -> None:
            self.admitted = admitted

    class _Record:
        def __init__(self, name: str) -> None:
            self.name = name
            self.manifest = type(
                "M",
                (),
                {
                    "metadata": type(
                        "Meta", (), {"description": "d", "labels": {"x": "y"}}
                    )()
                },
            )()

    registry = type(
        "R",
        (),
        {
            "list_workloads": lambda self: [_Record("good"), _Record("bad")],
            "check_admission": lambda self, name, context: _Decision(name == "good"),
        },
    )()
    catalog = RegistryCatalog(registry)
    candidates = catalog.candidates(AdmissionContext.PRODUCTION)
    assert [c.workload for c in candidates] == ["good"]
    assert candidates[0].description == "d"
    assert candidates[0].labels == {"x": "y"}
    assert RegistryService is not None
