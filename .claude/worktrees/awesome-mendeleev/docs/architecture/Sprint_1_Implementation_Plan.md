# Sprint 1 實作計畫：多模態與數據結構化基礎建設

## 目標
依照「NotebookLM 2026 架構演進藍圖」，完成 Sprint 1 的基礎建設。
1. **升級 Web Console**：在前端實作檔案上傳機制（圖片、PDF、文件），支援拖曳或點擊上傳。
2. **後端 API 擴充**：新增 `/api/documents/upload` 接口接收前端傳來的檔案，並暫存於工作區 (`WORKSPACE_DIR`)，並具備 Hash 去重機制。
3. **對接 Gemini Pro Vision**：修改 `gemini_adapter.py`，將上傳檔案與 Session 綁定，讓 LLM 直接具備「讀圖、讀表、轉文字 JSON」等多模態能力。
4. **增強型對話記憶基礎**：在 SessionManager 中導入「自適應壓縮」機制的初步框架。

## Proposed Changes

### 1. 後端上傳 API 實作 (含校驗與去重)
#### [MODIFY] [router.py](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/router.py)
- 匯入 FastAPI `File`, `UploadFile`, `hashlib`。
- 新增 `POST /api/documents/upload` 端點。
- **檔案哈希與去重**：在上傳時計算檔案的 SHA-256 Hash，若 `WORKSPACE_DIR` 已有相同 Hash 的檔案則直接共用，避免重複佔用空間。
- **嚴格路徑校驗**：強制匯入並使用 `core.executor.sanitize_path()`，確保落地檔名的絕對安全。
- 回傳儲存的絕對路徑及 Hash 給前端，前端放入 `attached_file` 變數中。

### 2. 前端上傳邏輯串接
#### [MODIFY] [app.js](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/static/app.js)
- 綁定 `#workspaceFileInput` 與 `#attachFileBtn` 的 `change` 與 `click` 事件。
- 實作上傳邏輯：使用 `FormData` 呼叫 `/api/documents/upload`。
- 上傳成功後，將路徑存入 `attachedFilePath` 並顯示 UI。
- 綁定清除檔案 UI，清空 `attachedFilePath`。

### 3. 多模態代理轉接 (Gemini Adapter 強化)
#### [MODIFY] [gemini_adapter.py](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/adapters/gemini_adapter.py)
- 當 `req.attached_file` 存在時，呼叫 `genai.upload_file()` 將檔案上傳到 Google AI 的 File API。
- **Session 綁定**：將回傳的 File 物件與當前的 Session ID 綁定，暫存於記憶體，確保同一個 Session 可以持續參照同一個上傳物件而無需重複編碼上傳。
- **主動提示詞注入**：當偵測到附件為圖片時，Adapter 主動在提示詞結尾偷偷注入：*「請描述此圖片並對應相關 Skills」*，落實 LLM 為主的自主分析能力。
- **Agentic Flow 引導**：在 System Prompt 提示 LLM 在規劃 JSON 時，若處理 >100MB 或超大檔案，應主動規劃「搜索 References/ 目錄」而非全文閱讀。

### 4. 自適應壓縮記憶機制 (Adaptive Memory Compression)
#### [MODIFY] [core/session.py](file:///c:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/core/session.py)
- 在 `SessionManager.append_message()` 中新增 Token 觀察器邏輯 (初步先以對話輪數或字數估算)。
- 當對話接近上限時，在背景觸發 LLM 對「前 50% 的對話」進行摘要。
- 將摘要與後 50% 的完整對話重新拼接，並更新至 `MEMORY.md` 確保全域規則不被遺忘。

## Verification Plan

### Manual Verification
1. 重啟伺服器並開啟 `http://127.0.0.1:8113/ui/`。
2. 上傳同一份檔案兩次，觀察後端 Log 是否觸發 Hash 去重。
3. 附加圖片並送出空白訊息，確認 Gemini 是否因為「主動提示詞注入」而自動描述圖片並建議技能。
4. 長時間對話測試，觀察 `MEMORY.md` 是否會出現被壓縮提煉的摘要區塊。
