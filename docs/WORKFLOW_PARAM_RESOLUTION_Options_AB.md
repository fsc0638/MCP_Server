# Workflow Block 參數解析 — 方案 A / B 設計紀錄

> 建立日期：2026-04-17
> 狀態：**擱置待決策**
> 觸發情境：使用者用 LINE 登入並在 workflow designer 建立「網路搜尋 V2」時，發現 block param 必須對應 skill 的實際 key 名稱（`query`），與「用自然語言變數 + LLM 自動推斷」的初衷不符。

---

## 1. 問題陳述

使用者期望：
> 「在 Workflow 上用自然語言命名變數（如『搜尋主體』），**由 LLM** 在執行時自動把變數映射到 skill 需要的參數（如 `query`）。」

目前實際行為：
> Block param 的 key 必須**精確等於** skill `scripts/main.py` 讀取的 key。`auto (LLM)` 模式並沒有真的呼叫 LLM，只是把 `accumulated_context` 機械塞進使用者指定的 key。UI 文案「由 LLM 自動推斷」屬於誤導。

---

## 2. 目前代碼位置

**檔案**：`server/services/workflow_executor.py`

```python
def _resolve_block_params(self, param_config, resolved_vars, accumulated_context, user_input):
    result = {}
    for param_name, param_def in param_config.items():
        source = param_def.get("source", "auto") if isinstance(param_def, dict) else "auto"
        value  = param_def.get("value", "")      if isinstance(param_def, dict) else ""

        if source == "variable":
            result[param_name] = _substitute_variables(value, resolved_vars)
        elif source == "fixed":
            result[param_name] = value
        elif source == "previous_step":
            result[param_name] = accumulated_context
        elif source == "auto":
            # ⚠️ 目前僅把 context 塞進 param_name，並無 LLM 介入
            result[param_name] = accumulated_context
        # ...
    return result
```

---

## 3. 方案 A — 嚴格對齊 skill schema（目前架構）

### 原則
> Block 的 param key 必須等於 skill SKILL.md `parameters.properties` 定義的 key。

### 優點
- 0 額外 LLM tokens（執行成本最低）
- 執行快、行為可預測
- 不會誤判使用者意圖
- 適合企業內部需要 deterministic 重現性的工作流

### 缺點
- 使用者必須看 skill 文件才知道要填什麼 key
- 新增 skill 時必須先把 `parameters` 寫入 SKILL.md frontmatter
- UI 的「自然語言變數」只能當 value，不能當 key

### 所需調整（已完成）
- ✅ `workflow_executor.py` 偵測 skill-level error（避免誤判成功）
- ✅ Workflow designer UI 從 `/skills/{name}` API 讀 `metadata.parameters`，顯示藍色 Skill 標籤 + 參數說明清單
- ✅ 支援手動新增/刪除自訂 param key
- ✅ `mcp-web-search/SKILL.md` 已補上 parameters schema（query、target_url、search_depth、max_results、include_domains）
- 🔲 **遺留**：其他 skill 若沒寫 parameters schema，需逐一補齊才能完全發揮此方案

---

## 4. 方案 B — LLM 翻譯層（符合原始初衷）

### 原則
> Block 僅描述「意圖」（用自然語言變數），執行時插入一次 LLM 呼叫，由 LLM 讀 SKILL.md 全文後自動決定實際 JSON 參數。

### 執行流程（新）
```
1. 解析工作流變數 → {搜尋主體: "量子運算"}
2. 【新增】在每個 skill block 前插入 LLM 呼叫：
   System: 你是工具調度員。以下是 skill 定義 {...SKILL.md 完整 YAML + body...}
   User:   意圖「搜尋量子運算相關新聞」
           可用變數：{搜尋主體: "量子運算"}
           對話脈絡：... (user_input + accumulated_context)
           請回傳應呼叫的 JSON 參數（必須符合 schema）
   → LLM 回: {"query": "量子運算", "max_results": 3}
3. 用這份 JSON 呼叫 UMA.execute_tool_call(skill_name, json)
4. skill 結果 → 累積到 accumulated_context 繼續下一 block
```

### 優點
- UI 真正「自然語言」驅動
- 不需對齊 key 名稱
- 自動處理缺漏欄位、型別轉換、別名
- 一個變數可映射到多個 skill
- 不需逐一補 SKILL.md 的 parameters 區塊

### 缺點
- 每個 block 多一次 LLM call（約 500-1000 tokens，視 SKILL.md 長度）
- 執行時間增加 1-3 秒/block
- 偶發不確定性（需用 JSON schema + structured output 約束）
- 成本隨 block 數量線性增加

### 實作清單
1. **後端**
   - 在 `workflow_executor.py` 新增 `async _llm_resolve_params(skill_meta, intent, variables, context)`：
     - 載入 skill 完整 SKILL.md（含 body）
     - 組 prompt 要求 LLM 輸出 JSON（用 JSON mode 或 function calling）
     - 回傳 dict 後再進 UMA
   - `auto` source 改為真正觸發此函式
   - 加 cache（同一工作流同一個 block 的 skill_meta 不重複讀檔）
   - 記錄 translator token usage 到 `token_usage.jsonl`
2. **前端**
   - Params 面板新增 source 選項「🤖 智能 (LLM)」
   - 為 block 新增一個「意圖描述」欄位（optional），讓使用者用自然語言補充 context
   - Schema hint 改為「參考資訊」而非「強制」
3. **Skill schema**
   - 保留現有 `parameters` 讀取，LLM 收到會更準
   - 但不再是必填

---

## 5. 混合建議（推薦實作順序）

### Phase 1（若要推 B）
保留 A 作為預設，B 作為進階選項：
- 既有的 `variable` / `fixed` / `previous_step` 三種 source **行為不變**
- `auto` 改名為 `smart`，行為改為「先嘗試機械映射；key 不匹配時自動走 LLM 翻譯」
- 使用者可手動選「🤖 強制 LLM」完全交由 LLM 判斷

### Phase 2（若要 fully commit B）
- 將「意圖描述」欄位放到 block 基本屬性
- 預設所有新 block 的 param source = `smart`
- 保留 `variable`/`fixed` 作為「鎖定值」使用

---

## 6. 決策參考矩陣

| 情境 | 建議 |
|------|------|
| 企業內部工作流、需要 audit log、成本敏感 | A |
| 一般員工 / 非工程師使用、希望像說話一樣設定 | B |
| 既有 skill 都有完整 SKILL.md parameters | A |
| 快速 prototype、skill 還在演進 | B |
| 需要跨 skill 動態組合 | B |
| 每秒執行多次、對延遲敏感 | A |

---

## 7. 待辦（重啟此議題時的 checklist）

- [ ] 選定方向（A / B / 混合）
- [ ] 若 A：盤點所有 skill，補齊 `parameters` schema
- [ ] 若 B：
  - [ ] 實作 `_llm_resolve_params()`
  - [ ] UI 新增 `smart` source 選項 + 意圖描述欄位
  - [ ] Token usage 記錄打通
  - [ ] 測試不同 skill 的翻譯準確度（web-search / meeting-to-notion / image-generator）
- [ ] 若混合：Phase 1 先上，觀察使用者實際行為決定 Phase 2

---

## 8. 相關檔案

- `server/services/workflow_executor.py` — 執行引擎，`_resolve_block_params()` 是改動核心
- `server/core/uma_core.py` — `execute_tool_call()` 為 skill 實際呼叫點，不需改
- `frontend/assets/js/workflow.js` — `_renderBlockParams()` 是 UI 核心，新增 smart source 選項的改動點
- `Agent_skills/system_skills/mcp-*/SKILL.md` — 若走 A 方案需逐一補 parameters
