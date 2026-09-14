from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from config import settings


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


@dataclass
class Symbol:
    owner: str
    method: str
    file_path: Path
    node: ast.AST
    tree: ast.Module
    module: str


class PythonCodeFlowService:
    """AST based Python call graph used by V3 defect investigation.

    The analysed application is never imported or executed.
    """

    MAX_DEPTH = 12

    def __init__(self):
        raw = getattr(settings, "PYTHON_PROJECT_PATH", None)
        if not raw:
            raise RuntimeError("PYTHON_PROJECT_PATH is not configured")
        self.root = Path(raw).expanduser().resolve()
        self.symbols: dict[tuple[str, str], Symbol] = {}
        self.functions_by_name: dict[str, list[Symbol]] = {}
        self.class_fields: dict[str, dict[str, str]] = {}
        self.module_objects: dict[str, dict[str, str]] = {}
        self.imported_names: dict[str, dict[str, str]] = {}
        self._load()

    def _iter_py_files(self):
        ignored = {".git", ".venv", "venv", "env", "__pycache__", "site-packages", "build", "dist"}
        for path in self.root.rglob("*.py"):
            if not any(part in ignored for part in path.parts):
                yield path

    def _load(self):
        for path in self._iter_py_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(text, filename=str(path))
            except (OSError, SyntaxError):
                continue
            module = ".".join(path.relative_to(self.root).with_suffix("").parts)
            module_owner = path.stem
            self.module_objects.setdefault(module_owner, {})
            self.imported_names.setdefault(module_owner, {})

            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        self.imported_names[module_owner][alias.asname or alias.name] = alias.name
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.imported_names[module_owner][alias.asname or alias.name.split(".")[-1]] = alias.name.split(".")[-1]
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    name, class_name = self._assignment_constructor(node)
                    if name and class_name:
                        self.module_objects[module_owner][name] = class_name
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._add_symbol(Symbol(module_owner, node.name, path, node, tree, module))
                elif isinstance(node, ast.ClassDef):
                    fields: dict[str, str] = {}
                    for child in node.body:
                        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            self._add_symbol(Symbol(node.name, child.name, path, child, tree, module))
                            if child.name == "__init__":
                                for inner in ast.walk(child):
                                    if isinstance(inner, (ast.Assign, ast.AnnAssign)):
                                        field, klass = self._self_assignment_constructor(inner)
                                        if field and klass:
                                            fields[field] = klass
                    self.class_fields[node.name] = fields

    def _add_symbol(self, symbol: Symbol):
        self.symbols[(symbol.owner, symbol.method)] = symbol
        self.functions_by_name.setdefault(symbol.method, []).append(symbol)

    @staticmethod
    def _assignment_constructor(node):
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Call):
            return target.id, PythonCodeFlowService._call_name(value.func)
        return None, None

    @staticmethod
    def _self_assignment_constructor(node):
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and isinstance(value, ast.Call)
        ):
            return target.attr, PythonCodeFlowService._call_name(value.func)
        return None, None

    @staticmethod
    def _call_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    def analyze(self, owner: str, method: str) -> dict:
        symbol = self.symbols.get((owner, method))
        if not symbol:
            candidates = self.functions_by_name.get(method, [])
            symbol = candidates[0] if len(candidates) == 1 else None
        if not symbol:
            raise RuntimeError(f"Python function not found: {owner}.{method}")
        flow = self._trace(symbol, set(), 0)
        return {"start_class": symbol.owner, "start_method": symbol.method, "flow": flow, "simplified_flow": self.flatten(flow)}

    def _trace(self, symbol: Symbol, visited: set[tuple[str, str, str]], depth: int) -> dict:
        key = (str(symbol.file_path), symbol.owner, symbol.method)
        base = self._node_dict(symbol)
        if key in visited:
            return {**base, "recursive": True, "calls": []}
        if depth >= self.MAX_DEPTH:
            return {**base, "max_depth_reached": True, "calls": []}
        visited = set(visited)
        visited.add(key)
        calls = []
        for call in [n for n in ast.walk(symbol.node) if isinstance(n, ast.Call)]:
            target = self._resolve_call(symbol, call)
            if target is None:
                operation = self._framework_operation(call)
                if operation:
                    calls.append(operation)
                continue
            calls.append(self._trace(target, visited, depth + 1))
        return {**base, "calls": calls}

    def _resolve_call(self, symbol: Symbol, call: ast.Call) -> Symbol | None:
        func = call.func
        # direct function call: helper(...)
        if isinstance(func, ast.Name):
            same_module = [s for s in self.functions_by_name.get(func.id, []) if s.module == symbol.module]
            return same_module[0] if same_module else None

        if not isinstance(func, ast.Attribute):
            return None
        method = func.attr
        value = func.value

        # self.mapper.method()
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "self":
            target_class = self.class_fields.get(symbol.owner, {}).get(value.attr)
            if target_class:
                return self.symbols.get((target_class, method))

        # service.method() where service = Service()
        if isinstance(value, ast.Name):
            target_class = self.module_objects.get(symbol.file_path.stem, {}).get(value.id)
            if target_class:
                return self.symbols.get((target_class, method))
            # variable name often directly indicates class role; if method is unique use it.
            candidates = self.functions_by_name.get(method, [])
            if len(candidates) == 1:
                return candidates[0]

        # chained/simple attribute call where method uniquely belongs to project symbol
        candidates = self.functions_by_name.get(method, [])
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _framework_operation(self, call: ast.Call) -> dict | None:
        if not isinstance(call.func, ast.Attribute):
            return None
        method = call.func.attr
        op_map = {
            "add": "SAVE", "merge": "SAVE", "commit": "COMMIT", "flush": "FLUSH",
            "delete": "DELETE", "execute": "QUERY", "query": "QUERY", "get": "FIND",
            "refresh": "REFRESH", "rollback": "ROLLBACK"
        }
        if method not in op_map:
            return None
        owner = "DatabaseSession"
        return {
            "class_name": owner,
            "method_name": method,
            "label": f"{owner}.{method}",
            "type": "DATABASE",
            "operation": op_map[method],
            "file_path": None,
            "line_number": getattr(call, "lineno", None),
            "calls": [],
        }

    @staticmethod
    def _node_dict(symbol: Symbol) -> dict:
        return {
            "class_name": symbol.owner,
            "method_name": symbol.method,
            "label": f"{symbol.owner}.{symbol.method}",
            "file_path": str(symbol.file_path),
            "line_number": getattr(symbol.node, "lineno", None),
            "end_line_number": getattr(symbol.node, "end_lineno", None),
            "input_parameters": PythonCodeFlowService._method_parameters(symbol.node),
            "return_type": PythonCodeFlowService._return_type(symbol.node),
            "found": True,
        }

    @staticmethod
    def _method_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[dict]:
        parameters = []
        args = list(node.args.posonlyargs) + list(node.args.args)

        defaults_offset = len(args) - len(node.args.defaults)

        for index, arg in enumerate(args):
            if arg.arg in {"self", "cls"}:
                continue

            annotation = PythonCodeFlowService._annotation_text(arg.annotation)
            default_value = None

            if index >= defaults_offset and node.args.defaults:
                default_index = index - defaults_offset
                default_value = PythonCodeFlowService._annotation_text(
                    node.args.defaults[default_index]
                )

            display = arg.arg
            if annotation:
                display = f"{arg.arg}: {annotation}"
            if default_value:
                display = f"{display} = {default_value}"

            parameters.append(
                {
                    "name": arg.arg,
                    "type": annotation or "",
                    "default": default_value or "",
                    "display": display
                }
            )

        if node.args.vararg:
            parameters.append(
                {
                    "name": node.args.vararg.arg,
                    "type": PythonCodeFlowService._annotation_text(node.args.vararg.annotation) or "",
                    "default": "",
                    "display": f"*{node.args.vararg.arg}"
                }
            )

        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            annotation = PythonCodeFlowService._annotation_text(arg.annotation)
            default_value = PythonCodeFlowService._annotation_text(default)
            display = arg.arg
            if annotation:
                display = f"{arg.arg}: {annotation}"
            if default_value:
                display = f"{display} = {default_value}"
            parameters.append(
                {
                    "name": arg.arg,
                    "type": annotation or "",
                    "default": default_value or "",
                    "display": display
                }
            )

        if node.args.kwarg:
            parameters.append(
                {
                    "name": node.args.kwarg.arg,
                    "type": PythonCodeFlowService._annotation_text(node.args.kwarg.annotation) or "",
                    "default": "",
                    "display": f"**{node.args.kwarg.arg}"
                }
            )

        return parameters

    @staticmethod
    def _return_type(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        return PythonCodeFlowService._annotation_text(node.returns) or "Nothing"

    @staticmethod
    def _annotation_text(node: ast.AST | None) -> str | None:
        if node is None:
            return None
        try:
            return ast.unparse(node)
        except Exception:
            return getattr(node, "id", None)

    @staticmethod
    def flatten(flow: dict) -> list[dict]:
        result = []
        def walk(node):
            result.append({k: v for k, v in node.items() if k != "calls"})
            for child in node.get("calls", []) or []:
                walk(child)
        walk(flow)
        return result

    def evidence_for_attribute(self, step: dict, attribute: str) -> list[dict]:
        path = step.get("file_path")
        method = step.get("method_name")
        owner = step.get("class_name")
        if not path or not method:
            return []
        symbol = self.symbols.get((owner, method))
        if not symbol:
            candidates = [s for s in self.functions_by_name.get(method, []) if str(s.file_path) == path]
            symbol = candidates[0] if candidates else None
        if not symbol:
            return []
        target = _norm(attribute)
        lines = symbol.file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        evidence = []
        for node in ast.walk(symbol.node):
            names = []
            usage = "READ"
            if isinstance(node, ast.Attribute):
                names.append(node.attr)
                if isinstance(node.ctx, ast.Store): usage = "WRITE"
            elif isinstance(node, ast.Name):
                names.append(node.id)
                if isinstance(node.ctx, ast.Store): usage = "ASSIGNMENT"
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.append(node.value)
            if not any(_norm(name) == target or target in _norm(name) or _norm(name) in target for name in names if _norm(name)):
                continue
            lineno = getattr(node, "lineno", None)
            code = lines[lineno - 1].strip() if lineno and lineno <= len(lines) else None
            # Mapper methods are especially strong evidence.
            if "mapper" in owner.lower() or "map" in method.lower() or "response" in method.lower():
                usage = "MAPPING"
            evidence.append({"line_number": lineno, "usage_type": usage, "code": code})
        # dedupe by line/type
        seen, clean = set(), []
        for item in evidence:
            key = (item["line_number"], item["usage_type"], item["code"])
            if key not in seen:
                seen.add(key); clean.append(item)
        return clean[:8]
