# AutoScan 升級整合 MCP_Server Agent_skills 評估與實作計畫

本報告旨在分析 **AutoScan** 專案目前的 AI 技能/提示詞管理機制，並提出將其遷移至 **MCP_Server** 中 `Agent_skills` 統管架構的具體步驟及架構設計。

---

## 1. 現狀分析 (AutoScan 當前架構)

透過檢視 AutoScan 的前端原始碼（特別是 `ai-api.js` 與 `ai-api-main.js`），以及後端中介層 (`server.js`)，可以發現目前系統有以下特徵：

### 1.1 寫死的 System Instructions (Hardcoded Prompts)
在目前的實作中，當選擇包含 "AutoScan" 標籤的 Agent 時，系統會在前端的 JavaScript 程式碼中**直接寫死 (Hardcode)** 一長串的 System Instruction（包含 Role, Constraints, Field Mapping Logic 等）。
*   **Gemini 實作**: 在 `callGeminiAPI` 中透過 `requestBody.system_instruction` 動態注入寫死的 Prompt。
*   **OpenAI 實作**: 在 `callOpenAIAPI` 中將 Prompt 包裝在 `messages` 陣列的 `system` 角色中。

### 1.2 無動態技能發現機制
AutoScan 目前沒有使用標準的 Tool Calling 機制。它是透過純 Prompt Engineer 的方式強制 LLM 輸出特定格式 (JSON Array)，再由前端的 `parseStructuredOutput` 函式進行解析與 UI 渲染。這導致每次想更改 Prompt、新增欄位或修改行為，**都必須開啟前端程式碼修改並重新部署**。

### 1.3 `server.js` 僅作為 Proxy
後端的 `server.js` 主要作為解決 CORS 及保護 API Key 的代理伺服器 (Proxy)，協助轉發請求到 OpenAI 或 Gemini 的 API，並處理 Notion 的資料庫新增/更新邏輯（Upsert）。伺服器本身並無管理「技能」的概念。

---

## 2. 整合 MCP_Server 的優勢

將 AutoScan 中的「結構化資料擷取邏輯」抽離，並轉型為 MCP_Server 中的一個 **Agent_skill**，可帶來以下巨大好處：

1.  **技能一處管理，多端共用**：AutoScan 的「議會紀錄分析/Notion 結構化拆解」邏輯可寫成一個標準的 `SKILL.md`，未來除了 AutoScan 網頁端外，Slack Bot、終端機或其他系統皆可直接呼叫。
2.  **不需修改 AutoScan 程式碼即可升級 Prompt**：專注維護 MCP_Server 上的技能定義檔，AutoScan 僅需呼叫 API，達到前後端完全解耦。
3.  **結合 Tool Calling 確保穩定性**：捨棄易錯的純 Prompt JSON 輸出，改由 MCP 提供的標準 Tool Schema 強制 LLM 透過 Function Call 輸出結構化資料。

---

## 3. 實作計畫與步驟 (Migration Plan)

要將 AutoScan 升級以讀取並利用 MCP_Server，我們需要分兩個階段進行：**階段一（建立 MCP 技能）** 與 **階段二（改造 AutoScan 客戶端）**。

### 階段一：在 MCP_Server 中建立 AutoScan 專用技能

1.  **建立技能資料夾**：在 `MCP_Server/Agent_skills/skills/` 下建立 `mcp-autoscan-parser`。
2.  **定義 SKILL.md**：將 AutoScan 寫死的 Prompt 轉移至此，明確定義該技能的功能。
3.  **撰寫執行腳本 (`scripts/main.py`)**：此腳本將負責接收 LLM 解析出的結構化 JSON，你可以選擇在這邊直接串接 Notion API（取代部分的 `server.js` 邏輯），或者純粹作為格式校驗，原封不動回傳 JSON 給 AutoScan 前端處理。

### 階段二：改造 AutoScan 代碼 (整合 MCP API)

AutoScan 的 `ai-api.js` 與 `ai-api-main.js` 需要大幅重構。放棄直接呼叫 OpenAI/Gemini API，改為向 `MCP_Server` 發送請求。

#### 作法 A：透過 MCP_Server Agent Mode 處理 (純 LLM 委託)
這是最推薦的作法，讓 AutoScan 退化為純粹的 UI 展示層，將複雜邏輯全交給 MCP_Server。

*   **修改前 (AutoScan `ai-api.js`)**:
    ```javascript
    const response = await fetch('https://api.openai.com/...', { /* 寫死 Prompt */ });
    ```
*   **修改後 (AutoScan 指向 MCP_Server)**:
    ```javascript
    const mcpServerUrl = "http://localhost:8000"; // 你的 MCP_Server 位址

    const response = await fetch(`${mcpServerUrl}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            user_input: text,                    // 使用者上傳的原始文本
            session_id: "autoscan-session-123",
            model: "openai",                     // 透過 MCP_Server 上的 LLM 轉發
            execute: true,                       // 啟動 Agent 模式
            injected_skill: "mcp-autoscan-parser"// 指定注入剛剛建立的技能！
        })
    });
    
    // 取得 LLM 呼叫技能後回傳的結構化結果
    const data = await response.json(); 
    return parseStructuredOutput(data.content);
    ```

#### 作法 B：前端主動索取 Prompt (Discovery 模式)
如果 AutoScan 還是想自己直接打 OpenAI，但希望 Prompt 是從 MCP_Server 動態獲取的。
1.  前端先發送 `GET http://localhost:8000/skills/mcp-autoscan-parser`
2.  取出 API 回傳的 `raw_content` (`SKILL.md` 內容)。
3.  將這段內容賦值給 `systemInstruction` 變數。
4.  依照目前的流程打 OpenAI/Gemini API。

---

## 4. 總結建議

最漂亮且符合架構演進的作法是採用 **階段二的「作法 A」**。

1.  **徹底廢棄** AutoScan `ai-api.js` 內 100 多行的 Prompt 寫死邏輯。
2.  將 `ai-api.js` 中的 `callAIModel` 函數改造成**只指向您的 MCP_Server `/chat` Endpoint**，並設定 `execute: true` 與 `injected_skill: "mcp-autoscan-parser"`。
3.  在 MCP_Server 新增名為 `mcp-autoscan-parser` 的專屬技能，將原本的 Prompt 寫入其 `SKILL.md` 中。

經過這樣改造後，未來要優化「會議重點擷取邏輯」，您只需要去更改 MCP_Server 上的 Markdown 檔案即可，AutoScan 前端不需做任何一行變動，真正實現技能的集中佈署與管理！
