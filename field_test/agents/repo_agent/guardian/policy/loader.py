import yaml
from pathlib import Path
from guardian.policy.models import PolicyConfig


from guardian.policy.models import PolicyConfig, RuleConfig


DEFAULT_POLICY = PolicyConfig(
    version=1,
    mode="advisory",
    detection="marker",
    rules=[
        RuleConfig(name="hallucinated-api", severity="critical", enabled=True, description=""),
        RuleConfig(name="missing-error-handling", severity="high", enabled=True, description=""),
        RuleConfig(name="hardcoded-secrets", severity="critical", enabled=True, description=""),
    ],
)


def load_policy_file(path: str) -> PolicyConfig:
    p = Path(path)
    if not p.exists():
        return DEFAULT_POLICY.model_copy(deep=True)

    try:
        with open(p) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML parse error in {path}: {e}")

    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}, got {type(data).__name__}")

    return PolicyConfig(**data)
