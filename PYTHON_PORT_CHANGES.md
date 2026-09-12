# Python CodeIntelligence - Hackathon Port

This build applies the finalized Scenario/Baseline workflow to the Python analysis engine.

## Included
- Scenario -> Release -> Version -> Test Baseline -> JIRA
- Existing release can create V2/V3; new release starts at V1
- Scenario-scoped Version History
- Same-release and cross-release version comparison
- Test Baselines attach to a selected Release/Version
- JIRA selection is saved with the Test Baseline
- Attribute Impact shows linked Scenario -> Release -> Version -> Test -> JIRA history
- Existing Scenario Registry test-data logic is preserved
- Python source/flow duplicate protection prevents accidental identical versions
- Additive DB column upgrades preserve existing data

## Keep from your machine
Keep your existing `.env`, `codeintelligence.db`/PostgreSQL data, project registry, and generated RAG indexes.
Run `pip install -r requirements.txt` if dependencies are not already installed.
