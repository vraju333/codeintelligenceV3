from __future__ import annotations

from collections import defaultdict

from services.python.flow.python_endpoint_flow_service import PythonEndpointFlowService
from services.python.lineage.python_attribute_lineage_service import PythonAttributeLineageService
from services.scenario.scenario_service import ScenarioService
from services.jira.jira_knowledge_service import jira_knowledge_service


class PythonAttributeImpactService:
    def __init__(self):
        self.lineage = PythonAttributeLineageService()
        self.endpoint_flow = PythonEndpointFlowService()
        self.scenarios = ScenarioService()

    def analyze(self, attribute: str, db) -> dict:
        lineage = self.lineage.analyze(attribute)
        occurrences = lineage.get("occurrences", [])
        impacted_files = {o.get("file_path") for o in occurrences if o.get("file_path")}
        impacted_classes = {o.get("class_name") for o in occurrences if o.get("class_name")}
        impacted_methods = {
            f"{o.get('class_name')}.{o.get('method_name')}"
            for o in occurrences if o.get("class_name") and o.get("method_name")
        }

        grouped = defaultdict(lambda: {"classes": [], "methods": []})
        for item in occurrences:
            role = item.get("role") or "PYTHON_CODE"
            cls = item.get("class_name")
            method = item.get("method_name")
            if cls and cls not in grouped[role]["classes"]:
                grouped[role]["classes"].append(cls)
            if method:
                label = f"{cls}.{method}" if cls else method
                if label not in grouped[role]["methods"]:
                    grouped[role]["methods"].append(label)
        layers = [{"role": role, **values} for role, values in grouped.items()]

        affected_endpoints = []
        for endpoint in self.endpoint_flow.discover_endpoints():
            try:
                result = self.endpoint_flow.analyze_endpoint(endpoint["http_method"], endpoint["endpoint"])
            except Exception:
                continue
            nodes = self._flow_nodes(result.get("flow") or {})
            matched_classes = sorted({n.get("class_name") for n in nodes if n.get("class_name") in impacted_classes})
            matched_methods = sorted({
                f"{n.get('class_name')}.{n.get('method_name')}"
                for n in nodes
                if f"{n.get('class_name')}.{n.get('method_name')}" in impacted_methods
            })
            matched_files = {n.get("file_path") for n in nodes if n.get("file_path") in impacted_files}
            if not (matched_classes or matched_methods or matched_files):
                continue
            path = [f"{endpoint['http_method']} {endpoint['endpoint']}"]
            for n in nodes:
                label = f"{n.get('class_name')}.{n.get('method_name')}"
                if n.get("class_name") in impacted_classes or label in impacted_methods or n.get("file_path") in impacted_files:
                    path.append(label)
            affected_endpoints.append({
                "http_method": endpoint["http_method"],
                "endpoint": endpoint["endpoint"],
                "controller": endpoint["class_name"],
                "method_name": endpoint["method_name"],
                "relevance": "DIRECT" if matched_methods else "FLOW",
                "matched_methods": matched_methods,
                "matched_classes": matched_classes,
                "dependency_path": path[:8],
            })

        scenarios = self.scenarios.get_all_for_active_project(db)
        endpoint_keys = {(e["http_method"], e["endpoint"]) for e in affected_endpoints}
        affected_scenarios = []
        for scenario in scenarios:
            key = (str(scenario.http_method).upper(), scenario.endpoint)
            if key not in endpoint_keys:
                continue
            affected_scenarios.append({
                "scenario_code": scenario.scenario_code,
                "http_method": scenario.http_method,
                "endpoint": scenario.endpoint,
                "reasons": [f"Scenario uses an endpoint whose Python flow contains '{attribute}'."],
            })

        related_jiras = []
        try:
            for jira in jira_knowledge_service.search(db, attribute, top_k=5):
                related_jiras.append({
                    **jira,
                    "relationship": "SEMANTIC_RELATED",
                    "reasons": [f"Saved requirement is semantically related to '{attribute}'."],
                })
        except Exception:
            related_jiras = []

        score = min(95, 25 + len(occurrences) * 5 + len(affected_endpoints) * 12) if occurrences else 0
        return {
            "attribute": attribute,
            "project_path": lineage.get("project_path"),
            "total_occurrences": len(occurrences),
            "occurrences": occurrences,
            "layers": layers,
            "affected_endpoints": affected_endpoints,
            "affected_scenarios": affected_scenarios,
            "related_jiras": related_jiras,
            "confidence": {
                "score": score,
                "level": "HIGH" if score >= 75 else "MEDIUM" if score >= 45 else "LOW",
            },
        }

    @staticmethod
    def _flow_nodes(root: dict) -> list[dict]:
        result = []
        def walk(node):
            if not isinstance(node, dict):
                return
            result.append(node)
            for child in node.get("calls", []):
                walk(child)
        walk(root)
        return result
