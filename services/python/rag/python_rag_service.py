from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from config import settings


class PythonRagService:
    IGNORED_DIRS = {".git", ".venv", "venv", "env", "__pycache__", "site-packages", "build", "dist"}

    def __init__(self):
        self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        self.splitter = RecursiveCharacterTextSplitter.from_language(
            language=Language.PYTHON, chunk_size=1200, chunk_overlap=150
        )
        self.index_root = Path("rag_indexes")
        self.index_root.mkdir(parents=True, exist_ok=True)
        self.vector_store: FAISS | None = None
        self.current_project_key: str | None = None
        self.current_index_path: Path | None = None
        self._load_current_project_index_if_valid()

    def index_project(self, force: bool = False) -> dict:
        root = self._project_root()
        py_files = self._python_files(root)
        project_key = self._project_key(root)
        index_path = self.index_root / project_key
        manifest_path = index_path / "manifest.json"
        fingerprint = self._fingerprint(root, py_files)
        existing = self._read_manifest(manifest_path)
        index_exists = self._index_exists(index_path)

        if not force and index_exists and existing and existing.get("project_path") == str(root) and existing.get("fingerprint") == fingerprint:
            self._load_index(index_path, project_key)
            return {**existing, "status": "reused", "message": "Python source is unchanged; existing RAG index reused."}

        documents = []
        for path in py_files:
            source = path.read_text(encoding="utf-8", errors="ignore")
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                tree = None
            module_name = self._module_name(root, path)
            chunks = self._ast_chunks(source, tree) if tree else []
            metadata = {
                "file_name": path.name,
                "file_path": str(path),
                "relative_path": str(path.relative_to(root)),
                "package_name": module_name,
                "class_name": path.stem,
                "language": "python",
                "project_key": project_key,
                "project_path": str(root),
            }
            if chunks:
                for owner, method, text in chunks:
                    documents.append(Document(page_content=text, metadata={
                        **metadata,
                        "class_name": owner or path.stem,
                        "method_name": method,
                        "chunk_type": "function" if method else "class",
                    }))
            else:
                documents.append(Document(page_content=source, metadata={**metadata, "method_name": None, "chunk_type": "module"}))

        if not documents:
            raise RuntimeError(f"No Python source documents were found under: {root}")

        split_docs = self.splitter.split_documents(documents)
        self.vector_store = FAISS.from_documents(split_docs, self.embeddings)
        index_path.mkdir(parents=True, exist_ok=True)
        self.vector_store.save_local(str(index_path))

        status = "updated" if index_exists or existing else "created"
        manifest = {
            "project_key": project_key,
            "project_name": root.name,
            "project_path": str(root),
            "fingerprint": fingerprint,
            "python_files": len(py_files),
            "documents": len(documents),
            "chunks": len(split_docs),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.current_project_key = project_key
        self.current_index_path = index_path
        return {**manifest, "index_path": str(index_path), "status": status,
                "message": "Existing Python index updated because source changed." if status == "updated" else "New Python project-specific RAG index created."}

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        expected = self._project_key(self._project_root())
        if self.current_project_key != expected:
            self.index_project()
        if self.vector_store is None:
            raise RuntimeError("RAG index does not exist. Call /api/rag/index first.")
        matches = self.vector_store.similarity_search_with_score(query=query, k=top_k)
        return [{
            "file_name": doc.metadata.get("file_name"),
            "class_name": doc.metadata.get("class_name"),
            "method_name": doc.metadata.get("method_name"),
            "chunk_type": doc.metadata.get("chunk_type"),
            "file_path": doc.metadata.get("file_path"),
            "project_key": doc.metadata.get("project_key"),
            "similarity_score": float(score),
            "content": doc.page_content,
        } for doc, score in matches]

    def _load_current_project_index_if_valid(self):
        try:
            root = self._project_root()
        except RuntimeError:
            return
        files = self._python_files(root)
        key = self._project_key(root)
        path = self.index_root / key
        manifest = self._read_manifest(path / "manifest.json")
        if manifest and self._index_exists(path) and manifest.get("fingerprint") == self._fingerprint(root, files):
            try:
                self._load_index(path, key)
            except Exception:
                self.vector_store = None

    def _load_index(self, path: Path, key: str):
        self.vector_store = FAISS.load_local(str(path), self.embeddings, allow_dangerous_deserialization=True)
        self.current_project_key = key
        self.current_index_path = path

    def _project_root(self) -> Path:
        raw = settings.PYTHON_PROJECT_PATH
        if not raw:
            raise RuntimeError("PYTHON_PROJECT_PATH is not configured")
        root = Path(raw).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise RuntimeError(f"Python project path does not exist: {raw}")
        return root

    def _python_files(self, root: Path) -> list[Path]:
        return sorted(p for p in root.rglob("*.py") if not any(part in self.IGNORED_DIRS for part in p.parts))

    @staticmethod
    def _project_key(root: Path) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "python-project"
        digest = hashlib.sha1(str(root).lower().encode()).hexdigest()[:10]
        return f"{safe}-{digest}"

    @staticmethod
    def _fingerprint(root: Path, files: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in files:
            digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _index_exists(path: Path) -> bool:
        return (path / "index.faiss").is_file() and (path / "index.pkl").is_file()

    @staticmethod
    def _read_manifest(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except Exception:
            return None

    @staticmethod
    def _module_name(root: Path, path: Path) -> str:
        rel = path.relative_to(root).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__": parts = parts[:-1]
        return ".".join(parts)

    @staticmethod
    def _ast_chunks(source: str, tree: ast.Module | None):
        if tree is None:
            return []
        result = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                class_text = ast.get_source_segment(source, node) or ""
                if class_text:
                    result.append((node.name, None, class_text))
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        text = ast.get_source_segment(source, child) or ""
                        if text:
                            result.append((node.name, child.name, text))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                text = ast.get_source_segment(source, node) or ""
                if text:
                    result.append((None, node.name, text))
        return result
