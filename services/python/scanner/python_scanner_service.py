from __future__ import annotations

import ast
from pathlib import Path

from fastapi import HTTPException

from config import settings
from schemas import JavaClassInfo, JavaMethod, ScanResponse


class PythonScannerService:
    """AST-based Python project scanner.

    The response intentionally keeps the existing ScanResponse/ClassInfo shape so
    the V2/V3 UI and scenario code do not need a second contract just for Python.
    `class_type` is PYTHON_CLASS or PYTHON_MODULE and `package` is the module path.
    """

    IGNORED_DIRS = {
        ".git", ".idea", ".vscode", ".venv", "venv", "env", "__pycache__",
        "site-packages", "node_modules", "dist", "build", ".pytest_cache",
    }

    def scan(self) -> ScanResponse:
        project_path = settings.PYTHON_PROJECT_PATH
        if not project_path:
            raise HTTPException(status_code=500, detail="PYTHON_PROJECT_PATH is not configured in .env")

        root = Path(project_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise HTTPException(status_code=400, detail=f"Python project path does not exist: {project_path}")

        python_files = self._python_files(root)
        classes: list[JavaClassInfo] = []

        for file_path in python_files:
            classes.extend(self._scan_file(root, file_path))

        # Backward-compatible ScanResponse uses total_java_files.  The Python
        # UI reads it as a generic source-file count; project APIs also expose
        # total_python_files explicitly.
        return ScanResponse(
            project_path=str(root),
            total_java_files=len(python_files),
            classes=classes,
        )

    def _python_files(self, root: Path) -> list[Path]:
        result = []
        for path in root.rglob("*.py"):
            if any(part in self.IGNORED_DIRS for part in path.parts):
                continue
            result.append(path)
        return sorted(result)

    def _scan_file(self, root: Path, file_path: Path) -> list[JavaClassInfo]:
        try:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source, filename=str(file_path))
        except (OSError, SyntaxError):
            return []

        module_name = self._module_name(root, file_path)
        imports = self._imports(tree)
        result: list[JavaClassInfo] = []

        module_functions = [
            self._method_info(node)
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if module_functions:
            result.append(
                JavaClassInfo(
                    file_path=str(file_path),
                    package=module_name,
                    class_name=file_path.stem,
                    class_type="PYTHON_MODULE",
                    extends=None,
                    implements=[],
                    annotations=[],
                    imports=imports,
                    methods=module_functions,
                )
            )

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = [self._expr_name(base) for base in node.bases]
            methods = [
                self._method_info(child)
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name != "__init__"
            ]
            decorators = [self._expr_name(item) for item in node.decorator_list]
            result.append(
                JavaClassInfo(
                    file_path=str(file_path),
                    package=module_name,
                    class_name=node.name,
                    class_type="PYTHON_CLASS",
                    extends=bases[0] if bases else None,
                    implements=bases[1:] if len(bases) > 1 else [],
                    annotations=[item for item in decorators if item],
                    imports=imports,
                    methods=methods,
                )
            )

        # A file with assignments/models but no functions/classes should still
        # be visible to RAG/project diagnostics.
        if not result:
            result.append(
                JavaClassInfo(
                    file_path=str(file_path),
                    package=module_name,
                    class_name=file_path.stem,
                    class_type="PYTHON_MODULE",
                    extends=None,
                    implements=[],
                    annotations=[],
                    imports=imports,
                    methods=[],
                )
            )
        return result

    def _method_info(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> JavaMethod:
        params = []
        args = list(node.args.posonlyargs) + list(node.args.args)
        for arg in args:
            if arg.arg in {"self", "cls"}:
                continue
            annotation = self._expr_name(arg.annotation) if arg.annotation else None
            params.append(f"{arg.arg}: {annotation}" if annotation else arg.arg)
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")
        return JavaMethod(
            name=node.name,
            return_type=self._expr_name(node.returns) if node.returns else "Nothing",
            parameters=params,
        )

    @staticmethod
    def _imports(tree: ast.AST) -> list[str]:
        values = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                values.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    values.append(f"{module}.{alias.name}".strip("."))
        return sorted(set(values))

    @staticmethod
    def _expr_name(node: ast.AST | None) -> str | None:
        if node is None:
            return None
        try:
            return ast.unparse(node)
        except Exception:
            return getattr(node, "id", None)

    @staticmethod
    def _module_name(root: Path, file_path: Path) -> str:
        relative = file_path.relative_to(root).with_suffix("")
        parts = list(relative.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)
