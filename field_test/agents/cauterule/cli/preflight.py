"""CLI: cauterule preflight — provider + corpus checks before expensive runs."""

from __future__ import annotations

import click

from cauterule.config import load_config
from cauterule.preflight import run_preflight


@click.command("preflight")
@click.option("--corpus", help="Path to corpus JSONL file or directory.")
@click.option("--catalog", help="Path to catalog.yaml for size/annotation checks.")
@click.option("--output-dir", help="Output directory to check for writability/space.")
@click.option("--no-probe", is_flag=True, help="Skip LLM latency probe.")
@click.option("--max-cost", type=float, default=None, help="Cost cap (USD).")
@click.option("--cost-table", is_flag=True, help="Print $/1k model table and exit.")
def preflight(
    corpus: str | None,
    catalog: str | None,
    output_dir: str | None,
    no_probe: bool,
    max_cost: float | None,
    cost_table: bool,
) -> None:
    """Run provider + corpus preflight checks (fail fast before sweep)."""
    if cost_table:
        from cauterule.preflight import cost_table as _table

        for row in _table():
            click.echo(
                f"  {row['model']:30s} ${row['cost_per_1k']:.4f}/1k  "
                f"p95={row['p95_latency_s']:.1f}s  tier={row['tier']}"
            )
        return
    cfg = load_config()
    result = run_preflight(
        cfg,
        corpus_path=corpus,
        probe=_simple_probe if not no_probe else None,
        output_dir=output_dir,
        catalog_path=catalog,
    )

    for check in result.provider_checks:
        status = "PASS" if check.passed else "FAIL"
        msg = f"  [{status}] {check.name}: {check.message}"
        if check.latency_s is not None:
            msg += f" ({check.latency_s:.1f}s)"
        click.echo(msg)

    for check in result.corpus_checks:
        status = "PASS" if check.passed else "FAIL"
        click.echo(f"  [{status}] {check.name}: {check.message}")

    if result.cost_estimate_usd is not None:
        click.echo(f"Estimated cost: ${result.cost_estimate_usd:.2f}")
        if max_cost is not None and result.cost_estimate_usd > max_cost:
            msg = (
                f"cost estimate ${result.cost_estimate_usd:.2f} exceeds --max-cost ${max_cost:.2f}"
            )
            raise click.ClickException(msg)

    if result.passed:
        click.echo("Preflight: PASS — ready to run.")
    else:
        click.echo("Preflight: FAIL — fix issues before running.")
        raise click.ClickException("Preflight checks failed")  # noqa: TRY003


def _simple_probe(_config: object) -> float:
    """Minimal latency probe: return 0.1s placeholder.

    Real probes are deferred to the field-test runner; this ensures the
    preflight code path is exercised without requiring a live LLM.
    """
    return 0.1
