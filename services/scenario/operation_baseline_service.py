from __future__ import annotations

import hashlib
import json
import difflib
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from baseline_models import OperationBaseline, OperationBaselineSourceSnapshot
from config import settings
from repositories.operation_baseline_repository import OperationBaselineRepository
from services.python.flow.python_endpoint_flow_service import PythonEndpointFlowService
from services.scenario.scenario_baseline_service import ScenarioBaselineService
from services.scenario.scenario_service import ScenarioService


class OperationBaselineService(ScenarioBaselineService):
    """Operation-level baseline/version manager.

    Key = selected project + HTTP method + endpoint.  Business scenarios are
    test cases under that operation.  A click cannot create V2/V3 unless the
    current relevant source/flow fingerprint differs from the active version.
    """

    def __init__(self):
        super().__init__()
        self.operation_repository = OperationBaselineRepository()

    @staticmethod
    def _normalise_endpoint(endpoint: str) -> str:
        value = "/" + "/".join(part for part in str(endpoint or "").strip().split("/") if part)
        return value if value != "/" else "/"

    def _project_path(self) -> str:
        raw = getattr(settings, "PYTHON_PROJECT_PATH", None) or getattr(settings, "JAVA_PROJECT_PATH", None)
        if not raw:
            raise RuntimeError("PYTHON_PROJECT_PATH is not configured")
        return str(Path(raw).expanduser().resolve())

    def _operation_key(self, method: str, endpoint: str) -> str:
        return f"{method.upper().strip()} {self._normalise_endpoint(endpoint)}"

    def _operation_scenarios(self, db: Session, method: str, endpoint: str):
        method = method.upper().strip()
        endpoint = self._normalise_endpoint(endpoint)
        return [
            row for row in ScenarioService().get_all_for_active_project(db)
            if str(row.http_method or "").upper() == method
            and self._normalise_endpoint(row.endpoint) == endpoint
        ]

    @staticmethod
    def _safe_json(value):
        if value is None:
            return None
        if isinstance(value, (dict, list, int, float, bool)):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return text

    def _scenario_contracts(self, scenarios) -> list[dict]:
        contracts = []
        for scenario in scenarios:
            contracts.append({
                "scenario_id": scenario.id,
                "scenario_code": scenario.scenario_code,
                "scenario_name": scenario.scenario_name,
                "request_json": self._safe_json(scenario.request_json),
                "expected_response_json": self._safe_json(scenario.expected_response_json),
                "expected_db_effect": scenario.expected_db_effect,
            })
        return contracts

    @staticmethod
    def _stable_hash(value: Any) -> str:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _current_state(self, method: str, endpoint: str) -> dict:
        flow = PythonEndpointFlowService().analyze_endpoint(method, endpoint)
        project_path = Path(self._project_path())
        snapshot = self._read_relevant_python_sources(
            project_path=project_path,
            flow=flow,
            endpoint=endpoint,
        )
        code_fingerprint = self._stable_hash(snapshot)
        flow_fingerprint = self._stable_hash(flow)
        combined_fingerprint = self._stable_hash({
            "code": code_fingerprint,
            "flow": flow_fingerprint,
        })
        return {
            "flow": flow,
            "snapshot": snapshot,
            "code_fingerprint": code_fingerprint,
            "flow_fingerprint": flow_fingerprint,
            "combined_fingerprint": combined_fingerprint,
        }

    def _read_relevant_python_sources(self, project_path: Path, flow: dict, endpoint: str) -> dict:
        """Capture Python files relevant to one FastAPI operation.

        We keep every file that appears in the AST flow and also include
        resource files named by the endpoint (address/email/contact/etc.).
        That second rule is important for SQLAlchemy/Pydantic model changes:
        model fields can change without appearing as executable call nodes.
        """
        result: dict[str, str] = {}
        if not project_path.exists():
            return result

        ignored = {".git", ".venv", "venv", "env", "__pycache__", "site-packages", "build", "dist"}
        explicit_paths: set[Path] = set()
        names: set[str] = set()

        def walk(value):
            if isinstance(value, dict):
                file_path = value.get("file_path")
                if file_path:
                    try:
                        explicit_paths.add(Path(file_path).resolve())
                    except Exception:
                        pass
                for key in ("class_name", "module", "label"):
                    raw = value.get(key)
                    if raw:
                        names.add(self._normalise_symbol(str(raw).split(".")[-1]))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(flow)

        # Resource tokens from the endpoint catch non-executable domain files
        # such as models/address.py and schemas/address_schema.py.
        generic = {"api", "organizations", "organization", "persons", "person", "id"}
        endpoint_tokens = {
            self._normalise_symbol(part.strip("{}"))
            for part in str(endpoint or "").strip("/").split("/")
            if part and not part.startswith("{")
        } - generic

        for py_file in project_path.rglob("*.py"):
            if any(part in ignored for part in py_file.parts):
                continue
            resolved = py_file.resolve()
            relative = str(py_file.relative_to(project_path)).replace("\\", "/")
            stem_norm = self._normalise_symbol(py_file.stem)
            path_norm = self._normalise_symbol(relative)

            directly_in_flow = resolved in explicit_paths
            name_match = any(name and (name == stem_norm or name in path_norm) for name in names)
            resource_match = any(token and token in path_norm for token in endpoint_tokens)

            if not (directly_in_flow or name_match or resource_match):
                continue

            try:
                result[relative] = py_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

        return result

    @staticmethod
    def _normalise_symbol(value: str) -> str:
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

    def _classify_python_git_diff(self, raw_diff: str) -> list[dict]:
        """Classify Python source changes for baseline history."""
        if not raw_diff:
            return []

        results: list[dict] = []
        current_file = None
        old_line = None
        new_line = None

        for line in raw_diff.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            if line.startswith("@@"):
                import re
                match = re.search(r"-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?", line)
                if match:
                    old_line = int(match.group(1))
                    new_line = int(match.group(2))
                continue
            if line.startswith("+") and not line.startswith("+++"):
                code = line[1:].strip()
                if current_file and current_file.endswith(".py"):
                    change_type = "SOURCE_CHANGED"
                    symbol = None
                    if code.startswith("def ") or code.startswith("async def "):
                        change_type = "FUNCTION_ADDED"
                        symbol = code.split("def ", 1)[1].split("(", 1)[0].strip()
                    elif "= Column(" in code or "= mapped_column(" in code or code.startswith("class "):
                        change_type = "MODEL_CHANGED"
                        symbol = code.split("=", 1)[0].strip() if "=" in code else code
                    elif ":" in code and "=" in code:
                        change_type = "FIELD_ADDED"
                        symbol = code.split(":", 1)[0].strip()
                    results.append({
                        "change_type": change_type,
                        "file_path": current_file,
                        "line_number": new_line,
                        "symbol": symbol,
                        "code": code,
                    })
                if new_line is not None:
                    new_line += 1
                continue
            if line.startswith("-") and not line.startswith("---"):
                code = line[1:].strip()
                if current_file and current_file.endswith(".py"):
                    results.append({
                        "change_type": "SOURCE_REMOVED",
                        "file_path": current_file,
                        "line_number": old_line,
                        "symbol": None,
                        "code": code,
                    })
                if old_line is not None:
                    old_line += 1
                continue
            if old_line is not None:
                old_line += 1
            if new_line is not None:
                new_line += 1

        return results

    def _migrate_legacy_if_needed(self, db: Session, method: str, endpoint: str, scenarios):
        project_path = self._project_path()
        existing = self.operation_repository.find_active(db, project_path, method, endpoint)
        if existing or not scenarios:
            return existing

        legacy_rows = []
        for scenario in scenarios:
            legacy = self.baseline_repository.find_active(db, scenario.id)
            if legacy:
                legacy_rows.append(legacy)
        if not legacy_rows:
            return None

        # Consolidate old scenario-level cards into one operation baseline.
        # Preserve the highest old version number as the starting operation
        # version so existing demos do not unexpectedly move backwards.
        state = self._current_state(method, endpoint)
        version = max(int(row.baseline_version or 1) for row in legacy_rows)
        row = OperationBaseline(
            project_path=project_path,
            operation_key=self._operation_key(method, endpoint),
            http_method=method,
            endpoint=endpoint,
            baseline_version=version,
            scenario_codes=[scenario.scenario_code for scenario in scenarios],
            scenario_contracts=self._scenario_contracts(scenarios),
            endpoint_flow=state["flow"],
            code_fingerprint=state["code_fingerprint"],
            flow_fingerprint=state["flow_fingerprint"],
            combined_fingerprint=state["combined_fingerprint"],
            is_active=True,
        )
        created = self.operation_repository.create(db, row)
        snapshot = OperationBaselineSourceSnapshot(
            baseline_id=created.id,
            source_snapshot=state["snapshot"],
            source_changes=[],
            git_diff=None,
        )
        self.operation_repository.create_snapshot(db, snapshot)
        return created

    def get_overview(self, db: Session, http_method: str | None = None):
        scenarios = ScenarioService().get_all_for_active_project(db)
        grouped: dict[tuple[str, str], list] = {}
        for scenario in scenarios:
            method = str(scenario.http_method or "").upper()
            endpoint = self._normalise_endpoint(scenario.endpoint)
            if http_method and method != http_method.upper().strip():
                continue
            grouped.setdefault((method, endpoint), []).append(scenario)

        project_path = self._project_path()
        result = []
        for (method, endpoint), rows in sorted(grouped.items()):
            latest = self.operation_repository.find_active(db, project_path, method, endpoint)
            if latest is None:
                try:
                    latest = self._migrate_legacy_if_needed(db, method, endpoint, rows)
                except Exception:
                    db.rollback()
                    latest = None
            capture_allowed = latest is None
            change_status = "NO_BASELINE" if latest is None else "UNCHANGED"
            current_fingerprint = None
            try:
                state = self._current_state(method, endpoint)
                current_fingerprint = state["combined_fingerprint"]
                if latest is not None and current_fingerprint != latest.combined_fingerprint:
                    capture_allowed = True
                    change_status = "CODE_CHANGED"
            except Exception:
                # Overview must stay usable even when one endpoint cannot be traced.
                change_status = "CHECK_ON_CAPTURE" if latest else "NO_BASELINE"
                capture_allowed = latest is None

            result.append({
                "operation_key": self._operation_key(method, endpoint),
                "http_method": method,
                "endpoint": endpoint,
                "scenario_count": len(rows),
                "scenario_codes": [row.scenario_code for row in rows],
                "scenario_ids": [row.id for row in rows],
                "baseline_captured": latest is not None,
                "active_baseline_version": latest.baseline_version if latest else None,
                "active_baseline_id": latest.id if latest else None,
                "history_count": len(self.operation_repository.find_history(db, project_path, method, endpoint)),
                "captured_at": latest.created_at.isoformat() if latest else None,
                "capture_allowed": capture_allowed,
                "change_status": change_status,
                "current_fingerprint": current_fingerprint,
            })
        return result

    def capture(self, db: Session, http_method: str, endpoint: str):
        method = http_method.upper().strip()
        endpoint = self._normalise_endpoint(endpoint)
        project_path = self._project_path()
        scenarios = self._operation_scenarios(db, method, endpoint)
        if not scenarios:
            raise HTTPException(status_code=404, detail="No registered scenarios found for this operation")

        state = self._current_state(method, endpoint)
        latest = self.operation_repository.find_latest(db, project_path, method, endpoint)

        if latest and latest.combined_fingerprint == state["combined_fingerprint"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "NO_RELEVANT_CODE_CHANGE",
                    "message": f"No relevant code or flow change detected. V{latest.baseline_version} remains the active baseline.",
                    "active_version": latest.baseline_version,
                    "operation": self._operation_key(method, endpoint),
                },
            )

        next_version = (latest.baseline_version + 1) if latest else 1
        self.operation_repository.deactivate_existing(db, project_path, method, endpoint)

        row = OperationBaseline(
            project_path=project_path,
            operation_key=self._operation_key(method, endpoint),
            http_method=method,
            endpoint=endpoint,
            baseline_version=next_version,
            scenario_codes=[scenario.scenario_code for scenario in scenarios],
            scenario_contracts=self._scenario_contracts(scenarios),
            endpoint_flow=state["flow"],
            code_fingerprint=state["code_fingerprint"],
            flow_fingerprint=state["flow_fingerprint"],
            combined_fingerprint=state["combined_fingerprint"],
            is_active=True,
        )
        created = self.operation_repository.create(db, row)

        git_diff = self._current_git_diff(Path(project_path))
        snapshot = OperationBaselineSourceSnapshot(
            baseline_id=created.id,
            source_snapshot=state["snapshot"],
            source_changes=self._classify_python_git_diff(git_diff),
            git_diff=git_diff or None,
        )
        self.operation_repository.create_snapshot(db, snapshot)

        return {
            "status": "BASELINE_CREATED",
            "operation": created.operation_key,
            "baseline_version": created.baseline_version,
            "baseline_id": created.id,
            "scenario_codes": created.scenario_codes,
            "message": f"V{created.baseline_version} captured for {created.operation_key}.",
        }

    def history(self, db: Session, http_method: str, endpoint: str):
        method = http_method.upper().strip()
        endpoint = self._normalise_endpoint(endpoint)
        rows = self.operation_repository.find_history(db, self._project_path(), method, endpoint)
        return [self._to_dict(row) for row in rows]

    def compare(self, db: Session, http_method: str, endpoint: str, from_version: int, to_version: int):
        method = http_method.upper().strip()
        endpoint = self._normalise_endpoint(endpoint)
        project_path = self._project_path()
        old = self.operation_repository.find_by_version(db, project_path, method, endpoint, from_version)
        new = self.operation_repository.find_by_version(db, project_path, method, endpoint, to_version)
        if not old or not new:
            raise HTTPException(
                status_code=404,
                detail="Both versions must belong to the same selected operation and project.",
            )

        old_snapshot_row = self.operation_repository.find_snapshot(db, old.id)
        new_snapshot_row = self.operation_repository.find_snapshot(db, new.id)
        old_sources = (old_snapshot_row.source_snapshot or {}) if old_snapshot_row else {}
        new_sources = (new_snapshot_row.source_snapshot or {}) if new_snapshot_row else {}
        source_diff = self._snapshot_diff(old_sources, new_sources)

        # Older V3 baselines may have been captured before Python model/schema
        # files were included in the source snapshot. Re-classify the Git diff
        # stored with the newer version so V1 -> V2 can still show the real
        # Python code changes instead of "0 changes".
        if new_snapshot_row and new_snapshot_row.git_diff:
            git_changes = self._classify_python_git_diff(new_snapshot_row.git_diff)
            if git_changes:
                source_diff["changes"] = git_changes
                source_diff["changed_files"] = sorted({
                    str(change.get("file_path"))
                    for change in git_changes
                    if change.get("file_path")
                })

        old_methods = self._flow_methods(old.endpoint_flow)
        new_methods = self._flow_methods(new.endpoint_flow)
        old_contracts = {c.get("scenario_code"): c for c in (old.scenario_contracts or [])}
        new_contracts = {c.get("scenario_code"): c for c in (new.scenario_contracts or [])}

        return {
            "operation": self._operation_key(method, endpoint),
            "http_method": method,
            "endpoint": endpoint,
            "from_version": from_version,
            "to_version": to_version,
            "source_comparison": source_diff,
            "flow_changed": old.flow_fingerprint != new.flow_fingerprint,
            "added_methods": sorted(new_methods - old_methods),
            "removed_methods": sorted(old_methods - new_methods),
            "scenario_changes": {
                "added": sorted(set(new_contracts) - set(old_contracts)),
                "removed": sorted(set(old_contracts) - set(new_contracts)),
                "changed": sorted(
                    code for code in set(old_contracts) & set(new_contracts)
                    if old_contracts[code] != new_contracts[code]
                ),
            },
            "changed": old.combined_fingerprint != new.combined_fingerprint,
        }

    @staticmethod
    def _snapshot_diff(old_sources: dict, new_sources: dict) -> dict:
        old_files = set(old_sources)
        new_files = set(new_sources)
        added_files = sorted(new_files - old_files)
        removed_files = sorted(old_files - new_files)
        modified_files = sorted(
            path for path in old_files & new_files
            if old_sources.get(path) != new_sources.get(path)
        )
        changes = []
        file_diffs = []

        for path in added_files:
            changes.append({"change_type": "FILE_ADDED", "file_path": path})
        for path in removed_files:
            changes.append({"change_type": "FILE_REMOVED", "file_path": path})

        for path in modified_files:
            changes.append({"change_type": "FILE_MODIFIED", "file_path": path})
            old_lines = str(old_sources.get(path) or "").splitlines()
            new_lines = str(new_sources.get(path) or "").splitlines()
            diff_lines = list(difflib.unified_diff(
                old_lines, new_lines,
                fromfile=f"VOLD/{path}", tofile=f"VNEW/{path}", lineterm=""
            ))
            added = [line[1:] for line in diff_lines if line.startswith("+") and not line.startswith("+++")]
            removed = [line[1:] for line in diff_lines if line.startswith("-") and not line.startswith("---")]
            file_diffs.append({
                "file_path": path,
                "added_lines": added[:100],
                "removed_lines": removed[:100],
                "diff": "\n".join(diff_lines[:300]),
            })

        return {
            "added_files": added_files,
            "removed_files": removed_files,
            "modified_files": modified_files,
            "changed_files": sorted(set(added_files + removed_files + modified_files)),
            "changes": changes,
            "file_diffs": file_diffs,
        }

    @staticmethod
    def _to_dict(row: OperationBaseline) -> dict:
        return {
            "id": row.id,
            "operation_key": row.operation_key,
            "http_method": row.http_method,
            "endpoint": row.endpoint,
            "baseline_version": row.baseline_version,
            "scenario_codes": row.scenario_codes or [],
            "scenario_contracts": row.scenario_contracts or [],
            "endpoint_flow": row.endpoint_flow,
            "is_active": row.is_active,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
