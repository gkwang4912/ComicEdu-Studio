let currentProjectId = new URLSearchParams(window.location.search).get('projectId');
let canEnterCharacterSettings = false;

function setCharacterStepEnabled(enabled) {
    canEnterCharacterSettings = Boolean(enabled);
    const button = document.getElementById('btn-enter-characters');
    if (button) {
        button.disabled = !canEnterCharacterSettings;
        button.title = canEnterCharacterSettings ? '' : '請先確認故事弧線並等待所有劇本生成完成';
    }
}

function syncCharacterStepState(project) {
    const scripts = project.multiPageScript || (project.script ? [project.script] : []);
    const expectedPages = Number(project.pageCount || 1);
    const scriptsComplete = scripts.length >= expectedPages && scripts.slice(0, expectedPages).every(page => Array.isArray(page) && page.length === 4);
    setCharacterStepEnabled(Boolean(project.storyArcConfirmed) && scriptsComplete);
}
let pollTimer = null;

function escapeStatusText(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
}

function isScannedPdfError(error) {
    return /PDF 幾乎無法擷取文字|掃描型 PDF|Stage 1 不執行 OCR|無法讀取這份 PDF 的文字/.test(String(error || ''));
}

function materialAnalysisErrorMarkup(error) {
    if (isScannedPdfError(error)) {
        return `<div role="alert" class="flex items-start gap-4 p-4 bg-amber-50 rounded-xl border border-amber-200">
            <span class="material-symbols-outlined text-amber-700 mt-0.5">document_scanner</span>
            <div class="min-w-0">
                <p class="text-sm font-bold text-amber-950">這份 PDF 沒有可讀取的文字</p>
                <p class="mt-1 text-sm leading-6 text-amber-900/80">檔案看起來是掃描影像。請先使用 OCR 轉成可搜尋文字的 PDF，或在上方改上傳 DOCX、TXT、MD 檔案。</p>
            </div>
        </div>`;
    }
    return `<div role="alert" class="flex items-start gap-3 p-4 bg-red-50 rounded-xl border border-red-200">
        <span class="material-symbols-outlined text-red-600">error</span>
        <div><p class="text-sm font-bold text-red-900">教材分析未完成</p><p class="mt-1 text-sm text-red-700">${escapeStatusText(error || '請稍後再試，或改用其他教材檔案。')}</p></div>
    </div>`;
}

// 從表單取得故事風格
function getSelectedStyle() {
    const radios = document.querySelectorAll('input[name="style"]');
    for (const r of radios) {
        if (r.checked) {
            return r.closest('label').querySelector('div').textContent.trim();
        }
    }
    return '冒險探索';
}

// 取得選擇的頁數
function getSelectedPageCount() {
    const select = document.getElementById('input-pageCount');
    return Math.max(2, Math.min(6, select ? parseInt(select.value) || 3 : 3));
}

// ===== 教材上傳功能 =====
async function handleMaterialUpload(file) {
    if (!file) return;

    const statusEl = document.getElementById('upload-status');
    const plansEl = document.getElementById('material-plans');
    statusEl.classList.remove('hidden');
    statusEl.innerHTML = `
        <div class="flex items-center gap-3 p-3 bg-primary-fixed/30 rounded-lg border border-primary/20">
            <div class="w-5 h-5 border-3 border-primary/30 border-t-primary rounded-full animate-spin"></div>
            <span class="text-sm font-semibold text-primary">正在上傳並分析教材「${file.name}」...</span>
        </div>`;
    plansEl.classList.add('hidden');

    // 確保已有專案 ID
    if (!currentProjectId) {
        const topic = document.getElementById('input-topic').value.trim() || '教材分析';
        const subject = document.getElementById('input-subject')?.value || '自然科學';
        const gradeLevel = document.getElementById('input-grade')?.value || '國小高年級';
        const teachingObjective = document.getElementById('input-objective')?.value.trim() || '理解教材中的核心概念';
        const storyStyle = getSelectedStyle();
        const createRes = await apiFetch(`${API_BASE}/projects`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic, subject, gradeLevel, teachingObjective, storyStyle })
        });
        const createJson = await createRes.json();
        if (!createJson.success) { statusEl.innerHTML = '<p class="text-error text-sm">建立專案失敗</p>'; return; }
        currentProjectId = createJson.data.projectId;
        history.replaceState(null, '', `?projectId=${currentProjectId}`);
        updateNavLinks();
    }

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/material/upload`, {
            method: 'POST',
            body: formData
        });
        const json = await res.json();
        if (!json.success) throw new Error(json.detail || '上傳失敗');

        // 輪詢等待分析完成
        pollMaterialAnalysis();
    } catch (e) {
        statusEl.innerHTML = `<p class="text-error text-sm font-bold">上傳失敗：${e.message}</p>`;
    }
}

function pollMaterialAnalysis() {
    const statusEl = document.getElementById('upload-status');
    const interval = setInterval(async () => {
        try {
            const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}`);
            const json = await res.json();
            if (!json.success) return;
            const proj = json.data;
            syncCharacterStepState(proj);

            if (proj.status === 'material_analyzed' && proj.materialPlans) {
                clearInterval(interval);
                statusEl.innerHTML = `
                    <div class="flex items-center gap-2 p-3 bg-green-50 rounded-lg border border-green-200">
                        <span class="material-symbols-outlined text-green-600">check_circle</span>
                        <span class="text-sm font-semibold text-green-800">教材分析完成！請選擇一個方案。</span>
                    </div>`;
                renderMaterialPlans(proj.materialPlans);
            } else if (proj.status === 'failed') {
                clearInterval(interval);
                statusEl.innerHTML = materialAnalysisErrorMarkup(proj.error);
            }
        } catch (e) { console.error('輪詢教材分析:', e); }
    }, 2000);
}

function renderMaterialPlans(plans) {
    const plansEl = document.getElementById('material-plans');
    plansEl.classList.remove('hidden');

    const icons = ['science', 'menu_book', 'psychology'];
    const colors = ['indigo', 'emerald', 'amber'];

    plansEl.innerHTML = `
        <h3 class="text-sm font-bold text-slate-700 mb-3 flex items-center gap-2">
            <span class="material-symbols-outlined text-primary">auto_awesome</span>
            AI 為您規劃了三種方案
        </h3>
        <div class="grid grid-cols-3 gap-4">
            ${plans.map((plan, i) => `
                <div class="plan-card p-4 border-2 border-slate-200 rounded-xl cursor-pointer hover:border-primary hover:shadow-md transition-all"
                     onclick="selectMaterialPlan(${i})" data-plan-index="${i}">
                    <div class="flex items-center gap-2 mb-3">
                        <span class="material-symbols-outlined text-${colors[i]}-600">${icons[i]}</span>
                        <span class="text-sm font-bold text-slate-800">${plan.title || plan.planName || '方案 ' + (i + 1)}</span>
                    </div>
                    <p class="text-xs text-slate-600 mb-2">${plan.description || plan.summary || ''}</p>
                    <div class="flex flex-wrap gap-1 mt-2">
                        ${[
                            plan.subject || plan.subject_name || plan.discipline,
                            plan.gradeLevel || plan.grade_level || plan.grade || plan.target_grade,
                            plan.storyStyle || plan.story_style || plan.style
                        ].filter(value => value !== undefined && value !== null && String(value).trim() !== '')
                          .map(value => `<span class="text-[10px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full">${value}</span>`)
                          .join('')}
                    </div>
                </div>
            `).join('')}
        </div>`;
}

async function selectMaterialPlan(index) {
    // 視覺選中
    document.querySelectorAll('.plan-card').forEach(card => {
        card.classList.remove('border-primary', 'bg-primary/5');
        card.classList.add('border-slate-200');
    });
    const selected = document.querySelector(`.plan-card[data-plan-index="${index}"]`);
    if (selected) {
        selected.classList.remove('border-slate-200');
        selected.classList.add('border-primary', 'bg-primary/5');
    }

    try {
        const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/material/select-plan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ planIndex: index })
        });
        const json = await res.json();
        if (json.success && json.data) {
            const plan = json.data;
            const valueOf = (...keys) => keys.map(key => plan[key]).find(value => value !== undefined && value !== null && String(value).trim() !== '') || '';
            const topic = valueOf('topic', 'title', 'plan_title');
            const subject = valueOf('subject', 'subject_name', 'discipline') || '自然科學';
            const gradeLevel = valueOf('gradeLevel', 'grade_level', 'grade', 'target_grade') || '國小高年級';
            const objective = valueOf('teachingObjective', 'teaching_objective', 'objective', 'learning_objective');
            const storyStyle = valueOf('storyStyle', 'story_style', 'style') || '冒險探索';
            const pageCount = parseInt(valueOf('recommended_page_count', 'page_count', 'pageCount', 'recommendedPageCount'), 10);

            document.getElementById('input-topic').value = topic;
            // 設定下拉選單
            const subjectSelect = document.getElementById('input-subject');
            if (![...subjectSelect.options].some(opt => opt.text === subject)) {
                subjectSelect.add(new Option(subject, subject));
            }
            subjectSelect.value = subject;
            const gradeSelect = document.getElementById('input-grade');
            if (![...gradeSelect.options].some(opt => opt.text === gradeLevel)) {
                gradeSelect.add(new Option(gradeLevel, gradeLevel));
            }
            gradeSelect.value = gradeLevel;
            document.getElementById('input-objective').value = objective;
            if (pageCount >= 2 && pageCount <= 6) {
                document.getElementById('input-pageCount').value = pageCount;
            }
            // 設定風格
            const styleRadios = document.querySelectorAll('input[name="style"]');
            let matchedStyle = false;
            styleRadios.forEach(r => {
                const label = r.closest('label').querySelector('div').textContent.trim();
                r.checked = (label === storyStyle);
                matchedStyle = matchedStyle || r.checked;
            });
            if (!matchedStyle) styleRadios[0].checked = true;
        }
    } catch (e) {
        console.error('選擇方案失敗:', e);
    }
}

// ===== 建立專案並生成劇本 =====
async function createAndGenerate() {
    setCharacterStepEnabled(false);
    const topic = document.getElementById('input-topic').value.trim();
    if (!topic) { alert('請輸入教材主題'); return; }
    const subject = document.getElementById('input-subject').value;
    const gradeLevel = document.getElementById('input-grade').value;
    const objective = document.getElementById('input-objective').value.trim();
    const storyStyle = getSelectedStyle();
    const pageCount = getSelectedPageCount();

    const preview = document.getElementById('script-preview');
    preview.innerHTML = `<div class="flex flex-col items-center justify-center py-16 gap-4">
        <div class="w-12 h-12 border-4 border-primary/20 border-t-primary rounded-full animate-spin"></div>
        <p class="text-sm text-slate-500">${pageCount > 1 ? `正在生成 ${pageCount} 頁長劇本（AI 規劃中）...` : '正在建立專案並生成劇本...'}</p>
    </div>`;

    try {
        // 建立專案（若尚未有）
        if (!currentProjectId) {
            const createRes = await apiFetch(`${API_BASE}/projects`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ topic, subject, gradeLevel, teachingObjective: objective, storyStyle })
            });
            const createJson = await createRes.json();
            if (!createJson.success) throw new Error('建立專案失敗');
            currentProjectId = createJson.data.projectId;
            history.replaceState(null, '', `?projectId=${currentProjectId}`);
            updateNavLinks();
        } else {
            // 更新現有專案設定
            await apiFetch(`${API_BASE}/projects/${currentProjectId}/script`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ script: [] })
            }).catch(() => {});
        }

        if (pageCount > 1) {
            // 長劇本生成
            await apiFetch(`${API_BASE}/projects/${currentProjectId}/script/generate-long`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pageCount })
            });
        } else {
            // 原始單頁生成
            await apiFetch(`${API_BASE}/projects/${currentProjectId}/script/generate`, { method: 'POST' });
        }

        startPolling();
    } catch (e) {
        preview.innerHTML = `<div class="text-center py-16"><p class="text-error font-bold">發生錯誤：${e.message}</p></div>`;
    }
}

function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
        try {
            const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}`);
            const json = await res.json();
            if (!json.success) return;
            const proj = json.data;
            syncCharacterStepState(proj);
            if (proj.status === 'script_generated') {
                clearInterval(pollTimer);
                const statusDiv = document.getElementById('assistant-status');
                if (statusDiv) statusDiv.classList.add('hidden');
                renderScript(proj.script, proj.multiPageScript, proj.pageCount);
            } else if (proj.status === 'story_arc_generated' && proj.storyArc) {
                clearInterval(pollTimer);
                const statusDiv = document.getElementById('assistant-status');
                if (statusDiv) statusDiv.classList.add('hidden');
                renderStoryArcPreview(proj.storyArc);
            } else if (proj.status === 'generating_page_scripts') {
                renderStoryArcPreview(proj.storyArc, true);
            } else if (proj.status === 'failed') {
                clearInterval(pollTimer);
                const statusDiv = document.getElementById('assistant-status');
                if (statusDiv) statusDiv.classList.add('hidden');
                document.getElementById('script-preview').innerHTML = `<div class="text-center py-16"><p class="text-error font-bold">生成失敗：${proj.error || '未知錯誤'}</p><button onclick="createAndGenerate()" class="mt-4 px-4 py-2 bg-primary text-white rounded-md text-sm">重試</button></div>`;
            }
        } catch (e) { console.error('輪詢錯誤:', e); }
    }, 3000);
}

let currentStoryArc = null;

function renderStoryArcPreview(storyArc, isGeneratingScripts = false) {
    if (!storyArc) return;
    currentStoryArc = JSON.parse(JSON.stringify(storyArc));
    const preview = document.getElementById('script-preview');
    const pages = storyArc.pageSummaries || [];
    const editable = !isGeneratingScripts;

    let pagesHtml = pages.map((p, i) => {
        const roleKey = p.role || p.storyRole || '';
        const pageKnowledge = p.panelKnowledge || p.keyKnowledgePoints || [];
        const roleMeta = {
            '起': { icon: 'flag', label: '起點', tone: 'arc-role-start' },
            '承': { icon: 'trending_up', label: '發展', tone: 'arc-role-build' },
            '轉': { icon: 'change_circle', label: '轉折', tone: 'arc-role-turn' },
            '合': { icon: 'task_alt', label: '收束', tone: 'arc-role-close' }
        };
        const role = roleMeta[roleKey] || { icon: 'more_horiz', label: roleKey || '段落', tone: 'arc-role-neutral' };
        const knowledgeHtml = pageKnowledge.map((k, ki) =>
            `<div class="arc-knowledge-row">
                <span class="arc-knowledge-index">${ki + 1}</span>
                ${editable
                    ? `<input type="text" value="${escapeStatusText(k)}" data-page="${i}" data-ki="${ki}" class="arc-knowledge-input">`
                    : `<span class="text-xs text-slate-600">${escapeStatusText(k)}</span>`}
            </div>`
        ).join('');
        return `<article class="story-arc-stop ${role.tone}">
            <div class="story-arc-marker" aria-hidden="true"><span class="material-symbols-outlined">${role.icon}</span></div>
            <div class="story-arc-card">
            <div class="story-arc-card-head">
                <span class="story-arc-page">PAGE ${String(i + 1).padStart(2, '0')}</span>
                <span class="story-arc-role">${escapeStatusText(role.label)}</span>
            </div>
            <div class="flex items-center gap-2 mb-2">
                ${editable
                    ? `<input type="text" value="${escapeStatusText(p.title || '')}" data-page="${i}" class="arc-title-input">`
                    : `<span class="text-sm font-bold text-slate-800">${escapeStatusText(p.title || '')}</span>`}
            </div>
            ${editable
                ? `<textarea data-page="${i}" class="arc-summary-input" rows="2">${escapeStatusText(p.summary || '')}</textarea>`
                : `<p class="text-xs text-slate-500 mb-3">${escapeStatusText(p.summary || '')}</p>`}
            <div class="story-arc-knowledge">${knowledgeHtml}</div>
            </div>
        </article>`;
    }).join('');

    const statusText = isGeneratingScripts
        ? '<div class="flex items-center gap-2"><div class="w-4 h-4 border-2 border-indigo-200 border-t-indigo-600 rounded-full animate-spin"></div><span class="text-sm text-indigo-600 font-semibold">正在根據弧線生成各頁劇本...</span></div>'
        : '';

    const confirmBtn = editable
        ? `<button onclick="confirmStoryArc()" class="story-arc-confirm-button">
            <span class="material-symbols-outlined text-base">check_circle</span> 確認弧線，開始生成劇本
           </button>`
        : '';

    preview.innerHTML = `
        <section class="story-arc-shell">
            <div class="story-arc-heading">
                <div><span class="story-arc-eyebrow">STORY ARC</span><h3>故事弧線：${escapeStatusText(storyArc.overallTheme || '')}</h3></div>
                ${statusText}
            </div>
            ${editable ? '<p class="story-arc-help">沿著節奏線由上往下檢查故事發展；標題、摘要與知識點都可直接修改。</p>' : ''}
            <div class="story-arc-track">${pagesHtml}</div>
            ${confirmBtn ? `<div class="story-arc-action"><div><strong>弧線確認完成了嗎？</strong><span>確認後，AI 會依序生成每一頁的四格劇本。</span></div>${confirmBtn}</div>` : ''}
        </section>`;
}

async function confirmStoryArc() {
    if (!currentStoryArc || !currentProjectId) return;
    document.querySelector('[onclick="confirmStoryArc()"]')?.setAttribute('disabled', 'true');
    setCharacterStepEnabled(false);

    // 收集編輯後的值
    document.querySelectorAll('.arc-title-input').forEach(input => {
        const pi = parseInt(input.dataset.page);
        if (currentStoryArc.pageSummaries[pi]) {
            currentStoryArc.pageSummaries[pi].title = input.value;
        }
    });
    document.querySelectorAll('.arc-summary-input').forEach(ta => {
        const pi = parseInt(ta.dataset.page);
        if (currentStoryArc.pageSummaries[pi]) {
            currentStoryArc.pageSummaries[pi].summary = ta.value;
        }
    });
    document.querySelectorAll('.arc-knowledge-input').forEach(input => {
        const pi = parseInt(input.dataset.page);
        const ki = parseInt(input.dataset.ki);
        if (currentStoryArc.pageSummaries[pi]) {
            const summary = currentStoryArc.pageSummaries[pi];
            const knowledge = summary.keyKnowledgePoints || summary.panelKnowledge || [];
            knowledge[ki] = input.value;
            summary.keyKnowledgePoints = knowledge;
        }
    });

    try {
        const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/confirm-story-arc`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ storyArc: currentStoryArc })
        });
        const json = await res.json();
        if (json.success) {
            renderStoryArcPreview(currentStoryArc, true);
            startPollingScript();
        }
    } catch (e) {
        alert('確認弧線失敗：' + e.message);
    }
}

function startPollingScript() {
    const pollTimer = setInterval(async () => {
        try {
            const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}`);
            const json = await res.json();
            if (!json.success) return;
            const proj = json.data;
            syncCharacterStepState(proj);
            if (proj.status === 'script_generated') {
                clearInterval(pollTimer);
                const statusDiv = document.getElementById('assistant-status');
                if (statusDiv) statusDiv.classList.add('hidden');
                renderScript(proj.script, proj.multiPageScript, proj.pageCount);
            } else if (proj.status === 'failed') {
                clearInterval(pollTimer);
                document.getElementById('script-preview').innerHTML = `<div class="text-center py-16"><p class="text-error font-bold">生成失敗：${proj.error || '未知錯誤'}</p></div>`;
            }
        } catch (e) { console.error('輪詢錯誤:', e); }
    }, 3000);
}

// 全域變數
let currentScriptList = [];
let currentMultiPageScript = null;
let currentPageCount = 1;
let viewingPage = 0; // 當前檢視的頁碼 (0-based)

const NARRATION_LABELS = [
    '指令', '風格', '場景', '時間', '空間', '人物', '表情', '環境細節', '鏡頭', '光影', '負向提示詞'
];

function parseNarrationFields(text) {
    if (!text) return [];
    const SEPARATORS = '[\\u3002\\u3001\\uFF1B;]';
    const labelPattern = new RegExp(
        `(?:^|${SEPARATORS})\\s*(${NARRATION_LABELS.join('|')})：`, 'g'
    );
    const matches = [];
    let m;
    while ((m = labelPattern.exec(text)) !== null) {
        matches.push({ index: m.index, label: m[1], contentStart: m.index + m[0].length });
    }
    if (matches.length === 0) return [{ label: null, content: text }];
    const fields = [];
    if (matches[0].index > 0) {
        const prefix = text.substring(0, matches[0].index).trim();
        if (prefix) fields.push({ label: null, content: prefix });
    }
    for (let i = 0; i < matches.length; i++) {
        const start = matches[i].contentStart;
        const end = i + 1 < matches.length ? matches[i + 1].index : text.length;
        const content = text.substring(start, end).trim().replace(/[。、；;]$/, '').trim();
        fields.push({ label: matches[i].label, content });
    }
    return fields;
}

function reconstructNarration(fields) {
    return fields
        .map(f => f.label ? `${f.label}：${f.content}` : f.content)
        .join('、');
}

let currentNarrationFields = {};

function saveNarrationField(sceneIdx, fieldIdx, newContent) {
    const fields = currentNarrationFields[sceneIdx];
    if (!fields || fields[fieldIdx] === undefined) return;
    const cleanContent = newContent.replace(/<[^>]+>/g, '').trim();
    if (fields[fieldIdx].content === cleanContent) return;
    fields[fieldIdx].content = cleanContent;
    const scene = currentScriptList[sceneIdx];
    if (!scene) return;
    const promptObject = scene['提示詞內容'] && typeof scene['提示詞內容'] === 'object' && !Array.isArray(scene['提示詞內容'])
        ? scene['提示詞內容'] : null;
    const narrationIsDict = scene.narration && typeof scene.narration === 'object' && !Array.isArray(scene.narration);
    if (narrationIsDict) {
        const key = fields[fieldIdx].label;
        if (key) scene.narration[key] = cleanContent;
    } else if (promptObject) {
        const key = fields[fieldIdx].label;
        if (key) promptObject[key] = cleanContent;
    } else {
        const narration = reconstructNarration(fields);
        if (scene.narration !== undefined) scene.narration = narration;
        else if (scene.description !== undefined) scene.description = narration;
        else scene.narration = narration;
    }
    apiFetch(`${API_BASE}/projects/${currentProjectId}/script`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ script: currentScriptList })
    }).catch(err => console.error('自動儲存失敗:', err));
}

function renderNarrationAsFields(narration, sceneIdx) {
    let fields;
    const isPromptObject = narration && typeof narration === 'object' && !Array.isArray(narration);
    if (isPromptObject) {
        fields = Object.entries(narration)
            .map(([label, content]) => ({ label, content: String(content ?? '') }))
            .filter(field => !(String(field.label).includes('負') && !field.content.trim()));
    } else {
        fields = parseNarrationFields(String(narration || ''));
    }
    currentNarrationFields[sceneIdx] = fields;
    if (fields.length === 0) return '';
    let html = '<div class="bg-surface-container-low p-4 rounded-lg">';
    html += `<p class="text-xs font-bold text-slate-400 mb-3">【${isPromptObject ? '提示詞' : '旁白'}】</p>`;
    html += '<div class="space-y-2">';
    fields.forEach((field, fieldIdx) => {
        if (field.label === null) {
            html += `<div class="text-xs text-slate-500 italic px-1">${field.content}</div>`;
        } else {
            html += `<div class="flex items-start gap-2">
                <span class="flex-shrink-0 select-none text-[11px] font-bold bg-indigo-50 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-400 border border-indigo-100 dark:border-indigo-800 px-2 py-1 rounded w-[5.5rem] text-center leading-snug">${field.label}</span>
                <span class="flex-1 text-sm text-slate-700 dark:text-slate-200 leading-relaxed outline-none focus:bg-white dark:focus:bg-slate-800 focus:shadow-sm rounded px-2 py-0.5 border border-transparent focus:border-slate-200 dark:focus:border-slate-600 transition-all cursor-text"
                    contenteditable="true" spellcheck="false"
                    onblur="saveNarrationField(${sceneIdx}, ${fieldIdx}, this.innerText)"
                >${field.content}</span>
            </div>`;
        }
    });
    html += '</div></div>';
    return html;
}

function renderScript(script, multiPageScript, pageCount) {
    const preview = document.getElementById('script-preview');
    if (!script || !Array.isArray(script)) {
        preview.innerHTML = '<p class="text-sm text-slate-500 p-4">劇本資料格式異常，或劇本為空。</p>';
        return;
    }

    currentScriptList = JSON.parse(JSON.stringify(script));
    currentMultiPageScript = multiPageScript || null;
    currentPageCount = pageCount || 1;

    if (currentPageCount > 1 && currentMultiPageScript) {
        renderMultiPageScript();
    } else {
        renderSinglePageScript(script, 0);
    }

    const genBtn = document.getElementById('generate-script-btn');
    if (genBtn) {
        genBtn.innerHTML = '<span class="material-symbols-outlined text-lg">refresh</span> 重新生成劇本';
    }
}

function renderMultiPageScript() {
    const preview = document.getElementById('script-preview');
    let html = '';

    // 頁碼導覽
    html += `<div class="flex items-center justify-between mb-6 pb-4 border-b border-slate-200">
        <div class="flex items-center gap-2">
            <span class="material-symbols-outlined text-primary">auto_stories</span>
            <span class="text-sm font-bold text-slate-700">共 ${currentPageCount} 頁漫畫</span>
        </div>
        <div class="flex items-center gap-2">
            <button onclick="switchScriptPage(-1)" id="prev-page-btn" class="p-2 rounded-lg hover:bg-slate-100 transition-colors disabled:opacity-30" ${viewingPage === 0 ? 'disabled' : ''}>
                <span class="material-symbols-outlined">chevron_left</span>
            </button>
            <div class="flex gap-1" id="page-dots">
                ${currentMultiPageScript.map((_, i) => `
                    <button onclick="goToScriptPage(${i})" class="w-8 h-8 rounded-full text-xs font-bold transition-all ${i === viewingPage ? 'bg-primary text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}">
                        ${i + 1}
                    </button>
                `).join('')}
            </div>
            <button onclick="switchScriptPage(1)" id="next-page-btn" class="p-2 rounded-lg hover:bg-slate-100 transition-colors disabled:opacity-30" ${viewingPage === currentPageCount - 1 ? 'disabled' : ''}>
                <span class="material-symbols-outlined">chevron_right</span>
            </button>
        </div>
    </div>`;

    // 當前頁面的劇本
    html += `<div id="page-script-content"></div>`;

    preview.innerHTML = html;
    renderPageContent(viewingPage);
}

function renderPageContent(pageIdx) {
    const container = document.getElementById('page-script-content');
    if (!container || !currentMultiPageScript) return;

    const pageScript = currentMultiPageScript[pageIdx] || [];
    const globalOffset = currentMultiPageScript.slice(0, pageIdx).reduce((sum, p) => sum + (p ? p.length : 0), 0);

    let html = `<div class="mb-4 px-3 py-2 bg-indigo-50 rounded-lg">
        <span class="text-sm font-bold text-indigo-700">第 ${pageIdx + 1} 頁</span>
        <span class="text-xs text-indigo-500 ml-2">共 ${pageScript.length} 格分鏡</span>
    </div>`;

    html += '<div class="space-y-10">';
    pageScript.forEach((scene, idx) => {
        const globalIdx = globalOffset + idx;
        const sceneNum = String(idx + 1).padStart(2, '0');
        const title = scene.title || scene.scene_title || `場景 ${sceneNum}`;
        const narration = scene.narration || scene.description || scene.scene_description || scene['提示詞內容'] || '';
        const dialogues = scene.dialogues || scene.dialogue || [];
        const textContent = scene['文字內容'] || '';

        html += `<div class="space-y-4 relative pl-0 border-l-0">
            <span class="text-xs font-bold ${idx === 0 ? 'text-indigo-500' : 'text-slate-400'} uppercase tracking-widest">Scene ${sceneNum}: <span contenteditable="true" spellcheck="false" onblur="saveInlineEdit(${globalIdx}, 'title', this.innerText)" class="outline-none focus:bg-indigo-50 rounded px-1 transition-all cursor-text">${title}</span></span>
            ${narration ? renderNarrationAsFields(narration, globalIdx) : ''}`;

        // 顯示文字內容
        if (textContent) {
            html += `<div class="bg-amber-50 p-3 rounded-lg border border-amber-200">
                <p class="text-xs font-bold text-amber-700 mb-1">文字內容</p>
                <p class="text-sm text-amber-900" contenteditable="true" spellcheck="false" onblur="saveInlineEdit(${globalIdx}, 'textContent', this.innerText)">${textContent}</p>
            </div>`;
        }

        html += '<div class="space-y-3">';
        if (Array.isArray(dialogues)) {
            dialogues.forEach((d, di) => {
                const speaker = d.speaker || d.character || '角色';
                const text = d.text || d.line || d.content || '';
                const isEven = di % 2 === 1;
                html += `<div class="flex gap-4 items-start ${isEven ? 'flex-row-reverse' : ''}">
                    <div class="w-12 h-12 rounded-full ${isEven ? 'bg-orange-100' : 'bg-indigo-100'} flex-shrink-0 flex items-center justify-center">
                        <span class="material-symbols-outlined ${isEven ? 'text-orange-600' : 'text-indigo-600'}">${isEven ? 'psychology' : 'face'}</span>
                    </div>
                    <div class="bg-white p-3 rounded-2xl ${isEven ? 'rounded-tr-none text-right' : 'rounded-tl-none'} border ${isEven ? 'border-orange-50' : 'border-indigo-50'} shadow-sm">
                        <p class="text-xs font-bold ${isEven ? 'text-orange-600' : 'text-indigo-600'} mb-1">${speaker}</p>
                        <p class="text-sm outline-none focus:bg-slate-50 rounded px-1 transition-all cursor-text" contenteditable="true" spellcheck="false" onblur="saveInlineEdit(${globalIdx}, 'dialogue', this.innerText, ${di})">${text}</p>
                    </div>
                </div>`;
            });
        }

        html += `
        <div class="mt-4 bg-slate-50 p-3 rounded-lg border border-slate-200">
            <p class="text-xs font-bold text-slate-500 mb-2 flex items-center gap-1">
                <span class="material-symbols-outlined text-[14px]">edit_note</span>修改此場景
            </p>
            <div class="flex gap-2">
                <input type="text" id="modify-input-${globalIdx}" class="flex-1 px-3 py-1.5 text-sm rounded bg-white border border-slate-200 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none" placeholder="例如：把主角的話改成生氣的語氣">
                <button onclick="modifyScene(${globalIdx})" id="modify-btn-${globalIdx}" class="px-3 py-1.5 bg-indigo-50 text-indigo-600 hover:bg-indigo-100 rounded text-sm font-semibold transition-colors flex items-center gap-1">
                    <span class="material-symbols-outlined text-[16px]">send</span>發送
                </button>
            </div>
            <div id="modify-status-${globalIdx}" class="hidden text-xs text-indigo-500 mt-2 font-bold animate-pulse">AI 正在修改中...</div>
        </div>
        </div>`;
    });
    html += '</div>';
    container.innerHTML = html;
}

function switchScriptPage(delta) {
    const newPage = viewingPage + delta;
    if (newPage < 0 || newPage >= currentPageCount) return;
    goToScriptPage(newPage);
}

function goToScriptPage(pageIdx) {
    viewingPage = pageIdx;
    renderMultiPageScript();
}

function renderSinglePageScript(script, offset) {
    const preview = document.getElementById('script-preview');
    let html = '<div class="space-y-10">';
    script.forEach((scene, idx) => {
        const globalIdx = offset + idx;
        const sceneNum = String(idx + 1).padStart(2, '0');
        const title = scene.title || scene.scene_title || `場景 ${sceneNum}`;
        const narration = scene.narration || scene.description || scene.scene_description || scene['提示詞內容'] || '';
        const dialogues = scene.dialogues || scene.dialogue || [];
        const textContent = scene['文字內容'] || '';

        html += `<div class="space-y-4 relative pl-0 border-l-0">
            <span class="text-xs font-bold ${idx === 0 ? 'text-indigo-500' : 'text-slate-400'} uppercase tracking-widest">Scene ${sceneNum}: <span contenteditable="true" spellcheck="false" onblur="saveInlineEdit(${globalIdx}, 'title', this.innerText)" class="outline-none focus:bg-indigo-50 rounded px-1 transition-all cursor-text">${title}</span></span>
            ${narration ? renderNarrationAsFields(narration, globalIdx) : ''}`;

        if (textContent) {
            html += `<div class="bg-amber-50 p-3 rounded-lg border border-amber-200">
                <p class="text-xs font-bold text-amber-700 mb-1">文字內容</p>
                <p class="text-sm text-amber-900" contenteditable="true" spellcheck="false" onblur="saveInlineEdit(${globalIdx}, 'textContent', this.innerText)">${textContent}</p>
            </div>`;
        }

        html += '<div class="space-y-3">';
        if (Array.isArray(dialogues)) {
            dialogues.forEach((d, di) => {
                const speaker = d.speaker || d.character || '角色';
                const text = d.text || d.line || d.content || '';
                const isEven = di % 2 === 1;
                html += `<div class="flex gap-4 items-start ${isEven ? 'flex-row-reverse' : ''}">
                    <div class="w-12 h-12 rounded-full ${isEven ? 'bg-orange-100' : 'bg-indigo-100'} flex-shrink-0 flex items-center justify-center">
                        <span class="material-symbols-outlined ${isEven ? 'text-orange-600' : 'text-indigo-600'}">${isEven ? 'psychology' : 'face'}</span>
                    </div>
                    <div class="bg-white p-3 rounded-2xl ${isEven ? 'rounded-tr-none text-right' : 'rounded-tl-none'} border ${isEven ? 'border-orange-50' : 'border-indigo-50'} shadow-sm">
                        <p class="text-xs font-bold ${isEven ? 'text-orange-600' : 'text-indigo-600'} mb-1">${speaker}</p>
                        <p class="text-sm outline-none focus:bg-slate-50 rounded px-1 transition-all cursor-text" contenteditable="true" spellcheck="false" onblur="saveInlineEdit(${globalIdx}, 'dialogue', this.innerText, ${di})">${text}</p>
                    </div>
                </div>`;
            });
        }
        html += `
        <div class="mt-4 bg-slate-50 p-3 rounded-lg border border-slate-200">
            <p class="text-xs font-bold text-slate-500 mb-2 flex items-center gap-1">
                <span class="material-symbols-outlined text-[14px]">edit_note</span>修改此場景
            </p>
            <div class="flex gap-2">
                <input type="text" id="modify-input-${globalIdx}" class="flex-1 px-3 py-1.5 text-sm rounded bg-white border border-slate-200 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none" placeholder="例如：把主角的話改成生氣的語氣">
                <button onclick="modifyScene(${globalIdx})" id="modify-btn-${globalIdx}" class="px-3 py-1.5 bg-indigo-50 text-indigo-600 hover:bg-indigo-100 rounded text-sm font-semibold transition-colors flex items-center gap-1">
                    <span class="material-symbols-outlined text-[16px]">send</span>發送
                </button>
            </div>
            <div id="modify-status-${globalIdx}" class="hidden text-xs text-indigo-500 mt-2 font-bold animate-pulse">AI 正在修改中...</div>
        </div>
        </div>`;
    });
    html += '</div>';
    preview.innerHTML = html;
}

// 更新導覽列連結
function updateNavLinks() {
    const links = document.querySelectorAll('aside nav a');
    const pages = ['1_劇本構思.html', '2_角色設定.html', '3_分鏡配置.html', '4_AI生圖.html', '5_匯出分享.html'];
    links.forEach((a, i) => {
        if (i < pages.length) {
            a.href = currentProjectId ? `${pages[i]}?projectId=${currentProjectId}` : pages[i];
        }
    });
}

// 載入現有專案
async function loadExistingProject() {
    if (!currentProjectId) return;
    try {
        const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}`);
        const json = await res.json();
        if (!json.success) return;
        const proj = json.data;
        syncCharacterStepState(proj);
        if (proj.settings) {
            document.getElementById('input-topic').value = proj.settings.topic || '';
            const subjectSelect = document.getElementById('input-subject');
            const gradeSelect = document.getElementById('input-grade');
            if (proj.settings.subject) {
                if (![...subjectSelect.options].some(opt => opt.text === proj.settings.subject)) {
                    subjectSelect.add(new Option(proj.settings.subject, proj.settings.subject));
                }
                subjectSelect.value = proj.settings.subject;
            }
            if (proj.settings.gradeLevel) {
                if (![...gradeSelect.options].some(opt => opt.text === proj.settings.gradeLevel)) {
                    gradeSelect.add(new Option(proj.settings.gradeLevel, proj.settings.gradeLevel));
                }
                gradeSelect.value = proj.settings.gradeLevel;
            }
            document.getElementById('input-objective').value = proj.settings.teachingObjective || '';
            const savedStyle = proj.settings.storyStyle;
            if (savedStyle) {
                document.querySelectorAll('input[name="style"]').forEach(r => {
                    r.checked = r.closest('label').querySelector('div').textContent.trim() === savedStyle;
                });
            }
        }
        // 設定頁數
        if (proj.pageCount) {
            const select = document.getElementById('input-pageCount');
            if (select) select.value = proj.pageCount;
        }
        if (proj.script || (Array.isArray(proj.multiPageScript) && proj.multiPageScript.some(page => page?.length))) {
            renderScript(proj.script, proj.multiPageScript, proj.pageCount);
        } else if (proj.status === 'generating_script') {
            document.getElementById('script-preview').innerHTML = '<div class="flex flex-col items-center justify-center py-16 gap-4"><div class="w-12 h-12 border-4 border-primary/20 border-t-primary rounded-full animate-spin"></div><p class="text-sm text-slate-500">劇本生成中...</p></div>';
            startPolling();
        } else if (proj.status === 'story_arc_generated' && proj.storyArc) {
            renderStoryArcPreview(proj.storyArc);
        } else if (proj.status === 'generating_page_scripts') {
            renderStoryArcPreview(proj.storyArc, true);
            startPollingScript();
        } else if (proj.status === 'modifying_script') {
            document.getElementById('script-preview').innerHTML = '<div class="flex flex-col items-center justify-center py-16 gap-4"><div class="w-12 h-12 border-4 border-primary/20 border-t-primary rounded-full animate-spin"></div><p class="text-sm text-slate-500">AI 正在修改劇本...</p></div>';
            startPolling();
        } else if (proj.status === 'material_analyzed' && proj.materialPlans) {
            const statusEl = document.getElementById('upload-status');
            statusEl.classList.remove('hidden');
            statusEl.innerHTML = `<div class="flex items-center gap-2 p-3 bg-green-50 rounded-lg border border-green-200">
                <span class="material-symbols-outlined text-green-600">check_circle</span>
                <span class="text-sm font-semibold text-green-800">教材分析完成！請選擇一個方案。</span>
            </div>`;
            renderMaterialPlans(proj.materialPlans);
        } else if (proj.status === 'analyzing_material') {
            const statusEl = document.getElementById('upload-status');
            statusEl.classList.remove('hidden');
            statusEl.innerHTML = `<div class="flex items-center gap-3 p-3 bg-primary-fixed/30 rounded-lg border border-primary/20">
                <div class="w-5 h-5 border-3 border-primary/30 border-t-primary rounded-full animate-spin"></div>
                <span class="text-sm font-semibold text-primary">正在分析教材...</span>
            </div>`;
            pollMaterialAnalysis();
        } else if (proj.status === 'failed' && isScannedPdfError(proj.error)) {
            const statusEl = document.getElementById('upload-status');
            statusEl.classList.remove('hidden');
            statusEl.innerHTML = materialAnalysisErrorMarkup(proj.error);
        }
    } catch (e) { console.error(e); }
}

document.addEventListener('DOMContentLoaded', () => {
    setCharacterStepEnabled(false);
    updateNavLinks();
    loadExistingProject();

    document.getElementById('generate-script-btn').addEventListener('click', createAndGenerate);
    document.getElementById('chatbot-send').addEventListener('click', sendChatbotMessage);
    document.getElementById('chatbot-input').addEventListener('keydown', event => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            sendChatbotMessage();
        }
    });

    // 教材上傳事件
    const fileInput = document.getElementById('material-file-input');
    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) handleMaterialUpload(e.target.files[0]);
        });
    }
    const uploadArea = document.getElementById('upload-area');
    if (uploadArea) {
        uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('border-primary', 'bg-primary/5'); });
        uploadArea.addEventListener('dragleave', () => { uploadArea.classList.remove('border-primary', 'bg-primary/5'); });
        uploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            uploadArea.classList.remove('border-primary', 'bg-primary/5');
            if (e.dataTransfer.files.length > 0) handleMaterialUpload(e.dataTransfer.files[0]);
        });
    }
});

let chatHistory = [];

async function sendChatbotMessage() {
    const input = document.getElementById('chatbot-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    appendChatMessage('user', text);
    chatHistory.push({ role: 'user', content: text });
    const statusDiv = document.getElementById('assistant-status');
    const sendButton = document.getElementById('chatbot-send');
    if (statusDiv) statusDiv.classList.remove('hidden');
    if (sendButton) sendButton.disabled = true;
    const context = {
        topic: document.getElementById('input-topic').value,
        subject: document.getElementById('input-subject').value,
        gradeLevel: document.getElementById('input-grade').value,
        teachingObjective: document.getElementById('input-objective').value
    };
    try {
        const res = await apiFetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ projectId: currentProjectId, messages: chatHistory, context })
        });
        const json = await res.json();
        if (json.success && json.reply) {
            chatHistory.push({ role: 'assistant', content: json.reply });
            appendChatMessage('assistant', json.reply);
        } else {
            appendChatMessage('error', json.detail || json.error || 'AI 助手暫時無法回覆');
        }
    } catch (e) {
        appendChatMessage('error', '連線失敗：' + e.message);
    } finally {
        if (statusDiv) statusDiv.classList.add('hidden');
        if (sendButton) sendButton.disabled = false;
    }
}

function appendChatMessage(role, content) {
    const container = document.getElementById('chatbot-messages');
    if (!container) return;
    const div = document.createElement('div');
    if (role === 'user') {
        div.className = 'bg-slate-100 p-3 rounded-xl rounded-tr-none text-sm text-slate-700 ml-8 border border-slate-200 mt-2';
    } else if (role === 'assistant') {
        div.className = 'bg-indigo-50 p-3 rounded-xl rounded-tl-none text-sm text-indigo-900 mr-8 border border-indigo-100 mt-2';
    } else {
        div.className = 'bg-red-50 p-3 rounded-xl text-xs text-red-600 mt-2';
    }
    div.innerText = content;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

async function modifyScene(sceneIndex) {
    if (!currentProjectId) { alert('無效的專案ID'); return; }
    const inputEl = document.getElementById(`modify-input-${sceneIndex}`);
    const btnEl = document.getElementById(`modify-btn-${sceneIndex}`);
    const statusEl = document.getElementById(`modify-status-${sceneIndex}`);
    const instruction = inputEl.value.trim();
    if (!instruction) { alert('請輸入修改指示！'); return; }
    inputEl.disabled = true;
    btnEl.disabled = true;
    statusEl.classList.remove('hidden');
    try {
        const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/script/scene/${sceneIndex}/modify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ instruction })
        });
        const json = await res.json();
        if (json.success && json.data) {
            await loadExistingProject();
        } else {
            alert('修改失敗：' + (json.error || '未知錯誤'));
            inputEl.disabled = false;
            btnEl.disabled = false;
            statusEl.classList.add('hidden');
        }
    } catch (e) {
        console.error(e);
        alert('修改發生錯誤，請檢查網路連線。');
        inputEl.disabled = false;
        btnEl.disabled = false;
        statusEl.classList.add('hidden');
    }
}

function saveInlineEdit(sceneIdx, field, value, dialogueIdx = null) {
    if (!currentScriptList || !currentScriptList[sceneIdx]) return;
    const cleanValue = value.replace(/<[^>]+>/g, '').trim();
    let changed = false;
    if (field === 'title') {
        if (currentScriptList[sceneIdx].title !== undefined) {
            if (currentScriptList[sceneIdx].title !== cleanValue) { currentScriptList[sceneIdx].title = cleanValue; changed = true; }
        } else if (currentScriptList[sceneIdx].scene_title !== undefined) {
            if (currentScriptList[sceneIdx].scene_title !== cleanValue) { currentScriptList[sceneIdx].scene_title = cleanValue; changed = true; }
        } else { currentScriptList[sceneIdx].title = cleanValue; changed = true; }
    } else if (field === 'textContent') {
        if (currentScriptList[sceneIdx]['文字內容'] !== cleanValue) {
            currentScriptList[sceneIdx]['文字內容'] = cleanValue;
            changed = true;
        }
    } else if (field === 'narration') {
        if (currentScriptList[sceneIdx].narration !== undefined) {
            if (currentScriptList[sceneIdx].narration !== cleanValue) { currentScriptList[sceneIdx].narration = cleanValue; changed = true; }
        } else if (currentScriptList[sceneIdx].description !== undefined) {
            if (currentScriptList[sceneIdx].description !== cleanValue) { currentScriptList[sceneIdx].description = cleanValue; changed = true; }
        } else { currentScriptList[sceneIdx].narration = cleanValue; changed = true; }
    } else if (field === 'dialogue') {
        const dialogues = currentScriptList[sceneIdx].dialogues || currentScriptList[sceneIdx].dialogue;
        if (dialogues && dialogues[dialogueIdx]) {
            if (dialogues[dialogueIdx].text !== undefined) { if (dialogues[dialogueIdx].text !== cleanValue) { dialogues[dialogueIdx].text = cleanValue; changed = true; } }
            else if (dialogues[dialogueIdx].line !== undefined) { if (dialogues[dialogueIdx].line !== cleanValue) { dialogues[dialogueIdx].line = cleanValue; changed = true; } }
            else if (dialogues[dialogueIdx].content !== undefined) { if (dialogues[dialogueIdx].content !== cleanValue) { dialogues[dialogueIdx].content = cleanValue; changed = true; } }
        }
    }
    if (changed) {
        apiFetch(`${API_BASE}/projects/${currentProjectId}/script`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script: currentScriptList })
        }).catch(err => console.error('自動儲存失敗:', err));
    }
}

function goToNextStep() {
    if (!currentProjectId || !canEnterCharacterSettings) {
        alert("請先確認故事弧線，並等待所有頁面的劇本生成完成。");
        return;
    }
    window.location.href = `2_角色設定.html?projectId=${currentProjectId}`;
}

