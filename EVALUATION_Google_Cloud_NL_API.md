# Google Cloud Natural Language API 導入評估報告

> 評估日期：2026-04-07
> 狀態：待評估
> 優先度：Phase 1 建議優先導入 Entity Sentiment Analysis

---

## 總覽

| 功能 | 日文名稱 | AgentK 適用度 | 判定 |
|------|---------|-------------|------|
| Entity Analysis | エンティティ分析 | ⭐⭐⭐⭐ | ✅ 可使用 |
| Syntax Analysis | 構文解析 | ⭐⭐ | △ 有限場景 |
| Entity Sentiment Analysis | エンティティ感情分析 | ⭐⭐⭐⭐⭐ | ✅ 強烈建議 |
| Content Classification | コンテンツ分類 | ⭐⭐⭐ | ✅ 可使用 |
| Text Moderation | テキストの管理 | ⭐⭐⭐⭐ | ✅ 可使用 |

---

## 1. エンティティ分析（Entity Analysis）

### 可以使用 — 具體原因

| 適用場景 | AgentK 對接方式 |
|---------|---------------|
| 會議逐字稿人名/公司名提取 | Groovenauts 會議分析 skill 前處理：自動識別「鈴木一郎（PM）」「田中美咲（エンジニア）」→ 填入出席者欄位 |
| LINE 對話中的實體辨識 | 用戶說「幫我查凱衛資訊的行程」→ 提取「凱衛資訊」為 ORGANIZATION → 精準搜尋 |
| 排程需求解析 | 「幫我在下週三跟田中開會」→ 提取 PERSON: 田中、DATE: 下週三 |
| Profile 自動更新 | 從對話中提取公司名、職稱、部門 → 自動豐富用戶 profile |

### 現狀 vs 導入後

| | 現狀（LLM 推理） | 導入 NL API 後 |
|---|---|---|
| 精度 | LLM 偶爾幻覺或漏抓 | API 回傳確定性分數，可設閾值 |
| 成本 | 每次都消耗 LLM token | Entity Analysis 每 1000 字元 ~$0.001 |
| 速度 | 需等 LLM 完整回應 | API ~200ms 回應 |

---

## 2. 構文解析（Syntax Analysis）

### 有限場景 — 具體原因

| 適用 | 不適用 |
|------|--------|
| 多語言斷詞（中/日/越/英混合逐字稿） | AgentK 已有 jieba 中文斷詞 + regex 日英 |
| 精準 POS tagging（品詞標注） | 目前 tool selection 的 token overlap 夠用 |
| 依存關係分析（主語-述語-目的語） | LLM 本身已能理解句法結構 |

### 結論

目前 LLM + jieba 組合已夠用，構文解析的投資報酬率不高。但未來若需要非 LLM 的輕量語言處理（如離線分析大量文本），可以考慮。

---

## 3. エンティティ感情分析（Entity Sentiment Analysis）

### 強烈建議導入 — 具體原因

這是對 AgentK 價值最高的功能。

| 適用場景 | 價值 |
|---------|------|
| LINE 對話 Signal Collection | 目前 Phase C1 用 regex 偵測「不對」「很好」等關鍵字判斷正負面 → 改用 API 可精準偵測對特定主題的情緒 |
| 會議記錄決策狀態 | Groovenauts 分析中「日方對此提案的態度是正面/保留/反對」→ 不靠 LLM 推測，用 API 量化 |
| Profile Deep Reasoning 輸入 | 目前 profile 更新依賴 LLM 分析對話 → 改用 API 先量化情緒再餵 LLM，品質更穩定 |
| 排程推播內容品質評估 | 推播新聞後，分析用戶回覆的情緒 → 自動調整推播主題/頻率 |
| GAI 學習單引導 | 即時偵測學員對每個區塊的投入度（積極/敷衍/困惑）→ 動態調整引導策略 |

### 導入後的架構變化

```
現狀：
  用戶訊息 → LLM 判斷情緒 → Profile / Signal

導入後：
  用戶訊息 → NL API (Entity Sentiment) → 量化數據
              ↓
         LLM 結合量化數據 → 更精準的 Profile / Signal
```

---

## 4. コンテンツ分類（Content Classification）

### 可以使用 — 具體原因

| 適用場景 | 價值 |
|---------|------|
| Model Router 前置分類 | 目前用 LLM-as-a-Router（gpt-4.1-nano）分類 → 改用 NL API 先分類內容類型，再決定 tier |
| 排程推播新聞分類 | 新聞搜尋結果 → API 自動分類（科技/金融/政治）→ 精準匹配用戶偏好 |
| 上傳文件自動分類 | 用戶上傳文件 → API 判斷類型 → 自動路由到對應 skill |

### 限制

需要至少 20 個 token（~20 詞），太短的訊息無法分類。LINE 對話經常很短，此功能適合用在文件/新聞/長文，不適合用在即時對話。

---

## 5. テキストの管理（Text Moderation）

### 可以使用 — 具體原因

| 適用場景 | 價值 |
|---------|------|
| 用戶輸入內容審核 | 偵測仇恨言論/色情/暴力內容 → 拒絕處理 |
| 推播內容品質控管 | LLM 產出的推播文字 → 發送前先過 moderation → 避免不當內容推送到 LINE |
| 群組對話監控 | 群組背景記憶中偵測到問題內容 → 標記但不回應 |
| GAI 學習單防護 | 學員輸入不當內容時 → 引導師角色婉拒 |

---

## 導入成本評估

| 項目 | 估算 |
|------|------|
| GCP 費用 | 免費額度 $300（新帳戶）；之後 Entity/Sentiment ~$1/1000 請求 |
| 開發成本 | 新增 1 個 service（`server/services/nl_api.py`）+ 各接入點 |
| API 啟用 | GCP Console 啟用 Cloud Natural Language API（已有 GCP project） |
| 依賴 | `google-cloud-language` pip 套件 |

---

## 未來銜接建議

| 階段 | 導入內容 | 優先度 | 預估工時 |
|------|---------|--------|---------|
| Phase 1（立即） | Entity Sentiment Analysis → 替換 C1 signal regex | 高 | 1-2 天 |
| Phase 2（短期） | Text Moderation → 推播內容品質控管 | 中 | 1 天 |
| Phase 3（中期） | Entity Analysis → 會議逐字稿前處理 | 中 | 2-3 天 |
| Phase 4（長期） | Content Classification → Router 前置分類 | 低 | 2 天 |
| 暫緩 | Syntax Analysis | — | 除非有離線大量文本分析需求 |

---

## 與現有架構的整合點

```
                    ┌─────────────────────┐
                    │  Google NL API      │
                    │  (Entity Sentiment) │
                    └────────┬────────────┘
                             ↓
  用戶訊息 → LINE Connector → NL API 前處理 → LLM Adapter
                    ↓                              ↓
             Signal Collection              Tool Calling
             Profile Update                 Skill Execution
```

---

## 參考資料

- [Cloud Natural Language API 基本概念](https://docs.cloud.google.com/natural-language/docs/basics?hl=ja)
- [Cloud Natural Language API 費用](https://cloud.google.com/natural-language/pricing)
