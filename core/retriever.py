import os
from pathlib import Path
from typing import List, Dict, Any
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
            # Security: Sanitize path
            safe_path = _dummy_engine.sanitize_path(file_path)
            
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

    def search_context(self, query: str, top_k: int = 3) -> str:
        """
        Retrieves relevant context based on query semantic similarity.
        """
        if self.vectorstore is None:
            return ""
            
        try:
            # Search FAISS
            docs = self.vectorstore.similarity_search(query, k=top_k)

            if not docs:
                return ""

            context_parts = []
            for doc in docs:
                filename = doc.metadata.get("filename", "Unknown")
                keywords = doc.metadata.get("keywords", "")
                chunk_idx = doc.metadata.get("chunk_index", 0)
                
                context_chunk = f"Document [{filename}#chunk_{chunk_idx}]:\n"
                if keywords:
                    context_chunk += f"(Keywords: {keywords})\n"
                context_chunk += f"{doc.page_content}\n"
                context_parts.append(context_chunk)
            
            return "\n---\n".join(context_parts)
            
        except Exception as e:
            logger.error(f"Search context failed: {e}")
            return ""

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


# Global instance
retriever = DocumentRetriever()
