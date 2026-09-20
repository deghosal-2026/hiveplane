# policy — Policy Configuration

Loads, validates, and provides access to `guardian-policy.yml`.

- `models.py` — PolicyConfig and RuleConfig pydantic models with schema validation
- `loader.py` — YAML file loader with error handling and default fallback

Input: path to YAML file (or None for defaults)
Output: `PolicyConfig` validated object
