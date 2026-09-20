import json
import logging
import os
import sys
import time
from argparse import Namespace

from guardian.config import GuardianConfig, load_policy
from guardian.detector.marker import analyze_diff
from guardian.pr.check_run import determine_conclusion, generate_check_run_output
from guardian.pr.client import GitHubClient
from guardian.pr.commentator import generate_comment
from guardian.pr.labeler import apply_risk_label
from guardian.reviewer.engine import evaluate
from guardian.audit.models import AuditEntry
from guardian.audit.logger import AuditLogger


def run_detect(args: Namespace) -> int:
    token = args.token or os.environ.get("GUARDIAN_TOKEN") or os.environ.get("INPUT_GITHUB_TOKEN", "")
    repo = args.repo or os.environ.get("GUARDIAN_REPO") or os.environ.get("GITHUB_REPOSITORY", "")

    pr_number = args.pr
    if pr_number is None and "GITHUB_EVENT_PATH" in os.environ:
        event = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
        if "pull_request" in event:
            event_name = os.environ.get("GITHUB_EVENT_NAME", "")
            if event_name in ("pull_request", "pull_request_target"):
                pr_number = event["pull_request"]["number"]

    commit_sha = args.commit or os.environ.get("GITHUB_SHA")

    policy_path = os.environ.get("GUARDIAN_POLICY_PATH") or args.policy
    audit_dir = os.environ.get("GUARDIAN_AUDIT_DIR") or args.audit_dir
    verbose = args.verbose or os.environ.get("GUARDIAN_DEBUG", "").lower() == "true"

    try:
        cfg = GuardianConfig(
            github_token=token,
            repo=repo,
            pr_number=pr_number,
            commit_sha=commit_sha,
            policy_path=policy_path,
            audit_dir=audit_dir,
            verbose=verbose,
        )
    except Exception as exc:
        logging.error("Configuration error: %s", exc)
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    if cfg.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    start = time.monotonic()

    try:
        return _run_analysis(cfg, start)
    except Exception as exc:
        logging.exception("Analysis failed")
        _log_error(cfg, exc, start)
        return 1


def _run_analysis(cfg: GuardianConfig, start: float) -> int:
    policy = load_policy(cfg.policy_path)

    client = GitHubClient(token=cfg.github_token, repo=cfg.repo)

    if cfg.pr_number:
        diff = client.get_pr_diff(cfg.pr_number)
    elif cfg.commit_sha:
        diff = client.get_commit_diff(cfg.commit_sha)
    else:
        raise ValueError("Either --pr or --commit must be provided")

    detections = analyze_diff(diff)
    review = evaluate(detections, policy, diff)

    if cfg.pr_number:
        comment_body = generate_comment(review, policy.mode)
        client.post_comment(cfg.pr_number, comment_body)

        apply_risk_label(client, cfg.pr_number, review.risk_level)

        stats = {
            "ai_file_count": sum(1 for d in detections if d.confidence != "low"),
            "total_file_count": len(detections) if detections else 0,
            "ai_pct": _compute_ai_pct(detections),
            "fpr": "N/A",
        }
        output = generate_check_run_output(review, stats)
        conclusion = determine_conclusion(review, policy.mode)
        client.create_check_run("AI Code Guardian", conclusion, output)

    duration_ms = int((time.monotonic() - start) * 1000)
    _log_audit(cfg, detections, review, duration_ms)

    if policy.mode == "enforcement" and review.risk_level == "critical":
        return 2
    return 0


def _compute_ai_pct(detections: list) -> float:
    if not detections:
        return 0.0
    ai_count = sum(1 for d in detections if d.confidence != "low")
    return round(ai_count / len(detections) * 100, 1)


def _log_audit(
    cfg: GuardianConfig,
    detections: list,
    review,
    duration_ms: int,
) -> None:
    try:
        logger = AuditLogger(repo=cfg.repo, audit_dir=cfg.audit_dir)
        entry = AuditEntry(
            repo=cfg.repo,
            pr_number=cfg.pr_number,
            commit_sha=cfg.commit_sha,
            action="analysis",
            detection_results=detections,
            violations=review.violations,
            risk_level=review.risk_level,
            duration_ms=duration_ms,
        )
        logger.append(entry)
    except Exception as exc:
        logging.warning("Failed to write audit log: %s", exc)


def _log_error(cfg: GuardianConfig, exc: Exception, start: float) -> None:
    try:
        logger = AuditLogger(repo=cfg.repo, audit_dir=cfg.audit_dir)
        duration_ms = int((time.monotonic() - start) * 1000)
        entry = AuditEntry(
            repo=cfg.repo,
            pr_number=cfg.pr_number,
            commit_sha=cfg.commit_sha,
            action="error",
            error=str(exc),
            duration_ms=duration_ms,
        )
        logger.append(entry)
    except Exception:
        pass
