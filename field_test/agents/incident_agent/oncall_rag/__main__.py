from __future__ import annotations

import argparse
import logging
from pathlib import Path

from oncall_rag.config import load_config, Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("oncall-rag")


def run_experiment(config: Config, corpus_path: str, eval_path: str = "data/eval.json") -> dict:
    strategies = ["recursive", "header-split", "fixed-size"]
    results = {}
    for strategy in strategies:
        print(f"\n--- Strategy: {strategy} ---")
        cfg = config.model_copy()
        cfg.chunking_strategy = strategy
        safe = strategy.replace("-", "_")
        cfg.chroma_path = f"data/chroma_db_{safe}"

        from oncall_rag.indexer import index_documents
        index_documents(corpus_path, cfg, rebuild=True)

        from oncall_rag.eval import run_eval
        metrics = run_eval(cfg, eval_path)
        print(f"  MRR: {metrics['mrr']:.4f}, Recall@5: {metrics['recall_at_5']:.4f}")
        results[strategy] = {"mrr": metrics["mrr"], "recall_at_5": metrics["recall_at_5"]}
    return results


def main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser(prog="oncall-rag", description="Runbook RAG assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("discord", help="Start the Discord bot")
    sub.add_parser("dashboard", help="Start the Streamlit dashboard")
    sub.add_parser("process-alerts", help="Process alerts from alerts/inbox/ through RAG pipeline")

    index_parser = sub.add_parser("index", help="Ingest markdown files into ChromaDB")
    index_parser.add_argument("path", help="Directory of markdown files")
    index_parser.add_argument("--rebuild", action="store_true", help="Drop and recreate collection")

    query_parser = sub.add_parser("query", help="Ask a question from runbooks")
    query_parser.add_argument("question", help="Your question")

    sub.add_parser("eval", help="Run eval set and print metrics")

    exp_parser = sub.add_parser("chunking-experiment", help="Compare chunking strategies on eval set")
    exp_parser.add_argument("path", help="Directory of markdown files")
    exp_parser.add_argument("--eval", default="data/eval.json", help="Eval set path")
    exp_parser.add_argument(
        "--output",
        default="docs/chunking-experiment-results.md",
        help="Output report path (raw results; the analysis lives in chunking-experiment.md)",
    )

    args = parser.parse_args()

    if args.command == "index":
        from oncall_rag.indexer import index_documents
        result = index_documents(args.path, config, rebuild=args.rebuild)
        skipped = result.get("files_skipped", 0)
        msg = f"Indexed {result['files_indexed']} files, {result['chunks_created']} chunks"
        if skipped:
            msg += f" ({skipped} already indexed, skipped)"
        print(msg)

    elif args.command == "query":
        from oncall_rag.responder import query_runbooks
        response = query_runbooks(args.question, config)
        print(response)

    elif args.command == "eval":
        from oncall_rag.eval import run_eval
        results = run_eval(config)
        if "error" in results:
            logger.warning(results["error"])
        print(f"MRR: {results['mrr']:.3f}, Recall@5: {results['recall_at_5']:.3f}")

    elif args.command == "discord":
        if not config.discord_bot_token:
            print("DISCORD_BOT_TOKEN is not set. Add it to .env or set the environment variable.")
            return
        from oncall_rag.bot import OncallBot
        bot = OncallBot(config)
        bot.run(config.discord_bot_token, log_handler=None)

    elif args.command == "process-alerts":
        from oncall_rag.alert_processor import process_alerts
        result = process_alerts(config)
        print(f"Processed {result['processed']} alerts ({result['errors']} errors)")
        if result["processed"]:
            outbox = Path("alerts/outbox")
            files = list(outbox.glob("*.json"))
            print(f"Results written to alerts/outbox/ ({len(files)} file(s))")

    elif args.command == "dashboard":
        import sys
        from streamlit.web import cli as st_cli
        dashboard_path = str(Path(__file__).resolve().parent / "dashboard" / "app.py")
        sys.exit(st_cli.main(["run", dashboard_path]))

    elif args.command == "chunking-experiment":
        results = run_experiment(config, args.path, args.eval)
        print("\n" + "=" * 50)
        print("CHUNKING EXPERIMENT RESULTS")
        print("=" * 50)
        print(f"{'Strategy':<20} {'MRR':<10} {'Recall@5':<10}")
        print("-" * 40)
        for strategy, metrics in results.items():
            print(f"{strategy:<20} {metrics['mrr']:<10.4f} {metrics['recall_at_5']:<10.4f}")
        with open(args.output, "w") as f:
            f.write(generate_report(results))
        print(f"\nRaw results written to {args.output}")
        print("See docs/chunking-experiment.md for full analysis.")


def generate_report(results: dict) -> str:
    lines = []
    lines.append("# Chunking Experiment — Raw Results\n")
    lines.append("Generated by `oncall-rag chunking-experiment`. For full analysis see `chunking-experiment.md`.\n")
    lines.append("| Strategy | MRR | Recall@5 |")
    lines.append("|---|---|---|")
    best = max(results, key=lambda s: results[s]["mrr"])
    for strategy, metrics in results.items():
        star = " ★" if strategy == best else ""
        lines.append(f"| {strategy}{star} | {metrics['mrr']:.4f} | {metrics['recall_at_5']:.4f} |")
    lines.append(f"\n**Best MRR:** `{best}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
