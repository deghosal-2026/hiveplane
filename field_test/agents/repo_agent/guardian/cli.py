import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="guardian")
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect", help="Run analysis on a PR or commit")
    detect.add_argument("--token", default=None)
    detect.add_argument("--repo", default=None)
    detect.add_argument("--pr", type=int, default=None)
    detect.add_argument("--commit", default=None)
    detect.add_argument("--policy", default="guardian-policy.yml")
    detect.add_argument("--audit-dir", default="~/.guardian/audit")
    detect.add_argument("--verbose", action="store_true")

    serve = sub.add_parser("serve", help="Start the local dashboard server")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--audit-dir", default="~/.guardian/audit")

    check_config = sub.add_parser("check-config", help="Validate a policy file")
    check_config.add_argument("--policy", default="guardian-policy.yml")

    sub.add_parser("generate-pages", help="Build static dashboard HTML")
    sub.add_parser("version", help="Print version")

    args = parser.parse_args()

    if args.command == "version":
        from importlib.metadata import version
        print(f"ai-code-guardian v{version('ai-code-guardian')}")
        sys.exit(0)

    if args.command == "detect":
        from guardian.action import run_detect
        sys.exit(run_detect(args))

    if args.command == "serve":
        _cmd_serve(args.port, args.audit_dir)
        sys.exit(0)

    if args.command == "check-config":
        _cmd_check_config(args.policy)
        sys.exit(0)

    if args.command == "generate-pages":
        _cmd_generate_pages()
        sys.exit(0)


def _cmd_serve(port: int, audit_dir: str) -> None:
    try:
        import uvicorn
        from guardian.server.app import app
        from guardian.audit.logger import AuditLogger
    except ImportError:
        print("Dashboard dependencies not installed. Install with: pip install 'ai-code-guardian[server]'")
        sys.exit(1)
    print(f"Starting Guardian dashboard on http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)


def _cmd_check_config(policy_path: str) -> None:
    from pathlib import Path
    if not Path(policy_path).exists():
        print(f"Policy file '{policy_path}' not found.")
        sys.exit(1)
    from guardian.config import load_policy
    try:
        policy = load_policy(policy_path)
        print(f"Policy file '{policy_path}' is valid.")
        print(f"  Mode: {policy.mode}")
        print(f"  Detection: {policy.detection}")
        print(f"  Rules ({len(policy.rules)}):")
        for rule in policy.rules:
            status = "enabled" if rule.enabled else "disabled"
            print(f"    - {rule.name} ({rule.severity}, {status})")
        sys.exit(0)
    except Exception as e:
        print(f"Policy file '{policy_path}' is INVALID: {e}")
        sys.exit(1)


def _cmd_generate_pages() -> None:
    print("guardian generate-pages — coming soon")
