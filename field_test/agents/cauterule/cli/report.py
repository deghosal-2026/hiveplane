"""CLI command: cauterule report --safety-adjusted."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from cauterule.benchmark.safety_ranking import ModelResult, rank_by_safety_adjusted, rank_by_total


@click.command("report")
@click.option(
    "--safety-adjusted",
    is_flag=True,
    help="Produce safety-adjusted model ranking from field-test summary files",
)
@click.option(
    "--results-dir",
    default="field-test/results/0.2.0",
    help="Directory containing per-corpus run subdirectories with summary.json files",
)
def report(safety_adjusted: bool, results_dir: str) -> None:
    """Generate field-test reports."""
    click.echo("# CauterRule Report")
    click.echo("")
    if safety_adjusted:
        _build_safety_adjusted_ranking(results_dir)
    else:
        click.echo("Pass --safety-adjusted to generate a safety-adjusted model ranking.")
        click.echo(f"Results directory: {results_dir}")


def _build_safety_adjusted_ranking(results_dir: str) -> None:
    """Walk run directories and produce safety-adjusted ranking table."""
    root = Path(results_dir)
    if not root.is_dir():
        print(f"Error: results directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    # Collect summary.json files from run directories
    # Structure: {results_dir}/{corpus_type}/{llm_label}/{date}/summary.json
    model_results: dict[str, ModelResult] = {}
    llm_labels: set[str] = set()

    for summary_path in root.rglob("summary.json"):
        try:
            data = json.loads(summary_path.read_text())
        except Exception as exc:
            print(f"  [warn] skipping {summary_path}: {exc}", file=sys.stderr)
            continue

        meta = data.get("meta", {})
        corpus_type = meta.get("corpus_type", "")
        llm_provider = meta.get("llm_provider", "?")
        llm_model = meta.get("llm_model", "?")
        llm_label = f"{llm_provider}/{llm_model}"
        llm_labels.add(llm_label)

        passing = data.get("passing", 0)
        safety = data.get("safety", {})
        successes_pass = (
            safety.get("accepted", 0) if corpus_type in ("successes", "failures/negative") else 0
        )
        failures_neg_pass = safety.get("accepted", 0) if corpus_type == "failures/negative" else 0
        inconclusive = data.get("inconclusive", 0)

        if llm_label not in model_results:
            model_results[llm_label] = ModelResult(
                model=llm_label,
                total_pass=0,
                successes_pass=0,
                failures_negative_pass=0,
                inconclusive=0,
            )

        mr = model_results[llm_label]
        from dataclasses import replace

        model_results[llm_label] = replace(
            mr,
            total_pass=mr.total_pass + passing,
            successes_pass=mr.successes_pass + successes_pass,
            failures_negative_pass=mr.failures_negative_pass + failures_neg_pass,
            inconclusive=mr.inconclusive + inconclusive,
        )

    if not model_results:
        print("No summary.json files found under", results_dir)
        return

    # Rankings
    by_total = rank_by_total(list(model_results.values()))
    by_safety = rank_by_safety_adjusted(list(model_results.values()))

    # Build the report as a string first so it can be both printed and saved
    # verbatim (#791: a real terminal has no sys.stdout.getvalue()).
    lines: list[str] = []
    lines.append("")
    lines.append("## Safety-Adjusted Model Ranking")
    lines.append("")
    header = (
        "| Model | Total Pass | Safety-Adjusted Pass | Violation Rate | "
        "Inconclusive | Rank (Total) | Rank (Safety) |"
    )
    sep = (
        "|-------|-----------|---------------------|----------------|"
        "-------------|-------------|--------------|"
    )
    lines.append(header)
    lines.append(sep)

    rank_total_map = {m.model: i + 1 for i, m in enumerate(by_total)}
    rank_safety_map = {m.model: i + 1 for i, m in enumerate(by_safety)}

    for m in sorted(model_results.values(), key=lambda x: x.safety_adjusted_pass, reverse=True):
        vr = f"{m.safety_violation_rate * 100:.1f}%"
        lines.append(
            f"| {m.model} | {m.total_pass} | {m.safety_adjusted_pass} | {vr} | {m.inconclusive} "
            f"| #{rank_total_map.get(m.model, '?')} | #{rank_safety_map.get(m.model, '?')} |"
        )

    lines.append("")

    # Pairwise comparison
    if len(model_results) >= 2:
        models = list(model_results.values())
        lines.append("## Decision Economics (Model Pairs)")
        lines.append("")
        lines.append("| Baseline → New | Resolved | New Pass | New Fail | Wrong-Decision Rate |")
        lines.append("|----------------|----------|----------|----------|---------------------|")
        for i, baseline in enumerate(models):
            for j in range(i + 1, len(models)):
                new = models[j]
                from cauterule.benchmark.safety_ranking import decision_economics

                econ = decision_economics(
                    baseline_inconclusive=baseline.inconclusive,
                    new_pass=(
                        new.total_pass - baseline.total_pass
                        if new.total_pass > baseline.total_pass
                        else 0
                    ),
                    new_fail=(
                        baseline.total_pass - new.total_pass
                        if new.total_pass < baseline.total_pass
                        else 0
                    ),
                )
                wrong = f"{econ['wrong_decision_rate'] * 100:.1f}%"
                lines.append(
                    f"| {baseline.model} → {new.model} | {econ['resolved']} | "
                    f"{econ['new_pass']} | {econ['new_fail']} | {wrong} |"
                )
        lines.append("")

    output = "\n".join(lines)
    print(output)

    # Save the same string that was printed.
    output_path = root / "safety-ranking.md"
    output_path.write_text(output + "\n", encoding="utf-8")
    print(f"\nRanking saved to: {output_path}")
