from __future__ import annotations
import argparse
import datetime
import os
import random
import sqlite3
import sys
import uuid
from collections import Counter
from typing import Any

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from langgraph.checkpoint.sqlite import SqliteSaver
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from tools.config import load_config
from tools.session import save_session, list_incomplete_sessions, mark_completed
from llm.logger import init_logger
from graph import build_graph


DEMO_REPOS = [
    "angular/angular",
    "facebook/react",
    "nodejs/node",
    "vercel/next.js",
    "microsoft/vscode",
    "docker/compose",
    "prometheus/prometheus",
    "grafana/grafana",
    "kubernetes/kubernetes",
    "hashicorp/terraform",
]


def _fetch_tags(repo: str, token: str | None) -> list[str]:
    import httpx
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = httpx.get(f"https://api.github.com/repos/{repo}/tags", headers=headers, timeout=15.0, follow_redirects=True)
    resp.raise_for_status()
    return [t["name"] for t in resp.json()]


def _random_repo(token: str | None) -> dict[str, Any]:
    repo = random.choice(DEMO_REPOS)
    try:
        tags = _fetch_tags(repo, token)
    except Exception as e:
        print(f"Warning: could not fetch tags for {repo}: {e}", file=sys.stderr)
        tags = []
    if len(tags) >= 2:
        idx = random.randint(0, len(tags) - 2)
        return {"repo": repo, "base_tag": tags[idx + 1], "head_tag": tags[idx]}
    return {"repo": repo, "base_tag": "", "head_tag": ""}


def _auto_tags(repo: str, token: str | None) -> dict[str, Any]:
    try:
        tags = _fetch_tags(repo, token)
    except Exception as e:
        print(f"Warning: could not fetch tags for {repo}: {e}", file=sys.stderr)
        return {"base_tag": "", "head_tag": ""}
    if len(tags) >= 2:
        return {"base_tag": tags[1], "head_tag": tags[0]}
    return {"base_tag": "", "head_tag": ""}


def _random_dates() -> tuple[str, str]:
    now = datetime.datetime.now(datetime.timezone.utc)
    head = now - datetime.timedelta(days=random.randint(0, 3))
    base = head - datetime.timedelta(days=random.randint(1, 14))
    return base.strftime("%Y-%m-%d"), head.strftime("%Y-%m-%d")


def _download_one(repo: str, base_tag: str, head_tag: str, token: str, max_commits: int) -> bool:
    from tools.github import GitHubClient
    from tools.parser import detect_tag_convention
    import tools.cache as cache

    if cache.load_commits(repo, base_tag, head_tag) is not None:
        print(f"    ✓ Already cached: {base_tag}...{head_tag}", file=sys.stderr)
        return True

    with GitHubClient(token=token) as gh:
        count_data = gh._request(
            "GET", f"/repos/{repo}/compare/{base_tag}...{head_tag}",
            params={"per_page": 1, "page": 1},
        )
        if count_data is None:
            print(f"    ✗ Tags not found: {base_tag}...{head_tag}", file=sys.stderr)
            return False
        total = count_data.get("total_commits", 0)
        if total > max_commits:
            print(f"    ⚠ {base_tag}...{head_tag}: {total} commits (>{max_commits}, skipping)", file=sys.stderr)
            return False

        print(f"    ↓ {base_tag}...{head_tag}: {total} commits", file=sys.stderr)
        commits_data = gh.fetch_commits(repo, base_tag, head_tag)
        cache.cache_commits(repo, base_tag, head_tag, commits_data)

        if cache.load_releases(repo) is None:
            releases = gh.fetch_releases(repo)
            cache.cache_releases(repo, releases)
            print(f"    ↓ releases: {len(releases)}", file=sys.stderr)

        if cache.load_readme(repo) is None:
            for candidate in ["README.md", "README", "readme.md", "README.rst", "README.markdown"]:
                readme = gh.fetch_file(repo, candidate)
                if readme is not None:
                    cache.cache_readme(repo, readme)
                    print(f"    ↓ {candidate}", file=sys.stderr)
                    break

        if cache.load_tag_convention(repo) is None:
            tags = gh.fetch_tags(repo)
            convention = detect_tag_convention(tags)
            cache.cache_tag_convention(repo, convention)
            print(f"    ↓ tag convention: {convention}", file=sys.stderr)

        print(f"    ✓ Cached {repo} {base_tag}...{head_tag} ({total} commits)", file=sys.stderr)
        return True


def _download_all(token: str, ranges: int, max_commits: int) -> None:
    repos = list(reversed(DEMO_REPOS))
    total = len(repos)
    downloaded = 0
    for i, repo in enumerate(repos):
        print(f"\n[{i+1}/{total}] {repo}", file=sys.stderr)
        try:
            tags = _fetch_tags(repo, token)
        except Exception as e:
            print(f"  Skipping: {e}", file=sys.stderr)
            continue
        if len(tags) < 2:
            print(f"  Skipping: not enough tags", file=sys.stderr)
            continue
        done = 0
        j = 0
        while j < len(tags) - 1 and done < ranges:
            base_tag, head_tag = tags[j + 1], tags[j]
            if _download_one(repo, base_tag, head_tag, token, max_commits):
                done += 1
                downloaded += 1
            j += 1
        print(f"  Downloaded {done}/{ranges} ranges for {repo}", file=sys.stderr)
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Downloaded {downloaded} ranges across {total} repos", file=sys.stderr)



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release-narrator",
        description="Generate release notes from conventional commits using LangGraph multi-agent pipeline.",
        epilog=(
            "Examples:\n"
            "  release-narrator --repo angular/angular --base v18.0.0 --head v18.1.0\n"
            "  release-narrator --audit --repo angular/angular --last 10\n"
            "  release-narrator --resume\n"
            "  release-narrator --random\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo", help="Repository in owner/name format")
    parser.add_argument("--base", help="Base tag for commit range")
    parser.add_argument("--head", help="Head tag for commit range")
    parser.add_argument("--audit", action="store_true", help="Run audit mode on last N releases")
    parser.add_argument("--last", type=int, default=10, help="Number of releases to audit (default: 10)")
    parser.add_argument("--resume", nargs="?", const=True, default=False, help="Resume interrupted session (optionally with thread ID)")
    parser.add_argument("--random", action="store_true", help="Pick a random repo and tag range")
    parser.add_argument("--all", action="store_true", help="Run all 10 repos × 5 tag ranges (field study)")
    parser.add_argument("--list-repos", action="store_true", help="List repos available for --random")
    parser.add_argument("--no-cache", action="store_true", help="Bypass cache and fetch fresh data")
    parser.add_argument("--reprocess", action="store_true", help="Re-process cached data (skip fetch, use saved commits)")
    parser.add_argument("--reprocess-all", action="store_true", help="Reprocess ALL cached repos in data/")
    parser.add_argument("--reprocess-repo", help="Reprocess all cached ranges for a single repo (e.g. hashicorp/terraform)")
    parser.add_argument("--max-ranges", type=int, default=0, help="Max ranges per repo for reprocess (0=unlimited)")
    parser.add_argument("--download", action="store_true", help="Download + cache data only (no LLM). Use with --all or --repo.")
    parser.add_argument("--ranges", type=int, default=3, help="Tag ranges per repo for --all/--download (default: 3)")
    parser.add_argument("--max-commits", type=int, default=200, help="Skip ranges exceeding this commit count (default: 200)")
    parser.add_argument("--yes", "-y", action="store_true", help="Auto-approve all interrupts (non-interactive)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--config", help="Path to config.yaml")
    return parser


def _resolve_resume(args: argparse.Namespace) -> tuple[str, dict[str, Any]] | None:
    sessions = list_incomplete_sessions()
    if not sessions:
        print("No interrupted sessions found.", file=sys.stderr)
        return None
    if isinstance(args.resume, str):
        tid = args.resume
        cfg = next((s["config"] for s in sessions if s["thread_id"] == tid), None)
        if cfg is None:
            print(f"Session {tid} not found.", file=sys.stderr)
            return None
        return tid, cfg
    latest = sessions[0]
    print(f"Resuming session {latest['thread_id']}", file=sys.stderr)
    return latest["thread_id"], latest["config"]


def _run_one(graph, cfg, repo, base_tag, head_tag, mode, no_cache, args, preload: dict | None = None) -> str:
    thread_id = str(uuid.uuid4())
    state: dict[str, Any] = {
        "repo": repo,
        "base_tag": base_tag,
        "head_tag": head_tag,
        "mode": mode,
        "no_cache": no_cache,
        "errors": [],
    }
    if preload:
        state.update(preload)

    save_session(thread_id, {
        "repo": repo,
        "base_tag": base_tag,
        "head_tag": head_tag,
        "mode": mode,
        "no_cache": no_cache,
        "_config": cfg,
    })

    config = {"configurable": {"thread_id": thread_id}}
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"Session ID: {thread_id}", file=sys.stderr)
    print(f"Repo: {repo} {base_tag}...{head_tag} (mode={mode})", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    from langgraph.types import Command

    initial_input = state
    while True:
        interrupted = False
        for event in graph.stream(initial_input, config):
            if isinstance(event, dict):
                if "__interrupt__" in event:
                    interrupted = True
                    interrupt_data = event["__interrupt__"]
                    if isinstance(interrupt_data, tuple) and interrupt_data:
                        for item in interrupt_data:
                            content = item.value if hasattr(item, "value") else str(item)
                            print(content, file=sys.stderr)
                    elif isinstance(interrupt_data, str):
                        print(interrupt_data, file=sys.stderr)
                    break
                for node_name, output in event.items():
                    if isinstance(output, dict) and output.get("errors"):
                        for err in output["errors"]:
                            print(f"Error: {err}", file=sys.stderr)

        if not interrupted:
            break

        if args.yes:
            user_input = ""
        else:
            print("\n> ", end="", flush=True)
            try:
                user_input = input()
            except (EOFError, KeyboardInterrupt):
                print("\nSession paused. Resume with: python main.py --resume", file=sys.stderr)
                return thread_id

        initial_input = Command(resume=user_input)

    mark_completed(thread_id)
    print(f"Done: {repo}", file=sys.stderr)
    return thread_id


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    jsonl_path = init_logger(verbose=args.verbose)
    print(f"Log: {jsonl_path}", file=sys.stderr)

    if args.resume:
        result = _resolve_resume(args)
        if result is None:
            sys.exit(1)
        thread_id, session_config = result
        cfg = session_config["_config"]
        repo = session_config.get("repo", "")
        base_tag = session_config.get("base_tag", "")
        head_tag = session_config.get("head_tag", "")
        mode = session_config.get("mode", "generate")
        no_cache = session_config.get("no_cache", False)
        print(f"Resuming {repo} {base_tag}...{head_tag} (mode={mode})", file=sys.stderr)
    else:
        cfg = load_config(args.config)
        mode = "audit" if args.audit else "generate"
        token = cfg["github_token"]

    if args.download:
        if not token:
            print("Error: GitHub token required for --download", file=sys.stderr)
            sys.exit(1)
        _download_all(token, args.ranges, args.max_commits)
        return

    os.environ.setdefault("GITHUB_TOKEN", cfg["github_token"])
    os.environ.setdefault("LLM_BASE_URL", cfg.get("llm_base_url", "http://127.0.0.1:8000/v1"))
    os.environ.setdefault("LLM_API_KEY", cfg.get("llm_api_key", ""))
    os.environ.setdefault("LLM_MODEL", cfg.get("llm_model", "gemma-3-12b-it-qat-4bit"))
    if cfg.get("output_dir"):
        os.environ["OUTPUT_DIR"] = cfg["output_dir"]

    os.makedirs("logs", exist_ok=True)
    conn = sqlite3.connect("logs/checkpoints.db", check_same_thread=False)
    graph = build_graph(mode=mode, checkpointer=SqliteSaver(conn))

    if args.resume:
        _run_one(graph, cfg, repo, base_tag, head_tag, mode, no_cache, args)
    elif args.reprocess or args.reprocess_all or args.reprocess_repo:
        from tools.parser import parse_conventional_commit
        import tools.cache as cache

        def _load_reprocess(repo: str, base_tag: str, head_tag: str) -> dict | None:
            raw = cache.load_commits(repo, base_tag, head_tag)
            if raw is None:
                return None
            commits = []
            for c in raw:
                parsed = parse_conventional_commit(c.get("commit", {}).get("message", ""))
                parsed["sha"] = c.get("sha", "")
                author_info = c.get("commit", {}).get("author", {})
                if author_info:
                    parsed["author"] = author_info.get("name", "")
                    parsed["date"] = author_info.get("date", "")
                commits.append(parsed)
            return {
                "commits": commits,
                "releases": cache.load_releases(repo) or [],
                "readme_content": cache.load_readme(repo) or "",
                "repo_tag_convention": cache.load_tag_convention(repo) or "",
            }

        if args.reprocess_all or args.reprocess_repo:
            import glob
            total = 0
            completed = 0
            per_repo = Counter()
            pattern = f"data/{args.reprocess_repo.replace('/', '-')}/commits/*.json" if args.reprocess_repo else "data/*/commits/*.json"
            for p in sorted(glob.glob(pattern)):
                parts = p.split("/")
                repo_dir = parts[1]
                fname = os.path.splitext(parts[3])[0]
                if "..." not in fname:
                    continue
                base_tag, head_tag = fname.split("...", 1)
                repo = repo_dir.replace("-", "/", 1)
                if args.max_ranges and per_repo[repo] >= args.max_ranges:
                    continue
                preload = _load_reprocess(repo, base_tag, head_tag)
                if preload is None:
                    continue
                total += 1
                try:
                    _run_one(graph, cfg, repo, base_tag, head_tag, mode, True, args, preload=preload)
                    completed += 1
                    per_repo[repo] += 1
                except Exception as e:
                    print(f"  Error: {e}", file=sys.stderr)
            print(f"\nDone: {completed}/{total} reprocessed", file=sys.stderr)
        else:
            repo = args.repo
            base_tag = args.base
            head_tag = args.head
            if not repo or not base_tag or not head_tag:
                print("Error: --repo, --base, and --head required for --reprocess", file=sys.stderr)
                sys.exit(1)
            preload = _load_reprocess(repo, base_tag, head_tag)
            if preload is None:
                cache_path = f"data/{repo.replace('/', '-')}/commits/{base_tag}...{head_tag}.json"
                print(f"Error: no cached data at {cache_path}", file=sys.stderr)
                sys.exit(1)
            _run_one(graph, cfg, repo, base_tag, head_tag, mode, True, args, preload=preload)
    elif args.random:
        run_count = 0
        print("Random mode: picking repos at random. Ctrl+C to stop.", file=sys.stderr)
        while True:
            repo = random.choice(DEMO_REPOS)
            try:
                tags = _fetch_tags(repo, token)
            except Exception as e:
                print(f"  Skipping {repo}: {e}", file=sys.stderr)
                continue
            if len(tags) < 2:
                print(f"  Skipping {repo}: not enough tags", file=sys.stderr)
                continue
            idx = random.randint(0, min(4, len(tags) - 2))
            base_tag = tags[idx + 1]
            head_tag = tags[idx]
            run_count += 1
            print(f"\n[Random run #{run_count}] {repo} {base_tag}...{head_tag}", file=sys.stderr)
            try:
                _run_one(graph, cfg, repo, base_tag, head_tag, mode, args.no_cache, args)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                print(f"  Error: {e}", file=sys.stderr)
                continue
    elif args.all:
        random.shuffle(DEMO_REPOS)
        total = len(DEMO_REPOS)
        completed = 0
        for i, repo in enumerate(DEMO_REPOS):
            print(f"\n[{i+1}/{total}] Processing {repo}", file=sys.stderr)
            try:
                tags = _fetch_tags(repo, token)
            except Exception as e:
                print(f"  Skipping {repo}: {e}", file=sys.stderr)
                continue
            if len(tags) < 2:
                print(f"  Skipping {repo}: not enough tags", file=sys.stderr)
                continue
            tag_pairs = []
            for j in range(min(5, len(tags) - 1)):
                tag_pairs.append((tags[j + 1], tags[j]))
            for base_tag, head_tag in tag_pairs:
                print(f"  Range: {base_tag}...{head_tag}", file=sys.stderr)
                try:
                    _run_one(graph, cfg, repo, base_tag, head_tag, mode, args.no_cache, args)
                    completed += 1
                except Exception as e:
                    print(f"  Error: {e}", file=sys.stderr)
                    continue
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Completed {completed} runs across {total} repos", file=sys.stderr)
    else:
        repo = args.repo or os.environ.get("REPO")
        base_tag = args.base or os.environ.get("BASE_TAG")
        head_tag = args.head or os.environ.get("HEAD_TAG")
        if repo and (not base_tag or not head_tag):
            r = _auto_tags(repo, cfg["github_token"])
            base_tag = base_tag or r["base_tag"]
            head_tag = head_tag or r["head_tag"]
            print(f"Auto-detected tags: {base_tag}...{head_tag}", file=sys.stderr)
        if not repo:
            print("Error: --repo is required (or set REPO env var)", file=sys.stderr)
            sys.exit(1)
        _run_one(graph, cfg, repo, base_tag, head_tag, mode, args.no_cache, args)

    conn.close()


if __name__ == "__main__":
    main()