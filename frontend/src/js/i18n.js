/* ============================================================
   MCP Agent Console — i18n (Internationalization)
   Supported: zh-TW, en, ja, ko
   Usage:  window.I18n.setLang('ja')   // switch language
           window.I18n.t('key')        // get translation
   ============================================================ */

(function () {
  'use strict';

  var STORAGE_KEY = 'mcp_lang';

  /* ── Translation table ──────────────────────────────────── */
  var T = {

    /* ──────────────── 繁體中文 ──────────────── */
    'zh-TW': {
      /* Topbar & Sidebar */
      'topbar.workspace':         '研發組工作台',
      'topbar.search':            '搜尋對話或輸入指令…',
      'sidebar.skills.title':     '技能控管',
      'sidebar.loaded.skills':    '已載入技能',
      'sidebar.knowledge':        '知識庫文件',
      'sidebar.add.source':       '新增來源',
      'sidebar.version':          '版本',
      /* Main area */
      'main.title':               'UMA 工作台',
      'main.badge':               '進行中',
      'welcome.heading':          '今天想聊什麼？',
      'welcome.desc':             '直接與 AI 對話，若需要技能輔助可從底部選單附加。',
      /* Suggestion cards */
      'sugg.skillmd.title':       'SKILL.md 格式說明',
      'sugg.skillmd.desc':        '查看 YAML 格式規範',
      'sugg.skillmd.query':       '說明 SKILL.md 的 YAML 格式規範',
      'sugg.addskill.title':      '如何新增 Skill',
      'sugg.addskill.desc':       '學習新增技能的步驟',
      'sugg.addskill.query':      '如何新增一個 MCP Skill？',
      'sugg.uma.title':           'UMA 架構解說',
      'sugg.uma.desc':            '了解設計理念與架構',
      'sugg.uma.query':           '解釋 UMA 架構的設計理念',
      'sugg.skills.title':        '技能總覽',
      'sugg.skills.desc':         '查看所有可用技能',
      'sugg.skills.query':        '目前有哪些技能可以使用？',
      /* Input area */
      'input.placeholder':        '開始輸入…（Enter 送出，Shift+Enter 換行）',
      'input.footer.html':        '<kbd>Enter</kbd> 送出 · <kbd>Shift+Enter</kbd> 換行 · AI 可能有誤差，重要決策請自行驗證',
      'attach.label':             '附加技能：',
      'attach.none':              '無（純對話）',
      'execute.label':            '🚀 啟用執行模式',
      /* Right panel */
      'panel.title':              '工作面板',
      'tab.info':                 '資訊',
      'tab.tools':                '工具',
      'tab.log':                  '紀錄',
      'info.conn.title':          '連線狀態',
      'info.model.title':         '目前模型',
      'info.model.desc':          '可從輸入框右下角切換 AI 模型',
      'info.isolation.title':     '隔離說明',
      'info.isolation.name':      '完全隔離模式',
      'info.isolation.desc':      '此對話面板與技能執行完全隔離。AI 僅接收對話內容，不存取技能庫。',
      'info.version.title':       '版本資訊',
      'info.version.desc':        'v2.0.0-UMA · 研發組',
      'tools.skills.title':       '已載入技能',
      'tools.kb.title':           '知識庫',
      'tools.kb.docs':            '已載入文件',
      'log.ready':                '系統就緒，純對話模式啟用。',
      /* ── Skill Drawer ── */
      'drawer.btn.view':          '🔍 唯讀',
      'drawer.btn.edit':          '✏️ 編輯',
      'drawer.close.title':       '關閉',
      'drawer.install.deps':      '⬇ 安裝缺失依賴',
      'drawer.edit.notice':       '✏️ 編輯技能定義',
      'drawer.edit.hint':         'YAML 將自動產生',
      'drawer.section.structured':'結構化內容',
      'drawer.raw.toggle':        '🌐 切換原始模式 (Markdown)',
      'drawer.field.displayname': '顯示名稱',
      'drawer.field.displayname.ph':'例如：郵件分析專家',
      'drawer.field.displayname.info':'技能顯示名稱',
      'drawer.field.desc':        '簡短描述',
      'drawer.field.desc.info':   '一句話描述這個技能的功能',
      'drawer.field.desc.ph':     '一句話描述這個技能的功能',
      'drawer.field.prompt':      '角色定義 / 提示詞',
      'drawer.field.prompt.info': '定義角色的最高指導原則與行為邊界',
      'drawer.field.prompt.ph':   '在此貼上角色的具體行為指示、限制或任何 Markdown 語法...',
      'drawer.upload.knowledge':  '知識 (References)',
      'drawer.upload.knowledge.info':'作為 LLM 回覆資訊的現有知識參考',
      'drawer.upload.knowledge.btn':'新增知識庫檔案',
      'drawer.upload.scripts':    '客製指定程式 (Scripts)',
      'drawer.upload.scripts.info':'當客製指示中有提到需要執行指定的程式時使用',
      'drawer.upload.scripts.btn':'新增執行腳本',
      'drawer.upload.assets':     '參照檔案 (Assets)',
      'drawer.upload.assets.info':'當客製指示有提到需要參照模板做資訊整理或匯出時參考使用',
      'drawer.upload.assets.btn': '新增參照模板',
      'drawer.raw.ph':            '在此編輯完整的 SKILL.md Markdown 內容...',
      'drawer.btn.delete':        '刪除技能',
      'drawer.btn.rollback':      '↩ 還原版本',
      'drawer.btn.save':          '儲存',
      /* ── Auth Modal ── */
      'auth.title':               '需要授權',
      'auth.subtitle':            '高風險操作請求',
      'auth.btn.deny':            '拒絕',
      'auth.btn.approve':         '授權執行',
      /* ── Create Skill Modal ── */
      'create.title':             '新增技能',
      'create.subtitle':          '建立一個新的 MCP Skill 模板',
      'create.close.title':       '關閉',
      'create.label.id':          '技能識別碼 (ID)*',
      'create.ph.id':             '例如: web-scraper (只能英文/數字/連字號)',
      'create.hint.id':           '自動加上 `mcp-` 前綴，將成為資料夾名稱',
      'create.label.name':        '顯示名稱*',
      'create.ph.name':           '例如: 網頁爬蟲',
      'create.label.desc':        '簡短描述*',
      'create.ph.desc':           '說明這個技能的功能...',
      'create.label.cat':         '分類',
      'create.ph.cat':            '請輸入新分類名稱...',
      'create.noscript.label':    '僅使用 LLM 語意邏輯處理 (無需程式腳本)',
      'create.btn.cancel':        '取消',
      'create.btn.confirm':       '建立',
      /* ── Add Source Modal ── */
      'source.modal.prefix':      '從您的',
      'source.modal.suffix':      '建構知識庫',
      'source.highlight.web':     '網頁',
      'source.highlight.doc':     '文件',
      'source.highlight.text':    '文本',
      'source.search.ph':         '在網路上搜尋新來源',
      'source.search.btn':        '搜尋',
      'source.drop.text':         '或者將檔案拖放到這裡',
      'source.drop.hint':         'PDF、圖片、文件、音訊等 (上限 50 份)',
      'source.pill.upload':       '上傳檔案',
      'source.pill.url':          '網站連結',
      'source.pill.paste':        '複製文字',
      /* ── Researching / Creating modals ── */
      'modal.researching':        '正在分析並搜尋相關網頁...',
      'modal.creating.skill':     '正在建立新技能並優化 SKILL.md...',
      /* ── Source Selection Modal ── */
      'selection.select.all':     '選取所有來源',
      'selection.stats.pre':      '已選取',
      'selection.stats.post':     '件來源',
      'selection.import':         '匯入',
      /* ── URL Sub-Modal ── */
      'url.title':                '網站與 YouTube 連結',
      'url.desc':                 '將網站或 YouTube 的 URL 貼到以下欄位，作為知識庫的來源。',
      'url.ph':                   '貼上連結...',
      'url.hint.1':               '複數 URL 請以換行或空格分隔。',
      'url.hint.2':               '目前僅支援公開可存取的網頁內容。',
      'url.btn.insert':           '插入',
      /* ── Text Sub-Modal ── */
      'text.title':               '貼上文字',
      'text.desc':                '將文字內容貼到以下欄位，作為知識庫的來源。',
      'text.ph.name':             '來源名稱（選填）',
      'text.ph.content':          '在此貼上文字內容...',
      'text.btn.insert':          '插入',
      /* ── Alert Modal ── */
      'alert.btn.ok':             '確定',
    },

    /* ──────────────── English ──────────────── */
    'en': {
      /* Topbar & Sidebar */
      'topbar.workspace':         'R&D Workstation',
      'topbar.search':            'Search chats or enter command…',
      'sidebar.skills.title':     'Skills',
      'sidebar.loaded.skills':    'Loaded Skills',
      'sidebar.knowledge':        'Knowledge Base',
      'sidebar.add.source':       'Add Source',
      'sidebar.version':          'Version',
      /* Main area */
      'main.title':               'UMA Workbench',
      'main.badge':               'Active',
      'welcome.heading':          'What would you like to discuss?',
      'welcome.desc':             'Chat directly with the AI. Attach skills from the menu below for specialized assistance.',
      /* Suggestion cards */
      'sugg.skillmd.title':       'SKILL.md Format',
      'sugg.skillmd.desc':        'View YAML format spec',
      'sugg.skillmd.query':       'Explain the YAML format spec of SKILL.md',
      'sugg.addskill.title':      'How to Add Skills',
      'sugg.addskill.desc':       'Learn how to add new skills',
      'sugg.addskill.query':      'How do I add a new MCP Skill?',
      'sugg.uma.title':           'UMA Architecture',
      'sugg.uma.desc':            'Understand the design philosophy',
      'sugg.uma.query':           'Explain the design philosophy of the UMA architecture',
      'sugg.skills.title':        'Skills Overview',
      'sugg.skills.desc':         'View all available skills',
      'sugg.skills.query':        'What skills are currently available?',
      /* Input area */
      'input.placeholder':        'Start typing… (Enter to send, Shift+Enter for newline)',
      'input.footer.html':        '<kbd>Enter</kbd> send · <kbd>Shift+Enter</kbd> newline · AI may make errors — verify critical decisions',
      'attach.label':             'Attach skill:',
      'attach.none':              'None (chat only)',
      'execute.label':            '🚀 Enable Execute Mode',
      /* Right panel */
      'panel.title':              'Work Panel',
      'tab.info':                 'Info',
      'tab.tools':                'Tools',
      'tab.log':                  'Log',
      'info.conn.title':          'Connection Status',
      'info.model.title':         'Current Model',
      'info.model.desc':          'Switch AI model from the bottom-right of the input box',
      'info.isolation.title':     'Isolation',
      'info.isolation.name':      'Full Isolation Mode',
      'info.isolation.desc':      'This chat panel is fully isolated from skill execution. The AI only receives chat content and has no access to the skill library.',
      'info.version.title':       'Version Info',
      'info.version.desc':        'v2.0.0-UMA · R&D',
      'tools.skills.title':       'Loaded Skills',
      'tools.kb.title':           'Knowledge Base',
      'tools.kb.docs':            'Loaded Documents',
      'log.ready':                'System ready, pure chat mode activated.',
      /* ── Skill Drawer ── */
      'drawer.btn.view':          '🔍 View',
      'drawer.btn.edit':          '✏️ Edit',
      'drawer.close.title':       'Close',
      'drawer.install.deps':      '⬇ Install Missing Deps',
      'drawer.edit.notice':       '✏️ Edit Skill Definition',
      'drawer.edit.hint':         'YAML auto-generated',
      'drawer.section.structured':'Structured Content',
      'drawer.raw.toggle':        '🌐 Switch to Raw Mode (Markdown)',
      'drawer.field.displayname': 'Display Name',
      'drawer.field.displayname.ph':'e.g. Email Analysis Expert',
      'drawer.field.displayname.info':'Skill display name',
      'drawer.field.desc':        'Short Description',
      'drawer.field.desc.info':   'Describe this skill in one sentence',
      'drawer.field.desc.ph':     'Describe this skill in one sentence',
      'drawer.field.prompt':      'Role Definition / Prompt',
      'drawer.field.prompt.info': 'Define the guiding principles and behavior boundaries of this role',
      'drawer.field.prompt.ph':   'Paste role-specific behavior instructions, restrictions, or any Markdown here...',
      'drawer.upload.knowledge':  'Knowledge (References)',
      'drawer.upload.knowledge.info':'Existing knowledge references for LLM responses',
      'drawer.upload.knowledge.btn':'Add Knowledge File',
      'drawer.upload.scripts':    'Custom Scripts',
      'drawer.upload.scripts.info':'Use when custom instructions require specific script execution',
      'drawer.upload.scripts.btn':'Add Script',
      'drawer.upload.assets':     'Reference Assets',
      'drawer.upload.assets.info':'Use when custom instructions require reference templates for data organization or export',
      'drawer.upload.assets.btn': 'Add Reference Template',
      'drawer.raw.ph':            'Edit the full SKILL.md Markdown content here...',
      'drawer.btn.delete':        'Delete Skill',
      'drawer.btn.rollback':      '↩ Rollback',
      'drawer.btn.save':          'Save',
      /* ── Auth Modal ── */
      'auth.title':               'Authorization Required',
      'auth.subtitle':            'High-risk operation request',
      'auth.btn.deny':            'Deny',
      'auth.btn.approve':         'Authorize',
      /* ── Create Skill Modal ── */
      'create.title':             'Create Skill',
      'create.subtitle':          'Create a new MCP Skill template',
      'create.close.title':       'Close',
      'create.label.id':          'Skill ID*',
      'create.ph.id':             'e.g. web-scraper (letters/numbers/hyphens only)',
      'create.hint.id':           'Auto-prefixed with `mcp-`, becomes the folder name',
      'create.label.name':        'Display Name*',
      'create.ph.name':           'e.g. Web Scraper',
      'create.label.desc':        'Short Description*',
      'create.ph.desc':           'Describe this skill...',
      'create.label.cat':         'Category',
      'create.ph.cat':            'Enter new category name...',
      'create.noscript.label':    'LLM-only mode (no script required)',
      'create.btn.cancel':        'Cancel',
      'create.btn.confirm':       'Create',
      /* ── Add Source Modal ── */
      'source.modal.prefix':      'Build knowledge from your',
      'source.modal.suffix':      '',
      'source.highlight.web':     'Web',
      'source.highlight.doc':     'Documents',
      'source.highlight.text':    'Text',
      'source.search.ph':         'Search for new sources online',
      'source.search.btn':        'Search',
      'source.drop.text':         'Or drag and drop files here',
      'source.drop.hint':         'PDF, images, docs, audio, etc. (max 50)',
      'source.pill.upload':       'Upload File',
      'source.pill.url':          'Website URL',
      'source.pill.paste':        'Paste Text',
      /* ── Researching / Creating modals ── */
      'modal.researching':        'Analyzing and searching related pages...',
      'modal.creating.skill':     'Creating new skill and optimizing SKILL.md...',
      /* ── Source Selection Modal ── */
      'selection.select.all':     'Select All Sources',
      'selection.stats.pre':      'Selected',
      'selection.stats.post':     'sources',
      'selection.import':         'Import',
      /* ── URL Sub-Modal ── */
      'url.title':                'Website & YouTube Links',
      'url.desc':                 'Paste website or YouTube URLs below as knowledge base sources.',
      'url.ph':                   'Paste links...',
      'url.hint.1':               'Separate multiple URLs with newlines or spaces.',
      'url.hint.2':               'Only publicly accessible web content is supported.',
      'url.btn.insert':           'Insert',
      /* ── Text Sub-Modal ── */
      'text.title':               'Paste Text',
      'text.desc':                'Paste text content below as a knowledge base source.',
      'text.ph.name':             'Source name (optional)',
      'text.ph.content':          'Paste text content here...',
      'text.btn.insert':          'Insert',
      /* ── Alert Modal ── */
      'alert.btn.ok':             'OK',
    },

    /* ──────────────── 日本語 ──────────────── */
    'ja': {
      /* Topbar & Sidebar */
      'topbar.workspace':         '研究開発ワークステーション',
      'topbar.search':            'チャットを検索またはコマンドを入力…',
      'sidebar.skills.title':     'スキル管理',
      'sidebar.loaded.skills':    '読み込み済みスキル',
      'sidebar.knowledge':        'ナレッジベース',
      'sidebar.add.source':       'ソースを追加',
      'sidebar.version':          'バージョン',
      /* Main area */
      'main.title':               'UMA ワークベンチ',
      'main.badge':               '稼働中',
      'welcome.heading':          '今日は何をお話しますか？',
      'welcome.desc':             'AI と直接チャットしてください。専門的なサポートが必要な場合は、下部メニューからスキルを追加してください。',
      /* Suggestion cards */
      'sugg.skillmd.title':       'SKILL.md フォーマット',
      'sugg.skillmd.desc':        'YAML 形式の仕様を確認',
      'sugg.skillmd.query':       'SKILL.md の YAML 形式仕様を説明してください',
      'sugg.addskill.title':      'スキルの追加方法',
      'sugg.addskill.desc':       '新しいスキルの追加手順を学ぶ',
      'sugg.addskill.query':      '新しい MCP スキルを追加するには？',
      'sugg.uma.title':           'UMA アーキテクチャ',
      'sugg.uma.desc':            '設計哲学と構造を理解する',
      'sugg.uma.query':           'UMA アーキテクチャの設計理念を説明してください',
      'sugg.skills.title':        'スキル一覧',
      'sugg.skills.desc':         '利用可能な全スキルを表示',
      'sugg.skills.query':        '現在利用可能なスキルは何ですか？',
      /* Input area */
      'input.placeholder':        '入力を開始…（Enter で送信、Shift+Enter で改行）',
      'input.footer.html':        '<kbd>Enter</kbd> 送信 · <kbd>Shift+Enter</kbd> 改行 · AI はエラーが発生する場合があります。重要な判断は必ず自己確認してください',
      'attach.label':             'スキルを添付：',
      'attach.none':              'なし（チャットのみ）',
      'execute.label':            '🚀 実行モードを有効化',
      /* Right panel */
      'panel.title':              'ワークパネル',
      'tab.info':                 '情報',
      'tab.tools':                'ツール',
      'tab.log':                  'ログ',
      'info.conn.title':          '接続状態',
      'info.model.title':         '現在のモデル',
      'info.model.desc':          '入力ボックスの右下から AI モデルを切り替えられます',
      'info.isolation.title':     '隔離設定',
      'info.isolation.name':      '完全隔離モード',
      'info.isolation.desc':      'このチャットパネルはスキル実行から完全に隔離されています。AI は会話内容のみを受信し、スキルライブラリにはアクセスしません。',
      'info.version.title':       'バージョン情報',
      'info.version.desc':        'v2.0.0-UMA · 研究開発',
      'tools.skills.title':       '読み込み済みスキル',
      'tools.kb.title':           'ナレッジベース',
      'tools.kb.docs':            '読み込み済みドキュメント',
      'log.ready':                'システム準備完了、ピュアチャットモード有効。',
      /* ── Skill Drawer ── */
      'drawer.btn.view':          '🔍 表示',
      'drawer.btn.edit':          '✏️ 編集',
      'drawer.close.title':       '閉じる',
      'drawer.install.deps':      '⬇ 不足依存関係をインストール',
      'drawer.edit.notice':       '✏️ スキル定義を編集',
      'drawer.edit.hint':         'YAML は自動生成されます',
      'drawer.section.structured':'構造化コンテンツ',
      'drawer.raw.toggle':        '🌐 RAW モードに切り替え (Markdown)',
      'drawer.field.displayname': '表示名',
      'drawer.field.displayname.ph':'例：メール分析エキスパート',
      'drawer.field.displayname.info':'スキルの表示名',
      'drawer.field.desc':        '短い説明',
      'drawer.field.desc.info':   'このスキルの機能を一文で説明',
      'drawer.field.desc.ph':     'このスキルの機能を一文で説明してください',
      'drawer.field.prompt':      'ロール定義 / プロンプト',
      'drawer.field.prompt.info': 'ロールの最高指導原則と行動境界を定義',
      'drawer.field.prompt.ph':   'ロールの具体的な行動指示、制限、または Markdown をここに貼り付け...',
      'drawer.upload.knowledge':  '知識 (References)',
      'drawer.upload.knowledge.info':'LLM の回答情報の既存知識リファレンス',
      'drawer.upload.knowledge.btn':'知識ファイルを追加',
      'drawer.upload.scripts':    'カスタムスクリプト (Scripts)',
      'drawer.upload.scripts.info':'カスタム指示で特定のプログラム実行が必要な場合に使用',
      'drawer.upload.scripts.btn':'スクリプトを追加',
      'drawer.upload.assets':     '参照ファイル (Assets)',
      'drawer.upload.assets.info':'カスタム指示でテンプレートを参照してデータ整理や出力が必要な場合に使用',
      'drawer.upload.assets.btn': '参照テンプレートを追加',
      'drawer.raw.ph':            'ここで完全な SKILL.md Markdown コンテンツを編集...',
      'drawer.btn.delete':        'スキルを削除',
      'drawer.btn.rollback':      '↩ バージョンを元に戻す',
      'drawer.btn.save':          '保存',
      /* ── Auth Modal ── */
      'auth.title':               '認証が必要です',
      'auth.subtitle':            '高リスク操作リクエスト',
      'auth.btn.deny':            '拒否',
      'auth.btn.approve':         '実行を許可',
      /* ── Create Skill Modal ── */
      'create.title':             'スキルを作成',
      'create.subtitle':          '新しい MCP Skill テンプレートを作成',
      'create.close.title':       '閉じる',
      'create.label.id':          'スキル ID*',
      'create.ph.id':             '例: web-scraper（英数字とハイフンのみ）',
      'create.hint.id':           '`mcp-` プレフィックスが自動付加され、フォルダ名になります',
      'create.label.name':        '表示名*',
      'create.ph.name':           '例: ウェブスクレイパー',
      'create.label.desc':        '短い説明*',
      'create.ph.desc':           'このスキルの機能を説明...',
      'create.label.cat':         'カテゴリ',
      'create.ph.cat':            '新しいカテゴリ名を入力...',
      'create.noscript.label':    'LLM のみで処理（スクリプト不要）',
      'create.btn.cancel':        'キャンセル',
      'create.btn.confirm':       '作成',
      /* ── Add Source Modal ── */
      'source.modal.prefix':      'あなたの',
      'source.modal.suffix':      'から知識ベースを構築',
      'source.highlight.web':     'ウェブ',
      'source.highlight.doc':     'ドキュメント',
      'source.highlight.text':    'テキスト',
      'source.search.ph':         'ネットで新しいソースを検索',
      'source.search.btn':        '検索',
      'source.drop.text':         'またはファイルをここにドラッグ＆ドロップ',
      'source.drop.hint':         'PDF、画像、文書、音声など（最大 50 件）',
      'source.pill.upload':       'ファイルをアップロード',
      'source.pill.url':          'ウェブサイトリンク',
      'source.pill.paste':        'テキストを貼り付け',
      /* ── Researching / Creating modals ── */
      'modal.researching':        '関連ウェブページを分析・検索中...',
      'modal.creating.skill':     '新しいスキルを作成して SKILL.md を最適化中...',
      /* ── Source Selection Modal ── */
      'selection.select.all':     'すべてのソースを選択',
      'selection.stats.pre':      '選択済み',
      'selection.stats.post':     '件のソース',
      'selection.import':         'インポート',
      /* ── URL Sub-Modal ── */
      'url.title':                'ウェブサイトと YouTube リンク',
      'url.desc':                 'ウェブサイトまたは YouTube の URL を以下に貼り付けて、ナレッジベースのソースとして使用してください。',
      'url.ph':                   'リンクを貼り付け...',
      'url.hint.1':               '複数の URL は改行またはスペースで区切ってください。',
      'url.hint.2':               '現在、公開アクセス可能なウェブコンテンツのみサポートしています。',
      'url.btn.insert':           '挿入',
      /* ── Text Sub-Modal ── */
      'text.title':               'テキストを貼り付け',
      'text.desc':                'テキストコンテンツを以下に貼り付けて、ナレッジベースのソースとして使用してください。',
      'text.ph.name':             'ソース名（任意）',
      'text.ph.content':          'ここにテキストコンテンツを貼り付け...',
      'text.btn.insert':          '挿入',
      /* ── Alert Modal ── */
      'alert.btn.ok':             'OK',
    },

    /* ──────────────── 한국어 ──────────────── */
    'ko': {
      /* Topbar & Sidebar */
      'topbar.workspace':         '연구개발 워크스테이션',
      'topbar.search':            '채팅 검색 또는 명령어 입력…',
      'sidebar.skills.title':     '스킬 관리',
      'sidebar.loaded.skills':    '로드된 스킬',
      'sidebar.knowledge':        '지식 베이스',
      'sidebar.add.source':       '소스 추가',
      'sidebar.version':          '버전',
      /* Main area */
      'main.title':               'UMA 워크벤치',
      'main.badge':               '진행 중',
      'welcome.heading':          '오늘 무엇을 이야기하고 싶으신가요?',
      'welcome.desc':             'AI와 직접 대화하세요. 전문적인 지원이 필요하다면 하단 메뉴에서 스킬을 첨부하세요.',
      /* Suggestion cards */
      'sugg.skillmd.title':       'SKILL.md 형식',
      'sugg.skillmd.desc':        'YAML 형식 사양 확인',
      'sugg.skillmd.query':       'SKILL.md의 YAML 형식 사양을 설명해 주세요',
      'sugg.addskill.title':      '스킬 추가 방법',
      'sugg.addskill.desc':       '새 스킬 추가 단계 학습',
      'sugg.addskill.query':      '새로운 MCP 스킬을 추가하는 방법은?',
      'sugg.uma.title':           'UMA 아키텍처',
      'sugg.uma.desc':            '설계 철학과 구조 이해',
      'sugg.uma.query':           'UMA 아키텍처의 설계 이념을 설명해 주세요',
      'sugg.skills.title':        '스킬 목록',
      'sugg.skills.desc':         '사용 가능한 모든 스킬 확인',
      'sugg.skills.query':        '현재 사용 가능한 스킬은 무엇인가요?',
      /* Input area */
      'input.placeholder':        '입력 시작… (Enter로 전송, Shift+Enter로 줄바꿈)',
      'input.footer.html':        '<kbd>Enter</kbd> 전송 · <kbd>Shift+Enter</kbd> 줄바꿈 · AI는 오류가 발생할 수 있으므로 중요한 결정은 반드시 직접 확인하세요',
      'attach.label':             '스킬 첨부：',
      'attach.none':              '없음（채팅만）',
      'execute.label':            '🚀 실행 모드 활성화',
      /* Right panel */
      'panel.title':              '작업 패널',
      'tab.info':                 '정보',
      'tab.tools':                '도구',
      'tab.log':                  '로그',
      'info.conn.title':          '연결 상태',
      'info.model.title':         '현재 모델',
      'info.model.desc':          '입력창 오른쪽 하단에서 AI 모델을 전환할 수 있습니다',
      'info.isolation.title':     '격리 설명',
      'info.isolation.name':      '완전 격리 모드',
      'info.isolation.desc':      '이 채팅 패널은 스킬 실행과 완전히 격리되어 있습니다. AI는 대화 내용만 수신하며 스킬 라이브러리에 접근하지 않습니다.',
      'info.version.title':       '버전 정보',
      'info.version.desc':        'v2.0.0-UMA · 연구개발',
      'tools.skills.title':       '로드된 스킬',
      'tools.kb.title':           '지식 베이스',
      'tools.kb.docs':            '로드된 문서',
      'log.ready':                '시스템 준비 완료, 순수 채팅 모드가 활성화되었습니다.',
      /* ── Skill Drawer ── */
      'drawer.btn.view':          '🔍 보기',
      'drawer.btn.edit':          '✏️ 편집',
      'drawer.close.title':       '닫기',
      'drawer.install.deps':      '⬇ 누락 의존성 설치',
      'drawer.edit.notice':       '✏️ 스킬 정의 편집',
      'drawer.edit.hint':         'YAML 자동 생성',
      'drawer.section.structured':'구조화 콘텐츠',
      'drawer.raw.toggle':        '🌐 원시 모드로 전환 (Markdown)',
      'drawer.field.displayname': '표시 이름',
      'drawer.field.displayname.ph':'예: 이메일 분석 전문가',
      'drawer.field.displayname.info':'스킬 표시 이름',
      'drawer.field.desc':        '간단한 설명',
      'drawer.field.desc.info':   '이 스킬의 기능을 한 문장으로 설명',
      'drawer.field.desc.ph':     '이 스킬의 기능을 한 문장으로 설명하세요',
      'drawer.field.prompt':      '역할 정의 / 프롬프트',
      'drawer.field.prompt.info': '역할의 최고 지도 원칙과 행동 경계를 정의',
      'drawer.field.prompt.ph':   '역할의 구체적인 행동 지침, 제한 또는 Markdown을 여기에 붙여넣기...',
      'drawer.upload.knowledge':  '지식 (References)',
      'drawer.upload.knowledge.info':'LLM 응답 정보를 위한 기존 지식 참조',
      'drawer.upload.knowledge.btn':'지식 파일 추가',
      'drawer.upload.scripts':    '사용자 지정 스크립트 (Scripts)',
      'drawer.upload.scripts.info':'사용자 지정 지침에서 특정 프로그램 실행이 필요할 때 사용',
      'drawer.upload.scripts.btn':'스크립트 추가',
      'drawer.upload.assets':     '참조 파일 (Assets)',
      'drawer.upload.assets.info':'사용자 지정 지침에서 템플릿을 참조하여 정보 정리나 내보내기가 필요할 때 사용',
      'drawer.upload.assets.btn': '참조 템플릿 추가',
      'drawer.raw.ph':            '여기에서 전체 SKILL.md Markdown 내용을 편집...',
      'drawer.btn.delete':        '스킬 삭제',
      'drawer.btn.rollback':      '↩ 버전 복원',
      'drawer.btn.save':          '저장',
      /* ── Auth Modal ── */
      'auth.title':               '인증 필요',
      'auth.subtitle':            '고위험 작업 요청',
      'auth.btn.deny':            '거부',
      'auth.btn.approve':         '실행 허가',
      /* ── Create Skill Modal ── */
      'create.title':             '스킬 생성',
      'create.subtitle':          '새 MCP Skill 템플릿 생성',
      'create.close.title':       '닫기',
      'create.label.id':          '스킬 ID*',
      'create.ph.id':             '예: web-scraper (영문/숫자/하이픈만 가능)',
      'create.hint.id':           '`mcp-` 접두사가 자동으로 추가되어 폴더 이름이 됩니다',
      'create.label.name':        '표시 이름*',
      'create.ph.name':           '예: 웹 스크래퍼',
      'create.label.desc':        '간단한 설명*',
      'create.ph.desc':           '이 스킬의 기능을 설명...',
      'create.label.cat':         '카테고리',
      'create.ph.cat':            '새 카테고리 이름 입력...',
      'create.noscript.label':    'LLM만으로 처리 (스크립트 불필요)',
      'create.btn.cancel':        '취소',
      'create.btn.confirm':       '생성',
      /* ── Add Source Modal ── */
      'source.modal.prefix':      '당신의',
      'source.modal.suffix':      '에서 지식 베이스 구축',
      'source.highlight.web':     '웹',
      'source.highlight.doc':     '문서',
      'source.highlight.text':    '텍스트',
      'source.search.ph':         '온라인에서 새 소스 검색',
      'source.search.btn':        '검색',
      'source.drop.text':         '또는 파일을 여기에 드래그 앤 드롭',
      'source.drop.hint':         'PDF, 이미지, 문서, 오디오 등 (최대 50개)',
      'source.pill.upload':       '파일 업로드',
      'source.pill.url':          '웹사이트 링크',
      'source.pill.paste':        '텍스트 붙여넣기',
      /* ── Researching / Creating modals ── */
      'modal.researching':        '관련 웹페이지를 분석하고 검색 중...',
      'modal.creating.skill':     '새 스킬을 생성하고 SKILL.md를 최적화 중...',
      /* ── Source Selection Modal ── */
      'selection.select.all':     '모든 소스 선택',
      'selection.stats.pre':      '선택됨',
      'selection.stats.post':     '개 소스',
      'selection.import':         '가져오기',
      /* ── URL Sub-Modal ── */
      'url.title':                '웹사이트 및 YouTube 링크',
      'url.desc':                 '웹사이트 또는 YouTube URL을 아래에 붙여넣어 지식 베이스 소스로 사용하세요.',
      'url.ph':                   '링크 붙여넣기...',
      'url.hint.1':               '여러 URL은 줄바꿈이나 공백으로 구분하세요.',
      'url.hint.2':               '현재 공개적으로 접근 가능한 웹 콘텐츠만 지원됩니다.',
      'url.btn.insert':           '삽입',
      /* ── Text Sub-Modal ── */
      'text.title':               '텍스트 붙여넣기',
      'text.desc':                '텍스트 내용을 아래에 붙여넣어 지식 베이스 소스로 사용하세요.',
      'text.ph.name':             '소스 이름 (선택 사항)',
      'text.ph.content':          '여기에 텍스트 내용을 붙여넣기...',
      'text.btn.insert':          '삽입',
      /* ── Alert Modal ── */
      'alert.btn.ok':             '확인',
    },
  };

  /* ── Font stacks per language ───────────────────────────── */
  var FONTS = {
    'zh-TW': "'Inter', 'Noto Sans TC', system-ui, -apple-system, sans-serif",
    'en':    "'Inter', system-ui, -apple-system, sans-serif",
    'ja':    "'Inter', 'Noto Sans JP', 'Noto Sans TC', system-ui, -apple-system, sans-serif",
    'ko':    "'Inter', 'Noto Sans KR', 'Noto Sans TC', system-ui, -apple-system, sans-serif",
  };

  /* ── HTML lang attribute map ────────────────────────────── */
  var LANG_ATTR = { 'zh-TW': 'zh-TW', 'en': 'en', 'ja': 'ja', 'ko': 'ko' };

  /* ── Core helpers ───────────────────────────────────────── */
  function getCurrentLang() {
    return localStorage.getItem(STORAGE_KEY) || 'zh-TW';
  }

  function t(key) {
    var lang = getCurrentLang();
    var dict = T[lang] || T['zh-TW'];
    return dict[key] !== undefined ? dict[key] : (T['zh-TW'][key] || key);
  }

  function applyLang(lang) {
    if (!T[lang]) lang = 'zh-TW';
    var dict = T[lang];

    /* textContent replacements */
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var key = el.getAttribute('data-i18n');
      if (dict[key] !== undefined) el.textContent = dict[key];
    });

    /* innerHTML replacements (for content with HTML tags) */
    document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-html');
      if (dict[key] !== undefined) el.innerHTML = dict[key];
    });

    /* placeholder replacements */
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-placeholder');
      if (dict[key] !== undefined) el.placeholder = dict[key];
    });

    /* title attribute replacements */
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      var key = el.getAttribute('data-i18n-title');
      if (dict[key] !== undefined) el.title = dict[key];
    });

    /* html lang attribute */
    document.documentElement.lang = LANG_ATTR[lang] || 'zh-TW';

    /* font stack */
    document.documentElement.style.setProperty('--font-sans', FONTS[lang] || FONTS['zh-TW']);

    /* sync all lang-select elements */
    document.querySelectorAll('.lang-select').forEach(function (sel) {
      sel.value = lang;
    });
  }

  function setLang(lang) {
    localStorage.setItem(STORAGE_KEY, lang);
    applyLang(lang);
  }

  /* ── Suggestion card helper ─────────────────────────────── */
  /* Called via onclick="insertSuggI18n(this)" */
  window.insertSuggI18n = function (el) {
    var key   = el.getAttribute('data-sugg-key');
    var text  = key ? t(key) : '';
    var input = document.getElementById('userInput');
    if (input && text) {
      input.value = text;
      input.focus();
      input.dispatchEvent(new Event('input'));
    }
  };

  /* ── Public API ─────────────────────────────────────────── */
  window.I18n = {
    setLang:        setLang,
    getCurrentLang: getCurrentLang,
    applyLang:      applyLang,
    t:              t,
  };

  /* ── Auto-apply on DOM ready ────────────────────────────── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { applyLang(getCurrentLang()); });
  } else {
    applyLang(getCurrentLang());
  }

})();
