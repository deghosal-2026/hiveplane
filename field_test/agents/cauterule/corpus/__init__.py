"""Corpus subsystem — tiered trajectory collections for testing and benchmarking."""

from cauterule.corpus.counterexample import build_counterexample_corpus
from cauterule.corpus.format import CORPUS_METADATA_FIELDS, CORPUS_SCHEMA_VERSION, CorpusMetadata
from cauterule.corpus.gold import GoldRuleFamily, load_gold_families
from cauterule.corpus.labels import label_trajectory
from cauterule.corpus.nearmiss import build_nearmiss_corpus
from cauterule.corpus.private import PrivateCorpus
from cauterule.corpus.public import load_public_corpus
from cauterule.corpus.staleness import build_staleness_corpus
from cauterule.corpus.tiers import build_tiered_corpus

__all__ = [
    "CORPUS_METADATA_FIELDS",
    "CORPUS_SCHEMA_VERSION",
    "CorpusMetadata",
    "GoldRuleFamily",
    "PrivateCorpus",
    "build_counterexample_corpus",
    "build_nearmiss_corpus",
    "build_staleness_corpus",
    "build_tiered_corpus",
    "label_trajectory",
    "load_gold_families",
    "load_public_corpus",
]
