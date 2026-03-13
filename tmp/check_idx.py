import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from server.core.retriever import DocumentRetriever

retriever = DocumentRetriever()
if retriever.vectorstore:
    indexed_files = retriever.list_indexed_files()
    print("Indexed files:")
    for f in indexed_files:
        print(f"  - {f}")
else:
    print("No vectorstore found.")

