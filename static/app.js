document.addEventListener('DOMContentLoaded', () => {
    // Elements
    const messageContainer = document.getElementById('messageContainer');
    const thoughtLog = document.getElementById('thoughtLog');
    const userInput = document.getElementById('userInput');
    const sendBtn = document.getElementById('sendBtn');
    const modelSelector = document.getElementById('modelSelector');
    const skillList = document.getElementById('skillList');
    const skillCount = document.getElementById('skillCount');
    const memorySummary = document.getElementById('memorySummary');
    const clearLogBtn = document.getElementById('clearLog');
    const scrollLockBtn = document.getElementById('scrollLock');
    const authModal = document.getElementById('authModal');
    const riskDesc = document.getElementById('riskDesc');
    const authDetails = document.getElementById('authDetails');
    const approveBtn = document.getElementById('approveBtn');
    const denyBtn = document.getElementById('denyBtn');

    let isScrollLocked = false;
    let eventSource = null;

    // --- Initialization ---
    async function init() {
        try {
            const res = await fetch('/health');
            const data = await res.json();
            if (data.status === 'healthy') {
                skillCount.textContent = data.total_skills;
                renderSkills(data.skills);
            }
        } catch (e) {
            addLog('System', 'Failed to connect to backend', 'error');
        }
        userInput.disabled = false;
        userInput.focus();
    }

    function renderSkills(skills) {
        skillList.innerHTML = '';
        Object.keys(skills).forEach(name => {
            const li = document.createElement('li');
            li.className = 'skill-item';
            li.innerHTML = `
                <span class="skill-name">${name}</span>
                <span class="skill-version">v${skills[name].version}</span>
            `;
            skillList.appendChild(li);
        });
    }

    // --- Logger Logic (Progressive Disclosure) ---
    function addLog(type, content, level = 'info') {
        const entry = document.createElement('div');
        entry.className = `log-entry ${level} ${type.toLowerCase()}`;

        const time = new Date().toLocaleTimeString([], { hour12: false });

        // Progressive Disclosure: Summary vs Details
        if (typeof content === 'object') {
            const summary = content.message || `${type} action`;
            const details = JSON.stringify(content, null, 2);

            entry.innerHTML = `
                <div class="log-summary">
                    <span class="time">${time}</span>
                    <span class="msg">${summary}</span>
                    <span class="expand-icon">▶</span>
                </div>
                <pre class="log-details hidden">${details}</pre>
            `;

            entry.querySelector('.log-summary').onclick = () => {
                const detailsEl = entry.querySelector('.log-details');
                const icon = entry.querySelector('.expand-icon');
                detailsEl.classList.toggle('hidden');
                icon.style.transform = detailsEl.classList.contains('hidden') ? 'rotate(0deg)' : 'rotate(90deg)';
            };
        } else {
            entry.innerHTML = `
                <span class="time">${time}</span>
                <span class="msg">${content}</span>
            `;
        }

        thoughtLog.appendChild(entry);

        if (!isScrollLocked) {
            thoughtLog.scrollTop = thoughtLog.scrollHeight;
        }
    }

    // --- Chat Logic ---
    function appendMessage(role, text) {
        const msgDiv = document.createElement('div');
        msgDiv.className = `message ${role}`;

        if (role === 'assistant') {
            msgDiv.innerHTML = marked.parse(text);
        } else {
            msgDiv.textContent = text;
        }

        messageContainer.appendChild(msgDiv);
        messageContainer.scrollTop = messageContainer.scrollHeight;
    }

    async function sendMessage() {
        const text = userInput.value.trim();
        if (!text) return;

        userInput.value = '';
        appendMessage('user', text);
        addLog('User', `Request: ${text}`);

        const model = modelSelector.value;
        userInput.disabled = true;
        sendBtn.disabled = true;
        startChatStream(text, model);
    }

    function startChatStream(input, model) {
        if (eventSource) eventSource.close();

        const url = `/chat?user_input=${encodeURIComponent(input)}&model=${model}`;
        eventSource = new EventSource(url);

        eventSource.addEventListener('thought', (e) => {
            const data = JSON.parse(e.data);
            addLog('Thought', data, 'thought');
        });

        eventSource.addEventListener('status', (e) => {
            const data = JSON.parse(e.data);
            addLog('Status', data, 'info');
        });

        eventSource.addEventListener('tool_start', (e) => {
            const data = JSON.parse(e.data);
            addLog('Tool', `Calling ${data.name}...`, 'tool_start');
        });

        eventSource.addEventListener('tool_result', (e) => {
            const data = JSON.parse(e.data);
            addLog('Result', data, 'tool_result');
        });

        eventSource.addEventListener('requires_approval', (e) => {
            const data = JSON.parse(e.data);
            showAuthModal(data);
        });

        eventSource.addEventListener('response', (e) => {
            const data = JSON.parse(e.data);
            appendMessage('assistant', data.content);
            eventSource.close();
            userInput.disabled = false;
            sendBtn.disabled = false;
            userInput.focus();
        });

        eventSource.addEventListener('memory_sync', (e) => {
            addLog('Memory', 'Knowledge base updated', 'info');
            flashMemoryMonitor();
        });

        eventSource.addEventListener('error', (e) => {
            console.error('SSE Error:', e);
            addLog('Error', 'Connection lost or server error', 'error');
            eventSource.close();
            userInput.disabled = false;
            sendBtn.disabled = false;
            userInput.focus();
        });
    }

    function flashMemoryMonitor() {
        const monitor = document.getElementById('memoryMonitor');
        monitor.classList.add('flash-cyan');
        setTimeout(() => monitor.classList.remove('flash-cyan'), 1000);

        // Update summary text
        memorySummary.innerHTML = `<p>Knowledge graph synced at ${new Date().toLocaleTimeString()}</p>`;
    }

    // --- HITL Modal ---
    function showAuthModal(data) {
        riskDesc.textContent = data.risk_description || 'High-risk tool call detected.';
        authDetails.innerHTML = `
            <div><strong>Skill:</strong> ${data.skill_name}</div>
            <pre style="margin-top:8px">${JSON.stringify(data.arguments, null, 2)}</pre>
        `;
        authModal.classList.remove('hidden');
        userInput.disabled = true;
        sendBtn.disabled = true;

        // Note: For real HITL via SSE, you'd need a separate POST endpoint to respond to the server.
        // For this demo UI, we simulate the 'Approved' event.
    }

    approveBtn.onclick = () => {
        authModal.classList.add('hidden');
        userInput.disabled = false;
        sendBtn.disabled = false;
        addLog('System', 'User AUTHORIZED execution', 'tool_result');
        // In full implementation: fetch('/approve', { method: 'POST', ... })
    };

    denyBtn.onclick = () => {
        authModal.classList.add('hidden');
        userInput.disabled = false;
        sendBtn.disabled = false;
        addLog('System', 'User DENIED execution', 'error');
    };

    // --- Event Listeners ---
    sendBtn.onclick = sendMessage;
    userInput.onkeydown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    };

    clearLogBtn.onclick = () => {
        thoughtLog.innerHTML = '';
        addLog('System', 'Logs cleared');
    };

    scrollLockBtn.onclick = () => {
        isScrollLocked = !isScrollLocked;
        scrollLockBtn.textContent = isScrollLocked ? '🔒' : '🔓';
        scrollLockBtn.title = `Scroll Lock ${isScrollLocked ? 'On' : 'Off'}`;
    };

    init();
});
