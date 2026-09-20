"""Score rule specificity by context, trigger, taxonomy, hits, and replay precision.

The score is a weighted combination of context count (25%), trigger
precision (25%), taxonomy depth (20%), hit-tested (15%), and replay
precision (15%).
"""

from __future__ import annotations

from cauterule.models.rule import StandingRule

_MAX_CONTEXT_ITEMS: int = 10
_MAX_TRIGGER_WORDS: int = 20


def score_specificity(rule: StandingRule) -> float:
    """Return a specificity score in [0.0, 1.0] for a standing rule.

    The score is a weighted combination of:
      - context count (25%): more context items → more specific
      - trigger precision (25%): more trigger words → more specific
      - taxonomy depth (20%): deeper taxonomy path → more specific
      - hit-tested (15%): has been hit at least once
      - replay precision (15%): replay evidence precision score
    """
    ctx_score = _compute_context_score(rule)
    trig_score = _compute_trigger_precision(rule)
    taxo_score = _compute_taxonomy_depth(rule)
    hit_score = _compute_hit_tested(rule)
    replay_score = _compute_replay_precision(rule)

    return (
        0.25 * ctx_score
        + 0.25 * trig_score
        + 0.20 * taxo_score
        + 0.15 * hit_score
        + 0.15 * replay_score
    )


def _compute_context_score(rule: StandingRule) -> float:
    ctx_count = len(rule.when.context)
    return min(ctx_count / _MAX_CONTEXT_ITEMS, 1.0)


def _compute_trigger_precision(rule: StandingRule) -> float:
    words = rule.when.trigger.strip().split()
    return min(len(words) / _MAX_TRIGGER_WORDS, 1.0)


def _compute_taxonomy_depth(rule: StandingRule) -> float:
    if rule.taxonomy is None:
        return 0.0
    depth = rule.taxonomy.count("/")
    return min(depth / 5.0, 1.0)


def _compute_hit_tested(rule: StandingRule) -> float:
    return 1.0 if rule.hit_count > 0 else 0.0


def _compute_replay_precision(rule: StandingRule) -> float:
    if rule.provenance.replay_evidence is None:
        return 0.0
    return rule.provenance.replay_evidence.precision
