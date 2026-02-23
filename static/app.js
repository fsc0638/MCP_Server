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

        const sessionId = 'web-' + Math.random().toString(36).slice(2, 8);

        function appendMessage(role, text) {
            if (welcomeBlock) welcomeBlock.style.display = 'none';
            const div = document.createElement('div');
            div.className = `message ${role}`;
            if (role === 'assistant') {
                div.innerHTML = marked.parse(text);
            } else {
                div.textContent = text;
            }
            msgContainer.appendChild(div);
            chatViewport.scrollTop = chatViewport.scrollHeight;
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
            if (!text) return;

            userInput.value = '';
            userInput.style.height = 'auto';
            appendMessage('user', text);
            logModule.addLog('USER', `發送：${text}`);

            const model = modelSelector.value;
            const attachedSkill = attachSelect.value || null;

            userInput.disabled = true;
            sendBtn.disabled = true;
            showTypingIndicator();

            try {
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        user_input: text,
                        session_id: sessionId,
                        model: model,
                        injected_skill: attachedSkill
                    })
                });

                removeTypingIndicator();

                if (!res.ok) {
                    const err = await res.json().catch(() => ({ message: `HTTP ${res.status}` }));
                    throw new Error(err.message || `HTTP ${res.status}`);
                }

                const data = await res.json();

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
            msgContainer.innerHTML = '';
            if (welcomeBlock) welcomeBlock.style.display = '';
            logModule.addLog('SYS', '對話已清除，記憶已儲存至 MEMORY.md');
        }

        // Attach Skill select → show hint
        attachSelect.onchange = () => {
            const v = attachSelect.value;
            attachHint.textContent = v ? `下一輪對話將包含「${v}」的技能描述（僅參考，不執行）` : '';
        };
        clearAttach.onclick = () => {
            attachSelect.value = '';
            attachHint.textContent = '';
        };

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

        return {
            enable() { userInput.disabled = false; sendBtn.disabled = false; userInput.focus(); },
            addSkillOption(name) {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                attachSelect.appendChild(opt);
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
            { label: '📄 文件處理', skills: ['mcp-docx-processor', 'mcp-pdf-processor', 'mcp-pptx-processor', 'mcp-xlsx-processor'] },
            { label: '🎨 設計與視覺', skills: ['mcp-brand-guidelines', 'mcp-canvas-design', 'mcp-frontend-design', 'mcp-theme-factory', 'mcp-algorithmic-art'] },
            { label: '🤖 開發工具', skills: ['mcp-python-executor', 'mcp-webapp-tester', 'mcp-skill-builder', 'mcp-skill-factory', 'mcp-legacy-skill-creator'] },
            { label: '💬 溝通協作', skills: ['mcp-internal-comms', 'mcp-doc-coauthoring', 'mcp-slack-gif-gen'] },
            { label: '🔧 系統技能', skills: ['mcp-my-first-tool', 'mcp-sample-converter', 'mcp-web-artifacts'] },
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
            if (others.length) allCats.push({ label: '📦 其他', skills: others });

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
            while (sel.options.length > 1) sel.remove(1);
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
    // INIT
    // =========================================================================
    skillModule.loadSkills();

}); // end DOMContentLoaded
