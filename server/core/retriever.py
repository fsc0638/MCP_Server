import os
from pathlib import Path
from typing import List, Dict, Any, Optional
import logging
import uuid

# FAISS and Langchain
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader, CSVLoader

from adapters import extract_tags
from core.executor import ExecutionEngine

logger = logging.getLogger("MCP_Server.Retriever")

# Ensure paths
PROJECT_ROOT = Path(os.path.abspath(__file__)).parent.parent
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
WORKSPACE_DIR.mkdir(exist_ok=True)

# FAISS requires an ASCII-only path (no Chinese characters)
# Use user's home directory to safely store the index
FAISS_DB_DIR = Path.home() / ".mcp_faiss"
FAISS_DB_DIR.mkdir(exist_ok=True)

# Initialize executor engine just to use its sanitize_path locally
_dummy_engine = ExecutionEngine(skills_home=WORKSPACE_DIR)

class DocumentRetriever:
    def __init__(self):
        # Use a multilingual embedding model better suited for Traditional Chinese and English
        self.embedding_fn = HuggingFaceEmbeddings(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
        
        # Load FAISS index if it exists, otherwise start with None
        self.vectorstore = None
        if FAISS_DB_DIR.exists() and list(FAISS_DB_DIR.glob("*.faiss")):
            try:
                self.vectorstore = FAISS.load_local(
                    str(FAISS_DB_DIR), 
                    self.embedding_fn,
                    allow_dangerous_deserialization=True
                )
            except Exception as e:
                logger.error(f"Failed to load FAISS index: {e}")
        
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            separators=["\n\n", "\n", "。", "！", "？", " ", ""]
        )

    def ingest_document(self, file_path: str) -> bool:
        """
        Parses a document, splits it into chunks, extracts keywords, and saves to FAISS.
        """
        try:
            # Resolve path: accept absolute paths directly, resolve relative against WORKSPACE_DIR
            p = Path(file_path)
            safe_path = p.resolve() if p.is_absolute() else (WORKSPACE_DIR / p).resolve()

            # Security: ensure path stays within WORKSPACE_DIR
            if not str(safe_path).startswith(str(WORKSPACE_DIR.resolve())):
                logger.error(f"Security: path outside workspace: {safe_path}")
                return False
            
            if not safe_path.exists():
                logger.error(f"File not found: {safe_path}")
                return False

            ext = safe_path.suffix.lower()
            filename = safe_path.name

            # Load document content
            if ext == ".pdf":
                loader = PyPDFLoader(str(safe_path))
                docs = loader.load()
                text = "\n".join([doc.page_content for doc in docs])
            elif ext in [".txt", ".md"]:
                loader = TextLoader(str(safe_path), autodetect_encoding=True)
                docs = loader.load()
                text = "\n".join([doc.page_content for doc in docs])
            elif ext == ".csv":
                loader = CSVLoader(str(safe_path), encoding="utf-8")
                docs = loader.load()
                text = "\n".join([doc.page_content for doc in docs])
            elif ext == ".docx":
                try:
                    import docx2txt
                    text = docx2txt.process(str(safe_path))
                except ImportError:
                    logger.error("docx2txt not installed. Cannot parse DOCX files.")
                    return False
            else:
                logger.warning(f"Unsupported file type for retriever: {ext}")
                return False

            if not text.strip():
                logger.warning(f"Empty content from file: {filename}")
                return False

            logger.info(f"Chunking document: {filename} ({len(text)} characters)")
            chunks = self.text_splitter.split_text(text)
            
            documents = []
            metadatas = []

            for i, chunk in enumerate(chunks):
                # Keyword extraction
                tags = extract_tags(chunk)
                tags_str = ", ".join(tags)

                # Sprint 4: Keyword Boosting. Prepend keywords to chunk to increase vector similarity
                enhanced_chunk = f"Meta-Keywords: {tags_str}\n\n{chunk}" if tags_str else chunk

                documents.append(enhanced_chunk)
                metadatas.append({
                    "filename": filename,
                    "chunk_index": i,
                    "keywords": tags_str
                })

            # Add to FAISS
            if self.vectorstore is None:
                self.vectorstore = FAISS.from_texts(documents, self.embedding_fn, metadatas=metadatas)
            else:
                self.vectorstore.add_texts(documents, metadatas=metadatas)
                
            # Persist to disk
            FAISS_DB_DIR.mkdir(exist_ok=True)
            self.vectorstore.save_local(str(FAISS_DB_DIR))
            
            logger.info(f"Successfully ingested {filename} into {len(chunks)} FAISS chunks.")
            return True

        except Exception as e:
            logger.error(f"Failed to ingest document {file_path}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def search_context(self, query: str, top_k: int = 3, filter_type: str = "workspace", allowed_filenames: list = None) -> str:
        """
        Retrieves relevant context based on query semantic similarity.
        When allowed_filenames is provided, uses diversified retrieval to ensure
        at least one chunk per selected file is included.

        Args:
            query: The search query.
            top_k: Number of chunks to retrieve.
            filter_type: 'workspace' (default, requires file extension), 'skill' (no extension), or 'all'.
            allowed_filenames: List of filenames to restrict workspace retrieval to.
        """
        if self.vectorstore is None:
            return ""
        
        # Load filename mapping from .names.json (original name registry)
        import json
        _names_file = WORKSPACE_DIR / ".names.json"
        try:
            _fn_map = json.loads(_names_file.read_text(encoding="utf-8")) if _names_file.exists() else {}
        except Exception:
            _fn_map = {}
            
        try:
            # When we have specific files selected, ensure we get chunks from EACH file
            if allowed_filenames and filter_type == "workspace":
                return self._diversified_search(query, top_k, allowed_filenames)

            # Default path: standard top-k similarity search
            fetch_multiplier = 20 if allowed_filenames else 4
            fetch_k = top_k * fetch_multiplier if filter_type != "all" else top_k
            docs = self.vectorstore.similarity_search(query, k=fetch_k)

            if not docs:
                return ""

            logger.info(f"Retriever: query='{query}', k={top_k}, filter='{filter_type}'")
            context_parts = []
            for doc in docs:
                filename = doc.metadata.get("filename", "Unknown")
                has_ext = bool(Path(filename).suffix)

                if filter_type == "workspace" and not has_ext:
                    continue
                if filter_type == "skill" and has_ext:
                    continue

                # Check if workspace file actually exists on disk (Self-healing)
                if has_ext:
                    if not (WORKSPACE_DIR / filename).exists():
                        continue
                
                type_label = "File" if has_ext else "Skill"
                keywords = doc.metadata.get("keywords", "")
                chunk_idx = doc.metadata.get("chunk_index", 0)
                
                # Use original name from registry if available
                display_name = _fn_map.get(filename, filename) if has_ext else filename
                
                context_chunk = f"{type_label} [{display_name}#chunk_{chunk_idx}]:\n"
                if keywords:
                    context_chunk += f"(Keywords: {keywords})\n"
                context_chunk += f"{doc.page_content}\n"
                context_parts.append(context_chunk)
                logger.debug(f"Retriever: Selected [{filename}] (type={type_label})")

                if len(context_parts) >= top_k:
                    break
            
            logger.info(f"Retriever: Found {len(context_parts)} context parts.")
            return "\n---\n".join(context_parts)
            
        except Exception as e:
            logger.error(f"Search context failed: {e}")
            return ""

    def _diversified_search(self, query: str, top_k: int, allowed_filenames: list) -> str:
        """
        Diversified retrieval: guarantees at least 1 chunk per selected file,
        then fills remaining slots with globally most relevant chunks.
        """
        # Load filename mapping from .names.json (original name registry)
        import json
        _names_file = WORKSPACE_DIR / ".names.json"
        try:
            _fn_map = json.loads(_names_file.read_text(encoding="utf-8")) if _names_file.exists() else {}
        except Exception:
            _fn_map = {}

        # Fetch a large pool of candidates
        num_files = len(allowed_filenames)
        fetch_k = max(top_k, num_files) * 20
        docs = self.vectorstore.similarity_search(query, k=fetch_k)

        if not docs:
            return ""

        # Bucket candidates by file, preserving similarity order
        per_file: dict = {fn: [] for fn in allowed_filenames}
        overflow = []  # candidates for remaining slots

        for doc in docs:
            filename = doc.metadata.get("filename", "Unknown")
            if not Path(filename).suffix:  # skip skill chunks
                continue
            if filename not in per_file:
                continue
            
            # Self-healing: skip chunks from files missing on disk
            if not (WORKSPACE_DIR / filename).exists():
                continue

            per_file[filename].append(doc)

        # Phase 1: pick the best chunk from each file (round-robin diversity)
        selected = []
        seen_keys = set()
        for fn in allowed_filenames:
            candidates = per_file.get(fn, [])
            if candidates:
                best = candidates[0]
                key = (best.metadata.get("filename"), best.metadata.get("chunk_index"))
                selected.append(best)
                seen_keys.add(key)

        # Phase 2: fill remaining slots with globally best chunks (across all files)
        remaining = max(0, top_k - len(selected))
        if remaining > 0:
            for fn in allowed_filenames:
                for doc in per_file.get(fn, [])[1:]:  # skip first (already picked)
                    key = (doc.metadata.get("filename"), doc.metadata.get("chunk_index"))
                    if key not in seen_keys:
                        overflow.append(doc)
            # overflow is already in similarity order from the original search
            selected.extend(overflow[:remaining])

        # Format output
        context_parts = []
        for doc in selected:
            filename = doc.metadata.get("filename", "Unknown")
            has_ext = bool(Path(filename).suffix)
            
            # Self-healing: skip chunks from files missing on disk
            if has_ext and not (WORKSPACE_DIR / filename).exists():
                continue

            type_label = "File" if has_ext else "Skill"
            keywords = doc.metadata.get("keywords", "")
            chunk_idx = doc.metadata.get("chunk_index", 0)

            # Use original name from registry if available
            display_name = _fn_map.get(filename, filename) if has_ext else filename

            context_chunk = f"{type_label} [{display_name}#chunk_{chunk_idx}]:\n"
            if keywords:
                context_chunk += f"(Keywords: {keywords})\n"
            context_chunk += f"{doc.page_content}\n"
            context_parts.append(context_chunk)

        return "\n---\n".join(context_parts)


    def delete_document(self, filename: str) -> bool:
        """
        Remove all chunks belonging to a specific filename from FAISS index.
        Rebuilds the in-memory index excluding chunks of the given filename.
        """
        if self.vectorstore is None:
            return True  # Nothing to delete

        try:
            # Get all stored documents
            index = self.vectorstore.index
            docstore = self.vectorstore.docstore
            index_to_docstore_id = self.vectorstore.index_to_docstore_id

            # Collect chunks that should be KEPT (not from this file)
            keep_texts = []
            keep_metas = []
            for i, doc_id in index_to_docstore_id.items():
                doc = docstore.search(doc_id)
                if doc and doc.metadata.get("filename") != filename:
                    keep_texts.append(doc.page_content)
                    keep_metas.append(doc.metadata)

            if not keep_texts:
                # No documents remain — clear the index
                self.vectorstore = None
                # Remove FAISS files if they exist
                for f in FAISS_DB_DIR.glob("*"):
                    f.unlink(missing_ok=True)
                logger.info(f"All documents removed. FAISS index cleared.")
                return True

            # Rebuild index from the surviving chunks
            self.vectorstore = FAISS.from_texts(keep_texts, self.embedding_fn, metadatas=keep_metas)
            FAISS_DB_DIR.mkdir(exist_ok=True)
            self.vectorstore.save_local(str(FAISS_DB_DIR))
            logger.info(f"Deleted '{filename}' from FAISS. {len(keep_texts)} chunks remain.")
            return True

        except Exception as e:
            logger.error(f"Failed to delete '{filename}' from FAISS: {e}")
            return False

    def ingest_skill(self, skill_name: str, skill_md_path: str) -> bool:
        """
        Ingest a SKILL.md into FAISS. The skill_name is used as the identifier
        (filename field in metadata) so users can query by skill name.
        """
        try:
            path = Path(skill_md_path)
            if not path.exists():
                return False

            text = path.read_text(encoding="utf-8").strip()
            if not text:
                return False

            # Remove old chunks for this skill before re-ingesting
            self.delete_document(skill_name)

            chunks = self.text_splitter.split_text(text)
            tags = extract_tags(text, name=skill_name)
            tags_str = ", ".join(tags)

            # Sprint 4: Keyword Boosting
            enhanced_chunks = [f"Meta-Keywords: {tags_str}\n\n{c}" if tags_str else c for c in chunks]

            documents = enhanced_chunks
            metadatas = [{"filename": skill_name, "chunk_index": i, "keywords": tags_str} for i, _ in enumerate(chunks)]

            if self.vectorstore is None:
                self.vectorstore = FAISS.from_texts(documents, self.embedding_fn, metadatas=metadatas)
            else:
                self.vectorstore.add_texts(documents, metadatas=metadatas)

            FAISS_DB_DIR.mkdir(exist_ok=True)
            self.vectorstore.save_local(str(FAISS_DB_DIR))
            logger.info(f"Skill '{skill_name}' ingested into FAISS ({len(chunks)} chunks).")
            return True

        except Exception as e:
            logger.error(f"Failed to ingest skill '{skill_name}': {e}")
            return False

    def list_indexed_files(self) -> list:
        """Return a unique list of indexed filenames in FAISS."""
        if self.vectorstore is None:
            return []
        try:
            seen = set()
            for doc_id in self.vectorstore.index_to_docstore_id.values():
                doc = self.vectorstore.docstore.search(doc_id)
                if doc:
                    seen.add(doc.metadata.get("filename", "unknown"))
            return sorted(seen)
        except Exception as e:
            logger.error(f"list_indexed_files error: {e}")
            return []

    def sync_workspace(self, workspace_dir) -> dict:
        """
        Startup sync: ensure every supported file in workspace/ is indexed in FAISS,
        and remove FAISS entries for files that no longer exist on disk.
        Returns a summary dict: {added: [...], removed: [...], already: [...]}.
        """
        workspace_dir = Path(workspace_dir)
        supported_exts = {".txt", ".md", ".pdf", ".csv", ".docx"}
        summary = {"added": [], "removed": [], "already": []}

        # 1. Get currently indexed workspace files (only those with extensions)
        indexed = set()
        for f in self.list_indexed_files():
            if Path(f).suffix:
                indexed.add(f)

        # 2. Get actual files on disk
        on_disk = set()
        if workspace_dir.exists():
            for f in workspace_dir.iterdir():
                if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in supported_exts:
                    on_disk.add(f.name)

        # 3. Index files that are on disk but NOT in FAISS
        to_add = on_disk - indexed
        for fname in sorted(to_add):
            fpath = workspace_dir / fname
            logger.info(f"[Sync] Indexing missing file: {fname}")
            if self.ingest_document(str(fpath)):
                summary["added"].append(fname)
            else:
                logger.warning(f"[Sync] Failed to index: {fname}")

        # 4. Remove FAISS entries for files no longer on disk
        to_remove = indexed - on_disk
        for fname in sorted(to_remove):
            logger.info(f"[Sync] Removing stale FAISS entry: {fname}")
            self.delete_document(fname)
            summary["removed"].append(fname)

        # 5. Already synced
        summary["already"] = sorted(indexed & on_disk)

        logger.info(
            f"[Sync] Workspace sync complete — "
            f"added:{len(summary['added'])} removed:{len(summary['removed'])} "
            f"already:{len(summary['already'])}"
        )
        return summary


class LazyDocumentRetriever:
    """Instantiate the heavy retriever only on first use."""

    def __init__(self):
        self._instance: Optional[DocumentRetriever] = None

    def _get_instance(self) -> DocumentRetriever:
        if self._instance is None:
            self._instance = DocumentRetriever()
        return self._instance

    def __getattr__(self, name):
        return getattr(self._get_instance(), name)


# Global instance
retriever = LazyDocumentRetriever()
