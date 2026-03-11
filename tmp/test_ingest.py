import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from core.retriever import DocumentRetriever
import logging
logging.basicConfig(level=logging.INFO)

retriever = DocumentRetriever()
workspace = Path(__file__).parent.parent / "workspace"

# Test with absolute path (like Watchdog would pass)
txt_abs = str((workspace / "03235306f2195711.txt").resolve())
print(f"Ingesting txt (absolute): {txt_abs}")
result_txt = retriever.ingest_document(txt_abs)
print(f"  Result: {result_txt}")

docx_abs = str((workspace / "921de8db256f05ec.docx").resolve())
print(f"Ingesting docx (absolute): {docx_abs}")
result_docx = retriever.ingest_document(docx_abs)
print(f"  Result: {result_docx}")

# Show what's now indexed
print("\nIndexed files after fix:")
for f in retriever.list_indexed_files():
    print(f"  - {f}")
