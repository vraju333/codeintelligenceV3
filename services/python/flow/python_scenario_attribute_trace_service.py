from __future__ import annotations

import re

from services.python.flow.python_endpoint_flow_service import PythonEndpointFlowService


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


class PythonScenarioAttributeTraceService:
    """Builds a Java-V2-style defect trace for a FastAPI/Python endpoint."""

    def __init__(self):
        self.endpoint_service = PythonEndpointFlowService()
        self.code_flow_service = self.endpoint_service.code_flow

    def trace(self, http_method: str, endpoint: str, attribute_name: str, input_context: dict | None = None) -> dict:
        result = self.endpoint_service.analyze_endpoint(http_method, endpoint)
        flat = self.code_flow_service.flatten(result["flow"])
        trace = []
        for step in flat:
            evidence = self.code_flow_service.evidence_for_attribute(step, attribute_name)
            direct = bool(evidence)
            trace.append({
                "class_name": step.get("class_name"),
                "method_name": step.get("method_name"),
                "label": step.get("label") or f"{step.get('class_name','')}.{step.get('method_name','')}",
                "file_path": step.get("file_path"),
                "line_number": step.get("line_number"),
                "type": step.get("type"),
                "operation": step.get("operation"),
                "direct_attribute_touch": direct,
                "evidence": evidence,
            })

        # If a field is present in the response/request model but accessed through
        # whole-object conversion (model_dump / constructor), mapper steps are the
        # strongest likely locations even when the literal field name is absent.
        if not any(s["direct_attribute_touch"] for s in trace):
            for step in trace:
                owner = (step.get("class_name") or "").lower()
                method = (step.get("method_name") or "").lower()
                if "mapper" in owner or "mapper" in method or "response" in method or "entity" in method:
                    step["direct_attribute_touch"] = True
                    step["evidence"] = [{
                        "line_number": step.get("line_number"),
                        "usage_type": "MAPPING",
                        "code": None,
                    }]

        return {
            "http_method": http_method.upper().strip(),
            "endpoint": endpoint,
            "attribute": attribute_name,
            "controller": result["controller"],
            "trace": trace,
            "filtered_flow": result["flow"],
            "direct_attribute_methods": [
                f"{s['class_name']}.{s['method_name']}" for s in trace if s.get("direct_attribute_touch")
            ],
        }
