from __future__ import annotations

import ast
from pathlib import Path

from config import settings
from services.python.flow.python_code_flow_service import PythonCodeFlowService


class PythonEndpointFlowService:
    HTTP = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}

    def __init__(self):
        raw = getattr(settings, "PYTHON_PROJECT_PATH", None)
        if not raw:
            raise RuntimeError("PYTHON_PROJECT_PATH is not configured")
        self.root = Path(raw).expanduser().resolve()
        self.code_flow = PythonCodeFlowService()

    def discover_endpoints(self) -> list[dict]:
        result = []
        ignored = {".git", ".venv", "venv", "env", "__pycache__", "site-packages", "build", "dist"}
        for path in self.root.rglob("*.py"):
            if any(part in ignored for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
            except (OSError, SyntaxError):
                continue
            prefixes = self._router_prefixes(tree)
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in node.decorator_list:
                    parsed = self._decorator(dec, prefixes)
                    if not parsed:
                        continue
                    method, endpoint, router_name = parsed
                    result.append({
                        "http_method": method,
                        "endpoint": self._normalize(endpoint),
                        "class_name": path.stem,
                        "method_name": node.name,
                        "file_path": str(path),
                        "router": router_name,
                    })
        return result

    def analyze_endpoint(self, http_method: str, endpoint: str) -> dict:
        method = http_method.upper().strip()
        endpoint = self._normalize(endpoint)
        match = next((e for e in self.discover_endpoints() if e["http_method"] == method and self._paths_match(e["endpoint"], endpoint)), None)
        if not match:
            raise RuntimeError(f"No FastAPI endpoint found for {method} {endpoint}")
        flow = self.code_flow.analyze(match["class_name"], match["method_name"])
        return {
            "http_method": method,
            "endpoint": endpoint,
            "controller": {
                "class_name": match["class_name"],
                "method_name": match["method_name"],
                "file_path": match["file_path"],
            },
            "flow": flow["flow"],
            "simplified_flow": flow["simplified_flow"],
        }

    @staticmethod
    def _router_prefixes(tree: ast.Module) -> dict[str, str]:
        prefixes = {}
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            name = PythonEndpointFlowService._name(value.func)
            if name != "APIRouter":
                continue
            prefix = ""
            for kw in value.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    prefix = str(kw.value.value)
            for target in targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = prefix
        return prefixes

    def _decorator(self, dec: ast.AST, prefixes: dict[str, str]):
        if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
            return None
        verb = dec.func.attr.lower()
        if verb not in self.HTTP:
            return None
        router_name = dec.func.value.id if isinstance(dec.func.value, ast.Name) else "router"
        path = ""
        if dec.args and isinstance(dec.args[0], ast.Constant):
            path = str(dec.args[0].value)
        return self.HTTP[verb], prefixes.get(router_name, "") + path, router_name

    @staticmethod
    def _name(node):
        if isinstance(node, ast.Name): return node.id
        if isinstance(node, ast.Attribute): return node.attr
        return None

    @staticmethod
    def _normalize(path: str) -> str:
        path = "/" + (path or "").strip().strip("/")
        return path if path != "" else "/"

    @staticmethod
    def _paths_match(left: str, right: str) -> bool:
        def parts(path):
            return [p for p in path.strip("/").split("/") if p]
        a, b = parts(left), parts(right)
        if len(a) != len(b): return False
        return all(x == y or (x.startswith("{") and x.endswith("}")) or (y.startswith("{") and y.endswith("}")) for x, y in zip(a, b))
