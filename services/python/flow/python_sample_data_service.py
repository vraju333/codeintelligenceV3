from __future__ import annotations

import ast
from pathlib import Path

from config import settings
from services.python.flow.python_endpoint_flow_service import PythonEndpointFlowService


class PythonSampleDataService:
    """Generate scenario data from FastAPI handler and Pydantic model AST.

    Source is parsed only; the analyzed project is never imported/executed. This
    keeps the intelligence engine isolated from user application dependencies.
    """

    MAX_DEPTH = 5
    PRIMITIVES = {"str", "int", "float", "bool", "bytes", "Any"}
    INFRA_TYPES = {"Session", "AsyncSession", "Request", "Response", "BackgroundTasks"}

    def __init__(self):
        raw = settings.PYTHON_PROJECT_PATH
        if not raw:
            raise RuntimeError("PYTHON_PROJECT_PATH is not configured")
        self.project_path = Path(raw).expanduser().resolve()
        self.endpoint_service = PythonEndpointFlowService()
        self._class_index: dict[str, tuple[Path, ast.ClassDef]] | None = None

    def generate(self, http_method: str, endpoint: str) -> dict:
        method = (http_method or "").upper().strip()
        selected = self._find_endpoint(method, endpoint)
        source_path = Path(selected["file_path"])
        tree = ast.parse(source_path.read_text(encoding="utf-8", errors="ignore"), filename=str(source_path))
        handler = self._find_function(tree, selected["method_name"])
        if handler is None:
            raise RuntimeError(f"Handler {selected['method_name']} was not found")

        request_type = self._request_model(handler, selected["endpoint"])
        response_type = self._response_model(handler)
        request_json = self._sample_for_annotation(request_type, 0, "request") if request_type else None
        response_json = self._sample_for_annotation(response_type, 0, "response") if response_type else None

        return {
            "language": "PYTHON",
            "http_method": method,
            "endpoint": selected["endpoint"],
            "controller": selected["class_name"],
            "handler": selected["method_name"],
            "request_model": request_type,
            "response_model": response_type,
            "request_json": request_json,
            "expected_response_json": response_json,
            "expected_db_effect": self._db_effect(method, request_type),
        }

    def _find_endpoint(self, method: str, endpoint: str) -> dict:
        for item in self.endpoint_service.discover_endpoints():
            if item["http_method"] == method and self.endpoint_service._paths_match(item["endpoint"], endpoint):
                return item
        raise RuntimeError(f"No FastAPI endpoint found for {method} {endpoint}")

    @staticmethod
    def _find_function(tree: ast.Module, name: str):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
        return None

    def _request_model(self, handler: ast.FunctionDef | ast.AsyncFunctionDef, endpoint: str) -> str | None:
        path_names = {part[1:-1] for part in endpoint.split("/") if part.startswith("{") and part.endswith("}")}
        all_args = list(handler.args.posonlyargs) + list(handler.args.args) + list(handler.args.kwonlyargs)
        for arg in all_args:
            if arg.arg in path_names or arg.annotation is None:
                continue
            annotation = self._annotation_text(arg.annotation)
            base = self._base_annotation(annotation)
            if base in self.PRIMITIVES or base in self.INFRA_TYPES:
                continue
            if "Depends" in annotation:
                continue
            if self._find_model(base):
                return annotation
        return None

    def _response_model(self, handler: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
        for decorator in handler.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for keyword in decorator.keywords:
                if keyword.arg == "response_model":
                    return self._annotation_text(keyword.value)
        if handler.returns:
            text = self._annotation_text(handler.returns)
            if self._base_annotation(text) not in {"None", "Response"}:
                return text
        return None

    def _sample_for_annotation(self, annotation: str | None, depth: int, field_name: str):
        if not annotation or depth > self.MAX_DEPTH:
            return None
        annotation = annotation.strip()

        # PEP 604 Optional: T | None
        union_parts = [part.strip() for part in annotation.split("|")]
        non_none = [part for part in union_parts if part not in {"None", "NoneType"}]
        if len(union_parts) > 1 and non_none:
            return self._sample_for_annotation(non_none[0], depth + 1, field_name)

        # typing.Optional[T]
        inner = self._generic_inner(annotation, "Optional")
        if inner:
            return self._sample_for_annotation(inner, depth + 1, field_name)

        # list[T], List[T], set[T], Sequence[T]
        for outer in ("list", "List", "set", "Set", "Sequence", "Iterable"):
            inner = self._generic_inner(annotation, outer)
            if inner:
                item = self._sample_for_annotation(inner, depth + 1, field_name)
                return [] if item is None else [item]

        for outer in ("dict", "Dict"):
            inner = self._generic_inner(annotation, outer)
            if inner:
                parts = self._split_top_level(inner)
                value_type = parts[-1] if parts else "str"
                return {"key": self._sample_for_annotation(value_type, depth + 1, field_name)}

        base = self._base_annotation(annotation)
        if base in self.PRIMITIVES:
            return self._sample_scalar(base, field_name)

        model = self._find_model(base)
        if not model:
            return self._sample_scalar(base, field_name)
        _, class_node = model

        # Enum-style classes: class X(str, Enum): ...
        if any(self._base_annotation(self._annotation_text(base_node)) == "Enum" for base_node in class_node.bases):
            for child in class_node.body:
                if isinstance(child, ast.Assign) and child.targets and isinstance(child.targets[0], ast.Name):
                    if isinstance(child.value, ast.Constant):
                        return child.value.value
            return "SAMPLE"

        result = {}
        for child in class_node.body:
            if not isinstance(child, ast.AnnAssign) or not isinstance(child.target, ast.Name):
                continue
            py_name = child.target.id
            json_name = self._field_alias(child.value) or py_name
            field_type = self._annotation_text(child.annotation)
            result[json_name] = self._sample_for_annotation(field_type, depth + 1, py_name)
        return result

    def _class_index_data(self) -> dict[str, tuple[Path, ast.ClassDef]]:
        if self._class_index is not None:
            return self._class_index
        result = {}
        ignored = {".git", ".venv", "venv", "env", "__pycache__", "site-packages", "build", "dist"}
        for path in self.project_path.rglob("*.py"):
            if any(part in ignored for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
            except (OSError, SyntaxError):
                continue
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    result.setdefault(node.name, (path, node))
        self._class_index = result
        return result

    def _find_model(self, name: str):
        return self._class_index_data().get(self._base_annotation(name))

    @staticmethod
    def _field_alias(value: ast.AST | None) -> str | None:
        if not isinstance(value, ast.Call):
            return None
        func = PythonSampleDataService._base_annotation(PythonSampleDataService._annotation_text(value.func))
        if func != "Field":
            return None
        for keyword in value.keywords:
            if keyword.arg in {"alias", "serialization_alias"} and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
        return None

    @staticmethod
    def _annotation_text(node: ast.AST | None) -> str:
        if node is None:
            return ""
        try:
            return ast.unparse(node)
        except Exception:
            return getattr(node, "id", "")

    @staticmethod
    def _base_annotation(value: str) -> str:
        value = (value or "").strip()
        if "[" in value:
            value = value.split("[", 1)[0]
        return value.split(".")[-1].strip()

    @staticmethod
    def _generic_inner(value: str, outer: str) -> str | None:
        prefix_options = (f"{outer}[", f"typing.{outer}[")
        stripped = value.strip()
        for prefix in prefix_options:
            if stripped.startswith(prefix) and stripped.endswith("]"):
                return stripped[len(prefix):-1].strip()
        return None

    @staticmethod
    def _split_top_level(value: str) -> list[str]:
        parts, current = [], []
        depth = 0
        for char in value or "":
            if char in "[({<": depth += 1
            elif char in "])}>" and depth: depth -= 1
            if char == "," and depth == 0:
                item = "".join(current).strip()
                if item: parts.append(item)
                current = []
            else:
                current.append(char)
        item = "".join(current).strip()
        if item: parts.append(item)
        return parts

    @staticmethod
    def _sample_scalar(type_name: str, field_name: str):
        lower = (field_name or "").lower()
        base = PythonSampleDataService._base_annotation(type_name)
        if base == "bool": return True
        if base == "int":
            if "age" in lower: return 25
            if "year" in lower: return 2
            return 1
        if base == "float":
            if "gpa" in lower: return 8.2
            if "salary" in lower: return 75000.0
            return 1.0
        if "email" in lower: return "user@example.com"
        if any(token in lower for token in ("contact", "phone", "mobile")): return "9876543210"
        if "firstname" in lower or lower == "first_name": return "Ravi"
        if "lastname" in lower or lower == "last_name": return "Kumar"
        if "name" in lower: return "Sample User"
        if "line1" in lower or "address" in lower: return "10 Main Road"
        if "line2" in lower: return "Hitech City"
        if "city" in lower: return "Hyderabad"
        if "state" in lower: return "Telangana"
        if "country" in lower: return "India"
        if "postal" in lower or "zip" in lower or "pin" in lower: return "500081"
        if "industry" in lower: return "Technology"
        if "role" in lower: return "Developer"
        if "code" in lower or "number" in lower: return "SAMPLE001"
        return "sample"

    @staticmethod
    def _db_effect(method: str, request_type: str | None) -> str | None:
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if method == "DELETE":
            return "Selected resource is deleted from persistence."
        if request_type:
            return f"Persist fields supplied by {PythonSampleDataService._base_annotation(request_type)}."
        return "Persist changes performed by the selected operation."
