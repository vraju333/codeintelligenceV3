# CodeIntelligence V3 — Python Intelligence

This project is the Python-only intelligence engine used behind `codeintelligence-core`.
It keeps its own UI and runs independently from the Java V2 engine.

## Architecture

```text
CodeIntelligence Core :8000
        |
        |-- JAVA   -> codeintelligenceV2 :8081
        |
        `-- PYTHON -> codeintelligenceV3 :8082
```

V3 analyzes Python source locally. It does not reuse the Java parser.

## Python analysis implemented

- Python project registration and switching (`*.py` projects only)
- AST-based scanner for modules, classes and functions
- FastAPI/APIRouter endpoint discovery
- AST call-flow tracing across Router -> Service -> Mapper -> Repository
- Repository-operation classification (SAVE/FIND/DELETE/QUERY/COMMIT)
- Flowchart generation using the existing UI/report layer
- Python RAG indexing using `Language.PYTHON`
- Attribute lineage / impact using AST plus camelCase <-> snake_case normalization
- JIRA impact using local requirement parsing + Python source/RAG evidence
- Existing scenario registry, testing baselines and version-history UI remain available
- Git regression source filtering uses `.py` files

## Setup

Copy `.env.example` to `.env` and set the Python project:

```env
APP_NAME=CodeIntelligencePython
APP_VERSION=1.0.0
DATABASE_MODE=sqlite
LOCAL_DATABASE_URL=sqlite:///./codeintelligence.db
PYTHON_PROJECT_PATH=D:\AIlearning\organization-details
AUTO_PROJECT_INITIALIZATION=true
JIRA_LLM_ENABLED=false
```

For the hackathon, `JIRA_LLM_ENABLED=false` is a good first run; the Python JIRA impact service works locally without an LLM.

Install dependencies:

```bat
cd D:\AIlearning\codeintelligenceV3
.venv\Scripts\activate
pip install -r requirements.txt
```

Run on port 8082:

```bat
python -m uvicorn main:app --port 8082
```

Open directly:

```text
http://127.0.0.1:8082
```

Or enter `D:\AIlearning\organization-details` in CodeIntelligence Core on port 8000. Core detects Python and opens V3.

## Main Python logic

### 1. Scanner
`services/python/scanner/python_scanner_service.py`

Uses Python's built-in `ast` module, not regex, to identify modules, classes, functions, parameters, return annotations and imports.

### 2. FastAPI endpoint discovery
`services/python/flow/python_endpoint_flow_service.py`

Finds `APIRouter(prefix=...)` and decorators such as `@router.get`, `@router.post`, `@router.put`, `@router.patch`, and `@router.delete`, then combines the router prefix with the route path.

### 3. Code flow
`services/python/flow/python_code_flow_service.py`

Builds a local symbol index and resolves calls such as:

```text
organization_controller.update_person_email
  -> PersonService.update_email
  -> EmailDetailsMapper.update_entity
  -> PersonRepository.save [SAVE]
  -> DatabaseSession.commit [COMMIT]
  -> PersonMapper.to_response
```

It recognizes module-level instances (`person_service = PersonService()`) and class fields (`self.repository = PersonRepository()`).

### 4. Attribute impact
`services/python/lineage/`

Normalizes names so `primaryEmail` can match Python `primary_email`. It finds source occurrences, intersects them with endpoint execution flows and then maps impacted endpoints to registered scenarios.

### 5. Python RAG
`services/python/rag/python_rag_service.py`

Indexes `.py` files per project and chunks classes/functions using AST before storing them in FAISS.

### 6. JIRA impact
`services/python/jira/python_jira_impact_service.py`

Extracts requirement concepts, combines source matches with local RAG hits, traces affected FastAPI endpoints and maps those endpoints to scenarios. Python source code stays local.

## Test project

The supplied `organization-details` sample is designed for this engine and contains:

```text
FastAPI Router -> Service -> Mapper -> Repository -> SQLAlchemy model
```

A useful first test is:

```text
PATCH /api/organizations/{organization_id}/persons/{person_id}/email
```

Expected simplified flow includes:

```text
organization_controller.update_person_email
PersonService.update_email
EmailDetailsMapper.update_entity
PersonRepository.save [SAVE]
DatabaseSession.commit [COMMIT]
PersonMapper.to_response
```
