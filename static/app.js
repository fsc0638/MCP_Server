/**
 * MCP Agent Console — app.js (v2)
 *
 * STRICT SEPARATION:
 *   - Module A: CHAT   — pure LLM conversation, calls /chat POST endpoint only
 *   - Module B: SKILLS — management, drawer, edit/save/rollback/install
 *
 * These two modules NEVER call each other's functions.
 */

document.addEventListener('DOMContentLoaded', () => {

    // =========================================================================
    // MODULE A: CHAT (Pure LLM, no skill execution)
    // =========================================================================

    const chatModule = (() => {
        const msgContainer = document.getElementById('messageContainer');
        const chatViewport = document.getElementById('chatViewport');
        const userInput = document.getElementById('userInput');
        const sendBtn = document.getElementById('sendBtn');
        const modelSelector = document.getElementById('modelSelector');
        const welcomeBlock = document.getElementById('welcomeBlock');
        const clearChatBtn = document.getElementById('clearChatBtn');
        const attachSelect = document.getElementById('attachSkillSelect');
        const attachHint = document.getElementById('attachHint');
        const clearAttach = document.getElementById('clearAttach');

        // New Sandbox DOM elements
        const executeSwitchWrapper = document.getElementById('executeSwitchWrapper');
        const executeSkillSwitch = document.getElementById('executeSkillSwitch');
        const fileChipContainer = document.getElementById('fileChipContainer');
        const attachedFileName = document.getElementById('attachedFileName');
        const uploadProgressBar = document.getElementById('uploadProgressBar');
        const uploadProgressFill = document.getElementById('uploadProgressFill');
        const removeFileBtn = document.getElementById('removeFileBtn');
        const workspaceFileInput = document.getElementById('workspaceFileInput');
        const attachFileBtn = document.getElementById('attachFileBtn');

        let attachedFilePath = null;
        let isUploading = false;

        function escapeHtml(s) {
            if (!s) return '';
            return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        window.previewWorkspaceFile = async (filename) => {
            try {
                const url = `/workspace/download/${filename}`;
                const lowerName = filename.toLowerCase();
                let contentHtml = '';
                if (lowerName.endsWith('.png') || lowerName.endsWith('.jpg') || lowerName.endsWith('.jpeg') || lowerName.endsWith('.webp') || lowerName.endsWith('.gif')) {
                    contentHtml = `<img src="${url}" class="preview-image">`;
                } else {
                    const res = await fetch(url);
                    const text = await res.text();
                    contentHtml = `<pre class="preview-text">${escapeHtml(text)}</pre>`;
                }

                const modal = document.createElement('div');
                modal.className = 'modal-overlay';
                modal.style.zIndex = '9999';
                modal.innerHTML = `
                    <div class="modal-card" style="max-width: 800px; width: 90%;">
                        <div class="modal-card-header">
                            <h3>檔案預覽：${filename}</h3>
                            <button class="icon-btn" onclick="this.closest('.modal-overlay').remove()">✕</button>
                        </div>
                        <div class="modal-card-body">
                            <div class="preview-content">
                                ${contentHtml}
                            </div>
                        </div>
                    </div>
                `;
                document.body.appendChild(modal);
            } catch (e) {
                alert('預覽失敗: ' + e.message);
            }
        };

        function processWorkspaceLinks(htmlText) {
            // Regex to match "workspace/filename.ext" paths in the output text
            // We want to replace it with a nice card UI
            return htmlText.replace(/(?:file:\/\/\/.*?\/)?workspace\/([a-zA-Z0-9.\-_]+)/g, (match, filename) => {
                const lowerName = filename.toLowerCase();
                const isPreviewable = lowerName.endsWith('.txt') || lowerName.endsWith('.md') || lowerName.endsWith('.json') ||
                    lowerName.endsWith('.png') || lowerName.endsWith('.jpg') || lowerName.endsWith('.jpeg') || lowerName.endsWith('.webp');

                let previewBtnStr = '';
                if (isPreviewable) {
                    previewBtnStr = `<button class="action-btn preview-btn" onclick="previewWorkspaceFile('${filename}')">👁️ 預覽</button>`;
                }

                return `
                <div class="workspace-file-card">
                    <span class="file-icon">📄</span>
                    <span class="file-name" title="${filename}">${filename}</span>
                    <div class="workspace-file-actions">
                        ${previewBtnStr}
                        <a href="/workspace/download/${filename}" class="action-btn download-btn" download="${filename}" target="_blank">⬇️ 下載</a>
                    </div>
                </div>`;
            });
        }

        const SESSION_KEY = 'mcp_session_id';
        let sessionId = localStorage.getItem(SESSION_KEY);
        if (!sessionId) {
            sessionId = 'web-' + Math.random().toString(36).slice(2, 8);
            localStorage.setItem(SESSION_KEY, sessionId);
        }

        function appendMessage(role, text) {
            if (welcomeBlock) welcomeBlock.style.display = 'none';
            const div = document.createElement('div');
            div.className = `message ${role}`;
            if (role === 'assistant') {
                let html = marked.parse(text);
                html = processWorkspaceLinks(html);

                // Citation rendering: [1] or [FileName#chunk_x]
                html = html.replace(/\[(\d+)\]/g, '<span class="citation" title="檢視來源">$1</span>');
                html = html.replace(/\[([a-zA-Z0-9.\-_]+)#chunk_\d+\]/g, (match, filename) => {
                    return `<span class="citation-file" onclick="window.previewWorkspaceFile('${filename}')" title="開啟檔案: ${filename}">[來源: ${filename}]</span>`;
                });

                div.innerHTML = html;
            } else {
                div.textContent = text;
            }
            msgContainer.appendChild(div);
            // Add a small delay for dom render to scroll accurately
            setTimeout(() => { chatViewport.scrollTop = chatViewport.scrollHeight; }, 50);
        }

        function appendErrorMsg(text) {
            const div = document.createElement('div');
            div.className = 'message assistant';
            div.style.borderLeft = '3px solid var(--red)';
            div.style.color = 'var(--red)';
            div.textContent = '⚠ ' + text;
            msgContainer.appendChild(div);
            chatViewport.scrollTop = chatViewport.scrollHeight;
        }

        function showTypingIndicator() {
            const div = document.createElement('div');
            div.className = 'message assistant typing-indicator';
            div.id = 'typingIndicator';
            div.innerHTML = '<span></span><span></span><span></span>';
            msgContainer.appendChild(div);
            chatViewport.scrollTop = chatViewport.scrollHeight;
        }
        function removeTypingIndicator() {
            const el = document.getElementById('typingIndicator');
            if (el) el.remove();
        }

        async function sendMessage() {
            const text = userInput.value.trim();
            if (!text && !attachedFilePath) return;

            userInput.value = '';
            userInput.style.height = 'auto';

            const displayMsg = text || `[附加檔案: ${attachedFileName.textContent}]`;
            appendMessage('user', displayMsg);
            logModule.addLog('USER', `發送：${displayMsg}`);

            const model = modelSelector.value;
            const attachedSkill = attachSelect.value || null;
            const executeMode = executeSkillSwitch.checked;

            userInput.disabled = true;
            sendBtn.disabled = true;
            if (executeMode) {
                sendBtn.innerHTML = '<span style="font-size:11px; white-space:nowrap;">腳本執行中...</span>';
                sendBtn.style.width = 'auto';
                sendBtn.style.padding = '0 12px';
                sendBtn.style.borderRadius = '16px';
            }
            showTypingIndicator();

            try {
                const payload = {
                    user_input: text,
                    session_id: sessionId,
                    model: model,
                    injected_skill: attachedSkill,
                    execute: executeMode,
                    attached_file: attachedFilePath,
                    selected_docs: window.docModule ? window.docModule.getSelectedDocs() : []
                };
                console.log('[CHAT] Sending payload:', JSON.stringify(payload, null, 2));
                logModule.addLog('SYS', `發送模式: execute=${executeMode}, 檔案=${attachedFilePath ? attachedFilePath.split('/').pop() : '無'}`);

                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                removeTypingIndicator();

                // Reset send button UI
                sendBtn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M22 2L11 13M22 2L15 22L11 13M11 13L2 9L22 2" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
                sendBtn.style.width = '36px';
                sendBtn.style.padding = '0';
                sendBtn.style.borderRadius = '50%';

                if (!res.ok) {
                    const err = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
                    throw new Error(err.message || `HTTP ${res.status}`);
                }

                const data = await res.json();
                console.log('[CHAT] Response data:', data);
                logModule.addLog('SYS', `AI 回覆: status=${data.status}`);

                if (data.status === 'success') {
                    appendMessage('assistant', data.content);
                    logModule.addLog('AI', '回覆完成');
                    if (attachedSkill) {
                        logModule.addLog('INFO', `附加技能「${attachedSkill}」的 metadata 已注入本輪對話`);
                    }
                } else {
                    appendErrorMsg(data.message || '未知錯誤');
                    logModule.addLog('ERR', data.message || '未知錯誤', 'error');
                }

            } catch (e) {
                removeTypingIndicator();
                appendErrorMsg(e.message);
                logModule.addLog('ERR', e.message, 'error');
            } finally {
                userInput.disabled = false;
                sendBtn.disabled = false;
                userInput.focus();
            }
        }

        async function clearChat() {
            if (!confirm('確定要清除此對話紀錄？內容將先儲存至 MEMORY.md')) return;
            try {
                await fetch(`/chat/flush/${sessionId}`, { method: 'POST' });
                await fetch(`/chat/session/${sessionId}`, { method: 'DELETE' });
            } catch (_) { }
            // Generate a fresh session for the next conversation
            sessionId = 'web-' + Math.random().toString(36).slice(2, 8);
            localStorage.setItem(SESSION_KEY, sessionId);
            msgContainer.innerHTML = '';
            if (welcomeBlock) welcomeBlock.style.display = '';
            logModule.addLog('SYS', '對話已清除，記憶已儲存至 MEMORY.md');
        }

        // Attach Skill select → show hint and toggle wrapper
        if (attachSelect) attachSelect.onchange = () => {
            const v = attachSelect.value;
            attachHint.textContent = v ? `準備載入「${v}」的相關資訊` : '';
            if (v) {
                executeSwitchWrapper.classList.remove('hidden');
            } else {
                executeSwitchWrapper.classList.add('hidden');
                executeSkillSwitch.checked = false;
            }
        };
        if (clearAttach) clearAttach.onclick = () => {
            attachSelect.value = '';
            attachHint.textContent = '';
            executeSwitchWrapper.classList.add('hidden');
            executeSkillSwitch.checked = false;
        };

        // Workspace Attach File Logic
        if (attachFileBtn) {
            attachFileBtn.onclick = () => {
                if (isUploading) return;
                workspaceFileInput.click();
            };
        }

        if (workspaceFileInput) workspaceFileInput.onchange = () => {
            const file = workspaceFileInput.files[0];
            if (!file) return;

            // Reset UI for upload
            fileChipContainer.classList.remove('hidden');
            attachedFileName.textContent = file.name;
            uploadProgressBar.classList.remove('hidden');
            uploadProgressFill.style.width = '0%';
            isUploading = true;

            const formData = new FormData();
            formData.append('file', file);

            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/documents/upload', true); // Changed endpoint to match Sprint 1 backend

            xhr.upload.onprogress = (e) => {
                if (e.lengthComputable) {
                    const percent = Math.round((e.loaded / e.total) * 100);
                    uploadProgressFill.style.width = percent + '%';
                }
            };

            xhr.onload = () => {
                isUploading = false;
                uploadProgressBar.classList.add('hidden');
                if (xhr.status === 200) {
                    const res = JSON.parse(xhr.responseText);
                    attachedFilePath = res.path;     // Backend now returns 'path'

                    if (res.vectorized === 'pending') {
                        attachedFileName.textContent = `${res.original_filename} (索引建立中...)`;
                        logModule.addLog('SYS', `檔案上傳成功: ${res.original_filename}，系統正在背景建立索引...`);
                    } else {
                        attachedFileName.textContent = res.original_filename;
                        logModule.addLog('SYS', `檔案上傳成功: ${res.original_filename}`);
                    }

                    // Refresh document list if docModule is active
                    if (window.docModule) window.docModule.loadDocuments();

                } else {
                    const errRes = JSON.parse(xhr.responseText || '{}');
                    alert(`上傳失敗: ${errRes.detail || '未知錯誤'}`);
                    clearAttachedFile();
                }
                workspaceFileInput.value = '';
            };

            xhr.onerror = () => {
                isUploading = false;
                alert('網路錯誤導致上傳失敗');
                clearAttachedFile();
                workspaceFileInput.value = '';
            };

            xhr.send(formData);
        };

        function clearAttachedFile() {
            attachedFilePath = null;
            fileChipContainer.classList.add('hidden');
            uploadProgressBar.classList.add('hidden');
        }

        if (removeFileBtn) removeFileBtn.onclick = clearAttachedFile;

        // Event listeners
        sendBtn.onclick = sendMessage;
        clearChatBtn.onclick = clearChat;
        userInput.onkeydown = e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
        };
        userInput.oninput = () => {
            userInput.style.height = 'auto';
            userInput.style.height = Math.min(userInput.scrollHeight, 160) + 'px';
        };

        // Persist session memory on page/tab close
        window.addEventListener('beforeunload', () => {
            navigator.sendBeacon(`/chat/flush/${sessionId}`);
        });

        return {
            enable() { userInput.disabled = false; sendBtn.disabled = false; userInput.focus(); },
            addSkillOption(name) {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                if (attachSelect) attachSelect.appendChild(opt);
            }
        };
    })();


    // =========================================================================
    // MODULE B: LOG (Right panel — conversation log)
    // =========================================================================

    const logModule = (() => {
        const thoughtLog = document.getElementById('thoughtLog');
        const clearLogBtn = document.getElementById('clearLog');
        const scrollLockBtn = document.getElementById('scrollLock');
        let isLocked = false;

        const BADGE = {
            'SYS': 'badge-sys',
            'USER': 'badge-sys',
            'AI': 'badge-result',
            'INFO': 'badge-mem',
            'ERR': 'badge-err',
            'SKILL': 'badge-tool',
        };

        function addLog(label, msg, cls = 'system') {
            const entry = document.createElement('div');
            entry.className = `log-entry ${cls}`;
            const badge = BADGE[label] || 'badge-sys';
            const time = new Date().toLocaleTimeString('zh-TW', { hour12: false });
            entry.innerHTML = `
                <span class="log-badge ${badge}">${label}</span>
                <div class="log-body">
                    <span class="log-time">${time} </span>
                    <span class="log-msg">${escHtml(String(msg))}</span>
                </div>`;
            thoughtLog.appendChild(entry);
            if (!isLocked) thoughtLog.scrollTop = thoughtLog.scrollHeight;
        }

        function escHtml(s) {
            return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        clearLogBtn.onclick = () => { thoughtLog.innerHTML = ''; addLog('SYS', '日誌已清除'); };
        scrollLockBtn.onclick = () => {
            isLocked = !isLocked;
            scrollLockBtn.textContent = isLocked ? '🔒' : '🔓';
            scrollLockBtn.title = `捲動鎖定：${isLocked ? '開' : '關'}`;
        };

        return { addLog };
    })();


    // =========================================================================
    // MODULE C: SKILL MANAGEMENT (Left panel + Drawer)
    // =========================================================================

    const skillModule = (() => {
        const skillList = document.getElementById('skillList');
        const skillCount = document.getElementById('skillCount');
        const statusDot = document.getElementById('statusDot');
        const statusLabel = document.getElementById('statusLabel');
        const rescanBtn = document.getElementById('rescanBtn');

        // Drawer elements
        const drawer = document.getElementById('skillDrawer');
        const drawerOverlay = document.getElementById('drawerOverlay');
        const drawerClose = document.getElementById('drawerCloseBtn');
        const drawerViewBtn = document.getElementById('drawerViewBtn');
        const drawerEditBtn = document.getElementById('drawerEditBtn');
        const drawerReadView = document.getElementById('drawerReadView');
        const drawerEditView = document.getElementById('drawerEditView');
        const drawerTitle = document.getElementById('drawerSkillName');
        const drawerMeta = document.getElementById('drawerMeta');
        const drawerBody = document.getElementById('drawerBody');
        const drawerBadge = document.getElementById('drawerStatusBadge');
        const installBtn = document.getElementById('installDepsBtn');
        const skillEditor = document.getElementById('skillEditor');
        const saveBtn = document.getElementById('saveSkillBtn');
        const rollbackBtn = document.getElementById('rollbackBtn');
        const yamlError = document.getElementById('yamlError');
        const yamlErrorMsg = document.getElementById('yamlErrorMsg');

        // Create skill elements
        const createSkillBtn = document.getElementById('createSkillBtn');
        const createModal = document.getElementById('createSkillModal');
        const closeCreateModalBtn = document.getElementById('closeCreateModalBtn');
        const cancelCreateBtn = document.getElementById('cancelCreateBtn');
        const confirmCreateBtn = document.getElementById('confirmCreateBtn');
        const newSkillId = document.getElementById('newSkillId');
        const newSkillName = document.getElementById('newSkillName');
        const newSkillDesc = document.getElementById('newSkillDesc');
        const newSkillCatSelect = document.getElementById('newSkillCatSelect');
        const newSkillCat = document.getElementById('newSkillCat');
        const createError = document.getElementById('createSkillError');

        let currentSkill = null;
        let globalCategories = new Set();

        // ── Category map (Method 2) ─────────────────────────────────────────
        const CATEGORIES = [
            { label: '文件處理', skills: ['mcp-docx-processor', 'mcp-pdf-processor', 'mcp-pptx-processor', 'mcp-xlsx-processor'] },
            { label: '設計與視覺', skills: ['mcp-brand-guidelines', 'mcp-canvas-design', 'mcp-frontend-design', 'mcp-theme-factory', 'mcp-algorithmic-art'] },
            { label: '開發工具', skills: ['mcp-python-executor', 'mcp-webapp-tester', 'mcp-skill-builder', 'mcp-skill-factory', 'mcp-legacy-skill-creator'] },
            { label: '溝通協作', skills: ['mcp-internal-comms', 'mcp-doc-coauthoring', 'mcp-slack-gif-gen'] },
            { label: '系統技能', skills: ['mcp-my-first-tool', 'mcp-sample-converter', 'mcp-web-artifacts'] },
        ];

        // ── Load skills list ──────────────────────────────────────────────────
        async function loadSkills() {
            try {
                // Use /skills/list for rich data (includes description)
                const res = await fetch('/skills/list');
                const data = await res.json();

                // Extract categories dynamically from all skills
                globalCategories = new Set(CATEGORIES.map(c => c.label));
                Object.values(data.skills).forEach(s => {
                    if (s.category) globalCategories.add(s.category);
                });

                renderSkillList(data.skills, data.total);
                populateAttachSelect(data.skills);
                statusDot.className = 'dot dot-green pulse';
                statusLabel.textContent = `已連線 · ${data.total} 個技能`;
                skillCount.textContent = data.total;
                logModule.addLog('SYS', `技能庫掃描完成：${data.total} 個技能`);
            } catch (e) {
                statusDot.className = 'dot dot-orange';
                statusLabel.textContent = '後端連線失敗';
                logModule.addLog('ERR', '無法連線後端，請確認伺服器是否運行', 'error');
            }
            chatModule.enable();
        }

        function renderSkillList(skills, total) {
            skillList.innerHTML = '';

            // Collect uncategorised skills as fallback
            const categorisedNames = new Set();
            const allCats = CATEGORIES.map(c => ({ label: c.label, skills: [...c.skills] }));

            // Add dynamically found categories that aren't in the hardcoded LIST
            let dynamicCats = new Map();
            Object.entries(skills).forEach(([name, s]) => {
                if (s.category) {
                    // Check if this skill is already mapped in hardcoded CATEGORIES
                    let found = CATEGORIES.find(c => c.skills.includes(name));
                    if (!found) {
                        if (!dynamicCats.has(s.category)) dynamicCats.set(s.category, []);
                        dynamicCats.get(s.category).push(name);
                        categorisedNames.add(name);
                    } else {
                        categorisedNames.add(name);
                    }
                } else {
                    // Pre-mapped in CATEGORIES but maybe no category field?
                    let found = CATEGORIES.find(c => c.skills.includes(name));
                    if (found) categorisedNames.add(name);
                }
            });

            // Merge dynamic cats into allCats
            dynamicCats.forEach((skillNames, catLabel) => {
                allCats.push({ label: catLabel, skills: skillNames });
            });

            const others = Object.keys(skills).filter(n => !categorisedNames.has(n));
            if (others.length) allCats.push({ label: '其他', skills: others });

            allCats.forEach(cat => {
                const inCat = cat.skills.filter(n => skills[n]);
                if (!inCat.length) return;

                // Category header
                const header = document.createElement('li');
                header.className = 'skill-category-header';
                header.textContent = cat.label;
                skillList.appendChild(header);

                inCat.forEach(name => {
                    const s = skills[name];
                    const li = document.createElement('li');
                    li.className = 'skill-item';

                    // Method 4: tooltip = full description
                    const desc = s.description || '';
                    const statusTip = s.ready ? '✅ 就緒' : `⚠ 降級（缺：${(s.missing_deps || []).join(', ')}）`;
                    li.title = `${statusTip}\n${desc}`;

                    li.innerHTML = `
                        <span class="skill-dot ${s.ready ? 'ready' : 'degraded'}"></span>
                        <span class="skill-name">${name.replace('mcp-', '')}</span>
                        <span class="skill-version">v${s.version}</span>`;
                    li.onclick = () => openDrawer(name);
                    skillList.appendChild(li);
                });
            });
        }

        function populateAttachSelect(skills) {
            // Clear old options except the first (none)
            const sel = document.getElementById('attachSkillSelect');
            if (sel) { while (sel.options.length > 1) sel.remove(1); }
            Object.keys(skills).sort().forEach(name => {
                chatModule.addSkillOption(name);
            });
        }

        // ── Drawer ────────────────────────────────────────────────────────────
        async function openDrawer(skillName) {
            currentSkill = skillName;
            drawerTitle.textContent = skillName;
            drawerMeta.innerHTML = '<p style="color:var(--text-muted);font-size:12px">載入中...</p>';
            drawerBody.innerHTML = '';
            yamlError.classList.add('hidden');
            showView(); // Default to read mode

            drawer.classList.remove('hidden');
            drawerOverlay.classList.remove('hidden');
            logModule.addLog('SKILL', `開啟技能詳情：${skillName}`);

            try {
                const res = await fetch(`/skills/${skillName}`);
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                renderMeta(data);
                renderBody(data.raw_content);

                if (data.has_backup) {
                    rollbackBtn.classList.remove('hidden');
                    rollbackBtn.title = `還原至備份 (${data.backup_modified})`;
                } else {
                    rollbackBtn.classList.add('hidden');
                }

                skillEditor.value = data.raw_content;

            } catch (e) {
                drawerMeta.innerHTML = `<p style="color:var(--red)">載入失敗：${e.message}</p>`;
            }
        }

        function renderMeta(data) {
            const m = data.metadata || {};
            const ready = m._env_ready !== false; // if undefined assume ready
            drawerBadge.textContent = ready ? '✅ 就緒' : '⚠ 降級';
            drawerBadge.className = `drawer-status-badge ${ready ? 'ready' : 'degraded'}`;

            if (!ready) {
                installBtn.classList.remove('hidden');
                installBtn.setAttribute('data-skill', data.skill_name);
            } else {
                installBtn.classList.add('hidden');
            }

            const rows = [
                ['版本', m.version || m.Version || 'unknown'],
                ['狀態', ready ? '就緒' : `降級 (缺：${(data.metadata._missing_deps || []).join(', ')})`],
                ['備份', data.has_backup ? `有 (${data.backup_modified})` : '無'],
                ['描述', (m.description || '').slice(0, 80) + ((m.description || '').length > 80 ? '…' : '')],
            ];
            drawerMeta.innerHTML = rows.map(([k, v]) => `
                <div class="drawer-meta-row">
                    <span class="meta-key">${k}</span>
                    <span class="meta-val ${k === '狀態' ? (ready ? 'ready' : 'degraded') : ''}">${v}</span>
                </div>`).join('');
        }

        function renderBody(rawContent) {
            // Strip YAML frontmatter, render the markdown body
            const parts = rawContent.split('---');
            const body = parts.length >= 3 ? parts.slice(2).join('---').trim() : rawContent;
            drawerBody.innerHTML = marked.parse(body);
        }

        function closeDrawer() {
            drawer.classList.add('hidden');
            drawerOverlay.classList.add('hidden');
            currentSkill = null;
        }

        function showView() {
            drawerReadView.classList.remove('hidden');
            drawerEditView.classList.add('hidden');
            drawerViewBtn.classList.add('active');
            drawerEditBtn.classList.remove('active');
        }

        function showEdit() {
            drawerReadView.classList.add('hidden');
            drawerEditView.classList.remove('hidden');
            drawerEditBtn.classList.add('active');
            drawerViewBtn.classList.remove('active');
            yamlError.classList.add('hidden');
        }

        // ── Save SKILL.md ─────────────────────────────────────────────────────
        async function saveSkill() {
            if (!currentSkill) return;
            const content = skillEditor.value.trim();
            yamlError.classList.add('hidden');

            try {
                const res = await fetch(`/skills/${currentSkill}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ yaml_content: content })
                });
                const data = await res.json();
                if (!res.ok) {
                    yamlErrorMsg.textContent = data.detail || '儲存失敗';
                    yamlError.classList.remove('hidden');
                    logModule.addLog('ERR', `儲存失敗：${data.detail}`, 'error');
                } else {
                    logModule.addLog('SKILL', `技能「${currentSkill}」已更新並備份`);
                    rollbackBtn.classList.remove('hidden');
                    showView();
                    // Refresh read view
                    openDrawer(currentSkill);
                }
            } catch (e) {
                yamlErrorMsg.textContent = e.message;
                yamlError.classList.remove('hidden');
            }
        }

        // ── Rollback ──────────────────────────────────────────────────────────
        async function rollbackSkill() {
            if (!currentSkill) return;
            if (!confirm(`確定要將「${currentSkill}」回退至上次備份版本？`)) return;

            try {
                const res = await fetch(`/skills/${currentSkill}/rollback`, { method: 'POST' });
                const data = await res.json();
                if (!res.ok) {
                    logModule.addLog('ERR', `回退失敗：${data.detail}`, 'error');
                } else {
                    logModule.addLog('SKILL', `技能「${currentSkill}」已回退至備份`);
                    openDrawer(currentSkill);
                }
            } catch (e) {
                logModule.addLog('ERR', e.message, 'error');
            }
        }

        // ── Install deps ──────────────────────────────────────────────────────
        async function installDeps(skillName) {
            installBtn.disabled = true;
            installBtn.textContent = '⬇ 安裝中...';
            logModule.addLog('SKILL', `開始安裝「${skillName}」的缺失依賴`);

            try {
                const res = await fetch(`/skills/${skillName}/install`, { method: 'POST' });
                const data = await res.json();
                const ok = data.results?.filter(r => r.status === 'installed') || [];
                const fail = data.results?.filter(r => r.status !== 'installed') || [];
                logModule.addLog('SKILL', `安裝完成：${ok.length} 成功，${fail.length} 失敗`);
                if (fail.length) logModule.addLog('ERR', fail.map(f => f.package).join(', '), 'error');
                await rescan();
            } catch (e) {
                logModule.addLog('ERR', e.message, 'error');
            } finally {
                installBtn.disabled = false;
                installBtn.textContent = '⬇ 安裝缺失依賴';
            }
        }

        // ── Create Skill ──────────────────────────────────────────────────────
        function populateCategoryDropdown() {
            newSkillCatSelect.innerHTML = '';

            // Default empty option
            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            defaultOpt.textContent = '請選擇...';
            newSkillCatSelect.appendChild(defaultOpt);

            // Populate from globalCategories
            Array.from(globalCategories).sort().forEach(cat => {
                const opt = document.createElement('option');
                opt.value = cat;
                opt.textContent = cat;
                newSkillCatSelect.appendChild(opt);
            });

            // Add new category option
            const newOpt = document.createElement('option');
            newOpt.value = '__new__';
            newOpt.textContent = '➕ 新增分類...';
            newSkillCatSelect.appendChild(newOpt);
        }

        newSkillCatSelect.onchange = () => {
            if (newSkillCatSelect.value === '__new__') {
                newSkillCat.classList.remove('hidden');
                newSkillCat.focus();
            } else {
                newSkillCat.classList.add('hidden');
            }
        };

        function openCreateModal() {
            newSkillId.value = '';
            newSkillName.value = '';
            newSkillDesc.value = '';
            newSkillCat.value = '';
            newSkillCat.classList.add('hidden');
            populateCategoryDropdown();
            newSkillCatSelect.value = '';
            createError.classList.add('hidden');
            createModal.classList.remove('hidden');
        }

        function closeCreateModal() {
            createModal.classList.add('hidden');
        }

        async function submitCreateSkill() {
            const id = newSkillId.value.trim();
            const name = newSkillName.value.trim();
            const desc = newSkillDesc.value.trim();
            let cat = newSkillCatSelect.value;

            if (cat === '__new__') {
                cat = newSkillCat.value.trim();
            }
            if (!cat) cat = '📦 其他';

            if (!id || !name || !desc) {
                createError.textContent = '識別碼、顯示名稱與描述為必填。';
                createError.classList.remove('hidden');
                return;
            }

            confirmCreateBtn.disabled = true;
            confirmCreateBtn.textContent = '建立中...';
            createError.classList.add('hidden');

            try {
                const res = await fetch('/skills/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: id,
                        display_name: name,
                        description: desc,
                        category: cat
                    })
                });

                const data = await res.json();
                if (!res.ok) {
                    throw new Error(data.detail || '建立失敗');
                }

                logModule.addLog('SYS', `成功新建技能: ${data.skill_name}`);
                closeCreateModal();
                await rescan(); // Refresh list to show new skill
                // Automatically open the new skill's drawer
                setTimeout(() => openDrawer(data.skill_name), 300);

            } catch (e) {
                createError.textContent = e.message;
                createError.classList.remove('hidden');
                logModule.addLog('ERR', `建立技能失敗: ${e.message}`, 'error');
            } finally {
                confirmCreateBtn.disabled = false;
                confirmCreateBtn.textContent = '建立';
            }
        }

        // ── Rescan ────────────────────────────────────────────────────────────
        async function rescan() {
            rescanBtn.textContent = '…';
            try {
                await fetch('/skills/rescan', { method: 'POST' });
                await loadSkills();
            } catch (e) {
                logModule.addLog('ERR', '重新掃描失敗', 'error');
            } finally {
                rescanBtn.textContent = '↺';
            }
        }

        // ── Event wiring ──────────────────────────────────────────────────────
        drawerClose.onclick = closeDrawer;
        drawerOverlay.onclick = closeDrawer;
        drawerViewBtn.onclick = showView;
        drawerEditBtn.onclick = showEdit;
        saveBtn.onclick = saveSkill;
        rollbackBtn.onclick = rollbackSkill;
        rescanBtn.onclick = rescan;
        installBtn.onclick = () => installDeps(installBtn.getAttribute('data-skill'));

        // Create modal wiring
        createSkillBtn.onclick = openCreateModal;
        closeCreateModalBtn.onclick = closeCreateModal;
        cancelCreateBtn.onclick = closeCreateModal;
        confirmCreateBtn.onclick = submitCreateSkill;

        return { loadSkills };
    })();


    // =========================================================================
    // MODULE D: SOURCE INTEGRATION (Add Source Modal)
    // =========================================================================
    const sourceModule = (() => {
        const modalOverlay = document.getElementById('addSourceModalOverlay');
        const openBtn = document.getElementById('openAddSourceBtn');
        const closeBtn = document.getElementById('closeAddSourceBtn');
        const dropZone = document.getElementById('sourceDropZone');
        const fileInput = document.getElementById('sourceFileInput');

        // Action buttons
        const uploadBtn = document.getElementById('uploadFileBtnAction');
        const urlBtn = document.getElementById('addUrlBtnAction');
        const textBtn = document.getElementById('copyPasteBtnAction');
        const searchInput = document.getElementById('webSearchInput');
        const searchGo = document.getElementById('webSearchGoBtn');

        // Sub-modals
        const urlOverlay = document.getElementById('addUrlModalOverlay');
        const confirmUrl = document.getElementById('confirmAddUrlBtn');
        const closeUrl = document.getElementById('closeAddUrlBtn');
        const urlInput = document.getElementById('sourceUrlInput');

        const textOverlay = document.getElementById('addTextModalOverlay');
        const confirmText = document.getElementById('confirmAddTextBtn');
        const closeText = document.getElementById('closeAddTextBtn');
        const textNameInput = document.getElementById('sourceTextName');
        const textContentInput = document.getElementById('sourceTextContent');

        function openModal() {
            modalOverlay.classList.add('active');
            // Clear search field each time modal opens
            if (searchInput) { searchInput.value = ''; searchInput.style.height = 'auto'; }
            // Sync document count when modal opens
            if (window.docModule) window.docModule.loadDocuments();
        }
        function closeModal() { modalOverlay.classList.remove('active'); }

        // --- URL sourcing ---
        async function submitUrl() {
            const url = urlInput.value.trim();
            if (!url) return;
            confirmUrl.disabled = true;
            confirmUrl.textContent = '擷取中...';
            try {
                const res = await fetch('/api/documents/url', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '擷取失敗');
                logModule.addLog('SYS', `成功擷取網頁來源: ${data.title}`);
                urlOverlay.classList.remove('active');
                urlInput.value = '';
                docModule.loadDocuments();
            } catch (e) {
                alert(e.message);
            } finally {
                confirmUrl.disabled = false;
                confirmUrl.textContent = '插入';
            }
        }

        // --- Text sourcing ---
        async function submitText() {
            const name = textNameInput.value.trim();
            const content = textContentInput.value.trim();
            if (!content) return;
            confirmText.disabled = true;
            confirmText.textContent = '儲存中...';
            try {
                const res = await fetch('/api/documents/text', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, content })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '儲存失敗');
                logModule.addLog('SYS', `成功新增文字來源: ${name}`);
                textOverlay.classList.remove('active');
                textNameInput.value = '';
                textContentInput.value = '';
                docModule.loadDocuments();
            } catch (e) {
                alert(e.message);
            } finally {
                confirmText.disabled = false;
                confirmText.textContent = '插入';
            }
        }

        // --- File Upload Logic (Reuse existing but in modal context) ---
        function handleFiles(files) {
            Array.from(files).forEach(file => {
                const formData = new FormData();
                formData.append('file', file);
                logModule.addLog('SYS', `準備上傳檔案: ${file.name}`);

                fetch('/api/documents/upload', {
                    method: 'POST',
                    body: formData
                }).then(res => res.json()).then(data => {
                    logModule.addLog('SYS', `檔案上傳完成: ${file.name}`);
                    docModule.loadDocuments();
                }).catch(e => {
                    logModule.addLog('ERR', `上傳失敗: ${file.name}`, 'error');
                });
            });
        }

        // Wiring
        if (openBtn) openBtn.onclick = openModal;
        if (closeBtn) closeBtn.onclick = closeModal;

        if (uploadBtn) uploadBtn.onclick = () => fileInput.click();
        if (fileInput) fileInput.onchange = () => handleFiles(fileInput.files);

        if (urlBtn) urlBtn.onclick = () => urlOverlay.classList.add('active');
        if (closeUrl) closeUrl.onclick = () => urlOverlay.classList.remove('active');
        const closeUrl2 = document.getElementById('closeAddUrlBtn2');
        if (closeUrl2) closeUrl2.onclick = () => urlOverlay.classList.remove('active');
        if (confirmUrl) confirmUrl.onclick = submitUrl;

        if (textBtn) textBtn.onclick = () => textOverlay.classList.add('active');
        if (closeText) closeText.onclick = () => textOverlay.classList.remove('active');
        const closeText2 = document.getElementById('closeAddTextBtn2');
        if (closeText2) closeText2.onclick = () => textOverlay.classList.remove('active');
        if (confirmText) confirmText.onclick = submitText;

        const googleBtn = document.getElementById('googleSearchBtn');
        function triggerSearch() {
            const q = searchInput.value.trim();
            if (q) {
                researchModule.startResearch(q);
            } else {
                alert('請輸入搜尋內容');
            }
        }
        if (googleBtn) googleBtn.onclick = triggerSearch;
        if (searchInput) {
            searchInput.addEventListener('keydown', e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    triggerSearch();
                }
            });
        }

        // Drag & Drop
        if (dropZone) {
            ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(evt => {
                dropZone.addEventListener(evt, e => {
                    e.preventDefault();
                    e.stopPropagation();
                });
            });
            dropZone.addEventListener('dragover', () => dropZone.classList.add('dragover'));
            dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
            dropZone.addEventListener('drop', e => {
                dropZone.classList.remove('dragover');
                handleFiles(e.dataTransfer.files);
            });
        }

        // Search logic (Dummy trigger)
        if (searchGo) {
            searchGo.onclick = () => {
                const q = searchInput.value.trim();
                if (q) {
                    logModule.addLog('SYS', `觸發網路搜尋來源: ${q}`);
                    // Trigger a system message in chat
                    chatModule.enable(); // Ensure chat is ready
                    document.getElementById('userInput').value = `幫我搜尋關於「${q}」的資訊並整理成參考來源。`;
                    closeModal();
                    // Optionally trigger the send button programmatically if desired
                }
            };
        }

        return {
            updateProgress: (count) => {
                const fill = document.getElementById('sourceLimitFill');
                const text = document.getElementById('sourceLimitText');
                if (fill && text) {
                    const pct = Math.min((count / 50) * 100, 100);
                    fill.style.width = pct + '%';
                    text.textContent = `${count} / 50`;
                    if (count >= 50) fill.style.background = 'var(--red)';
                    else fill.style.background = 'var(--cis-blue)';
                }
            }
        };
    })();
    window.sourceModule = sourceModule;


    // =========================================================================
    // MODULE C: DOCUMENTS (File Management)
    // =========================================================================
    const docModule = (() => {
        const docList = document.getElementById('docList');
        const docCount = document.getElementById('docCount');
        const unselectedFiles = new Set();
        let currentLoadedFiles = [];

        const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

        async function loadDocuments() {
            try {
                const res = await fetch('/api/documents/list');
                const data = await res.json();
                renderDocList(data.files, data.total);
                if (window.sourceModule) sourceModule.updateProgress(data.total);
            } catch (e) {
                console.error('Failed to load documents:', e);
            }
        }

        function renderDocList(files, total) {
            currentLoadedFiles = files;
            docCount.textContent = total;
            const sourceHint = document.getElementById('sourceCountHint');
            if (sourceHint) {
                if (total > 0) {
                    sourceHint.textContent = `${total} 個來源`;
                    sourceHint.classList.remove('hidden');
                } else {
                    sourceHint.classList.add('hidden');
                }
            }
            docList.innerHTML = '';

            if (total === 0) {
                docList.innerHTML = '<li class="skill-item-placeholder" style="color: #666; font-size: 0.9rem; padding: 12px;">無上傳文件</li>';
                return;
            }

            files.forEach(f => {
                const li = document.createElement('li');
                li.className = 'skill-item doc-item';

                const sizeKB = (f.size / 1024).toFixed(1);
                // Use original_name for display; fall back to hashed filename if not available
                const displayName = f.original_name || f.filename;

                const isChecked = !unselectedFiles.has(f.filename);
                li.innerHTML = `
                    <button class="doc-menu-btn" title="選項" data-filename="${escapeHtml(f.filename)}">&#8942;</button>
                    <div class="doc-item-info">
                        <span class="doc-filename" title="${escapeHtml(displayName)}">${escapeHtml(displayName)}</span>
                        <input type="text" class="doc-rename-input hidden" spellcheck="false">
                        <span class="doc-item-size">${sizeKB} KB</span>
                    </div>
                    <label class="doc-checkbox-wrap" title="選取">
                        <input type="checkbox" class="doc-checkbox" ${isChecked ? 'checked' : ''}>
                        <span class="doc-checkmark"></span>
                    </label>
                `;

                li.dataset.filename = f.filename;

                const menuBtn = li.querySelector('.doc-menu-btn');
                menuBtn.addEventListener('click', e => {
                    e.stopPropagation();
                    openDocMenu(menuBtn, f.filename, displayName, li);
                });

                const checkbox = li.querySelector('.doc-checkbox');
                checkbox.addEventListener('change', e => {
                    if (e.target.checked) unselectedFiles.delete(f.filename);
                    else unselectedFiles.add(f.filename);
                });

                docList.appendChild(li);
            });
        }

        // ── Global floating doc menu (position:fixed, body-level) ─────────────
        let _docMenuTarget = null;
        let _docMenuDisplayName = "";
        let _docMenuTargetLi = null;
        const _docMenu = (() => {
            const el = document.createElement('div');
            el.id = 'docFloatMenu';
            el.className = 'doc-float-menu';
            el.innerHTML = `
                <button class="doc-menu-item" id="docFloatDelete">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                    刪除來源
                </button>
                <button class="doc-menu-item" id="docFloatRename">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    重新命名
                </button>
            `;
            document.body.appendChild(el);

            el.querySelector('#docFloatDelete').addEventListener('click', e => {
                e.stopPropagation();
                const fn = _docMenuTarget;
                const dn = _docMenuDisplayName;
                closeDocMenu();
                if (fn) {
                    deleteDocument(fn, dn);
                }
            });
            el.querySelector('#docFloatRename').addEventListener('click', e => {
                e.stopPropagation();
                const fn = _docMenuTarget;
                const dn = _docMenuDisplayName;
                const li = _docMenuTargetLi;
                closeDocMenu();
                if (fn && li) renameDocument(fn, dn, li);
            });
            return el;
        })();

        function openDocMenu(triggerBtn, filename, displayName, li) {
            const isOpen = _docMenu.classList.contains('open') && _docMenuTarget === filename;
            closeDocMenu();
            if (isOpen) return;

            _docMenuTarget = filename;
            _docMenuDisplayName = displayName;
            _docMenuTargetLi = li;
            const rect = triggerBtn.getBoundingClientRect();
            _docMenu.style.top = (rect.bottom + 4) + 'px';
            _docMenu.style.left = rect.left + 'px';
            _docMenu.classList.add('open');
        }

        function closeDocMenu() {
            _docMenu.classList.remove('open');
            _docMenuTarget = null;
            _docMenuDisplayName = "";
            _docMenuTargetLi = null;
        }

        function closeAllDocMenus() { closeDocMenu(); }
        document.addEventListener('click', closeDocMenu);


        const _deleteConfirmModal = (() => {
            const el = document.createElement('div');
            el.id = 'deleteConfirmModalDynamic';
            el.className = 'modal-overlay hidden';
            el.style.zIndex = '10000';
            el.innerHTML = `
                <div class="modal-card">
                    <div class="modal-card-header">
                        <div>
                            <h2 class="modal-title">確定刪除</h2>
                            <p class="modal-subtitle">這個動作無法復原</p>
                        </div>
                    </div>
                    <div class="modal-body">
                        <p id="deleteConfirmTextDynamic" style="font-size: 14px; margin-bottom: 8px;"></p>
                    </div>
                    <div class="modal-footer">
                        <button class="btn-secondary" id="cancelDeleteBtnDynamic">取消</button>
                        <button class="btn-primary" id="confirmDeleteBtnDynamic" style="background: var(--red); color: white; border: none; padding: 6px 16px; border-radius: 6px; font-weight: 600; cursor: pointer;">確定刪除</button>
                    </div>
                </div>
            `;
            document.body.appendChild(el);
            return el;
        })();

        function awaitDeleteConfirm(displayName) {
            return new Promise(resolve => {
                const textEl = _deleteConfirmModal.querySelector('#deleteConfirmTextDynamic');
                const cancelBtn = _deleteConfirmModal.querySelector('#cancelDeleteBtnDynamic');
                const confirmBtn = _deleteConfirmModal.querySelector('#confirmDeleteBtnDynamic');

                textEl.innerHTML = `確定要刪除文件 '${escapeHtml(displayName)}' 嗎？<br>此動作會將它從知識庫中永久移除。`;

                _deleteConfirmModal.style.display = 'flex';
                _deleteConfirmModal.style.opacity = '1';
                _deleteConfirmModal.style.pointerEvents = 'auto';
                _deleteConfirmModal.style.visibility = 'visible';
                _deleteConfirmModal.classList.remove('hidden');

                const cleanup = () => {
                    cancelBtn.removeEventListener('click', onCancel);
                    confirmBtn.removeEventListener('click', onConfirm);
                    _deleteConfirmModal.classList.add('hidden');
                    _deleteConfirmModal.style.opacity = '0';
                    _deleteConfirmModal.style.pointerEvents = 'none';
                    _deleteConfirmModal.style.display = 'none';
                };

                const onCancel = () => { cleanup(); resolve(false); };
                const onConfirm = () => { cleanup(); resolve(true); };

                cancelBtn.addEventListener('click', onCancel);
                confirmBtn.addEventListener('click', onConfirm);
            });
        }

        async function deleteDocument(filename, displayName) {
            try {
                const confirmed = await awaitDeleteConfirm(displayName);
                if (!confirmed) return;

                const res = await fetch(`/api/documents/${encodeURIComponent(filename)}`, { method: 'DELETE' });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || data.message || '刪除失敗');
                logModule.addLog('SYS', `已刪除文件: ${filename}`);
                loadDocuments();
            } catch (e) {
                alert(e.message);
            }
        }

        async function renameDocument(filename, displayName, li) {
            const span = li.querySelector('.doc-filename');
            let input = li.querySelector('.doc-rename-input');

            // Extract base name without extension
            let baseName = displayName;
            const extMatch = displayName.match(/\.[^.]+$/);
            const ext = extMatch ? extMatch[0] : '';
            if (ext) {
                baseName = displayName.slice(0, -ext.length);
            }

            // Remove previous listeners if any, by cloning the input
            const newInput = input.cloneNode(true);
            input.parentNode.replaceChild(newInput, input);
            input = newInput;

            span.classList.add('hidden');
            input.classList.remove('hidden');
            input.value = baseName;

            async function commitRename() {
                input.blur(); // prevent double trigger
                span.classList.remove('hidden');
                input.classList.add('hidden');

                const newVal = input.value.trim();
                if (!newVal || newVal === baseName) return;

                const newName = newVal + ext;

                try {
                    const res = await fetch(`/api/documents/${encodeURIComponent(filename)}/rename`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ new_name: newName })
                    });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.detail || data.message || '重新命名失敗');

                    logModule.addLog('SYS', `文件已重新命名: ${displayName} → ${newName}`);
                    loadDocuments();
                } catch (e) {
                    alert(e.message);
                }
            }

            const handleKeydown = (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    commitRename();
                } else if (e.key === 'Escape') {
                    input.blur();
                    span.classList.remove('hidden');
                    input.classList.add('hidden');
                }
            };

            input.addEventListener('keydown', handleKeydown);
            input.addEventListener('blur', commitRename, { once: true });

            // Focus and put cursor at end
            input.focus();
            input.setSelectionRange(input.value.length, input.value.length);
        }

        function getSelectedDocs() {
            return currentLoadedFiles
                .filter(f => !unselectedFiles.has(f.filename))
                .map(f => f.filename);
        }

        return { loadDocuments, deleteDocument, renameDocument, getSelectedDocs };
    })();
    window.docModule = docModule;


    // =========================================================================
    // RESEARCH MODULE (Automated Sourcing)
    // =========================================================================
    const researchModule = (() => {
        const researchOverlay = document.getElementById('researchingModalOverlay');
        const selectionOverlay = document.getElementById('sourceSelectionModalOverlay');
        const sourceList = document.getElementById('sourceListContainer');
        const selectAll = document.getElementById('selectAllSources');
        const selectedCountText = document.getElementById('selectedCountText');
        const confirmBtn = document.getElementById('confirmImportBtn');
        const closeSelection = document.getElementById('closeSelectionBtn');
        const addSourceOverlay = document.getElementById('addSourceModalOverlay');

        let currentSources = [];

        function startResearch(query) {
            addSourceOverlay.classList.remove('active');
            researchOverlay.classList.add('active');
            logModule.addLog('SYS', `開始研究新來源: ${query}`);

            fetch('/api/research', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query })
            })
                .then(res => res.json())
                .then(data => {
                    if (data.status === 'success') {
                        showSelection(data.sources);
                    } else {
                        throw new Error(data.detail || data.message || '研究失敗');
                    }
                })
                .catch(e => {
                    researchOverlay.classList.remove('active');
                    alert('研究過程發生錯誤: ' + e.message);
                    addSourceOverlay.classList.add('active');
                });
        }

        function showSelection(sources) {
            currentSources = sources;
            researchOverlay.classList.remove('active');
            selectionOverlay.classList.add('active');
            renderSources();
            updateStats();
        }

        function renderSources() {
            sourceList.innerHTML = '';
            currentSources.forEach((s, idx) => {
                const item = document.createElement('div');
                item.className = 'source-selection-item';
                item.innerHTML = `
                    <label class="checkbox-container">
                        <input type="checkbox" class="source-chk" data-idx="${idx}" checked>
                        <span class="checkmark"></span>
                    </label>
                    <div class="source-item-meta">
                        <div class="source-item-top">
                            ${s.favicon ? `<img src="${s.favicon}" class="source-item-favicon" onerror="this.style.display='none'">` : ''}
                            <a href="${s.url}" target="_blank" class="source-item-title">${escapeHtml(s.title)}</a>
                        </div>
                        <div class="source-item-snippet">${escapeHtml(s.snippet)}</div>
                    </div>
                `;
                sourceList.appendChild(item);
            });

            // Re-wire individual checkboxes
            document.querySelectorAll('.source-chk').forEach(chk => {
                chk.onchange = updateStats;
            });
        }

        function updateStats() {
            const checked = document.querySelectorAll('.source-chk:checked');
            selectedCountText.textContent = checked.length;
            if (checked.length > 0) {
                confirmBtn.classList.add('active');
                confirmBtn.disabled = false;
            } else {
                confirmBtn.classList.remove('active');
                confirmBtn.disabled = true;
            }
        }

        async function importSelected() {
            const checked = Array.from(document.querySelectorAll('.source-chk:checked'));
            if (checked.length === 0) return;

            const toImport = checked.map(chk => currentSources[parseInt(chk.dataset.idx)]);
            selectionOverlay.classList.remove('active');
            logModule.addLog('SYS', `準備匯入 ${toImport.length} 個來源...`);

            let successCount = 0;
            for (const s of toImport) {
                try {
                    const res = await fetch('/api/documents/url', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: s.url })
                    });
                    if (res.ok) successCount++;
                } catch (e) {
                    console.error('Import failed for:', s.url, e);
                }
            }

            logModule.addLog('SYS', `匯入完成: 成功 ${successCount}/${toImport.length}`);
            window.docModule.loadDocuments();
        }

        // Wiring
        if (selectAll) {
            selectAll.onchange = () => {
                document.querySelectorAll('.source-chk').forEach(chk => {
                    chk.checked = selectAll.checked;
                });
                updateStats();
            };
        }

        if (confirmBtn) confirmBtn.onclick = importSelected;
        if (closeSelection) closeSelection.onclick = () => selectionOverlay.classList.remove('active');

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        return { startResearch };
    })();
    window.researchModule = researchModule;


    // =========================================================================
    // INIT
    // =========================================================================
    // --- UI Logic for Search TextArea (Auto-size) ---
    const searchArea = document.getElementById('webSearchInput');
    if (searchArea) {
        searchArea.addEventListener('input', () => {
            searchArea.style.height = 'auto';
            searchArea.style.height = (searchArea.scrollHeight) + 'px';
        });
    }

    // --- Dynamic Highlight Rotation ---
    const highlightTexts = document.querySelectorAll('#sourceHighlight .highlight-text');
    let highlightIndex = 0;
    if (highlightTexts.length > 0) {
        setInterval(() => {
            highlightTexts[highlightIndex].classList.remove('active');
            highlightIndex = (highlightIndex + 1) % highlightTexts.length;
            highlightTexts[highlightIndex].classList.add('active');
        }, 3000);
    }

    skillModule.loadSkills();
    docModule.loadDocuments();

}); // end DOMContentLoaded

// Accordion Logic
['skill', 'doc'].forEach(prefix => {
    const btn = document.getElementById(prefix + 'AccordionBtn');
    const content = document.getElementById(prefix + 'AccordionContent');
    if (btn && content) {
        btn.addEventListener('click', () => {
            btn.classList.toggle('active');
            content.classList.toggle('active');
        });
    }
});
