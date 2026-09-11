const currentProjectId = new URLSearchParams(window.location.search).get('projectId');
let selectedCharacters = [];
let availableCharacters = [];
let selectionLocked = false;
let recommendedCharacterIds = [];

function updateNavLinks() {
    const navItems = document.querySelectorAll('aside nav > div, aside nav > a');
    const pages = ['1_劇本構思.html', '2_角色設定.html', '3_分鏡配置.html', '4_AI生圖.html', '5_匯出分享.html'];
    let idx = 0;
    navItems.forEach(el => {
        const a = el.tagName === 'A' ? el : el.querySelector('a');
        if (a && idx < pages.length) {
            // 略過純 div（非連結）
        }
        if (idx < pages.length) {
            if (el.tagName === 'A') {
                el.href = currentProjectId ? `${pages[idx]}?projectId=${currentProjectId}` : pages[idx];
            }
            idx++;
        }
    });
}

/**
 * 生成角色卡片 HTML
 */
function generateCharacterCard(character, isRecommended = false, isSelected = false) {
    const traits = character.traits || [];
    const traitsHTML = traits
        .slice(0, 2)
        .map(trait => `<span class="text-[10px] bg-primary/10 text-primary px-2 py-1 rounded-md font-bold">${trait}</span>`)
        .join('');

    const cardClass = isSelected
        ? 'border-2 border-primary-container comic-panel-shadow'
        : 'border border-outline-variant/30 hover:border-primary/50';
    const starBadge = isRecommended ? `
        <div class="absolute -top-3 left-4 bg-primary text-on-primary text-[10px] font-bold px-3 py-1 rounded-full flex items-center gap-1">
            <span class="material-symbols-outlined text-xs" style="font-variation-settings: 'FILL' 1;">star</span> AI 推薦
        </div>
    ` : '';

    return `
        <div class="bg-surface-container-lowest rounded-xl p-5 ${cardClass} relative group cursor-pointer hover:shadow-xl transition-all duration-300" data-char-id="${character.id}">
            ${starBadge}
            <div class="h-48 rounded-lg bg-surface-container mb-4 overflow-hidden flex items-center justify-center">
                ${character.image_url ? `<img alt="${character.name_zh}" class="w-full h-full object-contain group-hover:scale-105 transition-transform" src="${apiAssetUrl(character.image_url)}" />` : '<div class="w-full h-full flex items-center justify-center bg-slate-100 text-slate-400"><span class="material-symbols-outlined text-4xl">image_not_supported</span></div>'}
            </div>
            <h3 class="font-bold text-lg mb-1">${character.name_zh}</h3>
            <p class="text-xs text-on-surface-variant mb-2">${character.description || ''}</p>
            <div class="flex flex-wrap gap-2 mt-2">
                ${traitsHTML}
            </div>
        </div>
    `;
}

/**
 * 從後端加載角色列表
 */
async function loadCharacters() {
    try {
        const response = await apiFetch(`${API_BASE}/characters`);
        const result = await response.json();
        
        const characterList = Array.isArray(result) ? result : result.data;
        if (characterList) {
            availableCharacters = characterList;
            renderCharacters();
        } else {
            renderNoCharacters();
        }
    } catch (error) {
        console.error('Failed to load characters:', error);
        renderNoCharacters();
    }
}

/**
 * 渲染角色卡片到頁面
 */
function renderCharacters() {
    const grid = document.getElementById('characterGrid');
    
    if (!availableCharacters || availableCharacters.length === 0) {
        renderNoCharacters();
        return;
    }

    let html = '';
    availableCharacters.forEach(char => {
        const isSelected = selectedCharacters.includes(String(char.id));
        const isRecommended = recommendedCharacterIds.includes(String(char.id));
        html += generateCharacterCard(char, isRecommended, isSelected);
    });

    grid.innerHTML = html;

    // 重新綁定選擇事件
    setupCharacterSelection();
}

/**
 * 渲染空白狀態（無角色時）
 */
function renderNoCharacters() {
    const grid = document.getElementById('characterGrid');
    grid.innerHTML = `
        <div class="col-span-full flex items-center justify-center py-12 text-slate-400">
            <div class="flex flex-col items-center gap-2 text-center">
                <span class="material-symbols-outlined text-4xl">sentiment_dissatisfied</span>
                <span class="font-semibold">暫無可用的角色</span>
                <span class="text-xs">請檢查角色配置是否正確設置</span>
            </div>
        </div>
    `;
}

/**
 * 設定角���選擇邏輯（使用事件委派，避免重複綁定造成延遲）
 */
let selectionDelegateSetup = false;
function setupCharacterSelection() {
    if (selectionDelegateSetup) return;
    selectionDelegateSetup = true;

    const grid = document.getElementById('characterGrid');
    grid.addEventListener('click', (e) => {
        if (selectionLocked) return;
        const card = e.target.closest('[data-char-id]');
        if (!card) return;

        const charId = String(card.dataset.charId);
        const isSelected = selectedCharacters.includes(charId);

        if (isSelected) {
            selectedCharacters = selectedCharacters.filter(c => c !== charId);
        } else {
            if (selectedCharacters.length >= 2) {
                showSelectionMessage('每個專案需選擇兩名角色；請先取消一名已選角色。');
                return;
            }
            selectedCharacters.push(charId);
        }
        setCardSelected(card, !isSelected);
        showSelectionMessage(selectedCharacters.length === 2 ? '已選擇兩名角色，可以進入分鏡配置。' : `還需要選擇 ${2 - selectedCharacters.length} 名角色。`, selectedCharacters.length === 2);
    });
}

function setCardSelected(card, isSelected) {
    if (isSelected) {
        card.classList.remove('border-outline-variant/30', 'border');
        card.classList.add('border-primary-container', 'border-2', 'comic-panel-shadow');
    } else {
        card.classList.remove('border-primary-container', 'border-2', 'comic-panel-shadow');
        card.classList.add('border-outline-variant/30', 'border');
    }
}

function setSelectionLock(locked) {
    selectionLocked = locked;
    const grid = document.getElementById('characterGrid');
    const guard = document.getElementById('character-recommendation-guard');
    const nextButton = document.getElementById('character-next-button');
    if (locked) {
        grid.classList.add('pointer-events-none', 'opacity-60');
    } else {
        grid.classList.remove('pointer-events-none', 'opacity-60');
    }
    if (guard) guard.style.display = locked ? 'flex' : 'none';
    if (nextButton) nextButton.disabled = locked;
}

function showSelectionMessage(message, success = false) {
    const element = document.getElementById('character-selection-message');
    if (!element) return;
    element.textContent = message;
    element.className = `mt-4 rounded-xl border px-4 py-3 text-sm font-semibold ${success ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-amber-50 border-amber-200 text-amber-900'}`;
    element.classList.remove('hidden');
}

/**
 * 儲存角色並進入下一步
 */
async function saveCharactersAndNext() {
    if (!currentProjectId) {
        alert('尚未建立專案，請先回到劇本設計頁面建立專案。');
        window.location.href = '1_劇本構思.html';
        return;
    }
    if (selectionLocked) return;
    if (selectedCharacters.length !== 2) {
        showSelectionMessage(`請選擇兩名角色後再進入下一階段，目前已選 ${selectedCharacters.length} 名。`);
        return;
    }
    try {
        const response = await apiFetch(`${API_BASE}/projects/${currentProjectId}/characters`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ selectedCharacterIds: selectedCharacters })
        });
        const payload = await response.json();
        if (!response.ok || !payload.success) throw new Error(payload.detail || payload.error || '角色儲存失敗');
        window.location.href = `3_分鏡配置.html?projectId=${currentProjectId}`;
    } catch (e) {
        showSelectionMessage('儲存角色失敗：' + e.message);
    }
}

/**
 * 頁面初始化
 */
document.addEventListener('DOMContentLoaded', () => {
    updateNavLinks();
    loadExistingCharacters();

    // 綁定「下一步」按鈕
    document.querySelectorAll('button').forEach(btn => {
        if (btn.textContent.includes('下一步')) {
            btn.addEventListener('click', saveCharactersAndNext);
        }
    });
});

async function loadExistingCharacters() {
    setSelectionLock(true);
    if (currentProjectId) {
        try {
            const response = await apiFetch(`${API_BASE}/projects/${currentProjectId}`);
            const payload = await response.json();
            selectedCharacters = payload?.data?.characters?.selectedCharacterIds || [];
        } catch (error) {
            console.error('載入已選角色失敗', error);
        }
    }
    await loadCharacters();
    await loadCharacterRecommendation();
}

/**
 * 呼叫 AI 推薦端點，動態渲染「靈感小助手」面板
 */
async function loadCharacterRecommendation() {
    if (!currentProjectId) {
        showRecommendationError('尚未開啟專案，無法取得推薦。');
        return;
    }

    setSelectionLock(true);

    try {
        let res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/character-recommendation?t=${Date.now()}`, { cache: 'no-store' });
        let json = await res.json();

        if (!json.success || !json.data) {
            const startResponse = await apiFetch(`${API_BASE}/projects/${currentProjectId}/pages/1/characters/recommend`, { method: 'POST' });
            const startPayload = await startResponse.json();
            if (!startResponse.ok) throw new Error(startPayload.detail || startPayload.error || '無法啟動角色推薦');
            const jobId = startPayload.job_id || startPayload.data?.job_id;
            if (!jobId) throw new Error('角色推薦任務未建立');
            for (;;) {
                await new Promise(resolve => setTimeout(resolve, 1200));
                const jobResponse = await apiFetch(`${API_BASE}/jobs/${jobId}?t=${Date.now()}`, { cache: 'no-store' });
                const job = await jobResponse.json();
                if (job.status === 'failed') throw new Error(job.error || 'AI 角色推薦失敗');
                if (job.status === 'completed') break;
            }
            res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/character-recommendation?t=${Date.now()}`, { cache: 'no-store' });
            json = await res.json();
            if (!json.success || !json.data) throw new Error(json.error || 'AI 沒有回傳角色推薦');
        }

        const data = json.data;

        // 填入摘要
        const summaryEl = document.getElementById('ai-rec-summary');
        if (summaryEl) summaryEl.textContent = data.summary || '';

        // 填入推薦理由
        const reasonsEl = document.getElementById('ai-rec-reasons');
        if (reasonsEl && data.reasons) {
            let html = '';
            for (const [id, reason] of Object.entries(data.reasons)) {
                const char = availableCharacters.find(c => String(c.id) === String(id));
                const name = char ? char.name_zh : `角色 ${id}`;
                html += `<li class="flex gap-3">
                    <span class="material-symbols-outlined text-primary text-sm">tips_and_updates</span>
                    <span class="text-xs text-on-surface-variant"><span class="font-bold text-slate-700">${name}</span>：${reason}</span>
                </li>`;
            }
            reasonsEl.innerHTML = html;
        }

        // 填入搭配建議
        const pairingEl = document.getElementById('ai-rec-pairing');
        if (pairingEl) pairingEl.textContent = data.pairing_tip || '';

        // 自動選取 AI 推薦的角色（並重渲染卡片）
        if (data.recommended_ids && data.recommended_ids.length > 0) {
            recommendedCharacterIds = data.recommended_ids.map(id => String(id));
            if (selectedCharacters.length === 0) {
                selectedCharacters = recommendedCharacterIds.slice(0, 2);
            }
            renderCharacters();
            showSelectionMessage(selectedCharacters.length === 2 ? 'AI 已推薦並選取兩名角色，您可以調整後進入下一步。' : '請選擇兩名角色。', selectedCharacters.length === 2);
        }

        // 顯示推薦內容
        document.getElementById('ai-rec-loading')?.classList.add('hidden');
        document.getElementById('ai-rec-content')?.classList.remove('hidden');

    } catch (e) {
        console.error('載入角色推薦失敗:', e);
        showRecommendationError('與 AI 連線失敗，請稍後再試。');
    } finally {
        setSelectionLock(false);
    }
}

/**
 * 顯示靈感小助手錯誤狀態
 */
function showRecommendationError(message) {
    document.getElementById('ai-rec-loading')?.classList.add('hidden');
    const errEl = document.getElementById('ai-rec-error');
    const errMsg = document.getElementById('ai-rec-error-msg');
    if (errEl) errEl.classList.remove('hidden');
    if (errMsg) errMsg.textContent = message;
}

