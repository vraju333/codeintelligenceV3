# Shared DB project isolation

This patch makes Python V3 treat `project_path` as the isolation boundary for project-owned data.

Protected areas:
- Scenario Registry
- Scenario code lookup / duplicate checks
- Scenario baselines (through project-scoped scenario lookup)
- Regression legacy-baseline lookup
- JIRA Knowledge DB rows
- JIRA RAG indexes

JIRA indexes are now stored per project under `jira_rag_indexes/<project>-<path-hash>/`.

For a brand-new shared database, the ORM schema uses composite uniqueness:
- `(project_path, scenario_code)`
- `(project_path, jira_id)`

Important for an already-existing DB: SQLAlchemy `create_all()` does not rewrite old unique constraints. If the existing database was created with a global unique constraint on `scenario_code` or `jira_id`, the app is still project-filtered and will not cross-read data, but reusing the exact same scenario/JIRA key in two different projects may still be rejected by the old database constraint. Migrate/drop that old constraint later if duplicate keys across projects are required.
