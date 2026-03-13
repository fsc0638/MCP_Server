# Sprint 4: OpenAI Model Integration (Model Selection Support)

## 目標說明

目前系統以 Gemini 為主力，使用者希望能夠透過畫面上方右側的「語言模型下拉選單」，動態切換使用 OpenAI (GPT-4o) 模型來進行對話與技能操作。

## 盤點現有架構狀態

經過系統掃描，我們確認以下基礎架構已經存在：
1. **[UI 層]** `static/index.html` 已經具備 ID 為 `modelSelector` 的下拉選單，且包含 `value="openai"` 的選項。
2. **[中介層]** `static/app.js` 的 `sendMessage()` 已經會擷取 `modelSelector.value`，並封裝至 payload 的 `model` 屬性中發送至 `/chat` API。
3. **[路由層]** `router.py` 的 `/chat` 端點已經預留判斷邏輯 `if req.model == "openai": adapter = OpenAIAdapter(uma)`。
4. **[介接層]** `adapters/openai_adapter.py` 已經具備基礎框架，實作了 `chat` 與 `simple_chat` 方法。

## Proposed Action Plan (實作計畫)

由於前後端管線（Pipeline）均已預留 OpenAI 介面，本次整合的重點在於 **「驗證、除錯與優化 OpenAI Adapter」**。以下為三個實作階段：

### Stage 1. 認證環境設定 (Environment & Auth)
- 取代寫死的判斷，確保系統能正確讀取 `.env` 中的 `OPENAI_API_KEY`。
- 若使用者尚未配置 Key，需在前端與後端保持穩定，並回傳友善的 503 HTTP 錯誤提示使用者處理。

### Stage 2. 檢視與優化 OpenAI Adapter (Adapter Optimization)
- 確保 `core/session.py` 能夠像 Gemini 一樣，正確將 OpenAI 的對話歷史（System Prompt + 歷史對話 + 當次詢問）組織並傳遞。
- **[核心差異處理: Tool JSON Schema]** 確保 `adapters/openai_adapter.py` 產出的 Tool Calling 結構嚴格符合 OpenAI 最新規範。OpenAI 的 tool arguments 必須是以標準 JSON Schema 定義。
- **[提示詞一致性 D-08]** 徹底移除 `openai_adapter.py` 中可能殘留的硬編碼技能名稱。必須確保 SKILL.md 內容能完整注入為 OpenAI 的 developer 或 system message，以維持「定義成為提示詞」的執行強度。
- **[多模態能力對等 (Multimodal Parity)]** 補充 `openai_adapter.py` 對於 `attached_file` 參數的支援。將附加檔案轉為 Base64 格式送入 OpenAI API，確保圖片與文件解析能力與 Gemini 齊平。
- **[Session 管理統一化 (D-07)]** 趁整合之際，將 `router.py` 中零散的對話歷史邏輯完全遷移至 `core/session.py` 的 SessionManager 中，確保 OpenAI 與 Gemini 共用一套對話摘要與檔案清理機制。
- 修復 `openai_adapter.py` 內隱藏的 Bug，例如確保當次 Assistant 回傳的 Tool Call ID，在再次呼叫 Model 時能與 Tool Message 精確關聯。

### Stage 3. End-to-End 驗證測試 (Integration Testing)
- **A. 基礎對話測試 (Pure Chat)**：切換到 OpenAI，發送閒聊確認是否回覆正常。
- **B. 工具調用測試 (Agent Tool Calling)**：切換到 OpenAI 開啟「執行技能」，使用 `mcp-python-executor` 執行腳本，確認 OpenAI 能成功發動 Tool Call 回圈並把執行結果帶回總結。
- **C. 記憶體同步確認 (Offset Sync)**：確保切換成 OpenAI 後，先前寫好的 Chunk Offset citation 機制 (`[filename#chunk_0:片段]`) 在 OpenAI 身上也能被觸發並寫入 `MEMORY.md`。
