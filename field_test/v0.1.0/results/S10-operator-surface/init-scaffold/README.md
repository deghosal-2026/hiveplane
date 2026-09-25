# HivePlane project

Scaffolded by `hiveplane init`.

- `workloads/hello-agent.yaml` — a sample AgentWorkload manifest.
- `corpora/hello-agent/v1/corpus.yaml` — its sample benchmark corpus.

## Next steps

1. Validate the manifest: `hiveplane validate workloads/hello-agent.yaml`
2. Start the control plane: `docker compose up -d`
3. Register the workload: `hiveplane register workloads/hello-agent.yaml`
4. Certify it: `hiveplane certify hello-agent --context staging`
5. Submit a run: `hiveplane submit --agent hello-agent --task '{{"name": "world"}}'`
