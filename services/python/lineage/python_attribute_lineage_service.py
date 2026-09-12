from __future__ import annotations

import ast
import re
from pathlib import Path

from config import settings


class PythonAttributeLineageService:
    IGNORED_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "site-packages", "build", "dist"}

    def __init__(self):
        raw = settings.PYTHON_PROJECT_PATH
        if not raw:
            raise RuntimeError("PYTHON_PROJECT_PATH is not configured")
        self.root = Path(raw).expanduser().resolve()

    def analyze(self, attribute_name: str) -> dict:
        attribute = (attribute_name or "").strip()
        if not attribute:
            raise RuntimeError("attribute_name is required")

        normalized = self._normalize(attribute)
        occurrences = []

        for path in sorted(self.root.rglob("*.py")):
            if any(part in self.IGNORED_DIRS for part in path.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(path))
            except (OSError, SyntaxError):
                continue

            parents = self._parent_map(tree)
            lines = source.splitlines()
            seen = set()
            for node in ast.walk(tree):
                matched = None
                if isinstance(node, ast.Name) and self._normalize(node.id) == normalized:
                    matched = node.id
                elif isinstance(node, ast.Attribute) and self._normalize(node.attr) == normalized:
                    matched = node.attr
                elif isinstance(node, ast.arg) and self._normalize(node.arg) == normalized:
                    matched = node.arg
                elif isinstance(node, ast.keyword) and node.arg and self._normalize(node.arg) == normalized:
                    matched = node.arg
                elif isinstance(node, ast.Constant) and isinstance(node.value, str) and self._normalize(node.value) == normalized:
                    matched = node.value
                elif isinstance(node, ast.AnnAssign):
                    target = self._target_name(node.target)
                    if target and self._normalize(target) == normalized:
                        matched = target

                if not matched:
                    continue

                line_no = getattr(node, "lineno", None)
                owner_class, owner_method = self._owners(node, parents)
                key = (str(path), line_no, owner_class, owner_method, matched)
                if key in seen:
                    continue
                seen.add(key)
                occurrences.append({
                    "file_path": str(path),
                    "file_name": path.name,
                    "relative_path": str(path.relative_to(self.root)),
                    "class_name": owner_class or path.stem,
                    "method_name": owner_method,
                    "attribute": matched,
                    "line_number": line_no,
                    "line": lines[line_no - 1].strip() if line_no and line_no <= len(lines) else "",
                    "role": self._role(path, owner_class),
                })

            # Also match snake_case/camelCase lexical forms that AST may not expose
            # as one node (e.g. SQLAlchemy column vs Pydantic alias conventions).
            for idx, line in enumerate(lines, start=1):
                tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line)
                if not any(self._normalize(tok) == normalized for tok in tokens):
                    continue
                if any(item["file_path"] == str(path) and item["line_number"] == idx for item in occurrences):
                    continue
                occurrences.append({
                    "file_path": str(path),
                    "file_name": path.name,
                    "relative_path": str(path.relative_to(self.root)),
                    "class_name": path.stem,
                    "method_name": None,
                    "attribute": attribute,
                    "line_number": idx,
                    "line": line.strip(),
                    "role": self._role(path, None),
                })

        return {
            "attribute": attribute,
            "project_path": str(self.root),
            "total_occurrences": len(occurrences),
            "occurrences": occurrences,
        }

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    @staticmethod
    def _target_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        return parents

    @staticmethod
    def _owners(node: ast.AST, parents: dict[ast.AST, ast.AST]):
        owner_class = None
        owner_method = None
        current = node
        while current in parents:
            current = parents[current]
            if owner_method is None and isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner_method = current.name
            if owner_class is None and isinstance(current, ast.ClassDef):
                owner_class = current.name
                break
        return owner_class, owner_method

    @staticmethod
    def _role(path: Path, owner_class: str | None) -> str:
        parts = {part.lower() for part in path.parts}
        name = (owner_class or path.stem).lower()
        if "schemas" in parts or "schema" in name or "request" in name or "response" in name:
            return "SCHEMA"
        if "models" in parts or "model" in name or "entity" in name:
            return "MODEL"
        if "mappers" in parts or "mapper" in name:
            return "MAPPER"
        if "services" in parts or "service" in name:
            return "SERVICE"
        if "repositories" in parts or "repository" in name:
            return "REPOSITORY"
        if "controllers" in parts or "routers" in parts or "controller" in name:
            return "ROUTER"
        return "PYTHON_CODE"
