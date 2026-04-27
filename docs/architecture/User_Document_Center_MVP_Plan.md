# User Document Center MVP Plan

## 1. Goal

目標是讓使用者可以在 Web Chat 內上傳文件，暫時保存，並在後續對話中用自然語言叫出這些文件，再由系統提供合適的展示方式。

本規劃聚焦於 MVP，不追求完整 DMS（Document Management System），而是先做好：

- 使用者個人文件暫存
- 可列出目前可查閱文件
- 可透過自然語言指定文件
- 可選擇展示方式
- 與既有聊天流程整合


## 2. Feasibility Summary

### 2.1 Recommended file types for MVP

建議第一版支援：

- `.txt`
- `.md`
- `.pdf`
- `.docx`

建議第一版不要支援：

- `.doc`

原因：

- `txt` / `md` 已可直接讀取純文字，風險最低。
- `pdf` 已有 `pdfplumber` / `pypdf` 抽取基礎，但僅適合文字型 PDF。
- `docx` 已有 `python-docx` / `docx2txt` 可用。
- `.doc` 是舊版二進位格式，現有專案未正式支援，若要納入通常需要額外轉檔工具或外部依賴，成本與不穩定性較高。

### 2.2 Preview / display feasibility

三種展示方式都可行，但成熟度不同：

1. 服務內預覽：可行，建議作為主方案
2. 文字訊息展示：可行，且最容易先完成
3. 一鍵啟用外部軟體：可行性取決於部署型態，不建議作為通用 Web 方案主功能


## 3. Architecture Decision

### 3.1 Do not reuse shared workspace documents directly

目前專案已有共用文件模組，檔案存放在 `workspace/`，並由 `.names.json` 管理顯示名稱。這套比較像共享知識庫或管理後台文件池，不適合直接拿來做「每位使用者自己的暫存文件」。

原因：

- 所有使用者共用同一個目錄
- 顯示名稱映射也是共享
- 目前語意更接近全域 workspace 資源，不是 user-scoped temporary storage

### 3.2 Recommended storage model

建議新增獨立的「使用者文件中心」儲存區，避免與下列用途混在一起：

- `workspace/`：共享文件與系統索引資料
- `Agent_workspace/line_uploads/`：LINE / Web 附件型上傳，偏暫時性訊息附件

建議新路徑：

`PROJECT_ROOT / "Agent_workspace" / "user_documents" / {user_id} /`

每位使用者一個目錄，底下存：

- 實體檔案
- `manifest.json` 或 `index.json`

### 3.3 Recommended metadata model

每筆文件建議保存以下欄位：

- `doc_id`
- `user_id`
- `original_filename`
- `stored_filename`
- `extension`
- `mime_type`
- `size`
- `created_at`
- `expires_at`
- `display_name`
- `text_extract_status`
- `preview_type`
- `source`

欄位說明：

- `doc_id`：給前端與 LLM 使用的穩定識別碼
- `stored_filename`：真正寫入磁碟的檔名，避免撞名
- `display_name`：給使用者看的名稱，可保留原名或允許改名
- `text_extract_status`：例如 `pending` / `ready` / `failed`
- `preview_type`：例如 `text` / `pdf-inline` / `download-only`
- `source`：可保留 `web_upload`、`line_upload` 等來源


## 4. Recommended User Flow

### 4.1 Upload flow

1. 使用者從聊天頁或文件面板上傳文件
2. 後端將文件存入 `user_documents/{user_id}/`
3. 後端建立 manifest 紀錄
4. 若檔案支援文字抽取，背景抽取文字並緩存
5. 聊天畫面顯示「已收到文件」與可執行動作

### 4.2 Recall flow

使用者可能會這樣說：

- 我目前有哪些文件可以看
- 幫我打開剛剛那份 PDF
- 我想看會議紀錄
- 顯示上次上傳的提案文件

系統流程建議：

1. 先做文件意圖判斷
2. 若使用者是在問「有哪些文件」，回傳文件清單
3. 若使用者是在指定某份文件，做文件名稱比對
4. 命中單一文件後，先詢問展示方式
5. 命中多份文件時，先要求使用者確認目標文件

### 4.3 Display option flow

當使用者要看文件時，固定走這個互動：

1. 系統先確認是哪份文件
2. 系統詢問展示方式
3. 系統依方式回傳內容或預覽入口

建議展示選單固定為：

- 在服務內預覽
- 以文字訊息顯示
- 提供開啟連結


## 5. Display Strategy Recommendation

### 5.1 Option A: In-app preview

這是最推薦的主方案。

建議行為：

- `txt` / `md`：直接用文字預覽頁或 modal 顯示
- `docx`：顯示抽取後文字預覽
- `pdf`：優先用 inline PDF route 預覽；若瀏覽器不支援，再退回下載或文字抽取

優點：

- 符合 Web 使用習慣
- 不需依賴本機軟體
- 可和聊天畫面深度整合

限制：

- `docx` 預覽不是原始版面，而是抽取後文字
- 掃描型 PDF 若沒有 OCR，文字預覽品質會差

### 5.2 Option B: Show content as chat text

這是最容易完成的方案，也是很好的 fallback。

建議行為：

- 直接抽出純文字內容
- 長文件分段回傳
- 首次顯示前先提示文件長度與是否要節錄 / 全文

優點：

- 實作最簡單
- 可直接供 LLM 後續分析

限制：

- 長文件會讓聊天體驗變差
- 版面與圖片內容會遺失

### 5.3 Option C: One-click open link

此功能不應作為通用 Web 功能主軸。

原因：

- 一般瀏覽器無法安全地直接在使用者本機執行 CLI 或桌面軟體
- 如果在伺服器端執行，打開的是伺服器那台機器，不是使用者自己的電腦

建議替代方案：

- 提供下載連結
- 提供預覽頁連結
- 如果未來是 Electron / 桌面 App，再考慮實作本機 `open` 行為


## 6. Backend Design

### 6.1 New route group

建議新增獨立路由，例如：

- `POST /api/user-documents/upload`
- `GET /api/user-documents`
- `GET /api/user-documents/{doc_id}`
- `GET /api/user-documents/{doc_id}/preview`
- `GET /api/user-documents/{doc_id}/content`
- `DELETE /api/user-documents/{doc_id}`
- `POST /api/user-documents/{doc_id}/rename`

### 6.2 Suggested endpoint behavior

`POST /api/user-documents/upload`

- 驗證副檔名
- 儲存到 user-scoped directory
- 建立 metadata
- 背景執行文字抽取
- 回傳 `doc_id` 與文件基本資訊

`GET /api/user-documents`

- 列出目前登入使用者可查閱文件
- 依 `created_at desc` 排序
- 可帶 `include_expired=false` 預設過濾過期檔

`GET /api/user-documents/{doc_id}/preview`

- 若是 `pdf`，回傳 inline file response
- 若是 `txt` / `md` / `docx`，回傳預覽 JSON
- 若目前尚無法預覽，回傳 fallback metadata

`GET /api/user-documents/{doc_id}/content`

- 回傳抽取文字
- 支援 `offset` / `limit`
- 供聊天文字展示或分段閱讀使用

### 6.3 Extraction service

建議新增 user document service，封裝：

- 存檔
- manifest 讀寫
- doc_id 查詢
- 檔案過期判定
- 文字抽取快取

可重用既有 `file_extractor.py`，但不要直接把 user document 邏輯塞進舊的共享文件路由。


## 7. Frontend Design

### 7.1 Recommended UI placement

建議在聊天頁增加一個「文件中心」區塊，可放在：

- 右側資訊面板
- 或聊天工具列的文件按鈕

建議最小 UI 功能：

- 上傳文件按鈕
- 我的文件清單
- 點選文件後顯示操作選單

### 7.2 Recommended chat integration

聊天流程不要只依賴 LLM 純文字輸出，建議前端與後端配合做結構化處理。

原因：

- 目前聊天 bubble renderer 只處理純文字格式化
- 沒有完整的 markdown link / rich action card renderer
- 單靠 LLM 回文字，不容易穩定做到「一鍵開啟」或「展示方式選單」

建議做法：

- 後端在判斷出文件意圖後，回傳結構化 payload
- 前端根據 payload 顯示文件卡片或展示方式選單

例如：

```json
{
  "status": "success",
  "response_type": "document_prompt",
  "document": {
    "doc_id": "doc_123",
    "display_name": "meeting-notes.docx"
  },
  "options": [
    {"key": "preview", "label": "在服務內預覽"},
    {"key": "text", "label": "以文字訊息顯示"},
    {"key": "link", "label": "提供開啟連結"}
  ]
}
```


## 8. Natural Language Recall Design

### 8.1 Recommended implementation layer

建議不要把「找文件」這件事完全交給 LLM 自由發揮，而是採混合模式：

- 規則 / intent handler 負責辨識文件查詢意圖
- 文件服務負責列出與搜尋候選文件
- LLM 負責生成自然語言回應

### 8.2 Recommended intents

先支援三類即可：

- `list_documents`
- `open_document`
- `show_document_content`

### 8.3 Recommended matching strategy

文件比對建議依序：

1. `doc_id`
2. 完整檔名
3. display name
4. 模糊名稱比對
5. 最近上傳文件 fallback

若命中多筆，回傳候選清單讓使用者選。


## 9. TTL and Cleanup Recommendation

### 9.1 Default TTL

因為需求明確提到「暫時儲存」，建議第一版就定義 TTL。

建議：

- 預設保存 7 天
- 或 14 天

### 9.2 Cleanup strategy

可沿用目前 scheduler 思路，新增：

- `user_documents_cleanup`

清理內容：

- 已過期文件
- 已失效的抽取文字快取
- 空資料夾

### 9.3 Why not reuse current line_upload TTL directly

目前 `line_uploads` 的 168 小時清理比較像附件池規則，不一定適合作為文件中心正式規則。

建議將文件中心 TTL 獨立管理，原因：

- 文件中心和聊天附件是不同產品語意
- 文件中心未來可能要允許延長保存
- 文件中心需要獨立 metadata 與查詢能力


## 10. MVP Scope Recommendation

### Phase 1

先完成最小可用版本：

- 支援 `txt` / `md` / `pdf` / `docx`
- 使用者個人文件目錄
- 文件上傳
- 文件清單
- 文件刪除
- 文件重新命名
- 文字抽取
- 服務內預覽
- 文字訊息展示

### Phase 2

再做聊天整合：

- 自然語言列文件
- 自然語言指定文件
- 展示方式選單
- 候選文件 disambiguation

### Phase 3

最後補強：

- TTL 設定
- 清理排程
- 預覽體驗優化
- PDF inline preview 改善
- 長文分段閱讀


## 11. Concrete Recommendation for This Repo

### 11.1 Backend

建議新增：

- `server/routes/user_documents.py`
- `server/schemas/user_documents.py`
- `server/services/user_document_service.py`

建議重用：

- `server/services/file_extractor.py`
- 既有 auth cookie / user id resolution 邏輯
- 既有 scheduler 設計模式

### 11.2 Frontend

建議修改：

- `frontend/assets/js/chat.js`
- `frontend/pages/chat.html`
- `frontend/assets/css/chat.css`

建議不要直接沿用舊版 `frontend/src/js/app.js` 文件模組做整包搬運，但可以借用其文件清單與勾選交互思路。

### 11.3 Recommended implementation choice

這個專案最適合的方案是：

- 後端新增 user-scoped document center
- 前端聊天頁新增文件中心入口
- 文件展示主打 `服務內預覽 + 文字訊息展示`
- `開啟連結` 先做成下載 / 預覽頁連結，而不是桌面程式啟動


## 12. Final Recommendation

結論如下：

- 第一版支援 `txt` / `md` / `pdf` / `docx`
- 第一版不要支援 `.doc`
- 不要直接沿用共享 `workspace/` 文件池
- 新增 `user_documents/{user_id}` 架構最合適
- 優先做 `服務內預覽` 與 `文字訊息展示`
- `一鍵啟用外部軟體` 不適合作為通用 Web 主流程
- 文件召回應採「intent handler + document service + LLM response」混合模式
