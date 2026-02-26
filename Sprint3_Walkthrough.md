# Sprint 3: 文件管理 UI 與 Agent Skills RAG 同步實作總結

## 總覽
本次衝刺（Sprint 3）旨在解決使用者體驗與 Agent 知識落差的兩個核心問題：
1. **文件管理**：提供使用者可以在網頁介面上檢視並管理已上傳至 Workspace 的文件清單。
2. **技能知識同步**：確保語言模型（Agent）隨時能參照最新版本的 `SKILL.md`，無論技能是新增、修改或重啟伺服器。

---

## 實作內容

### 1. 知識庫文件管理面板 (Feature A)
為了讓使用者能直觀掌控已上傳的知識庫文件，我們實作了完整的前後端管理功能：

*   **後端 API (API Endpoints)**
    *   `GET /api/documents/list`：掃描 `workspace/` 目錄，回傳所有檔案的清單、大小，並透過 `retriever.list_indexed_files()` 標記該檔案是否已成功寫入 FAISS 向量資料庫。
    *   `DELETE /api/documents/{filename}`：提供單一檔案的刪除功能。除了將實體檔案從硬碟移除外，也會同步呼叫 FAISS 移除對應的向量區塊。
*   **前端介面 (UI / UX)**
    *   **側邊欄新增區塊**：在左側面板 (`index.html`) 的「已載入技能」下方，新增了「📂 知識庫文件」區塊。
    *   **動態渲染 (`app.js` - docModule)**：
        *   建立獨立的 `docModule` 模組負責擷取與渲染文件清單。
        *   如果檔案已在知識庫中，左側會顯示**綠色燈號**；若為不支援的格式或尚未索引，則顯示灰色。
        *   清單每筆項目右側加入「🗑️」垃圾桶圖示，點擊後會跳出確認視窗，確認後呼叫 DELETE API，並即時從畫面上移除。
        *   **上傳連動**：在原本上傳檔案成功的邏輯（`xhr.onload`）中，加入 `docModule.loadDocuments()` 觸發清單自動更新。

### 2. Agent Skills 與 FAISS 向量資料庫自動同步 (Feature B)
確保 LLM 能夠將現有的 Skills 當作 RAG 知識庫來查詢，消除了「模型只認得上傳文件」的局限性。

*   **核心檢索器擴充 (`core/retriever.py`)**
    *   新增 `ingest_skill(skill_name, file_path)`：針對 `SKILL.md` 的專用寫入方法，強制將 `filename` metadata 設定為 `skill_name`，讓使用者詢問「mcp-{skill} 是什麼」時，準確對應。
    *   新增 `delete_document(filename)`：由於 FAISS 官方不支援依據 metadata 刪除向量，此方法實作了「記憶體內重建過濾」，將除了目標檔案以外的所有向量重新存檔。
*   **路由掛載與自動觸發 (`router.py`)**
    *   **伺服器啟動掛載 (`@app.on_event("startup")`)**：每次 Uvicorn 啟動時，會自動掃描 `uma.registry.skills`，將全數（21 個）技能的 `SKILL.md` 一次性建立到 FAISS 中，因此在日誌中會看到大量的 `ingested into FAISS`。
    *   **Skill 編輯更新 (`PUT /skills/{skill_name}`)**：儲存修改後的 `SKILL.md` 之後，觸發 `retriever.ingest_skill()` 覆蓋舊向量。
    *   **Skill 復原 (`POST /skills/{skill_name}/rollback`)**：將 `SKILL.md.bak` 還原後，同步觸發 `retriever.ingest_skill()`。

---

## 驗證與結果

1.  **文件刪除測試**：點擊垃圾桶後，成功觸發 FAISS 區塊移除與磁碟檔案永久刪除。
2.  **Startup 掛載測試**：伺服器重啟時，Log 正確顯示 `Startup SKILL indexing complete. Indexed 21 skills.`。
3.  **Knowledge Query 測試**：向對話面板詢問如 `mcp-brand-guidelines 是誰負責的？`，由於知識庫現在包含 `SKILL.md`，Agent 可直接從 RAG 中提取說明，並附帶 `[mcp-brand-guidelines:片段]` 的引用標籤寫入 `MEMORY.md`。

> [!NOTE]
> 目前的新增與刪除技能若透過非 UI 標準端點的操作（如：直接從底層 API `/skills/create` 或終端機），這部分的 Hook 接線還需確認。但只要經過標準重啟流程或是在畫面中編輯現有 Skill，FAISS 就會百分之百保持最新狀態。
