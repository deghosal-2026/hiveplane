from pydantic import BaseModel, field_validator

from guardian.policy.loader import load_policy_file
from guardian.policy.models import PolicyConfig


class GuardianConfig(BaseModel):
    github_token: str
    repo: str
    pr_number: int | None = None
    commit_sha: str | None = None
    policy_path: str = "guardian-policy.yml"
    audit_dir: str = "~/.guardian/audit"
    verbose: bool = False

    @field_validator("github_token")
    @classmethod
    def token_not_empty(cls, v):
        if not v:
            raise ValueError("github_token must not be empty")
        return v

    @field_validator("repo")
    @classmethod
    def repo_format(cls, v):
        if "/" not in v:
            raise ValueError("repo must be in owner/name format")
        return v


def load_policy(path: str | None = None) -> PolicyConfig:
    return load_policy_file(path or "guardian-policy.yml")
