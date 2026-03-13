# Sprint 3：文件管理 UI + Agent Skills RAG 同步與架構優化

## 目標說明

為了解決使用者體驗問題與精進系統底層架構，本次 Sprint 3 的核心目標涵蓋以下五點：
1. **文件管理 UI**：提供已上傳文件的管理介面，允許使用者在畫面上直接查看與刪除。
2. **Agent Skills 自動同步**：確保 Agent Skills 的 `SKILL.md` 異動後自動更新 LLM 的參照知識庫。
3. **即時檔案監聽機制**：採用 `watchdog` 套件進行自動化檔案監聽，補足非 UI 操作的系統死角。
4. **記憶體引用精確定位**：優化記憶體引用機制，在 `MEMORY.md` 紀錄 Chunk Offset 位移量。
5. **向量檢索權重強化**：針對 RAG 機制，藉由前置標籤來強化 FAISS 萃取出的多語言關鍵字權重。

---

## Proposed Changes (實作細節)

### Feature A：文件管理 UI + API

#### 1. [MODIFY] router.py 
新增專用 API 端點：
- `GET /api/documents/list`: 負責將 `workspace/` 下所有檔案的資訊與 FAISS 向量化狀態打包回傳給前端。
- `DELETE /api/documents/{filename}`: 負責同時清除檔案系統與 FAISS 記憶庫中對應的文獻及 Chunk。

#### 2. [MODIFY] static/app.js & static/index.html
- 在側邊欄新增「📂 知識庫文件」區塊。
- 透過 AJAX 動態拉取 API，渲染文件清單並附帶刪除按鈕 (🗑️)。
- 建立剛上傳文件後的自動重整機制。

---

### Feature B：底層架構強化 (基於 User Feedback 延伸擴充)

#### 1. 強化「低權限/非 UI 操作」的自動化 Hook (File Watcher)
為解決手動從檔案總管刪改檔案時不同步的問題，引入 `watchdog` 監聽實體檔案系統。

- **[NEW] core/watcher.py**: 實作 `WorkspaceWatcher` 與 `SkillWatcher`，繼承 `watchdog.events.FileSystemEventHandler`。
  - 當新增/修改檔案時：自動呼叫 `retriever.ingest_document()` 或 `ingest_skill()` 進行單檔向量化。
  - 當刪除檔案時：自動呼叫 `retriever.delete_document()` 進行單檔 FAISS 移除。
- **[MODIFY] main.py / router.py**: 將 Watchdog Observer 綁定至 FastAPI 的啟動與關閉生命週期中。

#### 2. 記憶體管理 (MEMORY.md) 位移量紀錄 (Chunk Offset Tracker)
為了未來的文本精確跳轉準備，讓 RAG 的 Context 提供並強制 LLM 回報參考的 Chunk Index。

- **[MODIFY] core/retriever.py**: 修改 `search_context`，將向量 metadata 提取出的索引號暴露給 LLM：`Document [{filename}#chunk_{chunk_idx}]:\n`
- **[MODIFY] adapters/gemini_adapter.py**: 於系統 Prompt 嚴格規範，要求 LLM 生成 `[filename#chunk_index:片段]` 格式的 citation。
- **[MODIFY] core/session.py**: 微調攔截的正規表達式 `_extract_citations`，支援擷取 `chunk_idx`，寫入 `MEMORY.md` 並打上 `(Offset: chunk_idx)`。

#### 3. 多語言標籤關鍵字的檢索權重調整 (Keyword Boosting)
為避免引入過重且難以維護的 BM25 依賴，採用文字預處理方式來增強 FAISS Dense Vector 對關鍵字的辨識度。

- **[MODIFY] core/retriever.py**: 於 `ingest_document` 與 `ingest_skill` 將 chunk 寫入 FAISS 前，將抽取出的多語言關鍵字列表強制塞在該 chunk 文字的最前方（例如 `Meta-Keywords: {tags_str}\n\n{chunk}`）。這將讓包含這些關鍵字詞的 User Query 能獲取顯著更高的 Cosine Similarity 相似度分數。
