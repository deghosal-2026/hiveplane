"""Tests for the hiveplane CLI."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any, Literal

import yaml
from typer.testing import CliRunner

from hiveplane.certification.corpus import load_corpus
from hiveplane.cli import _post_workload, app
from hiveplane.core.manifest import load_manifest

runner = CliRunner()

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "workloads"
REPO_MANIFEST = EXAMPLES_DIR / "repo-agent.yaml"


def _manifest() -> dict[str, Any]:
    return {
        "apiVersion": "hiveplane/v1",
        "kind": "AgentWorkload",
        "metadata": {"name": "repo-agent", "owner": "platform-team"},
        "spec": {
            "runtime": {"adapter": "raw-worker", "entrypoint": "examples.worker:run"},
            "budget": {"per_run_usd": 0.5, "per_day_usd": 5.0},
            "model": {
                "strategy": "tiered",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
            "certification": {
                "benchmark_corpus": "corpora/repo-agent/v1",
                "status": "uncertified",
            },
        },
    }


def test_validate_valid_manifest(tmp_path: Path) -> None:
    path = tmp_path / "repo-agent.yaml"
    path.write_text(yaml.safe_dump(_manifest()))

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_validate_invalid_manifest_exits_nonzero(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump({"kind": "AgentWorkload"}))

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 1


def test_validate_missing_file_exits_nonzero(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "nope.yaml")])

    assert result.exit_code != 0


def test_register_dry_run_reports_enforcement() -> None:
    result = runner.invoke(app, ["register", str(REPO_MANIFEST), "--dry-run"])

    assert result.exit_code == 0
    assert "production_admitted" in result.output


def test_register_posts_manifest_to_api(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_post(api_url: str, payload: dict[str, Any]) -> tuple[int, str]:
        captured["api_url"] = api_url
        captured["payload"] = payload
        return 201, "{}"

    monkeypatch.setattr("hiveplane.cli._post_workload", fake_post)

    result = runner.invoke(
        app, ["register", str(REPO_MANIFEST), "--api-url", "http://api.test"]
    )

    assert result.exit_code == 0
    assert captured["api_url"] == "http://api.test"
    assert captured["payload"]["metadata"]["name"] == "repo-agent"


def test_register_reports_api_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._post_workload", lambda api_url, payload: (409, "conflict")
    )

    result = runner.invoke(app, ["register", str(REPO_MANIFEST)])

    assert result.exit_code == 1
    assert "409" in result.output


def test_register_invalid_manifest_exits_nonzero(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump({"kind": "AgentWorkload"}))

    result = runner.invoke(app, ["register", str(path)])

    assert result.exit_code == 1


def test_post_workload_handles_success(monkeypatch: Any) -> None:
    class FakeResponse:
        status = 201

        def read(self) -> bytes:
            return b'{"ok": true}'

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> Literal[False]:
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda request: FakeResponse())

    code, body = _post_workload("http://api", {"a": 1})

    assert code == 201
    assert body == '{"ok": true}'


def test_post_workload_handles_http_error(monkeypatch: Any) -> None:
    def fake_urlopen(request: Any) -> Any:
        raise urllib.error.HTTPError(
            request.full_url, 409, "Conflict", Message(), io.BytesIO(b"nope")
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    code, body = _post_workload("http://api", {})

    assert code == 409
    assert body == "nope"


def test_post_workload_handles_url_error(monkeypatch: Any) -> None:
    def fake_urlopen(request: Any) -> Any:
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    code, _ = _post_workload("http://api", {})

    assert code == 0


# --------------------------------------------------------------------------- #
# Certification CLI
# --------------------------------------------------------------------------- #
def test_certify_reports_status_and_attestation(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = payload
        return (
            201,
            json.dumps(
                {
                    "certification": {"status": "provisional"},
                    "attestation": {"attestation_id": "att-1"},
                }
            ),
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(
        app, ["certify", "repo-agent", "--context", "staging", "--api-url", "http://api.test"]
    )

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["url"] == "http://api.test/certifications"
    assert captured["payload"]["workload"] == "repo-agent"
    assert captured["payload"]["target_context"] == "staging"
    assert "provisional" in result.output
    assert "att-1" in result.output


def test_certify_reports_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (422, "bad corpus")
    )

    result = runner.invoke(app, ["certify", "repo-agent"])

    assert result.exit_code == 1
    assert "422" in result.output


def test_certify_pins_model_identity(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        captured["payload"] = payload
        return (
            201,
            json.dumps(
                {
                    "certification": {"status": "certified"},
                    "attestation": {"attestation_id": "att-2"},
                }
            ),
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(
        app,
        [
            "certify",
            "repo-agent",
            "--model-identity",
            "openai/gpt-4o/2024-08-06",
        ],
    )

    assert result.exit_code == 0
    assert captured["payload"]["model_identity"] == "openai/gpt-4o/2024-08-06"


def test_certs_list(monkeypatch: Any) -> None:
    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        assert method == "GET"
        assert "workload=repo-agent" in url
        return (
            200,
            json.dumps(
                [
                    {
                        "certification": {
                            "certification_id": "cert-1",
                            "workload_id": "repo-agent",
                            "status": "certified",
                        },
                        "attestation": {"attestation_id": "att-1"},
                    }
                ]
            ),
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["certs", "list", "--workload", "repo-agent"])

    assert result.exit_code == 0
    assert "cert-1" in result.output
    assert "certified" in result.output


def test_certs_show(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps(
                {
                    "certification": {"certification_id": "cert-1", "status": "certified"},
                    "attestation": {"attestation_id": "att-1"},
                }
            ),
        ),
    )

    result = runner.invoke(app, ["certs", "show", "cert-1"])

    assert result.exit_code == 0
    assert "att-1" in result.output


def test_certs_compare_reports_regressions(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps(
                {
                    "blocked": True,
                    "total": 2,
                    "passed_before": 2,
                    "passed_after": 1,
                    "regressed": [{"task_id": "t2"}],
                    "improved": [],
                }
            ),
        ),
    )

    result = runner.invoke(app, ["certs", "compare", "cert-1", "cert-2"])

    assert result.exit_code == 0
    assert "t2" in result.output
    assert "blocked" in result.output.lower()


def test_verify_reports_a_valid_attestation(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        captured["url"] = url
        return 200, json.dumps({"attestation_id": "att-1", "valid": True, "log_seq": 0})

    monkeypatch.setattr("hiveplane.cli._request", _fake)

    result = runner.invoke(app, ["verify", "att-1"])

    assert result.exit_code == 0
    assert "att-1" in result.output
    assert captured["url"].endswith("/attestations/att-1/verify")


def test_verify_invalid_attestation_exits_nonzero(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps({"attestation_id": "att-1", "valid": False, "reason": "bad"}),
        ),
    )

    result = runner.invoke(app, ["verify", "att-1"])

    assert result.exit_code == 1


def test_verify_reports_unknown_attestation(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (404, json.dumps({"detail": "not found"})),
    )

    result = runner.invoke(app, ["verify", "missing"])

    assert result.exit_code == 1


# --------------------------------------------------------------------------- #
# Submission CLI (#52)
# --------------------------------------------------------------------------- #
def test_submit_posts_run(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = payload
        return 201, json.dumps({"id": "run-1", "state": "queued"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(
        app,
        ["submit", "--agent", "repo-agent", "--task", '{"repo": "hiveplane"}', "--api-url", "http://api.test"],
    )

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["url"] == "http://api.test/runs"
    assert captured["payload"]["workload"] == "repo-agent"
    assert captured["payload"]["task"] == {"repo": "hiveplane"}
    assert captured["payload"]["caller"] == "cli"
    assert captured["payload"]["context"] == "sandbox"
    assert "run-1" in result.output


def test_submit_passes_caller_context_and_model(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        captured["payload"] = payload
        return 201, json.dumps({"id": "run-2", "state": "queued"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(
        app,
        [
            "submit",
            "--agent",
            "repo-agent",
            "--caller",
            "ci",
            "--context",
            "production",
            "--model-identity",
            "openai:gpt-4o:2024-08-06",
        ],
    )

    assert result.exit_code == 0
    assert captured["payload"]["caller"] == "ci"
    assert captured["payload"]["context"] == "production"
    assert captured["payload"]["model_identity"] == "openai:gpt-4o:2024-08-06"


def test_submit_rejects_invalid_task_json(monkeypatch: Any) -> None:
    called = False

    def fake_request(*args: Any, **kwargs: Any) -> tuple[int, str]:
        nonlocal called
        called = True
        return 201, "{}"

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["submit", "--agent", "repo-agent", "--task", "not-json"])

    assert result.exit_code == 1
    assert called is False


def test_submit_reports_refusal(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (403, "refused")
    )

    result = runner.invoke(app, ["submit", "--agent", "repo-agent"])

    assert result.exit_code == 1
    assert "403" in result.output


# --------------------------------------------------------------------------- #
# Runs CLI (#52)
# --------------------------------------------------------------------------- #
def test_runs_list(monkeypatch: Any) -> None:
    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        assert method == "GET"
        assert "workload=repo-agent" in url
        assert "state=running" in url
        return (
            200,
            json.dumps(
                [
                    {"id": "run-1", "workload_id": "repo-agent", "state": "running"},
                    {"id": "run-2", "workload_id": "repo-agent", "state": "completed"},
                ]
            ),
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(
        app, ["runs", "list", "--workload", "repo-agent", "--state", "running"]
    )

    assert result.exit_code == 0
    assert "run-1" in result.output
    assert "run-2" in result.output


def test_runs_show(monkeypatch: Any) -> None:
    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        assert method == "GET"
        assert url.endswith("/runs/run-1")
        return 200, json.dumps({"id": "run-1", "state": "completed", "cost_usd": 0.12})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["runs", "show", "run-1"])

    assert result.exit_code == 0
    assert "run-1" in result.output
    assert "completed" in result.output


def test_runs_intervene(monkeypatch: Any) -> None:
    for action in ("pause", "resume", "stop"):
        captured: dict[str, Any] = {}

        def fake_request(
            method: str,
            url: str,
            payload: dict[str, Any] | None = None,
            _captured: dict[str, Any] = captured,
        ) -> tuple[int, str]:
            _captured["method"] = method
            _captured["url"] = url
            return 200, json.dumps({"id": "run-1", "state": "paused"})

        monkeypatch.setattr("hiveplane.cli._request", fake_request)

        result = runner.invoke(app, ["runs", action, "run-1"])

        assert result.exit_code == 0, (action, result.output)
        assert captured["method"] == "POST"
        assert captured["url"].endswith(f"/runs/run-1/{action}")


# --------------------------------------------------------------------------- #
# Approvals CLI (#52)
# --------------------------------------------------------------------------- #
def test_approvals_list(monkeypatch: Any) -> None:
    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        assert method == "GET"
        assert "status=pending" in url
        return (
            200,
            json.dumps(
                [
                    {
                        "approval_id": "appr-1",
                        "run_id": "run-1",
                        "status": "pending",
                        "workload": "repo-agent",
                    }
                ]
            ),
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["approvals", "list", "--status", "pending"])

    assert result.exit_code == 0
    assert "appr-1" in result.output


def test_approvals_approve(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = payload
        return 200, json.dumps({"approval_id": "appr-1", "status": "approved"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(
        app, ["approvals", "approve", "appr-1", "--operator", "alice", "--reason", "lgtm"]
    )

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/approvals/appr-1/approve")
    assert captured["payload"] == {"operator": "alice", "reason": "lgtm"}
    assert "approved" in result.output


def test_approvals_deny_requires_operator(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (200, "{}")
    )

    result = runner.invoke(app, ["approvals", "deny", "appr-1"])

    assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# Triggers CLI (#53)
# --------------------------------------------------------------------------- #
def test_triggers_list(monkeypatch: Any) -> None:
    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        assert method == "GET"
        assert url.endswith("/workloads/repo-agent/triggers")
        return (
            200,
            json.dumps(
                [
                    {
                        "trigger_id": "trig-1",
                        "workload": "repo-agent",
                        "rule": {"type": "cron", "schedule": "0 */6 * * *"},
                    }
                ]
            ),
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["triggers", "list", "--workload", "repo-agent"])

    assert result.exit_code == 0
    assert "trig-1" in result.output
    assert "cron" in result.output


def test_triggers_add(monkeypatch: Any, tmp_path: Path) -> None:
    rule_file = tmp_path / "trigger.yaml"
    rule_file.write_text(
        yaml.safe_dump(
            {
                "type": "github_pr",
                "events": ["opened"],
                "match": {"paths": ["src/**"]},
            }
        )
    )
    captured: dict[str, Any] = {}

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = payload
        return 201, json.dumps({"trigger_id": "trig-2", "workload": "repo-agent"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(
        app, ["triggers", "add", "--workload", "repo-agent", "--file", str(rule_file)]
    )

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/workloads/repo-agent/triggers")
    assert captured["payload"]["type"] == "github_pr"
    assert "trig-2" in result.output


def test_triggers_add_rejects_invalid_rule(monkeypatch: Any, tmp_path: Path) -> None:
    rule_file = tmp_path / "trigger.yaml"
    rule_file.write_text(yaml.safe_dump({"type": "webhook"}))

    result = runner.invoke(
        app, ["triggers", "add", "--workload", "repo-agent", "--file", str(rule_file)]
    )

    assert result.exit_code == 1


# --------------------------------------------------------------------------- #
# Tools CLI (#53)
# --------------------------------------------------------------------------- #
def test_tools_list(monkeypatch: Any) -> None:
    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        assert method == "GET"
        assert "trust_level=read_only" in url
        return (
            200,
            json.dumps(
                [
                    {
                        "tool_id": "mcp.github.list_pull_requests",
                        "name": "List pull requests",
                        "mcp_server": "github",
                        "trust_level": "read_only",
                    }
                ]
            ),
        )

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["tools", "list", "--trust-level", "read_only"])

    assert result.exit_code == 0
    assert "mcp.github.list_pull_requests" in result.output


def test_tools_add(monkeypatch: Any, tmp_path: Path) -> None:
    tool_file = tmp_path / "tool.yaml"
    tool_file.write_text(
        yaml.safe_dump(
            {
                "tool_id": "mcp.github.create_pr_comment",
                "name": "Create PR comment",
                "mcp_server": "github",
                "trust_level": "read_only",
            }
        )
    )
    captured: dict[str, Any] = {}

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = payload
        return 201, json.dumps({"tool_id": "mcp.github.create_pr_comment"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["tools", "add", "--file", str(tool_file)])

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/tools")
    assert captured["payload"]["tool_id"] == "mcp.github.create_pr_comment"
    assert "mcp.github.create_pr_comment" in result.output


def test_tools_add_rejects_invalid_file(monkeypatch: Any, tmp_path: Path) -> None:
    tool_file = tmp_path / "tool.yaml"
    tool_file.write_text(yaml.safe_dump({"tool_id": "x"}))

    result = runner.invoke(app, ["tools", "add", "--file", str(tool_file)])

    assert result.exit_code == 1


# --------------------------------------------------------------------------- #
# init CLI (#53)
# --------------------------------------------------------------------------- #
def test_init_scaffolds_working_project() -> None:
    result = runner.invoke(app, ["init", "myproject"])

    assert result.exit_code == 0, result.output
    project = Path("myproject")
    manifest = load_manifest(project / "workloads" / "hello-agent.yaml")
    assert manifest.name == "hello-agent"
    corpus = load_corpus(project / "corpora" / "hello-agent" / "v1")
    assert corpus.tasks
    assert (project / "README.md").exists()
    assert "init" in result.output.lower() or "next" in result.output.lower()


def test_init_scaffolds_resolvable_entrypoint() -> None:
    from hiveplane.adapters.loader import EntrypointLoader

    result = runner.invoke(app, ["init", "myproject"])
    assert result.exit_code == 0, result.output

    manifest = load_manifest(Path("myproject") / "workloads" / "hello-agent.yaml")
    entry = EntrypointLoader(root=".").load(manifest.spec.runtime.entrypoint)

    assert callable(entry)


def test_init_refuses_to_overwrite_existing_files() -> None:
    assert runner.invoke(app, ["init", "myproject"]).exit_code == 0

    second = runner.invoke(app, ["init", "myproject"])

    assert second.exit_code == 1
    assert "exists" in second.output.lower()


def test_init_force_overwrites_existing_files() -> None:
    assert runner.invoke(app, ["init", "myproject"]).exit_code == 0

    forced = runner.invoke(app, ["init", "myproject", "--force"])

    assert forced.exit_code == 0


def test_init_defaults_to_current_directory() -> None:
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0, result.output
    assert Path("workloads/hello-agent.yaml").exists()


# --------------------------------------------------------------------------- #
# Failure paths and document loading
# --------------------------------------------------------------------------- #
def test_api_failures_exit_nonzero(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (500, "boom")
    )
    commands = [
        ["runs", "list"],
        ["runs", "show", "run-1"],
        ["runs", "pause", "run-1"],
        ["runs", "resume", "run-1"],
        ["runs", "stop", "run-1"],
        ["approvals", "list"],
        ["approvals", "approve", "appr-1", "--operator", "alice"],
        ["approvals", "deny", "appr-1", "--operator", "alice"],
        ["triggers", "list", "--workload", "repo-agent"],
        ["tools", "list"],
        ["certs", "list"],
        ["certs", "show", "cert-1"],
        ["certs", "compare", "cert-1", "cert-2"],
    ]
    for argv in commands:
        result = runner.invoke(app, argv)
        assert result.exit_code == 1, (argv, result.output)


def test_submit_rejects_non_object_task(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (201, "{}")
    )

    result = runner.invoke(app, ["submit", "--agent", "repo-agent", "--task", "[1, 2]"])

    assert result.exit_code == 1


def test_triggers_add_reports_api_failure(monkeypatch: Any, tmp_path: Path) -> None:
    rule_file = tmp_path / "trigger.yaml"
    rule_file.write_text(yaml.safe_dump({"type": "cron", "schedule": "0 */6 * * *"}))
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (500, "boom")
    )

    result = runner.invoke(
        app, ["triggers", "add", "--workload", "repo-agent", "--file", str(rule_file)]
    )

    assert result.exit_code == 1


def test_tools_add_reports_api_failure(monkeypatch: Any, tmp_path: Path) -> None:
    tool_file = tmp_path / "tool.yaml"
    tool_file.write_text(
        yaml.safe_dump(
            {
                "tool_id": "mcp.github.read_issue",
                "name": "Read issue",
                "mcp_server": "github",
                "trust_level": "read_only",
            }
        )
    )
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (500, "boom")
    )

    result = runner.invoke(app, ["tools", "add", "--file", str(tool_file)])

    assert result.exit_code == 1


def test_tools_add_rejects_non_mapping_document(tmp_path: Path) -> None:
    tool_file = tmp_path / "tool.yaml"
    tool_file.write_text("- just\n- a\n- list\n")

    result = runner.invoke(app, ["tools", "add", "--file", str(tool_file)])

    assert result.exit_code == 1


def test_tools_add_reports_malformed_yaml(tmp_path: Path) -> None:
    tool_file = tmp_path / "tool.yaml"
    tool_file.write_text("tool_id: [unclosed\n")

    result = runner.invoke(app, ["tools", "add", "--file", str(tool_file)])

    assert result.exit_code == 1


def test_tools_list_without_filters(monkeypatch: Any) -> None:
    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        assert url.endswith("/tools")
        return 200, "[]"

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["tools", "list"])

    assert result.exit_code == 0


def _seed_manifest() -> dict[str, Any]:
    manifest = _manifest()
    manifest["spec"]["tools"] = {
        "allow": [
            {"tool_id": "mcp.github.read_issue", "trust_level": "read_only"},
            {"tool_id": "mcp.github.create_pr", "trust_level": "destructive",
             "require_approval": True},
        ]
    }
    return manifest


def test_tools_seed_registers_every_allowed_tool(
    monkeypatch: Any, tmp_path: Path
) -> None:
    workloads = tmp_path / "workloads"
    workloads.mkdir()
    (workloads / "repo-agent.yaml").write_text(yaml.safe_dump(_seed_manifest()))
    posted: list[dict[str, Any]] = []

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        assert url.endswith("/tools")
        posted.append(dict(payload or {}))
        return 201, json.dumps({"tool_id": (payload or {}).get("tool_id", "")})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["tools", "seed", "--workloads-dir", str(workloads)])

    assert result.exit_code == 0, result.output
    assert {entry["tool_id"] for entry in posted} == {
        "mcp.github.read_issue",
        "mcp.github.create_pr",
    }
    assert posted[0]["mcp_server"] == "github"
    assert "mcp.github.read_issue" in result.output


def test_tools_seed_treats_conflicts_as_already_present(
    monkeypatch: Any, tmp_path: Path
) -> None:
    workloads = tmp_path / "workloads"
    workloads.mkdir()
    (workloads / "repo-agent.yaml").write_text(yaml.safe_dump(_seed_manifest()))
    calls = {"n": 0}

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        calls["n"] += 1
        if calls["n"] == 1:
            return 201, json.dumps({"tool_id": (payload or {}).get("tool_id", "")})
        return 409, json.dumps({"detail": "already exists"})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["tools", "seed", "--workloads-dir", str(workloads)])

    assert result.exit_code == 0, result.output
    assert "mcp.github.read_issue" in result.output
    assert "mcp.github.create_pr" in result.output


def test_tools_seed_reports_api_failure(monkeypatch: Any, tmp_path: Path) -> None:
    workloads = tmp_path / "workloads"
    workloads.mkdir()
    (workloads / "repo-agent.yaml").write_text(yaml.safe_dump(_seed_manifest()))
    monkeypatch.setattr(
        "hiveplane.cli._request", lambda method, url, payload=None: (500, "boom")
    )

    result = runner.invoke(app, ["tools", "seed", "--workloads-dir", str(workloads)])

    assert result.exit_code == 1


def test_tools_seed_requires_a_workloads_dir(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["tools", "seed", "--workloads-dir", str(tmp_path / "missing")]
    )

    assert result.exit_code == 1


def test_route_reports_choice(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps(
                {
                    "outcome": "routed",
                    "chosen": "repo-agent",
                    "classifier_model": "gpt-4o",
                    "candidates": [{"score": 0.91, "workload": "repo-agent"}],
                }
            ),
        ),
    )

    result = runner.invoke(app, ["route", "triage this ticket"])

    assert result.exit_code == 0
    assert "repo-agent" in result.output


def test_route_reports_refusal(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps(
                {
                    "outcome": "refused",
                    "reason": "no certified candidate",
                    "candidates": [{"score": 0.2, "workload": "weak-agent"}],
                }
            ),
        ),
    )

    result = runner.invoke(app, ["route", "triage this ticket"])

    assert result.exit_code == 0
    assert "refused" in result.output
    assert "weak-agent" in result.output


def test_agents_list_prints_tools(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps([{"tool_id": "agent.triage", "description": "Triage tickets"}]),
        ),
    )

    result = runner.invoke(app, ["agents", "list"])

    assert result.exit_code == 0
    assert "agent.triage" in result.output


def test_agents_invoke_prints_nested_run(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps(
                {"tool_id": "agent.triage", "nested_run_id": "run-9", "depth": 1}
            ),
        ),
    )

    result = runner.invoke(
        app, ["agents", "invoke", "agent.triage", "--caller-run", "run-1"]
    )

    assert result.exit_code == 0
    assert "run-9" in result.output


def test_feedback_posts_a_verdict(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = payload
        return 201, json.dumps({"feedback_id": "fb-1", "verdict": "failed-with-lesson"})

    monkeypatch.setattr("hiveplane.cli._request", _fake)

    result = runner.invoke(
        app,
        ["feedback", "run-1", "--verdict", "failed-with-lesson", "--notes", "escalate"],
    )

    assert result.exit_code == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/runs/run-1/feedback")
    assert captured["payload"]["verdict"] == "failed-with-lesson"
    assert captured["payload"]["notes"] == "escalate"


def test_feedback_reports_api_failure(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (409, json.dumps({"detail": "not terminal"})),
    )

    result = runner.invoke(app, ["feedback", "run-1", "--verdict", "good"])

    assert result.exit_code == 1


def test_corpus_candidates_lists(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        captured["url"] = url
        return 200, json.dumps(
            [
                {
                    "candidate_id": "cand-1",
                    "workload_id": "repo-agent",
                    "status": "pending",
                    "source_run_id": "run-1",
                }
            ]
        )

    monkeypatch.setattr("hiveplane.cli._request", _fake)

    result = runner.invoke(app, ["corpus", "candidates", "--workload", "repo-agent"])

    assert result.exit_code == 0
    assert "cand-1" in result.output
    assert "workload=repo-agent" in captured["url"]


def test_corpus_approve_posts_reviewer(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        captured["url"] = url
        captured["payload"] = payload
        return 200, json.dumps({"candidate_id": "cand-1", "status": "approved"})

    monkeypatch.setattr("hiveplane.cli._request", _fake)

    result = runner.invoke(app, ["corpus", "approve", "cand-1", "--reviewer", "bob"])

    assert result.exit_code == 0
    assert captured["url"].endswith("/corpus/candidates/cand-1/approve")
    assert captured["payload"] == {"reviewer": "bob"}


def test_corpus_reject_sends_reason(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        captured["payload"] = payload
        return 200, json.dumps({"candidate_id": "cand-1", "status": "rejected"})

    monkeypatch.setattr("hiveplane.cli._request", _fake)

    result = runner.invoke(
        app, ["corpus", "reject", "cand-1", "--reason", "wrong"]
    )

    assert result.exit_code == 0
    assert captured["payload"]["reason"] == "wrong"


def test_eval_samples_lists(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake(method: str, url: str, payload: dict[str, Any] | None = None) -> tuple[int, str]:
        captured["url"] = url
        return 200, json.dumps(
            [{"sample_id": "sample-1", "run_id": "run-1", "rubric_version": 1}]
        )

    monkeypatch.setattr("hiveplane.cli._request", _fake)

    result = runner.invoke(app, ["eval", "samples", "--workload", "repo-agent"])

    assert result.exit_code == 0
    assert "sample-1" in result.output
    assert "workload=repo-agent" in captured["url"]


def test_eval_quality_prints_score(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hiveplane.cli._request",
        lambda method, url, payload=None: (
            200,
            json.dumps(
                {
                    "workload_id": "repo-agent",
                    "sample_count": 3,
                    "mean_score": 0.72,
                    "dip": True,
                }
            ),
        ),
    )

    result = runner.invoke(app, ["eval", "quality", "repo-agent"])

    assert result.exit_code == 0
    assert "0.72" in result.output
    assert "dip" in result.output.lower()


def test_tools_add_from_server_onboards_discovered_tools(monkeypatch: Any) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        calls.append((method, url))
        if url.endswith("/mcp/servers") and method == "POST":
            return 201, json.dumps({"server_id": "srv-1", "status": "connected"})
        if url.endswith("/mcp/tools"):
            return 200, json.dumps(
                [
                    {"tool_id": "tool-1", "tool_name": "read_file", "server_id": "srv-1"},
                    {"tool_id": "tool-2", "tool_name": "other", "server_id": "srv-2"},
                ]
            )
        if url.endswith("/mcp/tools/tool-1/onboard"):
            return 200, json.dumps({"tool_id": "tool-1"})
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    result = runner.invoke(app, ["tools", "add", "--server", "stdio://python -m x"])

    assert result.exit_code == 0
    assert "onboarded tool-1" in result.output
    assert ("POST", "/mcp/servers") not in calls  # base URL includes more path
    assert any(url.endswith("/mcp/tools/tool-1/onboard") for _, url in calls)
    assert not any("tool-2" in url for _, url in calls)


def test_mcp_servers_and_tools_commands(monkeypatch: Any) -> None:
    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        if url.endswith("/mcp/servers"):
            return 200, json.dumps(
                [{"server_id": "srv-1", "status": "connected", "fingerprint": "abc123"}]
            )
        if "/mcp/tools" in url:
            return 200, json.dumps(
                [{"tool_id": "tool-1", "tool_name": "read_file", "status": "active"}]
            )
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    servers = runner.invoke(app, ["mcp", "servers"])
    tools = runner.invoke(app, ["mcp", "tools", "--status", "active"])

    assert servers.exit_code == 0 and "srv-1" in servers.output
    assert tools.exit_code == 0 and "tool-1" in tools.output


def test_parse_server_uri_variants() -> None:
    from hiveplane.cli import _parse_server_uri

    assert _parse_server_uri("stdio://python -m my.tools") == {
        "kind": "stdio",
        "target": "python",
        "args": ["-m", "my.tools"],
    }
    assert _parse_server_uri("https://mcp.example.com/rpc")["kind"] == "http"
    assert _parse_server_uri("sse://mcp.example.com/sse")["target"] == "mcp.example.com/sse"


def test_secrets_cli_put_list_show_rotate(monkeypatch: Any) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        calls.append((method, url))
        assert payload is None or "value" in payload
        if url.endswith("/secrets") and method == "POST":
            return 201, json.dumps({"name": "pg", "current_version": 1})
        if url.endswith("/secrets") and method == "GET":
            return 200, json.dumps(
                [{"name": "pg", "current_version": 2, "versions": [1, 2]}]
            )
        if url.endswith("/secrets/pg/rotate"):
            return 200, json.dumps({"name": "pg", "current_version": 2})
        if url.endswith("/secrets/pg"):
            return 200, json.dumps(
                {"name": "pg", "current_version": 2, "versions": [1, 2]}
            )
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    put = runner.invoke(app, ["secrets", "put", "pg", "--value", "canary"])
    listed = runner.invoke(app, ["secrets", "list"])
    shown = runner.invoke(app, ["secrets", "show", "pg"])
    rotated = runner.invoke(app, ["secrets", "rotate", "pg", "--value", "new"])

    assert put.exit_code == 0 and "canary" not in put.output
    assert "pg" in listed.output
    assert "current=v2" in shown.output
    assert rotated.exit_code == 0


def test_secrets_cli_requires_a_value() -> None:
    result = runner.invoke(app, ["secrets", "put", "pg"])
    assert result.exit_code == 2


def test_secrets_cli_from_env(monkeypatch: Any) -> None:
    monkeypatch.setenv("MY_SECRET", "from-env")
    captured: dict[str, Any] = {}

    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        captured.update(payload or {})
        return 201, json.dumps({"name": "pg", "current_version": 1})

    monkeypatch.setattr("hiveplane.cli._request", fake_request)
    result = runner.invoke(app, ["secrets", "put", "pg", "--from-env", "MY_SECRET"])
    assert result.exit_code == 0
    assert captured["value"] == "from-env"

    missing = runner.invoke(app, ["secrets", "put", "pg", "--from-env", "NOPE"])
    assert missing.exit_code == 2


def test_keys_cli_create_list_revoke_and_auth_whoami(monkeypatch: Any) -> None:
    def fake_request(
        method: str, url: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, str]:
        if url.endswith("/keys") and method == "POST":
            return 201, json.dumps({"key_id": "key-1", "token": "hp_token"})
        if url.endswith("/keys") and method == "GET":
            return 200, json.dumps(
                [{"key_id": "key-1", "role": "approver", "revoked_at": None}]
            )
        if url.endswith("/keys/key-1"):
            return 200, json.dumps({"key_id": "key-1", "role": "approver", "revoked_at": "t"})
        if url.endswith("/auth/whoami"):
            return 200, json.dumps(
                {"operator_id": "alice", "role": "admin", "tenant_id": "default"}
            )
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr("hiveplane.cli._request", fake_request)

    created = runner.invoke(
        app, ["keys", "create", "--role", "approver", "--scope", "approvals:write"]
    )
    listed = runner.invoke(app, ["keys", "list"])
    revoked = runner.invoke(app, ["keys", "revoke", "key-1"])
    whoami = runner.invoke(app, ["auth", "whoami"])

    assert created.exit_code == 0 and "hp_token" in created.output
    assert "key-1" in listed.output
    assert revoked.exit_code == 0
    assert "alice" in whoami.output
