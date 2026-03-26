# MCP_Server 掃描報告

日期：2026-03-26

範圍：`.\code\MCP_Server`

方法：以目錄掃描為起點，之後逐一驗證入口、FastAPI 應用組裝、依賴提供層、主要路由、LINE 整合、前端入口、測試與部署檔案。這份報告偏架構與維運風險，不是程式風格檢查。

## 前端待辦清單

- [ ] 建立單一 `settings/bootstrap/lifecycle` 邊界，切斷執行期模組對 `main.py` 的反向依賴。
- [ ] 把 `skills` 路由中的 `git`、`pip`、刪檔與技能建立流程移出 API 層。
- [ ] 拆分 `line_connector.py`，至少切成 transport、orchestration、state 三層。
- [ ] 收斂前端 source of truth，決定正式採用 `frontend/pages + assets` 或 `frontend/src`。
- [ ] 抽出共用的檔名與路徑安全規則，移除 routes 內重複的 `sanitize_filename()`。
- [ ] 整理測試與診斷目錄，補上 `pyproject.toml` 與 pytest 設定。
- [ ] 統一本機、Docker、測試與前端提示的埠號與 `BASE_URL` 設定。

## Findings

### 1. High: 執行期套件仍依賴 `main.py`，套件邊界沒有真正成立

問題：
`server/` 內的執行期模組仍從腳本入口 `main.py` 取得 `PROJECT_ROOT`、`get_uma()` 與初始化副作用，代表 FastAPI app 雖然搬到 `server.app`，但核心邊界仍被入口腳本綁住。

為什麼重要：
這會讓匯入順序、測試啟動方式、CLI 腳本與容器啟動路徑彼此耦合。只要未來要補 `pyproject.toml`、做 package import、拆測試 fixture，這個邊界問題都會先卡住。

證據：
- `main.py:12-17` 在入口腳本內設定 `PROJECT_ROOT`、改寫 `sys.path`、載入 `.env`
- `server/app.py:11` 直接 `from main import PROJECT_ROOT`
- `server/dependencies/uma.py:17-24` 從 `main.py` 取 `PROJECT_ROOT`，並在 provider 內建立全域 UMA 單例
- `server/dependencies/session.py:5-11` 從 `main.py` 取根目錄來建立 `SessionManager`
- `server/routes/documents.py:12`
- `server/routes/resources.py:7`
- `server/routes/skills.py:15`
- `server/routes/workspace.py:12`
- `server/integrations/line_connector.py:698`, `server/integrations/line_connector.py:1596`, `server/integrations/line_connector.py:1648`
- `server/core/retriever.py:18-21` 又用另一種方式自行推導 `PROJECT_ROOT`

建議方向：
新增單一設定/啟動邊界，例如 `server/settings.py` 或 `server/bootstrap/`。讓 `main.py` 只負責啟動，`server.app` 與其他執行期模組只依賴 package 內設定物件，不再 import 腳本入口。

### 2. High: HTTP 路由直接執行 `git push`、`pip install`、刪檔，API 層與運維/系統層混在一起

問題：
`server/routes/skills.py` 不只是 controller，還直接在 request path 上做 repo 變更、套件安裝與檔案刪除。

為什麼重要：
這不是單純「邏輯太多」而已，而是把遠端 API、系統操作、套件管理與 Git 操作塞進同一層。這會讓權限模型、審計、錯誤回復與測試都變得脆弱，也讓 API 成為高風險操作入口。

證據：
- `server/routes/skills.py:31-59` `sync_skills_git()` 直接執行 `git add`、`git commit`、`git push`
- `server/routes/skills.py:46-54` `subprocess.run(...)` 執行 Git 命令
- `server/routes/skills.py:210-233` `/skills/{skill_name}/install` 直接 `pip install`
- `server/routes/skills.py:149-186` 刪除技能時直接 `shutil.rmtree(...)`
- `server/routes/skills.py:103-146`, `server/routes/skills.py:236-260`, `server/routes/skills.py:331-378` 同一個路由模組同時承擔檔案編輯、上傳、建立技能、重新註冊、Git 同步

建議方向：
把技能管理拆成至少三層：
- API 層：只驗證請求與回應
- Application service：技能建立/更新/重掃描流程
- Infrastructure/ops 層：Git、pip、檔案系統、背景任務

如果這些操作要保留在產品內，至少也應改成明確的 job queue 或 admin command，而不是同步執行於一般 API 路徑。

### 3. High: `line_connector.py` 已成為架構 choke point

問題：
`server/integrations/line_connector.py` 體積很大，且同時處理 webhook transport、Session 管理、pending-state、審批流程、排程推播支援、profile 更新、媒體/訊息回送等責任。

為什麼重要：
這種檔案一旦持續長大，任何 LINE 相關變更都會變成高風險修改，因為 transport、orchestration、state persistence 與 domain policy 沒有清楚 seam。

證據：
- 掃描結果顯示 `server/integrations/line_connector.py` 約 89 KB，為後端最大檔案之一
- `server/integrations/line_connector.py:54` 開始建立 LINE 元件
- `server/integrations/line_connector.py:356-392` 多處直接拉 `SessionManager`
- `server/integrations/line_connector.py:698-700` 在 pending-state 流程內從 `main.py` 取 `PROJECT_ROOT`
- `server/integrations/line_connector.py:743-748`, `server/integrations/line_connector.py:841-856`, `server/integrations/line_connector.py:916-919` 多段內嵌 runtime import 與 session/adapter 建立
- `server/integrations/line_connector.py:968`, `server/integrations/line_connector.py:1034`, `server/integrations/line_connector.py:1182`, `server/integrations/line_connector.py:1459-1460` 又混入 profile updater 與 runtime callable
- `server/integrations/line_connector.py:1595-1665` 同時處理 approval/choice pending state 寫入

建議方向：
至少拆成四個模組：
- `transport`: webhook 驗證、LINE SDK 封裝、reply/push client
- `orchestration`: LINE 訊息轉 chat request、工具審批流程
- `state`: pending state / session 交互
- `formatters`: LINE 訊息格式與輸出轉換

### 4. Medium: 啟動流程分散在 `main.py`、dependency provider 與 FastAPI event hook，初始化順序隱晦

問題：
UMA、watcher、scheduler、workspace sync 與 session flush 分散在三個地方：
- `main.py`
- `server/dependencies/uma.py`
- `server/app.py`

而且 `server/app.py` 仍用 `@app.on_event("startup"|"shutdown")` 舊式生命週期，搭配模組層級全域變數 `__watcher`、`__scheduler`。

為什麼重要：
初始化責任分裂後，很難回答「真正的 app 啟動路徑是哪一條」。這會直接影響測試、CLI 啟動、容器啟動與未來重構。

證據：
- `main.py:32-72` 有一套 `startup()` 初始化 UMA
- `main.py:75-95` `__main__` 區塊又透過 `get_uma()` 啟動 server
- `server/dependencies/uma.py:11-24` provider 懶建立全域 `_uma_instance`
- `server/app.py:19-20` 使用模組全域 `__watcher`、`__scheduler`
- `server/app.py:168-215` 以 `on_event` 管理 startup/shutdown
- `server/app.py:170-201` 在 startup 內同時啟動 delta index、workspace sync、directory watcher 與 APScheduler

建議方向：
改成單一 `lifespan` 啟動邊界，集中建立：
- settings
- UMA
- retriever/session providers
- watcher/scheduler/background tasks

把可關閉資源統一掛在 app state 或專門的 lifecycle container。

### 5. Medium: `documents` / `workspace` 路由不是薄控制器，還混合爬蟲、檔案系統、索引與 LLM fallback

問題：
目前多個 route module 直接處理檔案儲存、名稱映射、背景向量化、URL 抓取、Google Search API、OpenAI fallback 與 path policy；同時 `sanitize_filename()` 在多個路由重複定義。

為什麼重要：
這會讓路由層難測、規則容易漂移，也讓同一個檔名/路徑安全政策散落在不同檔案。

證據：
- `server/routes/documents.py:28-34`
- `server/routes/skills.py:23-28`
- `server/routes/workspace.py:20-25`
- `server/routes/documents.py:37-105` 同一支 endpoint 同時做 hash、存檔、更新 `.names.json`、觸發 retriever ingest
- `server/routes/documents.py:109-161` 直接在 route 中做 `httpx` 抓取與 `BeautifulSoup` 解析
- `server/routes/documents.py:164-249` 在 route 內做 Google Search 與 OpenAI fallback
- `server/routes/workspace.py:28-121` 同時負責 upload/download/image serving 與相容性路徑

建議方向：
抽出至少三個 service：
- `FilePolicyService` 或 shared path utils
- `WorkspaceDocumentService`
- `ResearchService`

route 保留 request/response 與例外轉換即可。

### 6. Medium: 前端存在兩套平行結構，遷移尚未收斂

問題：
目前真正被 `/ui` 提供的是 `frontend/pages + frontend/assets`，但 repo 同時保留 `frontend/src` 模組化前端樹，而且 `bootstrap.js` 明講「legacy app remains active」。

為什麼重要：
這會讓新加入的人無法立即判斷哪一套才是 source of truth，也會讓 CSS/JS 修正容易落在沒有被載入的檔案上。

證據：
- `frontend/index.html:7-15` 明確把 `/ui/` 導到 `pages/index.html`
- `server/app.py:37-38` 掛載整個 `frontend/`
- `frontend/pages/chat.html:7-9`, `frontend/pages/chat.html:315-317`
- `frontend/pages/index.html:7-9`, `frontend/pages/index.html:160`
- `frontend/pages/login.html:7-9`, `frontend/pages/login.html:163-164`
- `frontend/pages/settings.html:7-9`, `frontend/pages/settings.html:556-558`
- `frontend/src/js/bootstrap.js:1-8` 註明 modular graph 僅供「incremental migration」，legacy app 仍是 active path
- `server/services/chat_service.py:1-9` 也顯示後端 chat flow 仍有 bridge 模式

建議方向：
選一條路：
- 完成 `frontend/src` 遷移並讓 HTML/loader 真正引用它
- 或承認 `pages/assets` 是正式版，將 `src/` 移出主工作樹或標記為 archive

### 7. Medium: 測試與診斷資產分散，且不少檔案其實不是 pytest 測試

問題：
repo 內同時有 `tests/`、root-level `test_*.py`、`scripts/tests/`、`tmp/test_*.py`。其中多數檔案其實是手動腳本，依賴本機服務或外部 API，不是穩定的自動化測試。

為什麼重要：
這會讓測試發現規則不清楚，也會讓 CI、開發者本機與故障排查流程互相干擾。

證據：
- 掃描結果列出大量 `tests/` 之外的 `test_*.py`
- `tests/test_phase1.py:10-66` 以 `requests` 直接打 `http://localhost:8000`，並在 `if __name__ == "__main__"` 中執行
- `test_responses.py:1-28` 是直接打 OpenAI 的實驗腳本，不是 pytest 測試
- `scripts/tests/test_line_webhook.py:14-18` 先手動改 `sys.path`、載入 `.env`
- `scripts/tests/test_line_webhook.py:44-52` 直接打 `https://agentk.ngrok.dev/api/line/webhook`
- repo root 缺少 `pyproject.toml`、`pytest.ini`、`setup.cfg`

建議方向：
- 真正的自動化測試全部收斂到 `tests/`
- 手動診斷移到 `scripts/diagnostics/` 或 `tools/`
- 增加明確 pytest/package 設定

### 8. Medium: 埠號、啟動方式與交付配置有 drift，對本機開發與部署說明不夠一致

問題：
本機腳本、Docker、測試與前端提示分別使用不同埠號與 URL 假設。

為什麼重要：
這不一定會立刻壞掉，但它會增加 onboarding 與除錯成本，也容易造成「服務明明有跑、但測試/前端連錯位置」的誤判。

證據：
- `main.py:87-95` 直接啟動在 `8500`
- `scripts/dev/start_server.bat:4` 用 `uvicorn server.app:app --port 8500 --reload`
- `Dockerfile:30-34` 容器內用 `8000`
- `docker-compose.yml:9-10` 對外映射 `6888:8000`
- `tests/test_phase1.py:10` 固定打 `http://localhost:8000`
- `frontend/assets/js/login.js:100`, `frontend/assets/js/login.js:159-160` 又提示使用 `localhost:8500`
- `docker-compose.yml:1` 仍保留已過時的 top-level `version`

建議方向：
定義一份單一來源的 runtime config：
- `APP_PORT`
- `BASE_URL`
- `PUBLIC_UI_URL`

並讓開發腳本、Docker、測試與前端提示都從同一份設定推導。

## Open Questions / Assumptions

- 我假設目前正式對外路徑是 `server.app:app`，而不是舊的單檔 router 架構。
- 我假設 `frontend/pages + assets` 是目前正式使用中的前端，`frontend/src` 是未完成遷移。
- 我沒有啟動服務或實際執行網路型測試；本報告是靜態掃描加原始碼驗證，不是行為驗證。
- 部分中文字在終端輸出時出現編碼亂碼，但本次沒有把它列成 finding，因為尚未證實是檔案本身編碼問題還是讀取環境造成。

## Refactor Direction

建議重構順序如下：

1. 先建立 `settings/bootstrap/lifecycle` 單一路徑，切斷 `server/* -> main.py` 的反向依賴。
2. 把 `skills` 路由中的 Git、pip、刪檔與技能建立流程移出 API 層。
3. 拆 `line_connector.py`，至少切出 transport、state、orchestration。
4. 收斂前端 source of truth，決定 `assets/pages` 或 `src` 哪一套才是正式版。
5. 整理測試與診斷目錄，補 `pyproject.toml` 與 pytest 設定。

## Positive Notes

- `server/routes/chat.py:13-15` 已是相對乾淨的 thin route，代表 service extraction 有開始發生。
- `server/services/chat_service.py:1-9`、`server/services/chat_core.py:21-139` 顯示聊天主流程已有往 service 層移動。
- `server/` 目錄至少已按 `routes / services / adapters / integrations / dependencies / core` 分層，代表架構方向是對的，問題主要在遷移尚未收斂。
