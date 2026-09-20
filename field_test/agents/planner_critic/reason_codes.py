"""Stable machine-readable reason-code catalog (F-77).

Every deterministic gate and every loop decision maps to a stable code so
downstream tooling (escalation UI, forensics, field-test reports) can key on
``reason_code`` instead of brittle message parsing. Add codes here first,
then reference the constant — never inline a string literal in gate/loop code.

Each constant carries its precise ``Literal`` type so ``mypy --strict``
verifies every finding/decision uses a code that actually exists in the
catalog.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

# --- Deterministic gate codes (per PRD §2.5.2) ------------------------------
PLAN_SCHEMA_INVALID: Literal["plan_schema_invalid"] = "plan_schema_invalid"
DEPENDENCY_CYCLE: Literal["dependency_cycle"] = "dependency_cycle"
UNSAFE_ORDERING: Literal["unsafe_ordering"] = "unsafe_ordering"
MISSING_VERIFICATION: Literal["missing_verification"] = "missing_verification"
MISSING_ROLLBACK: Literal["missing_rollback"] = "missing_rollback"
UNVERIFIED_PRECONDITION: Literal["unverified_precondition"] = "unverified_precondition"
UNSAFE_PARALLELIZATION: Literal["unsafe_parallelization"] = "unsafe_parallelization"
VERIFICATION_AFTER_CONSUMER: Literal["verification_after_consumer"] = "verification_after_consumer"
ROLLBACK_UNREACHABLE: Literal["rollback_unreachable"] = "rollback_unreachable"
ROLLBACK_SELF_DEPENDENT: Literal["rollback_self_dependent"] = "rollback_self_dependent"
ROLLBACK_INCONSISTENT_STATE: Literal["rollback_inconsistent_state"] = "rollback_inconsistent_state"
ROLLBACK_POST_CONSUMED: Literal["rollback_post_consumed"] = "rollback_post_consumed"
ROLLBACK_STATE_UNDECLARED: Literal["rollback_state_undeclared"] = "rollback_state_undeclared"
STEP_NOT_TRACED_TO_CRITERION: Literal["step_not_traced_to_criterion"] = (
    "step_not_traced_to_criterion"
)
FAMILY_HISTOGRAM_CYCLING: Literal["family_histogram_cycling"] = "family_histogram_cycling"

# --- Deterministic auto-fix codes (PRD §2.6, M2) ------------------------------
AUTO_REPAIRED_ORDERING: Literal["auto_repaired_ordering"] = "auto_repaired_ordering"
AUTO_CLOSED_PRECONDITION: Literal["auto_closed_precondition"] = "auto_closed_precondition"
PLAN_OSCILLATION_DETECTED: Literal["plan_oscillation_detected"] = "plan_oscillation_detected"
AUTO_CONVERGE_PARTIAL_APPROVAL: Literal["auto_converge_partial_approval"] = (
    "auto_converge_partial_approval"
)
POLICY_EVALUATION_ERROR: Literal["policy_evaluation_error"] = "policy_evaluation_error"
POLICY_VIOLATION: Literal["policy_violation"] = "policy_violation"

# --- Domain pack reason codes (M4) --------------------------------------------
SECOPS_ISOLATION_WITHOUT_TRAFFIC_DRAIN: Literal["secops_isolation_without_traffic_drain"] = (
    "secops_isolation_without_traffic_drain"
)
SECOPS_FORENSIC_ORDER_VIOLATION: Literal["secops_forensic_order_violation"] = (
    "secops_forensic_order_violation"
)
SECOPS_BROAD_PRIVILEGE_WITHOUT_HITL: Literal["secops_broad_privilege_without_hitl"] = (
    "secops_broad_privilege_without_hitl"
)
SUPPLY_CHAIN_LOCKFILE_NOT_REGENERATED: Literal["supply_chain_lockfile_not_regenerated"] = (
    "supply_chain_lockfile_not_regenerated"
)
SUPPLY_CHAIN_BREAKING_CHANGE_WITHOUT_MIGRATION: Literal[
    "supply_chain_breaking_change_without_migration"
] = "supply_chain_breaking_change_without_migration"
SUPPLY_CHAIN_UNSIGNED_ARTIFACT: Literal["supply_chain_unsigned_artifact"] = (
    "supply_chain_unsigned_artifact"
)
SUPPLY_CHAIN_MISSING_SBOM: Literal["supply_chain_missing_sbom"] = "supply_chain_missing_sbom"
FINOPS_DELETE_WITHOUT_GRACE_PERIOD: Literal["finops_delete_without_grace_period"] = (
    "finops_delete_without_grace_period"
)
FINOPS_BUDGET_BOUNDARY_BREACHED: Literal["finops_budget_boundary_breached"] = (
    "finops_budget_boundary_breached"
)
DATA_ENG_DESTRUCTIVE_WITHOUT_BACKUP: Literal["data_eng_destructive_without_backup"] = (
    "data_eng_destructive_without_backup"
)
DATA_ENG_MIGRATION_OUTSIDE_MAINTENANCE_WINDOW: Literal[
    "data_eng_migration_outside_maintenance_window"
] = "data_eng_migration_outside_maintenance_window"
DATA_ENG_MIGRATION_WITHOUT_DUAL_WRITE: Literal["data_eng_migration_without_dual_write"] = (
    "data_eng_migration_without_dual_write"
)
DATA_ENG_MIGRATION_WITHOUT_FALLBACK: Literal["data_eng_migration_without_fallback"] = (
    "data_eng_migration_without_fallback"
)
ROLLBACK_DAG_GENERATED: Literal["rollback_dag_generated"] = "rollback_dag_generated"
ROLLBACK_EXECUTION_TRIGGERED: Literal["rollback_execution_triggered"] = (
    "rollback_execution_triggered"
)
ROLLBACK_NON_REVERSIBLE_STEP_SKIPPED: Literal["rollback_non_reversible_step_skipped"] = (
    "rollback_non_reversible_step_skipped"
)
ROLLBACK_MISSING_ACTION_MAPPING: Literal["rollback_missing_action_mapping"] = (
    "rollback_missing_action_mapping"
)

# --- Loop decision codes (PRD §2.6) ------------------------------------------
REVISION_CAP_REACHED: Literal["revision_cap_reached"] = "revision_cap_reached"
CONVERGED_STALLED: Literal["converged_stalled"] = "converged_stalled"
REGRESSION_THRASHING: Literal["regression_thrashing"] = "regression_thrashing"
BUDGET_EXCEEDED: Literal["budget_exceeded"] = "budget_exceeded"
REPLAN_ABORTED: Literal["replan_aborted"] = "replan_aborted"
APPROVED: Literal["approved"] = "approved"
PLANNING_UNAVAILABLE: Literal["planning_unavailable"] = "planning_unavailable"
CRITIC_SATISFIED: Literal["critic_satisfied"] = "critic_satisfied"

# --- Approval / fail-closed codes -------------------------------------------
APPROVAL_THRESHOLD_NOT_MET: Literal["approval_threshold_not_met"] = "approval_threshold_not_met"
RUNTIME_PRECONDITION_STALE: Literal["runtime_precondition_stale"] = "runtime_precondition_stale"

# --- LLM critic heuristic codes (PRD §2.5.1, F-80) ---------------------------
LLM_FEASIBILITY: Literal["llm_feasibility"] = "llm_feasibility"
LLM_RISK: Literal["llm_risk"] = "llm_risk"
LLM_MISSING_STEPS: Literal["llm_missing_steps"] = "llm_missing_steps"
LLM_UNSAFE_SEQUENCING: Literal["llm_unsafe_sequencing"] = "llm_unsafe_sequencing"
LLM_UNVERIFIED_DEPENDENCIES: Literal["llm_unverified_dependencies"] = "llm_unverified_dependencies"
LLM_WEAK_ROLLBACK: Literal["llm_weak_rollback"] = "llm_weak_rollback"

# --- Security oracle / eval codes (M5) ---------------------------------------
SECURITY_CRITIC_ALIGNED: Literal["security_critic_aligned"] = "security_critic_aligned"
SECURITY_CRITIC_MISSED: Literal["security_critic_missed"] = "security_critic_missed"
SECURITY_CRITIC_SPURIOUS: Literal["security_critic_spurious"] = "security_critic_spurious"
SECURITY_INJECTION_BLOCKED: Literal["security_injection_blocked"] = "security_injection_blocked"
SECURITY_INJECTION_BYPASSED: Literal["security_injection_bypassed"] = "security_injection_bypassed"
STANDING_RULE_PROPOSED: Literal["standing_rule_proposed"] = "standing_rule_proposed"
STANDING_RULE_PROMOTED: Literal["standing_rule_promoted"] = "standing_rule_promoted"
LABEL_MIGRATION_DETECTED: Literal["label_migration_detected"] = "label_migration_detected"
BOUNDARY_CASE_FLIP: Literal["boundary_case_flip"] = "boundary_case_flip"
IRREVERSIBLE_INVARIANT_BLOCKED: Literal["irreversible_invariant_blocked"] = (
    "irreversible_invariant_blocked"
)

# --- Enterprise safety codes (M6) --------------------------------------------
POSTURE_RESOLVED: Literal["posture_resolved"] = "posture_resolved"
RUN_BUDGET_EXCEEDED: Literal["run_budget_exceeded"] = "run_budget_exceeded"
RUN_DEPTH_EXCEEDED: Literal["run_depth_exceeded"] = "run_depth_exceeded"
RUN_TIMEOUT: Literal["run_timeout"] = "run_timeout"
TRANSIENT_RETRY_TRIGGERED: Literal["transient_retry_triggered"] = "transient_retry_triggered"
DETERMINISTIC_REPLAN_TRIGGERED: Literal["deterministic_replan_triggered"] = (
    "deterministic_replan_triggered"
)
AMBIGUOUS_REPLAN_ESCALATED: Literal["ambiguous_replan_escalated"] = "ambiguous_replan_escalated"
STEP_RETRY_BUDGET_EXCEEDED: Literal["step_retry_budget_exceeded"] = "step_retry_budget_exceeded"
STATE_VIEW_STALE: Literal["state_view_stale"] = "state_view_stale"
RESOURCE_LOCKED_BY_CONCURRENT_EXECUTION: Literal["resource_locked_by_concurrent_execution"] = (
    "resource_locked_by_concurrent_execution"
)
CONCURRENT_RESOURCE_CONFLICT: Literal["concurrent_resource_conflict"] = (
    "concurrent_resource_conflict"
)
FINDING_DRIFT_STORED: Literal["finding_drift_stored"] = "finding_drift_stored"
DRIFT_ALERT_TRIGGERED: Literal["drift_alert_triggered"] = "drift_alert_triggered"

# --- M9 scale-validation reason codes (§3.36-§3.40) --------------------------
IDP_RBAC_BROAD_ROLE: Literal["idp_rbac_broad_role"] = "idp_rbac_broad_role"
IDP_MISSING_CORPORATE_METADATA: Literal["idp_missing_corporate_metadata"] = (
    "idp_missing_corporate_metadata"
)
IDP_QUOTA_BREACH: Literal["idp_quota_breach"] = "idp_quota_breach"
IDP_NOISY_NEIGHBOR_RISK: Literal["idp_noisy_neighbor_risk"] = "idp_noisy_neighbor_risk"
MULTI_AGENT_CYCLIC_HANDOFF: Literal["multi_agent_cyclic_handoff"] = "multi_agent_cyclic_handoff"
MULTI_AGENT_UNVERIFIED_STATE_SIGNAL: Literal["multi_agent_unverified_state_signal"] = (
    "multi_agent_unverified_state_signal"
)
MULTI_AGENT_ROLLBACK_UNSYNCHRONIZED: Literal["multi_agent_rollback_unsynchronized"] = (
    "multi_agent_rollback_unsynchronized"
)
SRE_BLAST_RADIUS_EXCEEDED: Literal["sre_blast_radius_exceeded"] = "sre_blast_radius_exceeded"
SRE_MISSING_INTER_BATCH_HEALTHCHECK: Literal["sre_missing_inter_batch_healthcheck"] = (
    "sre_missing_inter_batch_healthcheck"
)
SRE_DESTRUCTIVE_WITHOUT_HITL: Literal["sre_destructive_without_hitl"] = (
    "sre_destructive_without_hitl"
)
SCP_NO_TOPOLOGICAL_PROPAGATION: Literal["scp_no_topological_propagation"] = (
    "scp_no_topological_propagation"
)
SCP_MISSING_PER_SERVICE_CI: Literal["scp_missing_per_service_ci"] = "scp_missing_per_service_ci"
SCP_BULK_DEPLOY_INTERNAL_DEP: Literal["scp_bulk_deploy_internal_dep"] = (
    "scp_bulk_deploy_internal_dep"
)
FNG_COST_IMPACT_EXCEEDS_BUDGET: Literal["fng_cost_impact_exceeds_budget"] = (
    "fng_cost_impact_exceeds_budget"
)
FNG_TERMINATES_COMMITTED_INSTANCE: Literal["fng_terminates_committed_instance"] = (
    "fng_terminates_committed_instance"
)
PRECONDITION_REDUNDANTLY_RE_INJECTED: Literal["precondition_redundantly_re_injected"] = (
    "precondition_redundantly_re_injected"
)
PRECONDITION_DROPPED_FROM_COMPACTION: Literal["precondition_dropped_from_compaction"] = (
    "precondition_dropped_from_compaction"
)
BLAST_RADIUS_QUOTA_BREACH: Literal["blast_radius_quota_breach"] = "blast_radius_quota_breach"
BLAST_RADIUS_RESTRICTED_CLUSTER: Literal["blast_radius_restricted_cluster"] = (
    "blast_radius_restricted_cluster"
)
BLAST_RADIUS_RESTRICTED_ACTION: Literal["blast_radius_restricted_action"] = (
    "blast_radius_restricted_action"
)
SECRET_REDACTED: Literal["secret_redacted"] = "secret_redacted"  # noqa: S105  # reason-code label, not a credential

ReasonCode: TypeAlias = Literal[
    "plan_schema_invalid",
    "dependency_cycle",
    "unsafe_ordering",
    "missing_verification",
    "missing_rollback",
    "unverified_precondition",
    "unsafe_parallelization",
    "verification_after_consumer",
    "rollback_unreachable",
    "rollback_self_dependent",
    "rollback_inconsistent_state",
    "rollback_post_consumed",
    "rollback_state_undeclared",
    "step_not_traced_to_criterion",
    "family_histogram_cycling",
    "auto_repaired_ordering",
    "auto_closed_precondition",
    "plan_oscillation_detected",
    "auto_converge_partial_approval",
    "policy_evaluation_error",
    "policy_violation",
    "secops_isolation_without_traffic_drain",
    "secops_forensic_order_violation",
    "secops_broad_privilege_without_hitl",
    "supply_chain_lockfile_not_regenerated",
    "supply_chain_breaking_change_without_migration",
    "supply_chain_unsigned_artifact",
    "supply_chain_missing_sbom",
    "finops_delete_without_grace_period",
    "finops_budget_boundary_breached",
    "data_eng_destructive_without_backup",
    "data_eng_migration_outside_maintenance_window",
    "data_eng_migration_without_dual_write",
    "data_eng_migration_without_fallback",
    "rollback_dag_generated",
    "rollback_execution_triggered",
    "rollback_non_reversible_step_skipped",
    "rollback_missing_action_mapping",
    "revision_cap_reached",
    "converged_stalled",
    "regression_thrashing",
    "budget_exceeded",
    "replan_aborted",
    "approved",
    "planning_unavailable",
    "critic_satisfied",
    "approval_threshold_not_met",
    "runtime_precondition_stale",
    "llm_feasibility",
    "llm_risk",
    "llm_missing_steps",
    "llm_unsafe_sequencing",
    "llm_unverified_dependencies",
    "llm_weak_rollback",
    "security_critic_aligned",
    "security_critic_missed",
    "security_critic_spurious",
    "security_injection_blocked",
    "security_injection_bypassed",
    "standing_rule_proposed",
    "standing_rule_promoted",
    "label_migration_detected",
    "boundary_case_flip",
    "irreversible_invariant_blocked",
    "posture_resolved",
    "run_budget_exceeded",
    "run_depth_exceeded",
    "run_timeout",
    "transient_retry_triggered",
    "deterministic_replan_triggered",
    "ambiguous_replan_escalated",
    "step_retry_budget_exceeded",
    "state_view_stale",
    "resource_locked_by_concurrent_execution",
    "concurrent_resource_conflict",
    "finding_drift_stored",
    "drift_alert_triggered",
    "precondition_redundantly_re_injected",
    "precondition_dropped_from_compaction",
    "blast_radius_quota_breach",
    "blast_radius_restricted_cluster",
    "blast_radius_restricted_action",
    "secret_redacted",
    "idp_rbac_broad_role",
    "idp_missing_corporate_metadata",
    "idp_quota_breach",
    "idp_noisy_neighbor_risk",
    "multi_agent_cyclic_handoff",
    "multi_agent_unverified_state_signal",
    "multi_agent_rollback_unsynchronized",
    "sre_blast_radius_exceeded",
    "sre_missing_inter_batch_healthcheck",
    "sre_destructive_without_hitl",
    "scp_no_topological_propagation",
    "scp_missing_per_service_ci",
    "scp_bulk_deploy_internal_dep",
    "fng_cost_impact_exceeds_budget",
    "fng_terminates_committed_instance",
]

# Descriptions are the source of truth for docs and any generated API reference.
REASON_CODE_DESCRIPTIONS: dict[ReasonCode, str] = {
    PLAN_SCHEMA_INVALID: "Plan does not parse against the typed schema",
    DEPENDENCY_CYCLE: "Dependency graph contains a cycle (not a DAG)",
    UNSAFE_ORDERING: "A task is ordered before a hard dependency",
    MISSING_VERIFICATION: "A high-blast-radius step lacks a verification step",
    MISSING_ROLLBACK: "A high-blast-radius step lacks a rollback step",
    UNVERIFIED_PRECONDITION: "A precondition does not reference an established earlier fact",
    UNSAFE_PARALLELIZATION: "Tasks in one parallel_group break concurrency safety",
    VERIFICATION_AFTER_CONSUMER: (
        "A consumer of a verified high-risk mutation runs before that "
        "mutation's verification point, making the verification vacuous"
    ),
    ROLLBACK_UNREACHABLE: (
        "A high-risk task claims a rollback but its action has no automated "
        "inverse, so the rollback path cannot execute"
    ),
    ROLLBACK_SELF_DEPENDENT: (
        "A guarded task's own preconditions claim establishment by the task "
        "itself — a circular basis for the step and its rollback"
    ),
    ROLLBACK_INCONSISTENT_STATE: (
        "A later task's precondition fact is established by a rollback-guarded "
        "task, and the later task can neither verify nor undo: restoring "
        "pre-write state silently invalidates its basis"
    ),
    ROLLBACK_POST_CONSUMED: (
        "A consumer runs inside a producer's write→rollback window with no "
        "verification or rollback of its own — a rollback would erase state "
        "the consumer already used (dual-write window)"
    ),
    ROLLBACK_STATE_UNDECLARED: (
        "A high-blast-radius task has a rollback but does not declare what "
        "state it restores — add restores_state and restoration_evidence "
        "for verifiable recovery"
    ),
    STEP_NOT_TRACED_TO_CRITERION: (
        "A plan step does not trace to any acceptance criterion bound to "
        "the goal — the plan may be structurally sound but semantically "
        "wrong (delivering the wrong goal)"
    ),
    FAMILY_HISTOGRAM_CYCLING: (
        "The blocker-family histogram repeats at lag ≥ 2 while consecutive "
        "revisions differ — the planner is reshuffling between defective "
        "shapes, not repairing"
    ),
    AUTO_REPAIRED_ORDERING: "Auto-repaired task ordering to satisfy hard-dependency precedences",
    AUTO_CLOSED_PRECONDITION: "Auto-closed a precondition gap from a template match",
    PLAN_OSCILLATION_DETECTED: "Plan oscillates between two structural signatures — no convergence",
    AUTO_CONVERGE_PARTIAL_APPROVAL: (
        "Auto-converged non-oscillating tasks; oscillating subset escalated"
    ),
    POLICY_EVALUATION_ERROR: "An external policy evaluation failed or the runtime was unavailable",
    POLICY_VIOLATION: "A policy-as-code rule produced a violation",
    SECOPS_ISOLATION_WITHOUT_TRAFFIC_DRAIN: "Isolation step without prior traffic drain",
    SECOPS_FORENSIC_ORDER_VIOLATION: "Destructive action before forensic preservation",
    SECOPS_BROAD_PRIVILEGE_WITHOUT_HITL: "Broad privilege escalation without human approval",
    SUPPLY_CHAIN_LOCKFILE_NOT_REGENERATED: "Manifest edited without lockfile regeneration",
    SUPPLY_CHAIN_BREAKING_CHANGE_WITHOUT_MIGRATION: "Major semver bump without migration/linter",
    SUPPLY_CHAIN_UNSIGNED_ARTIFACT: "Deploy of unsigned artifact",
    SUPPLY_CHAIN_MISSING_SBOM: "Deploy without generated SBOM",
    FINOPS_DELETE_WITHOUT_GRACE_PERIOD: "Instant delete without snapshot/notify/wait",
    FINOPS_BUDGET_BOUNDARY_BREACHED: "Expansion breaches localized budget cap",
    DATA_ENG_DESTRUCTIVE_WITHOUT_BACKUP: "Destructive query without verified backup",
    DATA_ENG_MIGRATION_OUTSIDE_MAINTENANCE_WINDOW: "Migration outside maintenance window",
    DATA_ENG_MIGRATION_WITHOUT_DUAL_WRITE: "Live migration without dual-write",
    DATA_ENG_MIGRATION_WITHOUT_FALLBACK: "Live migration without fallback path",
    ROLLBACK_DAG_GENERATED: "Rollback DAG generated at approval time",
    ROLLBACK_EXECUTION_TRIGGERED: "Rollback execution triggered on failure",
    ROLLBACK_NON_REVERSIBLE_STEP_SKIPPED: "Non-reversible step skipped during rollback",
    ROLLBACK_MISSING_ACTION_MAPPING: "Forward action has no rollback mapping",
    REVISION_CAP_REACHED: "Loop terminated because the revision cap was hit",
    CONVERGED_STALLED: "Revisions are circling the same blockers or diff converges to zero",
    REGRESSION_THRASHING: "A revision introduced a new blocker",
    BUDGET_EXCEEDED: "The per-goal spend budget was exceeded",
    REPLAN_ABORTED: "replan_policy=abort: the loop escalated without revising",
    APPROVED: "Loop terminated by meeting the approval threshold",
    PLANNING_UNAVAILABLE: "A provider failed; planning is unavailable and must fail closed",
    CRITIC_SATISFIED: (
        "The LLM critic explicitly endorsed the plan — no blockers found "
        "and the plan is structurally sound enough to approve"
    ),
    APPROVAL_THRESHOLD_NOT_MET: "Findings do not meet the goal's risk-tolerance threshold",
    RUNTIME_PRECONDITION_STALE: (
        "A precondition that held at planning time was stale at execution time — "
        "live state contradicted the plan's assumption"
    ),
    LLM_FEASIBILITY: "LLM critic: task is not achievable with the stated environment/tools",
    LLM_RISK: "LLM critic: risk/blast-radius exceeds the goal's tolerance",
    LLM_MISSING_STEPS: "LLM critic: an obvious prerequisite or step is missing",
    LLM_UNSAFE_SEQUENCING: "LLM critic: ordering or parallelization breaks safety",
    LLM_UNVERIFIED_DEPENDENCIES: "LLM critic: a step depends on a fact never established earlier",
    LLM_WEAK_ROLLBACK: "LLM critic: a high-blast-radius step lacks sound rollback coverage",
    SECURITY_CRITIC_ALIGNED: "Security oracle: critic finding aligns with ground-truth patch",
    SECURITY_CRITIC_MISSED: (
        "Security oracle: critic missed a finding the ground-truth patch addresses"
    ),
    SECURITY_CRITIC_SPURIOUS: (
        "Security oracle: critic reported a finding not supported by ground truth"
    ),
    SECURITY_INJECTION_BLOCKED: "Security oracle: an injection attempt was correctly blocked",
    SECURITY_INJECTION_BYPASSED: "Security oracle: an injection attempt bypassed the critic",
    STANDING_RULE_PROPOSED: (
        "Standing rule: a candidate rule was proposed from missed-critique analysis"
    ),
    STANDING_RULE_PROMOTED: "Standing rule: a candidate rule was promoted to the heuristic pack",
    LABEL_MIGRATION_DETECTED: (
        "Label-migration: a model-chosen severity family differs from the normalized family"
    ),
    BOUNDARY_CASE_FLIP: (
        "Label-migration: a boundary case flipped the verdict from blocker to non-blocker"
    ),
    IRREVERSIBLE_INVARIANT_BLOCKED: (
        "Irreversible invariant: an irreversible step without verified precondition was blocked"
    ),
    POSTURE_RESOLVED: "Posture resolved from context rules or fallback to goal posture",
    RUN_BUDGET_EXCEEDED: "Run-level spend ceiling (run_max_budget_usd) exceeded",
    RUN_DEPTH_EXCEEDED: "Cascading replan depth (run_max_depth) exceeded",
    RUN_TIMEOUT: "Run-level wall-clock timeout (run_max_time) exceeded",
    TRANSIENT_RETRY_TRIGGERED: (
        "Transient execution failure — retrying with backoff, not replanning"
    ),
    DETERMINISTIC_REPLAN_TRIGGERED: "Deterministic execution failure — triggering a replan (F-16)",
    AMBIGUOUS_REPLAN_ESCALATED: (
        "Execution failure could be transient or deterministic — escalated to human"
    ),
    STEP_RETRY_BUDGET_EXCEEDED: "Per-step retry budget (step_max_retries) exceeded — escalated",
    STATE_VIEW_STALE: (
        "State snapshot taken at approval time diverged from live state — re-gate triggered"
    ),
    RESOURCE_LOCKED_BY_CONCURRENT_EXECUTION: (
        "Resource is locked by concurrent agent execution — plan must wait or escalate"
    ),
    CONCURRENT_RESOURCE_CONFLICT: (
        "Plan targets a resource currently locked by another execution — blocker"
    ),
    FINDING_DRIFT_STORED: (
        "Finding stored with dual severity (raw + normalized) for drift observability"
    ),
    DRIFT_ALERT_TRIGGERED: "Drift z-score exceeded 2-sigma threshold for a heuristic family",
    PRECONDITION_REDUNDANTLY_RE_INJECTED: (
        "LLM re-injected a precondition the ledger shows as already satisfied"
    ),
    PRECONDITION_DROPPED_FROM_COMPACTION: (
        "LLM dropped a precondition the ledger shows as satisfied — compaction likely"
    ),
    BLAST_RADIUS_QUOTA_BREACH: "Plan exceeds a blast-radius quota (resource/action count limit)",
    BLAST_RADIUS_RESTRICTED_CLUSTER: "Plan targets a restricted cluster — escalation required",
    BLAST_RADIUS_RESTRICTED_ACTION: "Plan includes a restricted action — escalation required",
    SECRET_REDACTED: "A secret or PII was redacted from output",
    IDP_RBAC_BROAD_ROLE: "IDP: plan attaches broad RBAC role instead of namespace-scoped",
    IDP_MISSING_CORPORATE_METADATA: "IDP: plan omits required corporate metadata tags",
    IDP_QUOTA_BREACH: "IDP: plan exceeds shared-node resource quota",
    IDP_NOISY_NEIGHBOR_RISK: "IDP: plan risks noisy-neighbor interference on shared resources",
    MULTI_AGENT_CYCLIC_HANDOFF: "MAO: cross-agent dependency graph contains a cycle",
    MULTI_AGENT_UNVERIFIED_STATE_SIGNAL: (
        "MAO: agent starts before predecessor's state signal verified"
    ),
    MULTI_AGENT_ROLLBACK_UNSYNCHRONIZED: "MAO: distributed rollback steps are not synchronized",
    SRE_BLAST_RADIUS_EXCEEDED: "SRE: instant drain exceeds blast-radius cap (max 25% rolling)",
    SRE_MISSING_INTER_BATCH_HEALTHCHECK: "SRE: batch drain missing inter-batch health check probe",
    SRE_DESTRUCTIVE_WITHOUT_HITL: "SRE: destructive action during incident without HITL approval",
    SCP_NO_TOPOLOGICAL_PROPAGATION: "SCP: bulk update lacks topological propagation order",
    SCP_MISSING_PER_SERVICE_CI: "SCP: bulk update missing per-service CI pipeline check",
    SCP_BULK_DEPLOY_INTERNAL_DEP: "SCP: bulk deploy of internal dependency without canary",
    FNG_COST_IMPACT_EXCEEDS_BUDGET: "FNG: fleet scale-up exceeds cost-impact budget threshold",
    FNG_TERMINATES_COMMITTED_INSTANCE: "FNG: plan terminates RI/Savings-Plan-covered instance",
}

# All valid codes; a test asserts every produced reason code is in this set.
ALL_REASON_CODES: frozenset[ReasonCode] = frozenset(REASON_CODE_DESCRIPTIONS)
