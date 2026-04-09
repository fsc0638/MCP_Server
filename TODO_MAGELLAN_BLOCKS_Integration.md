# TODO: MAGELLAN BLOCKS Flow Designer — 與 AgentK 的對接評估

> 建立日期：2026-04-09
> 狀態：待評估 / 待實作

---

## 可直接採用的設計

### P0 — Flow 驗證（Start→End 連通性）
- [ ] 在 Run Flow 前加 `_validateFlow()` 驗證
- [ ] 檢查有 Start 和 End 節點
- [ ] 從 Start 做 BFS，確認所有路徑到達 End
- [ ] 檢查孤立節點（未連接的 block）
- [ ] 驗證失敗時顯示錯誤提示，標記問題節點

### P1 — Start 節點排程設定面板
- [ ] 擴充 Start block 屬性面板欄位：
  - [ ] 排程模式選擇（無排程 / cron / 簡易）
  - [ ] cron 輸入欄位
  - [ ] 簡易模式：每日/每週/工作日 + 時間選擇
- [ ] 成功/失敗通知設定（LINE / Email）
- [ ] 錯誤時觸發 Flow 選擇
- [ ] 日誌保留天數設定
- [ ] 排程存入 Workflow JSON

### P1 — 執行引擎接後端 API
- [ ] 建立 `server/services/workflow_engine.py`
- [ ] 讀取 Workflow JSON
- [ ] Topological sort blocks
- [ ] 依序呼叫 UMA.execute_tool_call() 或 adapter.chat()
- [ ] 收集每個 block 的 output → 傳給下一個 block
- [ ] 建立 `/api/workflows/{id}/execute` API 端點
- [ ] 前端 Run 按鈕接後端 API（取代純動畫）
- [ ] 即時回傳執行狀態到 Dashboard

### P2 — 錯誤 callback flow
- [ ] Block 失敗時記錄錯誤
- [ ] 若設定有 callback flow → 自動觸發
- [ ] 傳遞系統變數（job_name, target_time, user_info）
- [ ] 傳遞原 flow 的累積變數
- [ ] 推送失敗通知

### P2 — 通知設定（成功/失敗）
- [ ] Flow 執行完成 → 推送 LINE 通知
- [ ] 支援成功/失敗分別設定不同通知管道
- [ ] 通知內容包含：Flow 名稱、執行時間、結果摘要

### P3 — 平行分支合併驗證
- [ ] 偵測平行分支結構
- [ ] 驗證所有分支最終合併到同一個節點
- [ ] 不允許獨立終端分支
- [ ] 平行 block 的真正 parallel execution

---

## 建議不採用的設計

| 功能 | 不採用原因 |
|------|---------|
| BigQuery 整合 Block | AgentK 定位不是 ETL 工具 |
| Salesforce / Box Block | 超出目前範圍 |
| ML 推論 Block | AgentK 用 LLM 處理，不需要傳統 ML pipeline |
| GCS / GCE Block | 雲端基礎設施操作不在 AgentK 範圍 |

---

## 參考資料

- [MAGELLAN BLOCKS Flow Start Reference](https://www.magellanic-clouds.com/blocks/docs/reference/flow_start/)
- [MAGELLAN BLOCKS Parallel Branching Guide](https://www.magellanic-clouds.com/blocks/ja/guide/flow-make/parallel/)
- [MAGELLAN BLOCKS Basic Guide](https://www.magellanic-clouds.com/blocks/en/guide/flow-make/create-board/)
