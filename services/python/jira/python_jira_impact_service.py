from __future__ import annotations

import re
from pathlib import Path

from config import settings
from services.python.flow.python_endpoint_flow_service import PythonEndpointFlowService
from services.python.rag.python_rag_service import PythonRagService
from services.python.scanner.python_scanner_service import PythonScannerService
from services.scenario.scenario_service import ScenarioService


class PythonJiraImpactService:
    STOP_WORDS = {"the","a","an","and","or","to","in","of","for","with","on","is","are","be","should","need","needs","new","add","update","create","return","store","save","persist","response","request"}

    def __init__(self, rag_service: PythonRagService | None = None):
        self.scanner = PythonScannerService()
        self.endpoint_flow = PythonEndpointFlowService()
        self.rag = rag_service or PythonRagService()
        self.scenarios = ScenarioService()

    def analyze(self, jira_id: str | None, requirement: str, db) -> dict:
        requirement = (requirement or "").strip()
        if not requirement:
            raise ValueError("Requirement / description is required")

        scan = self.scanner.scan()
        root = Path(scan.project_path)
        terms = self._terms(requirement)
        code_terms = self._code_terms(requirement)
        concepts = self._concepts(requirement, terms, code_terms)
        understanding = self._understanding(requirement, concepts)

        rag_hits = []
        try:
            rag_hits = self.rag.search(requirement, top_k=8)
        except Exception:
            try:
                self.rag.index_project()
                rag_hits = self.rag.search(requirement, top_k=8)
            except Exception:
                rag_hits = []

        file_scores = {}
        for path in root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lowered = text.lower()
            score = 0
            reasons = []
            for concept in concepts:
                variants = {concept.lower(), self._to_snake(concept).lower()}
                if any(v and v in lowered for v in variants):
                    score += 3
                    reasons.append(f"Source contains concept '{concept}'")
            for term in terms:
                if len(term) >= 4 and term in lowered:
                    score += 1
            if score:
                file_scores[str(path.resolve())] = {
                    "file_name": path.name,
                    "relative_path": str(path.relative_to(root)),
                    "file_path": str(path.resolve()),
                    "class_name": path.stem,
                    "score": score,
                    "reasons": reasons[:6],
                }

        for hit in rag_hits:
            path = hit.get("file_path")
            if not path:
                continue
            item = file_scores.setdefault(path, {
                "file_name": hit.get("file_name"),
                "relative_path": self._relative(root, path),
                "file_path": path,
                "class_name": hit.get("class_name"),
                "score": 0,
                "reasons": [],
            })
            item["score"] += 4
            item["reasons"].append("Matched local Python RAG context")

        likely_files = sorted(file_scores.values(), key=lambda x: (-x["score"], x.get("file_name") or ""))[:12]
        candidate_paths = {item["file_path"] for item in likely_files}

        affected_endpoints = []
        dependency_paths = []
        for endpoint in self.endpoint_flow.discover_endpoints():
            try:
                flow = self.endpoint_flow.analyze_endpoint(endpoint["http_method"], endpoint["endpoint"])
            except Exception:
                continue
            nodes = self._flow_nodes(flow.get("flow") or {})
            matched = [n for n in nodes if n.get("file_path") in candidate_paths]
            text_match = any(term in endpoint["endpoint"].lower() or term in endpoint["method_name"].lower() for term in terms if len(term) >= 4)
            if not matched and not text_match:
                continue
            reasons = []
            if matched:
                reasons.append("Execution flow intersects likely Python source files")
            if text_match:
                reasons.append("Route/function name matches requirement concepts")
            affected_endpoints.append({
                "http_method": endpoint["http_method"],
                "endpoint": endpoint["endpoint"],
                "controller": endpoint["class_name"],
                "method_name": endpoint["method_name"],
                "reasons": reasons,
            })
            path = [f"{endpoint['http_method']} {endpoint['endpoint']}"]
            for node in nodes:
                if node.get("file_path") in candidate_paths:
                    path.append(f"{node.get('class_name')}.{node.get('method_name')}")
            dependency_paths.append({"endpoint": f"{endpoint['http_method']} {endpoint['endpoint']}", "path": path[:10]})

        endpoint_keys = {(e["http_method"], e["endpoint"]) for e in affected_endpoints}
        affected_scenarios = []
        for scenario in self.scenarios.get_all_for_active_project(db):
            if (str(scenario.http_method).upper(), scenario.endpoint) in endpoint_keys:
                affected_scenarios.append({
                    "scenario_code": scenario.scenario_code,
                    "http_method": scenario.http_method,
                    "endpoint": scenario.endpoint,
                    "reasons": ["Scenario uses an affected FastAPI endpoint"],
                })

        score = min(95, 20 + len(likely_files)*4 + len(affected_endpoints)*10 + len(affected_scenarios)*5)
        return {
            "status": "ANALYSIS_COMPLETE",
            "jira_id": (jira_id or "").strip() or None,
            "project_path": scan.project_path,
            "requirement": requirement,
            "requirement_understanding": {
                **understanding,
                "concepts": concepts,
                "code_terms": code_terms,
                "intents": self._intents(requirement),
                "note": "Requirement understanding is local; Python source evidence comes from AST + local RAG.",
            },
            "project_match": {"status": "MATCH" if likely_files else "WEAK_MATCH", "message": "Python project inspected locally."},
            "warnings": [],
            "likely_files": likely_files,
            "affected_endpoints": affected_endpoints,
            "affected_scenarios": affected_scenarios,
            "dependency_paths": dependency_paths,
            "confidence": {"score": score, "level": "HIGH" if score >= 75 else "MEDIUM" if score >= 45 else "LOW"},
            "analysis_basis": {
                "python_classes_scanned": len(scan.classes),
                "endpoints_scanned": len(self.endpoint_flow.discover_endpoints()),
                "rag_hits_used": len(rag_hits),
                "project_driven": True,
                "llm_used": False,
                "source_code_sent_to_llm": False,
            },
        }

    def _terms(self, text):
        return [t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower()) if t not in self.STOP_WORDS]

    @staticmethod
    def _code_terms(text):
        return list(dict.fromkeys(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", text)))

    def _concepts(self, text, terms, code_terms):
        values = []
        for value in code_terms + terms:
            if len(value) < 3 or value.lower() in self.STOP_WORDS:
                continue
            if value not in values:
                values.append(value)
        return values[:12]

    def _understanding(self, requirement, concepts):
        lower = requirement.lower()
        action = "CHANGE"
        if any(w in lower for w in ["add ", "introduce", "create "]): action = "ADD"
        elif any(w in lower for w in ["delete", "remove"]): action = "REMOVE"
        elif any(w in lower for w in ["update", "change", "modify"]): action = "UPDATE"
        attribute = concepts[0] if concepts else None
        return {"action": action, "attribute": attribute, "parent": None, "entity": None,
                "attributes": concepts[:5], "condition": None, "desired_behavior": requirement[:180]}

    @staticmethod
    def _intents(text):
        lower = text.lower(); result = []
        for name, words in {"ADD":["add","create","introduce"],"UPDATE":["update","change","modify"],"REMOVE":["remove","delete"],"READ":["return","fetch","show"],"PERSIST":["save","store","persist"]}.items():
            if any(w in lower for w in words): result.append(name)
        return result or ["CHANGE"]

    @staticmethod
    def _to_snake(value):
        value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
        return re.sub(r"[\s-]+", "_", value).lower()

    @staticmethod
    def _relative(root, value):
        try: return str(Path(value).resolve().relative_to(root))
        except Exception: return str(value)

    @staticmethod
    def _flow_nodes(root):
        result = []
        def walk(node):
            if not isinstance(node, dict): return
            result.append(node)
            for child in node.get("calls", []): walk(child)
        walk(root); return result
