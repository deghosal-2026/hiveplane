"""Benchmark profiles: fast (dev subset) vs. full (promotion) (M55-04)."""

from __future__ import annotations

from enum import StrEnum

from hiveplane.certification.models import BenchmarkCorpus, TargetContext


class Profile(StrEnum):
    """A benchmark profile selecting which tasks run."""

    FAST = "fast"
    FULL = "full"


class ProfileError(ValueError):
    """Base class for benchmark-profile errors."""


class EmptyProfileError(ProfileError):
    """Raised when a profile selects no tasks from a corpus."""

    def __init__(self, profile: Profile, corpus_id: str) -> None:
        super().__init__(
            f"corpus {corpus_id!r} has no tasks for the {profile.value!r} profile"
        )


class ProfileNotAllowedError(ProfileError):
    """Raised when a profile may not be used for a target context."""

    def __init__(self, profile: Profile, context: TargetContext) -> None:
        super().__init__(
            f"the {profile.value!r} profile cannot certify {context.value}; "
            "use the full profile"
        )


def select_profile(corpus: BenchmarkCorpus, profile: Profile) -> BenchmarkCorpus:
    """Return the corpus restricted to the tasks belonging to ``profile``."""
    if profile is Profile.FULL:
        return corpus
    tasks = [task for task in corpus.tasks if profile.value in task.profiles]
    if not tasks:
        raise EmptyProfileError(profile, corpus.id)
    return corpus.model_copy(update={"tasks": tasks})


def ensure_profile_allowed(profile: Profile, context: TargetContext) -> None:
    """Refuse a fast subset for production certification (M55-04 guardrail)."""
    if context is TargetContext.PRODUCTION and profile is not Profile.FULL:
        raise ProfileNotAllowedError(profile, context)
