# Contributing

## Writing a New Runbook

1. Copy `templates/runbook-template.md` to the appropriate domain directory
2. Follow the template structure exactly
3. Ensure all commands are tested and copy-pasteable
4. Update the **Runbook Index** table in `README.md` with the new entry
5. If the runbook has a corresponding monitoring alert, add an entry to the **Alert Quick Reference** table in `README.md`
6. Submit a PR with the `runbook` label

## Principles

- **Be specific**: Use exact commands, not descriptions of what to do
- **Be concise**: Engineers read runbooks at 3 AM under pressure
- **Be accurate**: Test every command before committing
- **Include verification**: Every action should have a way to confirm it worked
- **Add context**: Link to dashboards, alerts, and related runbooks

## Review Checklist

- [ ] Follows the standard template
- [ ] All 11 sections present: Metadata, Summary, Impact, Prerequisites, Triage & Diagnosis, Mitigation Steps, Verification, Rollback, Escalation, Related Runbooks, Changelog
- [ ] All commands are copy-pasteable
- [ ] Variables use `${VAR}` syntax
- [ ] Severity and Difficulty are correctly assigned
- [ ] Owner team is specified
- [ ] Escalation path is documented
- [ ] Related runbooks are linked
- [ ] README.md Runbook Index table updated
- [ ] README.md Alert Quick Reference table updated (if alert exists)

## Maintenance

- Runbooks are reviewed **quarterly** by the owning team
- After every P1/P2 incident, update the relevant runbook within 1 week
- Mark deprecated runbooks with a header notice, don't delete them
