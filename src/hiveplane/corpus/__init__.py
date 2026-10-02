"""Corpus & benchmark tooling: authoring, templates, and profiles (M55)."""

from hiveplane.corpus.authoring import (
    add_task,
    corpus_document,
    lint_corpus,
    scaffold_corpus,
    validate_corpus,
    write_corpus,
)
from hiveplane.corpus.expansion import CorpusExpansionService
from hiveplane.corpus.models import CorpusRelease
from hiveplane.corpus.profiles import (
    EmptyProfileError,
    Profile,
    ProfileError,
    ProfileNotAllowedError,
    ensure_profile_allowed,
    select_profile,
)
from hiveplane.corpus.service import (
    CorpusImmutabilityError,
    CorpusService,
    corpus_content_hash,
)
from hiveplane.corpus.store import (
    CorpusReleaseStore,
    InMemoryCorpusReleaseStore,
    PostgresCorpusReleaseStore,
    build_corpus_release_store,
)
from hiveplane.corpus.templates import TemplateKind, corpus_template

__all__ = [
    "CorpusExpansionService",
    "CorpusImmutabilityError",
    "CorpusRelease",
    "CorpusReleaseStore",
    "CorpusService",
    "EmptyProfileError",
    "InMemoryCorpusReleaseStore",
    "PostgresCorpusReleaseStore",
    "Profile",
    "ProfileError",
    "ProfileNotAllowedError",
    "TemplateKind",
    "add_task",
    "build_corpus_release_store",
    "corpus_content_hash",
    "corpus_document",
    "corpus_template",
    "ensure_profile_allowed",
    "lint_corpus",
    "scaffold_corpus",
    "select_profile",
    "validate_corpus",
    "write_corpus",
]
