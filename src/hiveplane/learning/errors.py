"""Learning-loop domain errors (M36)."""

from __future__ import annotations

from hiveplane.core.run import RunState


class LearningError(Exception):
    """Base class for learning-loop errors."""


class FeedbackNotFoundError(LearningError):
    """Raised when a feedback record does not exist (or is out of scope)."""

    def __init__(self, feedback_id: str) -> None:
        super().__init__(f"feedback {feedback_id!r} not found")
        self.feedback_id = feedback_id


class FeedbackNotAllowedError(LearningError):
    """Raised when feedback targets a run that is not in a terminal state."""

    def __init__(self, run_id: str, state: RunState) -> None:
        super().__init__(
            f"run {run_id!r} is {state.value!r}; feedback requires a completed or failed run"
        )
        self.run_id = run_id
        self.state = state


class CandidateNotFoundError(LearningError):
    """Raised when a corpus candidate does not exist (or is out of scope)."""

    def __init__(self, candidate_id: str) -> None:
        super().__init__(f"corpus candidate {candidate_id!r} not found")
        self.candidate_id = candidate_id


class CandidateNotAllowedError(LearningError):
    """Raised when a candidate transition is not permitted."""


class CandidateAlreadyExistsError(LearningError):
    """Raised when a candidate already exists for the same source feedback."""

    def __init__(self, source_run_id: str) -> None:
        super().__init__(f"a corpus candidate already exists for run {source_run_id!r}")
        self.source_run_id = source_run_id


class CandidateAlreadyReviewedError(LearningError):
    """Raised when a candidate that was already reviewed is reviewed again."""

    def __init__(self, candidate_id: str) -> None:
        super().__init__(f"corpus candidate {candidate_id!r} has already been reviewed")
        self.candidate_id = candidate_id


class RubricNotFoundError(LearningError):
    """Raised when a rubric version does not exist."""

    def __init__(self, name: str, version: int) -> None:
        super().__init__(f"rubric {name!r} version {version} not found")
        self.name = name
        self.version = version
