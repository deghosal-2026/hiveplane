# Field Test Workspace

## Scripts

- `setup.sh` — clone all 30 Round 1 agents into `agents/` at pinned SHAs
- `run-local.sh` — run tests locally (serial, 3-tier, resume mode)

## Directories

- `agents/` — git-cloned third-party repositories (gitignored)
- `results/` — per-test artifacts, LLM outputs, scores (committed)
- `scenarios/` — YAML scenario packs per agent category (future)

## Quick Start

```bash
# Clone all agents
bash field/setup.sh

# Clone specific agents
bash field/setup.sh --agent lg-surfsense,pa-deepagents

# Re-clone everything fresh
bash field/setup.sh --refresh

# Run MLX local sweep
bash field/run-local.sh --tier local

# Run cloud sweep
bash field/run-local.sh --tier cheap --key "$OPENROUTER_API_KEY" --model openai/gpt-4o-mini

# Resume (skip completed)
bash field/run-local.sh --tier cheap --key "$OPENROUTER_API_KEY" --model openai/gpt-4o-mini --resume latest
```

## Results Layout

```
field/results/
├── manifest.json
├── index.jsonl
├── summary.json
├── run.json
├── tests/<safe_nodeid>/
│   ├── info.json
│   ├── local__mlx/
│   │   ├── result.json
│   │   ├── console.log
│   │   ├── evalforge/{prompt.txt,completion.txt,envelope.json,trajectory.jsonl,...}
│   │   └── scores.json
│   ├── cheap__openai_gpt-4o-mini/...
│   └── better__openai_gpt-4o/...
```

## Notes

- Secrets must not be written to disk. Sanitizers mask API keys.
- Do not commit third-party agent code (agents/ is gitignored).
- Results (LLM outputs, scores) are committed — sanitize before push.