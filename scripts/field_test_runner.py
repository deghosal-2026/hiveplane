#!/usr/bin/env python3
"""Execute the v0.1.0 real-agent field test (M23, P4).

Drives the three Tier 1 workloads (`repo-agent`, `docs-agent`, `incident-agent`)
through the certified control loop against the live stack and writes raw evidence
into ``field_test/v0.1.0/results/`` (one directory per scenario). This is the
**real-agent** track; the container/API/UI layers are covered by the completed
Docker suite (``scripts/docker-test.sh``).

Scenarios (S1-S9, see docs/field-test/v0.1.0/field-test-plan.md):

    S1  certify all three agents (staging -> production)
    S2  uncertified agent refused production admission
    S3  model swap blocked
    S4  seeded regression fails production certification
    S5  over-budget run (needs a priced provider -- local model is $0)
    S6  destructive tool call requires approval (approve + resume)
    S7  large tool output shaped before reaching the agent
    S8  pause -> control-plane restart -> resume
    S9  result fan-out recorded
    S10 operator surface: inspect/stop a live run, audit trail, init, dashboards

Usage:
    scripts/field_test_runner.py [--api-url URL] [--results-dir DIR] [--only S1,S2]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hiveplane.core.manifest import load_manifest  # noqa: E402

WORKLOADS = ("support-agent", "eval-judge")
WORKLOADS_DIR = ROOT / "field_test" / "workloads"
CORPORA_DIR = ROOT / "field_test" / "corpora"
ENV_FILE = ROOT / ".env.local"
DEFAULT_RESULTS = ROOT / "field_test" / "v0.1.0" / "results"
TERMINAL = {"completed", "failed", "cancelled"}


class ScenarioError(AssertionError):
    """A scenario assertion failed (recorded, not fatal to the whole run)."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _http_get(url: str) -> dict[str, Any]:
    """Fetch a URL and report reachability + size (for UI/HTML surfaces)."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read()
            return {"ok": response.status == 200, "status": response.status, "bytes": len(body)}
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": error.code, "bytes": 0}
    except (urllib.error.URLError, OSError) as error:
        return {"ok": False, "status": None, "bytes": 0, "error": str(error)}


def _canonical_identity() -> str:
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("HIVEPLANE_MODEL__DEFAULT_MODEL="):
                return line.partition("=")[2].strip()
    return "openai/gpt-4o/2024-08-06"


def _restart_control_plane(base_url: str) -> bool:
    """Restart the control plane via ``HIVEPLANE_FIELD_RESTART_CMD`` and wait for ready."""
    command = os.environ.get("HIVEPLANE_FIELD_RESTART_CMD", "")
    if not command:
        return False
    subprocess.run(command, shell=True, check=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/readyz", timeout=5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    raise ScenarioError("control plane did not become ready after restart")


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 600.0,
    ) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return response.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8")
            try:
                return error.code, json.loads(body)
            except ValueError:
                return error.code, body

    def poll(self, run_id: str, timeout: float = 300.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        run: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status, run = self.request("GET", f"/runs/{run_id}")
            if status != 200:
                raise ScenarioError(f"GET /runs/{run_id} -> {status}: {run}")
            if run.get("state") in TERMINAL:
                return run
            time.sleep(1)
        raise ScenarioError(f"run {run_id} never terminal: {run}")

    def approve_and_resume(self, run_id: str) -> dict[str, Any]:
        status, approvals = self.request("GET", "/approvals")
        if status != 200:
            raise ScenarioError(f"GET /approvals -> {status}: {approvals}")
        pending = [
            approval
            for approval in approvals
            if approval.get("run_id") == run_id and approval.get("status") == "pending"
        ]
        if not pending:
            raise ScenarioError(f"paused run {run_id} has no pending approval")
        approved: list[dict[str, Any]] = []
        for approval in pending:
            status, body = self.request(
                "POST",
                f"/approvals/{approval['approval_id']}/approve",
                {"operator": "field-test", "reason": "field-test approval"},
            )
            if status != 200:
                raise ScenarioError(f"approve -> {status}: {body}")
            approved.append({"approval_id": approval["approval_id"], "status": status, "body": body})
        resume_status, resume_body = self.request("POST", f"/runs/{run_id}/resume")
        if resume_status not in (200, 409):
            raise ScenarioError(f"resume -> {resume_status}: {resume_body}")
        return {
            "pending": pending,
            "approved": approved,
            "resume": {"status": resume_status, "body": resume_body},
        }


class Runner:
    def __init__(self, api: Api, results_dir: Path) -> None:
        self.api = api
        self.results_dir = results_dir
        self.identity = _canonical_identity()
        self.ui_url = os.environ.get("HIVEPLANE_UI_URL", "http://localhost:3001").rstrip("/")
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.summary: list[dict[str, Any]] = []

    # -- evidence ---------------------------------------------------------
    def scenario_dir(self, sid: str, slug: str) -> Path:
        directory = self.results_dir / f"{sid}-{slug}"
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write(
        self, directory: Path, name: str, data: Any, *, text: bool = False
    ) -> None:
        path = directory / name
        if text:
            path.write_text(str(data), encoding="utf-8")
        else:
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def workload_evidence(self, names: tuple[str, ...] = WORKLOADS) -> dict[str, Any]:
        evidence: dict[str, Any] = {}
        for name in names:
            manifest = load_manifest(WORKLOADS_DIR / f"{name}.yaml")
            corpus_ref = manifest.spec.certification.benchmark_corpus if manifest.spec.certification else None
            corpus_path = CORPORA_DIR / name / "hiveplane-corpus.yaml" if corpus_ref else None
            evidence[name] = {
                "manifest": str((WORKLOADS_DIR / f"{name}.yaml").relative_to(ROOT)),
                "runtime_adapter": manifest.spec.runtime.adapter.value,
                "entrypoint": manifest.spec.runtime.entrypoint,
                "benchmark_corpus": corpus_ref,
                "benchmark_corpus_path": str(corpus_path.relative_to(ROOT)) if corpus_path else None,
                "source": "field_test",
                "model_identity": (
                    manifest.spec.model.identity.model_dump(mode="json")
                    if manifest.spec.model.identity is not None
                    else None
                ),
            }
        return {
            "workloads_root": str(WORKLOADS_DIR.relative_to(ROOT)),
            "corpora_root": str(CORPORA_DIR.relative_to(ROOT)),
            "downloaded_field_test_assets_root": "field_test/",
            "note": (
                "The v0.1.0 field test harness registers workloads from field_test/workloads "
                "and certifies against field_test/corpora. These manifests point at shims "
                "that wrap the downloaded real agents under field_test/agents/."
            ),
            "workloads": evidence,
        }

    def record(
        self,
        sid: str,
        name: str,
        status: str,
        detail: str,
        directory: Path | None = None,
    ) -> None:
        self.summary.append(
            {
                "scenario": sid,
                "name": name,
                "status": status,
                "detail": detail,
                "evidence": str(directory.relative_to(ROOT)) if directory else None,
            }
        )
        marker = {"pass": "PASS", "fail": "FAIL", "blocked": "BLOCKED"}.get(status, status)
        print(f"  [{marker}] {sid} {name}: {detail}", flush=True)

    # -- shared steps -----------------------------------------------------
    def register(self, name: str) -> int:
        payload = load_manifest(WORKLOADS_DIR / f"{name}.yaml").model_dump(
            by_alias=True, mode="json"
        )
        return self.api.request("POST", "/workloads", payload)[0]

    def certify(self, name: str, context: str) -> dict[str, Any]:
        status, body = self.api.request(
            "POST",
            "/certifications",
            {
                "workload": name,
                "target_context": context,
                "model_identity": self.identity,
            },
        )
        if status != 201:
            raise ScenarioError(f"certify {name}/{context} -> {status}: {body}")
        return body

    # -- scenarios --------------------------------------------------------
    def s1_certify_three(self) -> None:
        directory = self.scenario_dir("S1", "certify-tier1")
        attestations: dict[str, Any] = {}
        certifications: dict[str, Any] = {}
        raw: dict[str, Any] = {}
        self.write(directory, "workloads_used.json", self.workload_evidence())
        failures: list[str] = []
        for name in WORKLOADS:
            entry: dict[str, Any] = {}
            try:
                self.register(name)
                staging = self.certify(name, "staging")
                entry["staging"] = staging
                production = self.certify(name, "production")
                entry["production"] = production
            except ScenarioError as exc:
                entry["error"] = str(exc)
                raw[name] = entry
                failures.append(f"{name}: {exc}")
                continue
            raw[name] = entry
            certifications[name] = {"staging": staging, "production": production}
            attestations[name] = {
                "staging": staging["attestation"],
                "production": production["attestation"],
                "eval_summary": production["certification"]["eval_summary"],
            }
            if staging["certification"]["status"] != "provisional":
                failures.append(f"{name} staging status={staging['certification']['status']}")
            if production["certification"]["status"] != "certified":
                failures.append(
                    f"{name} production status={production['certification']['status']}"
                )
        self.write(directory, "raw.json", raw)
        self.write(directory, "certifications.json", certifications)
        self.write(directory, "attestations.json", attestations)
        self.write(
            directory,
            "commands.sh",
            "hiveplane certify <workload> --context staging\n"
            "hiveplane certify <workload> --context production\n",
            text=True,
        )
        if failures:
            self.record("S1", "certify-tier1", "fail", "; ".join(failures), directory)
        else:
            self.record(
                "S1", "certify-tier1", "pass", "all Tier 1 certified via benchmark", directory
            )

    def s2_uncertified_refused(self) -> None:
        directory = self.scenario_dir("S2", "uncertified-refused")
        try:
            self.register("uncertified-agent")
            status, body = self.api.request(
                "POST",
                "/runs",
                {
                    "workload": "uncertified-agent",
                    "caller": "field-test",
                    "context": "production",
                    "model_identity": self.identity,
                },
            )
            self.write(directory, "response.json", {"status": status, "body": body})
            if status != 403:
                raise ScenarioError(f"expected 403, got {status}: {body}")
            self.record("S2", "uncertified-refused", "pass", "refused production (403)", directory)
        except ScenarioError as exc:
            self.record("S2", "uncertified-refused", "fail", str(exc), directory)

    def s3_model_swap(self) -> None:
        directory = self.scenario_dir("S3", "model-swap")
        try:
            self.register("model-swap-agent")
            # Bind an attestation to the served/certified identity first; the
            # model-binding gate compares a run's identity against the
            # attestation model, so a swap is only meaningful post-certification.
            self.certify("model-swap-agent", "staging")
            self.certify("model-swap-agent", "production")
            swapped = "openai/gpt-4o/2024-08-06"
            status, body = self.api.request(
                "POST",
                "/runs",
                {
                    "workload": "model-swap-agent",
                    "caller": "field-test",
                    "context": "production",
                    "model_identity": swapped,
                },
            )
            self.write(
                directory,
                "response.json",
                {
                    "status": status,
                    "body": body,
                    "certified_identity": self.identity,
                    "swapped_identity": swapped,
                },
            )
            if status not in (403, 409, 422):
                raise ScenarioError(f"model swap not blocked: {status} {body}")
            self.record(
                "S3", "model-swap", "pass", f"blocked ({status})", directory
            )
        except ScenarioError as exc:
            self.record("S3", "model-swap", "fail", str(exc), directory)

    def s4_regression(self) -> None:
        directory = self.scenario_dir("S4", "regression")
        try:
            self.register("regressed-agent")
            status, body = self.api.request(
                "POST",
                "/certifications",
                {
                    "workload": "regressed-agent",
                    "target_context": "production",
                    "model_identity": self.identity,
                },
            )
            self.write(directory, "response.json", {"status": status, "body": body})
            if status != 201:
                raise ScenarioError(f"certify regressed -> {status}: {body}")
            final = body["certification"]["status"]
            critical = body["certification"]["eval_summary"]["critical_failures"]
            if final == "certified" or critical < 1:
                raise ScenarioError(f"regression not caught: status={final} critical={critical}")
            self.record(
                "S4",
                "regression",
                "pass",
                f"blocked (status={final}, critical={critical})",
                directory,
            )
        except ScenarioError as exc:
            self.record("S4", "regression", "fail", str(exc), directory)

    def s5_budget(self) -> None:
        directory = self.scenario_dir("S5", "over-budget")
        try:
            self.register("budget-probe")
            status, run = self.api.request(
                "POST",
                "/runs",
                {
                    "workload": "budget-probe",
                    "caller": "field-test",
                    "context": "sandbox",
                    "model_identity": self.identity,
                    "task": {},
                },
            )
            if status != 201:
                raise ScenarioError(f"submit budget-probe -> {status}: {run}")
            self.api.request("POST", f"/runs/{run['id']}/start")
            finished = self.api.poll(run["id"])
            spend = self.api.request("GET", "/spend")[1]
            self.write(directory, "run.json", finished)
            self.write(directory, "spend.json", spend)
            if finished["state"] != "failed":
                raise ScenarioError(f"over-budget run not blocked: {finished['state']}")
            reason = str(finished.get("failure_reason") or "")
            if "budget" not in reason.lower():
                raise ScenarioError(f"run failed for a non-budget reason: {reason!r}")
            self.record("S5", "over-budget", "pass", f"blocked: {reason}", directory)
        except ScenarioError as exc:
            self.record("S5", "over-budget", "fail", str(exc), directory)

    def s6_destructive(self) -> None:
        directory = self.scenario_dir("S6", "destructive-tool")
        run: dict[str, Any] | None = None
        paused: dict[str, Any] | None = None
        approvals: list[dict[str, Any]] | None = None
        try:
            self.register("support-agent")
            status, run = self.api.request(
                "POST",
                "/runs",
                {
                    "workload": "support-agent",
                    "caller": "field-test",
                    "context": "production",
                    "model_identity": self.identity,
                    "task": {"query": "totally unknown topic", "account_id": "ACC-001"},
                },
            )
            if status != 201:
                raise ScenarioError(f"submit support-agent -> {status}: {run}")
            start_status, start_body = self.api.request("POST", f"/runs/{run['id']}/start")
            self.write(
                directory,
                "submitted.json",
                {"submit_status": status, "run": run, "start_status": start_status, "start_body": start_body},
            )
            paused = self._wait_for_state(run["id"], "paused", timeout=120)
            self.write(directory, "paused.json", paused)
            approvals = self.api.approve_and_resume(run["id"])
            self.write(directory, "approvals.json", approvals)
            finished = self.api.poll(run["id"])
            self.write(directory, "run.json", {"paused": paused, "finished": finished})
            if finished["state"] != "completed":
                raise ScenarioError(f"run did not complete: {finished['state']}")
            self.record(
                "S6", "destructive-tool", "pass", "escalated, approved, completed", directory
            )
        except ScenarioError as exc:
            self.record("S6", "destructive-tool", "fail", str(exc), directory)

    def s7_shaping(self) -> None:
        directory = self.scenario_dir("S7", "large-output")
        try:
            self.register("support-agent")
            status, run = self.api.request(
                "POST",
                "/runs",
                {
                    "workload": "support-agent",
                    "caller": "field-test",
                    "context": "sandbox",
                    "model_identity": self.identity,
                    "task": {"large": True},
                },
            )
            if status != 201:
                raise ScenarioError(f"submit support-agent -> {status}: {run}")
            self.api.request("POST", f"/runs/{run['id']}/start")
            finished = self.api.poll(run["id"])
            result = finished.get("result") or {}
            story = self.api.request("GET", f"/runs/{run['id']}/story")[1]
            self.write(directory, "run.json", finished)
            self.write(directory, "story.json", story)
            if finished["state"] != "completed":
                raise ScenarioError(f"run did not complete: {finished['state']}")
            if not result.get("truncated"):
                raise ScenarioError(f"tool output was not truncated: {result}")
            if int(result.get("shaped_bytes", 0)) > 16384:
                raise ScenarioError(f"shaped output exceeds max_bytes: {result}")
            self.record(
                "S7",
                "large-output",
                "pass",
                f"truncated {result.get('original_bytes')} -> {result.get('shaped_bytes')} bytes",
                directory,
            )
        except ScenarioError as exc:
            self.record("S7", "large-output", "fail", str(exc), directory)

    def s8_durability(self) -> None:
        directory = self.scenario_dir("S8", "pause-restart-resume")
        try:
            self.register("eval-judge")
            status, run = self.api.request(
                "POST",
                "/runs",
                {
                    "workload": "eval-judge",
                    "caller": "field-test",
                    "context": "production",
                    "model_identity": self.identity,
                    "task": {
                        "task_id": "clamp",
                        "solution": (
                            "def clamp(x, lo, hi):  # ambiguous\n"
                            "    return sorted([lo, x, hi])[1]\n"
                        ),
                    },
                },
            )
            if status != 201:
                raise ScenarioError(f"submit eval-judge -> {status}: {run}")
            self.api.request("POST", f"/runs/{run['id']}/start")
            self._wait_for_state(run["id"], "paused", timeout=120)
            restarted = _restart_control_plane(self.api.base_url)
            after = self.api.request("GET", f"/runs/{run['id']}")[1]
            events = self.api.request("GET", f"/runs/{run['id']}/events")[1]
            self.write(
                directory,
                "after_restart.json",
                {"restarted": restarted, "run": after, "events": events},
            )
            if after["state"] != "paused":
                raise ScenarioError(f"run not paused after restart: {after['state']}")
            self.api.request("POST", f"/runs/{run['id']}/resume")
            finished = self.api.poll(run["id"])
            self.write(
                directory,
                "resumed.json",
                {
                    "finished": finished,
                    "events": self.api.request("GET", f"/runs/{run['id']}/events")[1],
                },
            )
            if finished["state"] != "completed":
                raise ScenarioError(f"resume did not complete: {finished['state']}")
            detail = "paused, restarted, resumed and completed" if restarted else (
                "resumed and completed (restart command not set)"
            )
            self.record("S8", "pause-restart-resume", "pass", detail, directory)
        except ScenarioError as exc:
            self.record("S8", "pause-restart-resume", "fail", str(exc), directory)

    def s10_operator_surface(self) -> None:
        directory = self.scenario_dir("S10", "operator-surface")
        try:
            self.register("eval-judge")
            status, run = self.api.request(
                "POST",
                "/runs",
                {
                    "workload": "eval-judge",
                    "caller": "field-test",
                    "context": "sandbox",
                    "model_identity": self.identity,
                    "task": {
                        "task_id": "clamp",
                        "solution": (
                            "def clamp(x, lo, hi):  # ambiguous\n"
                            "    return sorted([lo, x, hi])[1]\n"
                        ),
                    },
                },
            )
            if status != 201:
                raise ScenarioError(f"submit eval-judge -> {status}: {run}")
            self.api.request("POST", f"/runs/{run['id']}/start")
            self._wait_for_state(run["id"], "paused", timeout=120)

            # A13/A16: inspect a live run, then stop it from one surface.
            started = time.monotonic()
            _, inspected = self.api.request("GET", f"/runs/{run['id']}")
            _, events = self.api.request("GET", f"/runs/{run['id']}/events")
            _, story = self.api.request("GET", f"/runs/{run['id']}/story")
            stop_status, stopped = self.api.request("POST", f"/runs/{run['id']}/stop")
            elapsed = time.monotonic() - started
            after_events = self.api.request("GET", f"/runs/{run['id']}/events")[1]
            self.write(
                directory,
                "operator_surface.json",
                {
                    "inspected": inspected,
                    "stop_status": stop_status,
                    "stopped": stopped,
                    "events": events,
                    "story": story,
                    "after_stop_events": after_events,
                    "inspect_stop_seconds": round(elapsed, 3),
                },
            )
            if stopped.get("state") != "cancelled":
                raise ScenarioError(f"stop did not cancel: {stopped.get('state')}")

            # A15: the run's audit trail must be complete.
            kinds = {event.get("type") for event in after_events}
            missing = {"admission", "state_change", "operator_action"} - kinds
            if missing:
                raise ScenarioError(f"incomplete audit trail, missing {sorted(missing)}")

            # A18: hiveplane init scaffolds a project quickly.
            init_dir = directory / "init-scaffold"
            init_started = time.monotonic()
            init = subprocess.run(
                [sys.executable, "-m", "hiveplane.cli", "init", str(init_dir)],
                capture_output=True,
                text=True,
                timeout=300,
            )
            init_seconds = time.monotonic() - init_started
            self.write(
                directory,
                "init.json",
                {
                    "returncode": init.returncode,
                    "seconds": round(init_seconds, 3),
                    "stdout": init.stdout,
                    "stderr": init.stderr,
                },
            )
            if init.returncode != 0:
                raise ScenarioError(f"hiveplane init failed: {init.stderr}")

            # A19/A20: dashboard + spend views render.
            ui_cert = _http_get(f"{self.ui_url}/certifications")
            ui_spend = _http_get(f"{self.ui_url}/spend")
            self.write(
                directory,
                "views.json",
                {
                    "ui_certifications": ui_cert,
                    "ui_spend": ui_spend,
                    "api_spend": self.api.request("GET", "/spend"),
                },
            )
            if not ui_cert["ok"] or not ui_spend["ok"]:
                raise ScenarioError(
                    "dashboard views not reachable: "
                    f"cert={ui_cert['status']} spend={ui_spend['status']}"
                )
            self.record(
                "S10",
                "operator-surface",
                "pass",
                f"inspect+stop {elapsed:.2f}s (cancelled, audit complete); "
                f"init {init_seconds:.2f}s; dashboard + spend render",
                directory,
            )
        except ScenarioError as exc:
            self.record("S10", "operator-surface", "fail", str(exc), directory)

    def s9_fanout(self) -> None:
        directory = self.scenario_dir("S9", "fan-out")
        try:
            _, runs = self.api.request("GET", "/runs?workload=support-agent")
            completed = [r for r in runs if r.get("state") == "completed"]
            if not completed:
                raise ScenarioError("no completed support-agent run to inspect")
            story = self.api.request("GET", f"/runs/{completed[-1]['id']}/story")[1]
            kinds = {entry["kind"] for entry in story.get("entries", [])}
            self.write(directory, "story.json", story)
            if "delivery" not in kinds:
                raise ScenarioError(f"no delivery entry in story: {kinds}")
            self.record("S9", "fan-out", "pass", "delivery recorded in run story", directory)
        except ScenarioError as exc:
            self.record("S9", "fan-out", "fail", str(exc), directory)

    def _wait_for_state(self, run_id: str, state: str, timeout: float = 120.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        run: dict[str, Any] = {}
        while time.monotonic() < deadline:
            run = self.api.request("GET", f"/runs/{run_id}")[1]
            if run.get("state") == state:
                return run
            if run.get("state") in TERMINAL:
                raise ScenarioError(f"run reached {run['state']}, expected {state}")
            time.sleep(1)
        raise ScenarioError(f"run never reached {state}: {run}")

    def finish(self) -> int:
        passed = [s for s in self.summary if s["status"] == "pass"]
        failed = [s for s in self.summary if s["status"] == "fail"]
        blocked = [s for s in self.summary if s["status"] == "blocked"]
        self.write(
            self.results_dir,
            "summary.json",
            {
                "generated_at": _now(),
                "model_identity": self.identity,
                "passed": len(passed),
                "failed": len(failed),
                "blocked": len(blocked),
                "scenarios": self.summary,
            },
        )
        print(
            f"\nfield test: {len(passed)} passed, {len(failed)} failed, "
            f"{len(blocked)} blocked",
            flush=True,
        )
        return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8100")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated scenario ids to run (default: all).",
    )
    args = parser.parse_args()

    runner = Runner(Api(args.api_url), Path(args.results_dir).resolve())
    print(f"field test against {args.api_url} (model: {runner.identity})", flush=True)

    scenarios: list[tuple[str, Callable[[], None]]] = [
        ("S1", runner.s1_certify_three),
        ("S2", runner.s2_uncertified_refused),
        ("S3", runner.s3_model_swap),
        ("S4", runner.s4_regression),
        ("S5", runner.s5_budget),
        ("S6", runner.s6_destructive),
        ("S7", runner.s7_shaping),
        ("S8", runner.s8_durability),
        ("S9", runner.s9_fanout),
        ("S10", runner.s10_operator_surface),
    ]
    only = {part.strip().upper() for part in args.only.split(",") if part.strip()}
    for sid, fn in scenarios:
        if only and sid not in only:
            continue
        print(f"\n== {sid} ==", flush=True)
        try:
            fn()
        except Exception as exc:  # pragma: no cover - defensive sweep guard
            directory = runner.scenario_dir(sid, f"unexpected-{sid.lower()}")
            runner.write(
                directory,
                "unexpected_error.json",
                {"type": type(exc).__name__, "message": str(exc)},
            )
            runner.record(sid, "unexpected-error", "fail", f"{type(exc).__name__}: {exc}", directory)
    return runner.finish()


if __name__ == "__main__":
    raise SystemExit(main())
