# Python Intelligence implementation notes

The Python engine deliberately does not modify or call the Java parser.

## Routing

CodeIntelligence Core detects the source project language. Python projects are sent to this V3 service on port 8082. The UI remains inside V3.

## Static analysis strategy

Python analysis uses the standard-library `ast` parser.

### Project scan

`PythonScannerService` walks `.py` files, ignores virtual environments/build folders, and records modules, classes, functions, imports, parameters and return annotations.

### FastAPI endpoint discovery

`PythonEndpointFlowService` parses:

- `APIRouter(prefix="...")`
- `@router.get(...)`
- `@router.post(...)`
- `@router.put(...)`
- `@router.patch(...)`
- `@router.delete(...)`

The prefix and decorator path are combined into the complete endpoint.

### Call graph

`PythonCodeFlowService` indexes every function/method. It resolves common local dependency patterns:

- module instance: `person_service = PersonService()`
- class field: `self.repository = PersonRepository()`
- calls: `person_service.update_email(...)`
- calls: `self.repository.save(...)`

Repository/SQLAlchemy calls are classified as SAVE, FIND, DELETE, QUERY and COMMIT.

### Attribute impact

`PythonAttributeLineageService` normalizes symbols before matching:

`primaryEmail` -> `primaryemail`
`primary_email` -> `primaryemail`

Therefore API/schema camelCase can be followed into Python model/repository snake_case.

`PythonAttributeImpactService` then intersects those source occurrences with endpoint flow nodes and registered scenarios.

### RAG

`PythonRagService` indexes only `.py` source. Classes and functions are extracted with AST before LangChain splitting and FAISS indexing.

### JIRA impact

`PythonJiraImpactService` combines:

1. requirement concepts,
2. direct source matches,
3. local RAG results,
4. endpoint execution flows,
5. registered scenarios.

No Python source is sent to an external LLM by this implementation.

### Regression

The Git diff service filters `.py` files and uses AST `lineno`/`end_lineno` to determine which Python function/method contains a changed line. Regression then compares those changed symbols with stored scenario/operation flows.
