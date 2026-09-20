from __future__ import annotations

import click

from cauterule import __version__
from cauterule.cli.audit import audit
from cauterule.cli.badge import badge
from cauterule.cli.benchmark import benchmark
from cauterule.cli.config import config
from cauterule.cli.conflicts import conflicts
from cauterule.cli.corpus import corpus
from cauterule.cli.counterfactual import counterfactual
from cauterule.cli.demo import demo
from cauterule.cli.diff import diff
from cauterule.cli.explain import explain
from cauterule.cli.export import export, import_cmd
from cauterule.cli.extract import extract
from cauterule.cli.frontier import frontier
from cauterule.cli.gaps import gaps
from cauterule.cli.harness_health import harness_health_cli
from cauterule.cli.health import health
from cauterule.cli.history import history
from cauterule.cli.init import init
from cauterule.cli.inject import inject
from cauterule.cli.journal import journal
from cauterule.cli.leaderboard import leaderboard
from cauterule.cli.list import list_rules
from cauterule.cli.mcp import mcp
from cauterule.cli.metrics import metrics
from cauterule.cli.observe import observe
from cauterule.cli.otel import otel
from cauterule.cli.pack import pack
from cauterule.cli.preflight import preflight
from cauterule.cli.promote import promote
from cauterule.cli.release import release
from cauterule.cli.report import report
from cauterule.cli.retire import retire
from cauterule.cli.review import review
from cauterule.cli.rewind import rewind_cmd
from cauterule.cli.search import search
from cauterule.cli.share import share
from cauterule.cli.show import show
from cauterule.cli.story import story
from cauterule.cli.taxonomy import taxonomy
from cauterule.cli.test import test
from cauterule.cli.validate import validate
from cauterule.cli.webhook import webhook
from cauterule.log import get_logger, setup_logging

log = get_logger(__name__)


@click.group(invoke_without_command=True)
@click.version_option(version=__version__, prog_name="cauterule")
@click.option("--verbose", is_flag=True, help="Enable debug logging.")
def main(verbose: bool) -> None:
    """CauterRule — automated standing-rule extraction from agent failures."""
    setup_logging(level="DEBUG" if verbose else "INFO")
    log.debug("cauterule cli invoked", extra={"verbose": verbose})


main.add_command(audit)
main.add_command(badge)
main.add_command(benchmark)
main.add_command(config)
main.add_command(corpus)
main.add_command(conflicts)
main.add_command(counterfactual)
main.add_command(demo)
main.add_command(diff)
main.add_command(explain)
main.add_command(export)
main.add_command(import_cmd, name="import")
main.add_command(extract)
main.add_command(observe)
main.add_command(otel)
main.add_command(health)
main.add_command(history)
main.add_command(init)
main.add_command(inject)
main.add_command(list_rules)
main.add_command(mcp)
main.add_command(frontier)
main.add_command(gaps)
main.add_command(leaderboard)
main.add_command(journal)
main.add_command(metrics)
main.add_command(harness_health_cli)
main.add_command(pack)
main.add_command(preflight)
main.add_command(promote)
main.add_command(release)
main.add_command(report)
main.add_command(retire)
main.add_command(review)
main.add_command(rewind_cmd, name="rewind")
main.add_command(search)
main.add_command(share)
main.add_command(show)
main.add_command(story)
main.add_command(taxonomy)
main.add_command(test)
main.add_command(validate)
main.add_command(webhook)
