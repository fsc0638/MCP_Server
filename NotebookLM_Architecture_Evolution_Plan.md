# MCP Server 升級藍圖：NotebookLM 2026 級別 Agentic RAG 架構實作報告

本報告旨在將您提供的「NotebookLM 2026 對話引擎」四大核心特性與運作邏輯，映射至我們剛完成重構的 `MCP_Server` 架構中。這份藍圖將作為未來階段性開發的最高指導原則。

---

## 一、 前提需求收斂與比對 (Requirements Alignment)

基於您提供的 NotebookLM 2026 特性圖表，我們的目標是將現有的單純對話介面 (Chat Engine) 升級為 **來源導向 (Source-grounding) 的自主代理人 (Agentic RAG)**。

| NotebookLM 2026 特性 | MCP Server 對應升級需求 | 預期達成效果 |
| :--- | :--- | :--- |
| **來源導向 (Source-grounding)** | 導入向量檢索 (Vector Retrieval) 機制與來源標記 (Citations) | LLM 回答將強制附帶來源檔案與頁數/段落，消除 AI 幻覺。 |
| **動態上下文檢索** | 建立文件解析代理 (Document Parser Agent) 與 ChromaDB / Pinecone 整合 | 支援突破 Token 限制的大量文件（如 50 份數百頁 PDF）精準檢索。 |
| **自主代理能力 (Agentic流程)** | 將 `gemini_adapter.py` 的對話迴圈升級為 **Plan-and-Solve (任務拆解)** 邏輯 | LLM 能在接收「分析會議並存入資料庫」指令時，自主規劃多步驟工具呼叫。 |
| **結構化數據轉換** | 新增 `mcp-data-extractor` (非結構轉結構表) 與 Web UI 的檔案上傳支援 | 能從 PDF/影片/網頁中抽出 Data Tables，並無縫拋轉至 Notion/Excel 技能。 |
| **深度研究模式 (Deep Research)** | 新增 `mcp-web-researcher` 技能 (串聯網 API) | 開啟選項後，Agent 具備主動上網搜尋文獻、交叉比對並產出初步報告的能力。 |
| **多模態音訊與影像引擎** | 將 Gemini API 的多模態輸入流 (Audio/Vision) 實作於後端，並強化 Web UI 上傳區 | 可直接讓引擎聆聽音檔或看圖表，打破純文字限制。 |
| **增強型對話記憶** | 實作「自適應壓縮 (Adaptive Summarization)」機制取代目前單純的 Array 歷史 | 解決長對話的「失憶」問題，確保全域規則（如特定欄位映射格式）永固。 |

---

## 二、 詳細實作藍圖與技術架構 (Implementation Architecture)

我們不需拋棄現有的 `MCP_Server`，而是將其作為核心樞紐，向外擴建三大子系統：

### 1. 記憶體流與檢索子系統 (Retrieval Subsystem)
*   **技術選型**: LangChain 進行文件切割（Chunking）+ 本地 ChromaDB 向量庫。
*   **流程優化**:
    *   新增 `/api/documents/upload` 接口。
    *   當 100MB 以上大型檔案上傳後，背景 Worker 自動執行 Embedding 並存入向量庫，而非直接將全文塞入提示詞。
    *   **檢索**: 建立新的核心模組 `core/retriever.py`。LLM 收到問題時，觸發檢索，將 Top-K 的段落作為 Context 注入 System Prompt 中。

### 2. 「任務鏈」式自主代理核心
*   **現狀改進**: 目前的 Adapter 雖有 10 輪迴圈，但缺乏規劃能力。
*   **建議**: 
    *   在模型適配器層級強制模型使用 **Plan-and-Solve** 策略。
    *   系統應扮演「監工」角色，確保 LLM 按照規劃逐步收集來源資料，最後才產出結果。
    *   最終輸出皆強制加上精準引用標籤 (如 `[doc_1:段落3]`)。

### 3. 多模態與外部研究技能擴充 (Skills Expansion)
這部分完全利用我們「一處管理」的 `Agent_skills` 母庫優勢，不需動核心架構：
*   **[NEW SKILL] `mcp-web-researcher`**:
    *   腳本：串接 Tavily API 或 Google Custom Search。
    *   描述：`深度研究工具。當使用者詢問最新資訊或需搜集網路文獻時，使用此工具抓取前 10 筆搜尋結果並摘錄摘要。`
*   **[NEW SKILL] `mcp-media-parser`**:
    *   腳本：處理非結構化數據至 Data Table (CSV/JSON) 的轉換工具。

### 4. Web Console 前端介面大改版
要達到 NotebookLM 的操作體驗，前端需要加上：
*   **左側來源欄 (Source Panel)**：管理已上傳的文件，讓用戶勾選本次對話要參與檢索的檔案。
*   **控制面板**：增加 「Deep Research」 開關，開啟後 Agent 會主動使用 `mcp-web-researcher` 技能進行聯網調研。
*   **對話區支援多模態輸入**：在文字框旁新增 📎 附件按鈕，支援上傳圖片、PDF、甚至是音訊檔。

---

## 三、 階段性推進建議 (Phased Roadmap)

建議將此龐大升級拆分為三個 Sprint 執行，以降低系統崩潰風險：

*   **Sprint 1: 基礎建設與多模態**
    *   升級 Web Console 支援文件與影像上傳。
    *   對接 Gemini Pro Vision，讓 LLM 具備讀圖與轉文字能力。
*   **Sprint 2: 來源導向與 RAG**
    *   引入本地 ChromaDB，實作文件自動切割與存入機制。
    *   要求 LLM 回答時支援引用 (Citations) 原文段落。
*   **Sprint 3: 深度研究與增強記憶**
    *   新增 `mcp-web-researcher` 聯網技能。
    *   實作多步驟任務規劃引擎，正式開啟 Agentic 模式 (多步驟任務規劃引擎)。
