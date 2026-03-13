# Sprint 2 實作計畫：來源導向與 RAG (Server-Side Document Retrieval)

## 目標
依照「NotebookLM 2026 架構演進藍圖」，進入 Sprint 2 階段。本階段致力於解決 LLM 無法精準檢索長篇文件且容易產生幻覺的問題。
1. **核心檢索模組**：建立負責文本切割 (Chunking) 與向量儲存 (ChromaDB) 的模組。
2. **上傳流程升級**：當用戶透過 `/api/documents/upload` 上傳文件時，自動觸發解析並存入向量庫，並於前端顯示進度。
3. **語義與關鍵字混合檢索**：結合您開發的 `extract_tags` 邏輯，在切割文件時一併提取關鍵字存入 metadata，提升檢索精準度。
4. **動態內容注入與來源引用**：在對話過程中動態檢索段落，並強制要求 LLM 標示參考來源（如：`[filename:片段]`）。
5. **記憶整合**：將本次對話中檢索到的來源引用同步寫入 `MEMORY.md`。

## Proposed Changes

### 1. 核心檢索模組 `core/retriever.py`
#### [NEW] [core/retriever.py](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/core/retriever.py)
建立 `DocumentRetriever` 類別處理 RAG 的核心生命週期：
- **依賴套件**: `langchain`, `chromadb`, `sentence-transformers` 等。
- **嚴格路徑校驗**：確保所有讀寫操作皆遵循 `executor.py` 中的 `sanitize_path()` 安全邏輯。
- **`ingest_document(file_path)` 機制**：
  - 偵測並讀取文件內容 (`.txt`, `.md`, `.pdf`)。
  - 使用 `RecursiveCharacterTextSplitter` (chunk_size: 1000, overlap: 200) 進行 Chunking。
  - **關鍵字萃取 (Keyword Extraction)**：在寫入前，呼叫您的 `extract_tags` 相關邏輯（或使用簡單的 LLM/NLP 萃取），為每個 chunk 提取關鍵字並存入 ChromaDB 的 metadata 中。
  - 將 Chunks 存入本地建立好的 ChromaDB Collection。
- **`search_context(query, top_k=3)` 機制**：
  - **混合檢索 (Hybrid Search)**：將使用者的 Query 轉換為向量進行「語義相似度」搜尋，同時比對 metadata 的「關鍵字」，確保檢索結果與問題高度語意一致。
  - 將結果組裝成字串 (e.g., `Document [file_name]:\nContent...`) 並回傳。

### 2. 檔案上傳流程與前端擴充
#### [MODIFY] [router.py](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/router.py)
- 在 `/api/documents/upload` 中判斷純文字/PDF 檔案。
- 引入 BackgroundTasks 以**非同步**方式呼叫 `DocumentRetriever.ingest_document(final_path)`。
- 回傳 JSON 中加入 `vectorized: "pending"` 等狀態。
- *(Optional)* 考慮新增一個 `/api/documents/status` 端點供前端輪詢向量化進度。

#### [MODIFY] [static/app.js](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/static/app.js)
- 調整上傳 UI，若後端告知檔案需要被向量化，則顯示「索引建立中...」的進度條或提示，避免用戶在上傳大檔 (如 >100MB) 時乾等。

### 3. 動態上下文檢索與 Prompt 注入
#### [MODIFY] [adapters/gemini_adapter.py](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/adapters/gemini_adapter.py)
- 攔截 `user_query`，呼叫 Retriever 取得高度相關的 Context。
- 在提示詞中強制要求 LLM 使用 `[filename:段落]` 的格式標示出處。

### 4. 增強型對話記憶更新
#### [MODIFY] [core/session.py](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/core/session.py)
- 當 Adapter 回傳的結果中包含成功的引用標籤 (`[filename:段落]`)，SessionManager 應將此對應關係提取出來。
- 呼叫 `_log_compression_event` 或相關寫入機制，將引用的檔案路徑與段落大意存入 `MEMORY.md`。

### 5. 依賴管理 (Dependencies)
#### [MODIFY] [requirements.txt](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/requirements.txt)
- 補齊 RAG 所需套件：`chromadb`, `langchain`, `langchain-community`, `pypdf`, `sentence-transformers` 等。

## Verification Plan

### Manual Verification
1. **混合檢索測試**：上傳一份包含特定關鍵字與複雜語義的文件，向 AI 詢問，驗證 Retriever 是否能同時基於關鍵字和向量精準命中目標 Chunk。
2. **非同步與 UI 更新**：上傳大型 PDF 檔，檢查後端是否快速回傳 200 OK，並在背景進行 Embedding，同時前端出現「索引建立中...」的提示。
3. **強制引用與記憶持久化**：確認 AI 回答中帶有 `[檔名:片段]` 格式，並開啟 `MEMORY.md` 確認該引用片段被正確記錄（以便未來 Agent 能回想來源）。
