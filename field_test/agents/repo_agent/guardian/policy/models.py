from pydantic import BaseModel, field_validator


class RuleConfig(BaseModel):
    name: str
    severity: str  # low, medium, high, critical
    enabled: bool = True
    description: str = ""

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v):
        allowed = {"low", "medium", "high", "critical"}
        if v not in allowed:
            raise ValueError(f"severity must be one of {allowed}, got '{v}'")
        return v


class PolicyConfig(BaseModel):
    version: int
    mode: str = "advisory"  # advisory, enforcement
    detection: str = "marker"  # marker, style, both
    model: str | None = None
    rules: list[RuleConfig]

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v):
        allowed = {"advisory", "enforcement"}
        if v not in allowed:
            raise ValueError(f"mode must be one of {allowed}, got '{v}'")
        return v

    @field_validator("detection")
    @classmethod
    def validate_detection(cls, v):
        allowed = {"marker", "style", "both"}
        if v not in allowed:
            raise ValueError(f"detection must be one of {allowed}, got '{v}'")
        return v

    @field_validator("rules")
    @classmethod
    def check_duplicate_names(cls, v):
        names = [r.name for r in v]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate rule names")
        return v
