# 開放語言模型調用自由度 - 實作計畫 (Implementation Plan)

## 📌 當前架構限制分析

經過對目前專案原始碼 (`router.py`, `auto_agent.py`, `adapters/*`) 的分析，目前的架構主要有以下限制：

1. **硬編碼的模型路由 (Hardcoded Routing)**：
   在 `router.py` 與 `auto_agent.py` 中，模型的選擇被硬編碼限制在 `openai`, `gemini`, `claude` 這三個選項中。
   例如在 `router.py` 的 `/chat` 端點：
   ```python
   if req.model == "openai": adapter = OpenAIAdapter(uma)
   elif req.model == "gemini": adapter = GeminiAdapter(uma)
   else: adapter = ClaudeAdapter(uma)
   ```
   這使得使用者無法直接傳入自訂的模型名稱（如 `llama3`、`mixtral` 或其他平台的模型）。
2. **缺乏 OpenAI 相容 API (Custom Base URL) 的支援彈性**：
   目前大多數的開源及第三方模型服務（如 Ollama, vLLM, LM Studio, Groq, OpenRouter）都支援 OpenAI 相容的 API。但目前的 `OpenAIAdapter` 綁定在官方的預設連線，並未將 `api_base` 與自訂 `model_name` 充分暴露給外部設定或 `ChatRequest` 控制。
3. **前端/請求層綁定**：
   `ChatRequest` API 模型中，`model` 預設限制在字串層級，且缺乏讓 Client 動態決定 Endpoint 或 Provider 的設計。

---

## 🚀 Proposed Changes (開放自由度的方案)

為了解決這些限制，我提出兩個方向供您選擇：

### 方案 A：強化「通用型 OpenAI Adapter」結合官方級體驗升級 (RECOMMENDED)
由於市面上 90% 的模型伺服器（Ollama, LM Studio, Groq, vLLM 等）都相容於 OpenAI API，我們將直接將目前的 `OpenAIAdapter` 擴展為「萬能相容 Adapter」，同時引入三個核心體驗升級。以下為四個階段的詳細實作計畫：

#### Phase 1: 升級 `OpenAIAdapter` 支援 `base_url` 與動態模型
**目標**：解除模型名稱寫死限制，讓使用者可連接本地或任何第三方 OpenAI-compatible API，並保留前端動態選單與各家模型版本的彈性支援。
**實作細節**：
1. **動態選單 API (`router.py`)**：
   新增 `GET /api/models` 端點，讀取 `.env` 並動態回傳可用模型清單，讓前端 `index.html` 下拉選單能依此渲染，實現前後端解耦。
2. **修改 API 請求模型 (`router.py`)**：
   在 `ChatRequest` 增加 `api_base` 與 `api_key` 選填欄位，並支援 `provider` 與 `model_name` 分離。
3. **重構模型路由邏輯 (`router.py:chat`)**：
   取消硬編碼 `if/elif`。如果 `model` (或 `provider`) 帶有特定前綴（或非 gemini/claude），實例化 `OpenAIAdapter`。
   保留 Gemini 與 Claude 自由度：若 `provider` 為 `gemini`，將正確的 `model_name` (如 gemini-1.5-pro) 傳入 GeminiAdapter，確保版本切換的彈性。
4. **擴充 Adapter 初始化 (`adapters/openai_adapter.py`)**：
   在 `__init__` 中接收自訂的 `api_base`, `api_key`, 與 `model`。
   修改 OpenAI Client 實例化邏輯，允許從 OS 環境變數（如 `OPENAI_BASE_URL`）作為備案。

#### Phase 2: 實作 SSE Streaming 端點 (打字機效果)
**目標**：將目前的「等待整段生成」改為「逐字吐出」的串流體驗，確保歷史記憶的正確寫入。
**實作細節**：
1. **改寫 Adapter 的產出為 Generator (`adapters/openai_adapter.py`)**：
   將 `client.chat.completions.create(...)` 加上 `stream=True`。
   使用 `yield` 逐步拋出 `{ "status": "streaming", "content": chunk_text }`。
   當生成完畢時，拋出 `{ "status": "success", "content": full_text }`。
2. **調整 SessionManager 寫入時機 (CRITICAL)**：
   將原本寫死在 `router.py` 的 `_session_mgr.append_message()` 及摘要邏輯，移至 Generator 的最後一步驟（當 AI 生成完成時觸發），避免 SSE 提早回傳導致對話未被記錄，或因非同步寫入衝突遺失對話記憶。
3. **升級前端通訊介面 (`router.py:chat`)**：
   將原本回傳 `JSONResponse` 的寫法，改為使用 `sse_starlette.sse.EventSourceResponse` 並回報前端。

#### Phase 3: 移除前端執行開關，實作後端 Auto Tool-Calling
**目標**：像 ChatGPT 一樣，讓模型自己決定是要聊天解釋還是執行腳本，並確保良好的前端提示與安全攔截。
**實作細節**：
1. **整併對話與執行模式 (`router.py`)**：
   移除 `ChatRequest.execute` 的區分。只要有 `req.injected_skill`，就將該技能註冊為 Tool 傳遞。
2. **狀態廣播與高風險授權攔截**：
   在 Streaming 過程中，若 LLM 決定呼叫 `tool_calls`：
   - A. **廣播狀態**：先 `yield` 一個特定的狀態給前端（例如：`{"status": "tool_call", "tool_name": "...", "message": "正在執行腳本..."}`），讓前端顯示動畫。
   - B. **授權攔截 (Auth Modal)**：若發現該腳本為高風險技能，在執行前 `yield {"status": "auth_required", ...}`，暫停並等待使用者按下同意後再接續執行。
   - C. 自動透過 `self.uma.execute_skill()` 執行腳本。
   - D. 將執行結果以 `role: tool` 附加後，重新啟動 LLM 的二次分析以總結結果。

#### Phase 4: 實作 Native Vision 文件解析與 Token 控管
**目標**：支援多模態輸入，讓 GPT-4o / Gemini 1.5 Pro 直接「看」圖檔，並建立良好的 Token 控管機制。
**實作細節**：
1. **智能回退機制 (`adapters/openai_adapter.py`)**：
   在 `_handle_attached_file()` 解析時，若判斷檔案為文件檔（.pdf, .docx 等），則退回使用 RAG 或文字擷取模式，避免誤將其當作圖檔轉譯。
2. **動態 Token 控制與 Payload 轉換**：
   若檔案確認是圖檔 (.png, .jpg)：
   - 讀取為 Base64。
   - 轉換 payload 為 OpenAI Vision 格式的 Message Array。
   - **(CRITICAL)** 加上 `"detail": "low"` 或 `"detail": "auto"` 參數，以避免圖片過大或解析度過高導致 Token 超標。

---

## 🛠️ User Review Required

> [!IMPORTANT]
> 已經依據您的要求，為 Phase 1 到 Phase 4 提供詳細的技術修改評估。
> 請您檢閱上述每個階段的 **實作細節**，若確認符合您的期待，請回覆同意，我將開始 **實作重點程式碼 (EXECUTION)**。

## Verification Plan
1. 修改完成後，我會利用 `pytest` 或透過 Python CLI 工具直接發送模擬的 `ChatRequest` (帶有各種不同的自訂 `model` 與本地端 endpoint 預期設定) 到我們修改後的邏輯中。
2. 驗證不管傳入什麼模型名稱，系統都能順暢派發至對應的 Adapter 而不會拋出硬編碼阻擋錯誤。
