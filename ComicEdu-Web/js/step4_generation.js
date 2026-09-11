const currentProjectId = new URLSearchParams(window.location.search).get('projectId');
let pollTimer = null;
let panelData = {};

// ===== 多頁狀態 =====
let totalPages = 1;
let currentViewPage = 0; // 0-based
let pageGenerationStatus = {}; // { pageIndex: 'pending' | 'generating' | 'completed' | 'failed' }
let isGenerating = false;
let multiPageLayouts = []; // 每頁的版面名稱
let renderedCompletedPanels = -1;
let pendingCompletedTransition = null;

function setGenerationHintVisible(visible) {
    document.getElementById('generation-complete-hint')?.classList.toggle('hidden', !visible);
}

function updateNavLinks() {
    const links = document.querySelectorAll('aside nav a');
    const pages = ['1_劇本構思.html', '2_角色設定.html', '3_分鏡配置.html', '4_AI生圖.html', '5_匯出分享.html'];
    let idx = 0;
    links.forEach(a => {
        if (idx < pages.length) {
            a.href = currentProjectId ? `${pages[idx]}?projectId=${currentProjectId}` : pages[idx];
            idx++;
        }
    });
}

// ===== 生成鎖定（生成中禁止操作）=====
function lockUI() {
    isGenerating = true;
    document.getElementById('generation-lock-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'generation-lock-overlay';
    overlay.className = 'fixed inset-0 bg-black/10 z-40 pointer-events-auto';
    overlay.style.cursor = 'not-allowed';
    overlay.innerHTML = `<div class="absolute top-20 left-1/2 -translate-x-1/2 bg-white px-6 py-3 rounded-full shadow-lg border border-indigo-200 flex items-center gap-3 z-50">
        <div class="w-5 h-5 border-3 border-indigo-200 border-t-indigo-600 rounded-full animate-spin"></div>
        <span class="text-sm font-bold text-indigo-700">漫畫生成中，請稍候...</span>
    </div>`;
    document.body.appendChild(overlay);
}

function unlockUI() {
    isGenerating = false;
    document.getElementById('generation-lock-overlay')?.remove();
}

// ===== 翻頁控制列 =====
function renderPageNav() {
    let nav = document.getElementById('page-nav-bar');
    if (totalPages <= 1) {
        if (nav) nav.remove();
        return;
    }

    if (!nav) {
        nav = document.createElement('div');
        nav.id = 'page-nav-bar';
        nav.className = 'col-span-12 flex items-center justify-between py-3 px-4 bg-white rounded-xl border border-slate-200 shadow-sm mb-4';
        const grid = document.querySelector('.grid.grid-cols-12');
        const banner = document.getElementById('gen-status-banner');
        if (banner) {
            grid.insertBefore(nav, banner.nextSibling);
        } else if (grid) {
            grid.prepend(nav);
        }
    }

    const prevDisabled = currentViewPage === 0;
    const nextDisabled = currentViewPage === totalPages - 1;

    nav.innerHTML = `
        <button onclick="flipPage(-1)" class="flex items-center gap-2 px-4 py-2 rounded-lg font-bold text-sm transition-all ${prevDisabled ? 'bg-slate-100 text-slate-300 cursor-not-allowed' : 'bg-slate-100 text-slate-700 hover:bg-slate-200 active:scale-95'}" ${prevDisabled ? 'disabled' : ''}>
            <span class="material-symbols-outlined">chevron_left</span> 上一頁
        </button>
        <div class="flex items-center gap-2">
            ${Array.from({length: totalPages}, (_, i) => {
                const status = pageGenerationStatus[i] || 'pending';
                let colorClass = 'bg-slate-100 text-slate-600';
                if (i === currentViewPage) colorClass = 'bg-indigo-600 text-white ring-2 ring-indigo-300';
                else if (status === 'completed') colorClass = 'bg-green-500 text-white';
                else if (status === 'generating') colorClass = 'bg-indigo-400 text-white animate-pulse';
                else if (status === 'failed') colorClass = 'bg-red-400 text-white';
                return `<button onclick="flipToPage(${i})" class="w-9 h-9 rounded-full text-xs font-bold transition-all ${colorClass}">${i + 1}</button>`;
            }).join('')}
        </div>
        <button onclick="flipPage(1)" class="flex items-center gap-2 px-4 py-2 rounded-lg font-bold text-sm transition-all ${nextDisabled ? 'bg-slate-100 text-slate-300 cursor-not-allowed' : 'bg-slate-100 text-slate-700 hover:bg-slate-200 active:scale-95'}" ${nextDisabled ? 'disabled' : ''}>
            下一頁 <span class="material-symbols-outlined">chevron_right</span>
        </button>
    `;
}

function flipPage(delta) {
    const newPage = currentViewPage + delta;
    if (newPage < 0 || newPage >= totalPages) return;
    flipToPage(newPage);
}

function flipToPage(pageIdx) {
    if (pageIdx < 0 || pageIdx >= totalPages) return;
    currentViewPage = pageIdx;
    resetPageView();
    renderPageNav();
    renderCurrentPageContent();
}

function resetPageView() {
    document.getElementById('generation-preview')?.remove();
    document.getElementById('final-preview-section')?.remove();
    document.getElementById('inline-prompt-editor')?.remove();
    selectedPanelId = null;
    panelData = {};
    renderedCompletedPanels = -1;
    pendingCompletedTransition = null;
}

// ===== 全局狀態看板 =====
function getStatusBanner() {
    let banner = document.getElementById('gen-status-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'gen-status-banner';
        banner.className = 'col-span-12 flex flex-col gap-2 py-4 px-6 rounded-xl border';
        const grid = document.querySelector('.grid.grid-cols-12');
        if (grid) grid.prepend(banner);
    }
    return banner;
}

function showStatus(type, message, details = null) {
    const banner = getStatusBanner();
    let content = `<span class="text-sm font-semibold">${message}</span>`;

    if (type === 'loading') {
        banner.className = 'col-span-12 flex flex-col gap-2 py-4 px-6 rounded-xl border border-primary/20 bg-primary-fixed/30 text-primary';
        const progressBar = details?.totalProgress ? `
            <div class="w-full bg-primary/20 rounded-full h-2 overflow-hidden">
                <div class="bg-primary h-full transition-all" style="width: ${details.totalProgress}%;"></div>
            </div>
            <div class="text-xs opacity-75">${details.completedPanels}/${details.totalPanels} 分鏡已完成 · 進度 ${details.totalProgress}%${totalPages > 1 ? ` · 第 ${currentViewPage + 1}/${totalPages} 頁` : ''}</div>
        ` : '';
        banner.innerHTML = `
            <div class="flex items-center gap-3">
                <div class="w-5 h-5 border-3 border-primary/30 border-t-primary rounded-full animate-spin"></div>
                ${content}
            </div>
            ${progressBar}
        `;
    } else if (type === 'success') {
        banner.className = 'col-span-12 flex items-center gap-4 py-4 px-6 rounded-xl border border-indigo-200 bg-indigo-50 text-indigo-900';
        banner.innerHTML = `
            <span class="material-symbols-outlined text-indigo-600" style="font-variation-settings: 'FILL' 1;">check_circle</span>
            ${content}
        `;
    } else if (type === 'error') {
        banner.className = 'col-span-12 flex items-center gap-4 py-4 px-6 rounded-xl border border-error/20 bg-error-container/30 text-error';
        banner.innerHTML = `
            <span class="material-symbols-outlined">error</span>
            ${content}
            <button onclick="triggerGenerate()" class="ml-auto px-4 py-2 bg-error text-white rounded-md text-sm font-bold hover:opacity-90">重試</button>
        `;
    }
}

// ===== 注入覆蓋層樣式 =====
function injectPreviewStyles() {
    if (document.getElementById('gen-preview-styles')) return;
    const style = document.createElement('style');
    style.id = 'gen-preview-styles';
    style.textContent = `
        .panel-mask-overlay { position:absolute; box-sizing:border-box; pointer-events:none; transition:background 0.5s ease,opacity 0.5s ease; -webkit-mask-size:100% 100%; mask-size:100% 100%; -webkit-mask-repeat:no-repeat; mask-repeat:no-repeat; }
        .panel-mask-overlay.panel-pending { background:rgba(148,163,184,0.25); }
        .panel-mask-overlay.panel-generating { background:rgba(79,70,229,0.22); animation:panel-breathe 1.8s ease-in-out infinite; overflow:hidden; }
        .panel-mask-overlay.panel-completed { background:transparent; opacity:0; }
        .panel-mask-overlay.panel-failed { background:rgba(220,38,38,0.15); }
        .panel-label { position:absolute; box-sizing:border-box; pointer-events:none; display:flex; flex-direction:column; align-items:center; justify-content:center; }
        @keyframes panel-breathe { 0%,100%{background:rgba(79,70,229,0.15);} 50%{background:rgba(79,70,229,0.35);} }
        @keyframes scan-line { 0%{top:5%;} 100%{top:85%;} }
        @keyframes spin { from{transform:rotate(0deg);} to{transform:rotate(360deg);} }
        .scan-bar-masked { position:absolute; left:5%; width:90%; height:3px; background:linear-gradient(90deg,transparent,rgba(79,70,229,0.8),transparent); border-radius:2px; animation:scan-line 2s ease-in-out infinite alternate; }
    `;
    document.head.appendChild(style);
}

function revealGenerationOverlays(image) {
    const section = image?.closest('#generation-preview');
    if (!section) return;
    section.dataset.previewReady = 'true';
    const overlays = section.querySelector('.overlays-container');
    if (overlays) overlays.style.opacity = '1';
}

// ===== 生成中的預覽顯示 =====
function renderGenerationPreview(data) {
    setGenerationHintVisible(false);
    injectPreviewStyles();
    const grid = document.querySelector('.grid.grid-cols-12');
    if (!grid) return;

    let section = document.getElementById('generation-preview');
    if (!section) {
        grid.querySelectorAll('.panel-card, #progress-overview, #live-preview-section').forEach(el => el.remove());
        section = document.createElement('div');
        section.id = 'generation-preview';
        section.className = 'col-span-12 mt-2';
        section.dataset.initialized = "false";
        const nav = document.getElementById('page-nav-bar');
        const statusBanner = document.getElementById('gen-status-banner');
        const insertAfter = nav || statusBanner;
        if (insertAfter) {
            grid.insertBefore(section, insertAfter.nextSibling);
        } else {
            grid.appendChild(section);
        }
    }

    const canvasSize = data.canvasSize;
    const positions = data.panelPositions || [];
    const panels = data.panels || [];
    const previewAvailable = data.previewAvailable;

    // A poll can already report the next panel as generating by the time the
    // newly composed Stage 8 image arrives. Commit that image first, briefly
    // show the completed panel, and only then start the next panel animation.
    const completedCount = Number(data.completedPanels || 0);
    const isTransitionFrame = Boolean(data.__transitionFrame);
    if (!isTransitionFrame && pendingCompletedTransition !== null) return;
    if (!isTransitionFrame && renderedCompletedPanels >= 0 && completedCount > renderedCompletedPanels) {
        pendingCompletedTransition = completedCount;
        const settledFrame = {
            ...data,
            __transitionFrame: true,
            panels: panels.map(panel => panel.status === 'generating' ? { ...panel, status: 'pending' } : panel),
        };
        renderGenerationPreview(settledFrame);
        const previewImage = document.querySelector('#generation-preview #gen-preview-img');
        const showNextAnimation = () => window.setTimeout(() => {
            renderedCompletedPanels = completedCount;
            pendingCompletedTransition = null;
            renderGenerationPreview({ ...data, __transitionFrame: true });
        }, 350);
        if (previewImage?.complete && previewImage.naturalWidth) showNextAnimation();
        else if (previewImage) previewImage.addEventListener('load', showNextAnimation, { once: true });
        else showNextAnimation();
        return;
    }
    if (!isTransitionFrame || pendingCompletedTransition === null) {
        renderedCompletedPanels = Math.max(renderedCompletedPanels, completedCount);
    }

    const cw = canvasSize ? canvasSize.w : 768;
    const ch = canvasSize ? canvasSize.h : 1086;

    const generatingIndex = panels.findIndex(p => p.status === 'generating');
    const progressText = generatingIndex >= 0
        ? `正在生成第 ${generatingIndex + 1} / ${data.totalPanels} 格分鏡...`
        : `已完成 ${data.completedPanels} / ${data.totalPanels} 格`;

    let overlaysHtml = '';
    if (positions.length > 0) {
        positions.forEach((pos, i) => {
            const panel = panels[i] || {};
            const status = panel.status || 'pending';
            const left = (pos.x / cw * 100).toFixed(2);
            const top = (pos.y / ch * 100).toFixed(2);
            const width = (pos.w / cw * 100).toFixed(2);
            const height = (pos.h / ch * 100).toFixed(2);
            const centerLeft = ((Number.isFinite(pos.cx) ? pos.cx : pos.x + pos.w / 2) / cw * 100).toFixed(2);
            const centerTop = ((Number.isFinite(pos.cy) ? pos.cy : pos.y + pos.h / 2) / ch * 100).toFixed(2);
            const maskUrl = `${API_BASE}/projects/${currentProjectId}/masks/${i}?page=${currentViewPage}&t=${Date.now()}`;
            const maskStyle = `-webkit-mask-image:url('${maskUrl}');mask-image:url('${maskUrl}');`;
            let extraContent = '';
            if (status === 'generating') extraContent = `<div class="scan-bar-masked"></div>`;
            overlaysHtml += `<div class="panel-mask-overlay panel-${status}" style="left:${left}%;top:${top}%;width:${width}%;height:${height}%;${maskStyle}">${extraContent}</div>`;
            if (status === 'generating') {
                overlaysHtml += `<div class="panel-label" style="left:${centerLeft}%;top:${centerTop}%;transform:translate(-50%,-50%);">
                    <div style="width:28px;height:28px;border:3px solid rgba(99,102,241,0.3);border-top-color:#6366f1;border-radius:50%;animation:spin 0.8s linear infinite;margin-bottom:6px;"></div>
                    <span style="color:#4338ca;font-size:11px;font-weight:700;">生成中</span>
                </div>`;
            } else if (status === 'pending') {
                overlaysHtml += `<div class="panel-label" style="left:${centerLeft}%;top:${centerTop}%;transform:translate(-50%,-50%);">
                    <span style="color:rgba(100,116,139,0.5);font-size:11px;font-weight:600;">第 ${i + 1} 格</span>
                </div>`;
            }
        });
    }

    const previewVersion = data.previewVersion || Date.now();
    const previewImgSrc = previewAvailable ? `${API_BASE}/projects/${currentProjectId}/preview?page=${currentViewPage}&v=${previewVersion}` : '';
    const previewImgHtml = previewImgSrc
        ? `<img id="gen-preview-img" src="${previewImgSrc}" alt="漫畫預覽" style="position:absolute;inset:0;width:100%;height:100%;border-radius:8px;" onload="revealGenerationOverlays(this)" onerror="this.style.opacity='0.3';revealGenerationOverlays(this)">`
        : '';

    // 檢查 canvas 尺寸是否改變（換頁時版面不同），若不同則強制全量重繪
    const prevCw = section.dataset.cw;
    const prevCh = section.dataset.ch;
    const sizeChanged = prevCw !== String(cw) || prevCh !== String(ch);
    const overlaySignature = JSON.stringify({
        page: currentViewPage,
        positions,
        statuses: panels.map(panel => panel.status || 'pending')
    });

    if (section.dataset.initialized === "true" && !sizeChanged) {
        const progressSpan = section.querySelector('.progress-text');
        if (progressSpan) progressSpan.textContent = progressText;
        const img = section.querySelector('#gen-preview-img');
        if (img) { if (previewImgSrc) img.src = previewImgSrc; }
        else if (previewImgSrc) { const container = section.querySelector('.canvas-container'); if (container) container.insertAdjacentHTML('afterbegin', previewImgHtml); }
        const overlaysContainer = section.querySelector('.overlays-container');
        if (overlaysContainer && section.dataset.overlaySignature !== overlaySignature) {
            overlaysContainer.innerHTML = overlaysHtml;
            section.dataset.overlaySignature = overlaySignature;
        }
    } else {
        section.dataset.initialized = "true";
        section.dataset.cw = String(cw);
        section.dataset.ch = String(ch);
        section.dataset.overlaySignature = overlaySignature;
        section.dataset.previewReady = "false";
        section.innerHTML = `
            <div style="text-align:center;margin-bottom:12px;">
                <span class="progress-text" style="font-size:14px;font-weight:600;color:#475569;">${progressText}</span>
            </div>
            <div class="canvas-container" style="position:relative;max-width:550px;margin:0 auto;aspect-ratio:${cw}/${ch};background:white;border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,0.12);">
                ${previewImgHtml}
                <div class="overlays-container" style="position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity 160ms ease;">${overlaysHtml}</div>
            </div>
        `;
    }
    panelData = data;
}

// ===== 渲染當前頁面內容 =====
function renderCurrentPageContent() {
    const status = pageGenerationStatus[currentViewPage] || 'pending';
    if (status === 'completed') {
        renderFinalImage();
    } else if (status === 'generating') {
        fetchAndRenderPagePreview(currentViewPage);
    }
}

async function fetchAndRenderPagePreview(pageIdx) {
    try {
        const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/panels?page=${pageIdx}`);
        const json = await res.json();
        if (json.success && json.data) {
            renderGenerationPreview(json.data);
        }
    } catch (e) { console.error('取得頁面預覽失敗:', e); }
}

// ===== 面板選擇和編輯（保留原功能）=====
let selectedPanelId = null;
const PROMPT_LABELS = ['指令', '風格', '場景', '時間', '空間', '人物', '表情', '環境細節', '鏡頭', '光影', '負向提示詞'];

function parsePromptFields(text) {
    if (!text) return [];
    const SEPARATORS = '[\\u3002\\u3001\\uFF1B;]';
    const labelPattern = new RegExp(`(?:^|${SEPARATORS})\\s*(${PROMPT_LABELS.join('|')})：`, 'g');
    const matches = [];
    let m;
    while ((m = labelPattern.exec(text)) !== null) {
        matches.push({ index: m.index, label: m[1], contentStart: m.index + m[0].length });
    }
    if (matches.length === 0) return [{ label: null, content: text }];
    const fields = [];
    if (matches[0].index > 0) { const prefix = text.substring(0, matches[0].index).trim(); if (prefix) fields.push({ label: null, content: prefix }); }
    for (let i = 0; i < matches.length; i++) {
        const start = matches[i].contentStart;
        const end = i + 1 < matches.length ? matches[i + 1].index : text.length;
        fields.push({ label: matches[i].label, content: text.substring(start, end).trim().replace(/[。、；;]$/, '').trim() });
    }
    return fields;
}

function reconstructPrompt(fields) {
    return fields.map(f => f.label ? `${f.label}：${f.content}` : f.content).join('、');
}

function renderPromptFields(fields, panelId) {
    let html = '<div class="space-y-2">';
    fields.forEach((field, fi) => {
        if (field.label === null) {
            html += `<div class="text-xs text-slate-500 italic px-1"><span contenteditable="true" spellcheck="false" class="outline-none focus:bg-white focus:shadow-sm rounded px-1 transition-all cursor-text" data-panel-id="${panelId}" data-field-idx="${fi}">${field.content}</span></div>`;
        } else {
            html += `<div class="flex items-start gap-2">
                <span class="flex-shrink-0 select-none text-[11px] font-bold bg-indigo-50 text-indigo-600 border border-indigo-100 px-2 py-1 rounded w-[5.5rem] text-center leading-snug">${field.label}</span>
                <span class="flex-1 text-sm text-slate-700 leading-relaxed outline-none focus:bg-white focus:shadow-sm rounded px-2 py-0.5 border border-transparent focus:border-indigo-200 transition-all cursor-text"
                    contenteditable="true" spellcheck="false" data-panel-id="${panelId}" data-field-idx="${fi}">${field.content}</span>
            </div>`;
        }
    });
    html += '</div>';
    return html;
}

let currentPromptFields = {};
let panelMaskCanvases = [];

function preloadPanelMasks(totalPanels) {
    panelMaskCanvases = [];
    for (let i = 0; i < totalPanels; i++) {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        const idx = i;
        img.onload = () => {
            const c = document.createElement('canvas');
            c.width = img.naturalWidth; c.height = img.naturalHeight;
            c.getContext('2d').drawImage(img, 0, 0);
            panelMaskCanvases[idx] = c;
        };
        img.src = `${API_BASE}/projects/${currentProjectId}/masks/${i}?page=${currentViewPage}`;
        panelMaskCanvases.push(null);
    }
}

function hitTestPanels(clickX, clickY, containerEl) {
    const positions = panelData.panelPositions || [];
    const canvasSize = panelData.canvasSize || { w: 768, h: 1086 };
    const rect = containerEl.getBoundingClientRect();
    const scaleX = canvasSize.w / rect.width;
    const scaleY = canvasSize.h / rect.height;
    const cx = (clickX - rect.left) * scaleX;
    const cy = (clickY - rect.top) * scaleY;
    for (let i = positions.length - 1; i >= 0; i--) {
        const pos = positions[i];
        if (cx >= pos.x && cx < pos.x + pos.w && cy >= pos.y && cy < pos.y + pos.h) {
            const maskCanvas = panelMaskCanvases[i];
            if (maskCanvas) {
                const mx = Math.floor((cx - pos.x) / pos.w * maskCanvas.width);
                const my = Math.floor((cy - pos.y) / pos.h * maskCanvas.height);
                if (maskCanvas.getContext('2d').getImageData(mx, my, 1, 1).data[0] > 128) return i;
            } else return i;
        }
    }
    return -1;
}

function setupCanvasClickHandler(containerEl) {
    containerEl.style.cursor = 'pointer';
    containerEl.addEventListener('click', (e) => {
        if (isGenerating) return;
        const idx = hitTestPanels(e.clientX, e.clientY, containerEl);
        if (idx >= 0) {
            const panels = panelData.panels || [];
            const panel = panels[idx] || {};
            selectPanel(panel.id !== undefined ? panel.id : idx);
        }
    });
    let lastHoveredIdx = -1;
    containerEl.addEventListener('mousemove', (e) => {
        const idx = hitTestPanels(e.clientX, e.clientY, containerEl);
        if (idx === lastHoveredIdx) return;
        document.querySelectorAll('.panel-clickable.panel-hovered').forEach(el => el.classList.remove('panel-hovered'));
        lastHoveredIdx = idx;
        if (idx >= 0) {
            document.querySelector(`.panel-clickable[data-panel-id="${idx}"]`)?.classList.add('panel-hovered');
            containerEl.style.cursor = isGenerating ? 'not-allowed' : 'pointer';
        } else containerEl.style.cursor = 'default';
    });
    containerEl.addEventListener('mouseleave', () => {
        lastHoveredIdx = -1;
        document.querySelectorAll('.panel-clickable.panel-hovered').forEach(el => el.classList.remove('panel-hovered'));
    });
}

function injectPanelSelectStyles() {
    if (document.getElementById('panel-select-styles')) return;
    const style = document.createElement('style');
    style.id = 'panel-select-styles';
    style.textContent = `
        .panel-clickable { position:absolute; box-sizing:border-box; pointer-events:none; background:transparent; transition:background 0.2s ease; -webkit-mask-size:100% 100%; mask-size:100% 100%; -webkit-mask-repeat:no-repeat; mask-repeat:no-repeat; z-index:2; }
        .panel-clickable.panel-hovered { background:rgba(99,102,241,0.15); }
        .panel-clickable.panel-selected { background:rgba(99,102,241,0.25); animation:select-breathe 1.5s ease-in-out infinite; }
        @keyframes select-breathe { 0%,100%{background:rgba(99,102,241,0.18);} 50%{background:rgba(99,102,241,0.32);} }
        .panel-editor-container { animation:editor-slide-in 0.25s ease-out; }
        @keyframes editor-slide-in { from{opacity:0;transform:translateY(-8px);} to{opacity:1;transform:translateY(0);} }
    `;
    document.head.appendChild(style);
}

function selectPanel(panelId) {
    if (isGenerating) return;
    if (selectedPanelId === panelId) { deselectPanel(); return; }
    selectedPanelId = panelId;
    document.querySelectorAll('.panel-clickable').forEach(el => el.classList.remove('panel-selected'));
    document.querySelector(`.panel-clickable[data-panel-id="${panelId}"]`)?.classList.add('panel-selected');
    showInlinePromptEditor(panelId);
}

function deselectPanel() {
    selectedPanelId = null;
    document.querySelectorAll('.panel-clickable').forEach(el => el.classList.remove('panel-selected'));
    document.getElementById('inline-prompt-editor')?.remove();
}

function showInlinePromptEditor(panelId) {
    const panels = panelData.panels || [];
    const panel = panels.find(p => p.id === panelId) || panels[panelId] || null;
    const promptText = panel ? (panel.prompt || '') : '';
    const dialogueText = panel ? (panel.dialogueText || '') : '';
    const fields = parsePromptFields(promptText);
    currentPromptFields[panelId] = fields;
    let editor = document.getElementById('inline-prompt-editor');
    if (editor) editor.remove();
    editor = document.createElement('div');
    editor.id = 'inline-prompt-editor';
    editor.className = 'panel-editor-container col-span-12 mt-4 p-5 bg-white rounded-xl border border-indigo-200 shadow-md';
    editor.innerHTML = `
        <div class="flex items-center justify-between mb-4">
            <h4 class="text-sm font-bold text-slate-800 flex items-center gap-2">
                <span class="material-symbols-outlined text-indigo-600 text-lg">edit</span>
                分鏡 ${panelId + 1} — 提示詞編輯
            </h4>
            <button onclick="deselectPanel()" class="p-1 rounded-full hover:bg-slate-100 transition-colors">
                <span class="material-symbols-outlined text-slate-400 text-lg">close</span>
            </button>
        </div>
        <div class="bg-slate-50 p-4 rounded-lg border border-slate-100">${renderPromptFields(fields, panelId)}</div>
        <div class="mt-4 bg-amber-50 p-4 rounded-lg border border-amber-200">
            <div class="flex items-center gap-2 mb-2">
                <span class="material-symbols-outlined text-amber-600 text-base">chat_bubble</span>
                <span class="text-sm font-bold text-amber-800">文字內容</span>
            </div>
            <input type="text" id="dialogue-text-input-${panelId}" value="${dialogueText.replace(/"/g, '&quot;')}" placeholder="（無對話文字）"
                   class="w-full px-3 py-2 text-sm border border-amber-300 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-amber-400 transition-all" />
        </div>
        <div class="flex items-center justify-end gap-3 mt-4">
            <span class="text-xs text-slate-400 mr-auto">點擊各欄位可直接編輯</span>
            <button onclick="regenerateSinglePanel(${panelId})" class="px-5 py-2.5 bg-indigo-600 text-white rounded-lg text-sm font-bold shadow hover:bg-indigo-700 transition-all active:scale-95 flex items-center gap-2">
                <span class="material-symbols-outlined text-base">refresh</span> 重新生成此格
            </button>
        </div>
    `;
    const finalSection = document.getElementById('final-preview-section');
    if (finalSection) {
        finalSection.parentNode.insertBefore(editor, finalSection.nextSibling);
        setTimeout(() => editor.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
    }
}

function showPanelRegenOverlay(panelId) {
    injectPreviewStyles();
    const positions = panelData.panelPositions || [];
    const canvasSize = panelData.canvasSize || { w: 768, h: 1086 };
    if (panelId >= positions.length) return;
    const pos = positions[panelId];
    const cw = canvasSize.w, ch = canvasSize.h;
    const left = (pos.x / cw * 100).toFixed(2), top = (pos.y / ch * 100).toFixed(2);
    const width = (pos.w / cw * 100).toFixed(2), height = (pos.h / ch * 100).toFixed(2);
    const maskUrl = `${API_BASE}/projects/${currentProjectId}/masks/${panelId}?page=${currentViewPage}`;
    const maskStyle = `-webkit-mask-image:url('${maskUrl}');mask-image:url('${maskUrl}');`;
    const overlayContainer = document.querySelector('#final-preview-section [style*="position:absolute;inset:0"]');
    if (!overlayContainer) return;
    const overlay = document.createElement('div');
    overlay.id = `regen-overlay-${panelId}`;
    overlay.className = 'panel-mask-overlay panel-generating';
    overlay.style.cssText = `left:${left}%;top:${top}%;width:${width}%;height:${height}%;${maskStyle}pointer-events:none;z-index:10;`;
    overlay.innerHTML = `<div class="scan-bar-masked"></div>`;
    overlayContainer.appendChild(overlay);
    const label = document.createElement('div');
    label.id = `regen-label-${panelId}`;
    label.className = 'panel-label';
    label.style.cssText = `left:${left}%;top:${top}%;width:${width}%;height:${height}%;z-index:11;`;
    label.innerHTML = `<div style="width:32px;height:32px;border:3px solid rgba(99,102,241,0.3);border-top-color:#6366f1;border-radius:50%;animation:spin 0.8s linear infinite;margin-bottom:8px;"></div>
        <span style="color:#4338ca;font-size:12px;font-weight:700;text-shadow:0 1px 4px rgba(255,255,255,0.8);">重新生成中</span>`;
    overlayContainer.appendChild(label);
}

function removePanelRegenOverlay(panelId) {
    document.getElementById(`regen-overlay-${panelId}`)?.remove();
    document.getElementById(`regen-label-${panelId}`)?.remove();
}

async function pollSinglePanelRegen(panelId) {
    let attempts = 0;
    while (attempts < 300) {
        try {
            const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/panels?page=${currentViewPage}`);
            const json = await res.json();
            if (!json.success) { await new Promise(r => setTimeout(r, 2000)); attempts++; continue; }
            const data = json.data;
            if (data.status === 'panels_completed') {
                panelData = data;
                removePanelRegenOverlay(panelId);
                const img = document.querySelector('#final-preview-section img[alt="最終成果"]');
                if (img) img.src = `${API_BASE}/projects/${currentProjectId}/export/image?page=${currentViewPage}&t=${Date.now()}`;
                showStatus('success', `分鏡 ${panelId + 1} 已重新生成完成！`);
                return;
            } else if (data.status === 'failed') {
                removePanelRegenOverlay(panelId);
                showStatus('error', `重新生成失敗：${data.error || '未知錯誤'}`);
                return;
            }
        } catch (e) { console.error('輪詢錯誤:', e); }
        await new Promise(r => setTimeout(r, 2000));
        attempts++;
    }
    removePanelRegenOverlay(panelId);
    showStatus('error', '重新生成逾時');
}

async function regenerateSinglePanel(panelId) {
    if (isGenerating) return;
    const fields = currentPromptFields[panelId];
    let newPrompt = null;
    if (fields) {
        const editorEl = document.getElementById('inline-prompt-editor');
        if (editorEl) {
            editorEl.querySelectorAll(`[data-panel-id="${panelId}"]`).forEach(span => {
                const fi = parseInt(span.dataset.fieldIdx);
                if (fields[fi] !== undefined) fields[fi].content = span.innerText.trim();
            });
        }
        newPrompt = reconstructPrompt(fields);
    }
    const dialogueTextInput = document.getElementById(`dialogue-text-input-${panelId}`);
    let newDialogueText = null;
    if (dialogueTextInput) { const trimmed = dialogueTextInput.value.trim(); if (trimmed) newDialogueText = trimmed; }
    const newSeed = Math.floor(Math.random() * 2147483647);
    deselectPanel();
    showPanelRegenOverlay(panelId);
    const banner = document.getElementById('gen-status-banner');
    if (banner) banner.remove();
    try {
        if (newPrompt !== null || newDialogueText !== null) {
            const updatePayload = { seed: newSeed };
            if (newPrompt !== null) updatePayload.prompt = newPrompt;
            if (newDialogueText !== null) updatePayload.dialogueText = newDialogueText;
            const updateRes = await apiFetch(`${API_BASE}/projects/${currentProjectId}/panels/${panelId}/prompt?page=${currentViewPage}`, {
                method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(updatePayload)
            });
            if (!updateRes.ok) { removePanelRegenOverlay(panelId); showStatus('error', '更新分鏡提示詞失敗'); return; }
        }
        const genPayload = { seed: newSeed };
        if (newPrompt !== null) genPayload.prompt = newPrompt;
        if (newDialogueText !== null) genPayload.dialogueText = newDialogueText;
        const genRes = await apiFetch(`${API_BASE}/projects/${currentProjectId}/panels/${panelId}/regenerate?page=${currentViewPage}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(genPayload)
        });
        if (!genRes.ok) { removePanelRegenOverlay(panelId); alert('重新生成請求失敗'); return; }
        pollSinglePanelRegen(panelId);
    } catch (e) { removePanelRegenOverlay(panelId); alert('操作失敗: ' + e.message); }
}

// ===== 觸發生成 =====
async function triggerGenerate() {
    setGenerationHintVisible(false);
    if (!currentProjectId) {
        alert('尚未建立專案');
        window.location.href = '1_劇本構思.html';
        return;
    }

    lockUI();
    showStatus('loading', '正在啟動漫畫生成任務...');

    try {
        const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/panels/generate-all`, { method: 'POST' });
        if (!res.ok) throw new Error('請求失敗');
        startPolling();
    } catch (e) {
        unlockUI();
        showStatus('error', '觸發生成失敗：' + e.message);
    }
}

// ===== 長輪詢 =====
let isPolling = false;
let stopPollingFlag = false;
let lastMtime = 0;

async function pollPanels() {
    if (isPolling) return;
    isPolling = true;
    stopPollingFlag = false;
    let prevGeneratingPage = -1;

    while (!stopPollingFlag) {
        try {
            const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/panels/wait?last_mtime=${lastMtime}`);
            const json = await res.json();
            if (!json.success) { await new Promise(r => setTimeout(r, 2000)); continue; }
            if (json.mtime !== undefined) lastMtime = json.mtime;
            const data = json.data;

            if (data.status === 'generating_panels') {
                const genPage = data.currentGeneratingPage ?? 0;

                // 偵測換頁：完整重置畫面，丟棄本次可能含舊頁資料的 response，
                // 重設 lastMtime 讓下一輪 poll 取得新頁的全新資料
                if (genPage !== prevGeneratingPage) {
                    if (prevGeneratingPage >= 0) {
                        pageGenerationStatus[prevGeneratingPage] = 'completed';
                    }
                    currentViewPage = genPage;
                    resetPageView();
                    lastMtime = 0;
                    prevGeneratingPage = genPage;
                    pageGenerationStatus[genPage] = 'generating';
                    renderPageNav();

                    const pageLabel = totalPages > 1 ? `（第 ${genPage + 1}/${totalPages} 頁）` : '';
                    showStatus('loading', `漫畫正在生成中${pageLabel}，請稍待...`);
                    continue; // 跳過本次渲染，等下一輪取到新頁資料再畫
                }

                pageGenerationStatus[genPage] = 'generating';
                prevGeneratingPage = genPage;

                const pageLabel = totalPages > 1 ? `（第 ${genPage + 1}/${totalPages} 頁）` : '';
                showStatus('loading', `漫畫正在生成中${pageLabel}，請稍待...`, {
                    totalProgress: data.totalProgress,
                    completedPanels: data.completedPanels,
                    totalPanels: data.totalPanels
                });

                renderGenerationPreview(data);
                renderPageNav();
            } else if (data.status === 'generation_interrupted') {
                stopPollingFlag = true;
                isPolling = false;
                showStatus('loading', '後端已重新連線，正在從未完成的分鏡自動接續...');
                await triggerGenerate();
                return;
            } else if (data.status === 'panels_completed') {
                stopPollingFlag = true;
                unlockUI();
                resetPageView();
                showStatus('success', totalPages > 1 ? `全部 ${totalPages} 頁漫畫生成已完成！` : '漫畫生成已完成！');
                for (let i = 0; i < totalPages; i++) pageGenerationStatus[i] = 'completed';
                currentViewPage = 0;
                renderPageNav();
                renderFinalImage();
            } else if (data.status === 'failed') {
                stopPollingFlag = true;
                unlockUI();
                showStatus('error', '生成失敗：' + (data.error || '未知錯誤'));
            }
        } catch (e) {
            console.error('輪詢錯誤:', e);
            await new Promise(r => setTimeout(r, 2000));
        }
    }
    isPolling = false;
}

function startPolling() {
    if (window.pollTimer) { clearInterval(window.pollTimer); window.pollTimer = null; }
    stopPollingFlag = false;
    pollPanels();
}

function goToExport() {
    window.location.href = `5_匯出分享.html?projectId=${currentProjectId}`;
}

// ===== 初始化 =====
async function initPage() {
    if (!currentProjectId) return;

    try {
        // 先載入專案資訊判斷多頁
        const projRes = await apiFetch(`${API_BASE}/projects/${currentProjectId}`);
        const projJson = await projRes.json();
        if (projJson.success && projJson.data) {
            const proj = projJson.data;
            totalPages = proj.pageCount || 1;
            if (proj.multiPageScript && proj.multiPageScript.length > 0) {
                totalPages = proj.multiPageScript.length;
            }
            // 載入多頁版面配置
            const layoutPages = proj.layout?.pages;
            if (layoutPages && Array.isArray(layoutPages)) {
                multiPageLayouts = layoutPages.map(p => p.selectedLayoutName);
            }
        }

        renderPageNav();

        const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/panels`);
        const json = await res.json();
        if (!json.success) return;
        const data = json.data;

        if (data.status === 'generating_panels') {
            const genPage = data.currentGeneratingPage ?? 0;
            currentViewPage = genPage;
            for (let i = 0; i < genPage; i++) pageGenerationStatus[i] = 'completed';
            pageGenerationStatus[genPage] = 'generating';
            lockUI();
            renderPageNav();
            showStatus('loading', '漫畫正在生成中，請稍待...', {
                totalProgress: data.totalProgress,
                completedPanels: data.completedPanels,
                totalPanels: data.totalPanels
            });
            renderGenerationPreview(data);
            startPolling();
        } else if (data.status === 'generation_interrupted') {
            showStatus('loading', '偵測到上次生成因後端中斷，正在從未完成的分鏡自動接續...');
            await triggerGenerate();
        } else if (data.status === 'panels_completed') {
            panelData = data;
            for (let i = 0; i < totalPages; i++) pageGenerationStatus[i] = 'completed';
            renderPageNav();
            showStatus('success', totalPages > 1 ? `全部 ${totalPages} 頁漫畫生成已完成！` : '漫畫生成已完成！');
            renderFinalImage();
        } else if (data.status === 'failed') {
            showStatus('error', '生成失敗：' + (data.error || '未知錯誤'));
        } else {
            triggerGenerate();
        }
    } catch (e) { console.error(e); }
}

document.addEventListener('DOMContentLoaded', () => {
    updateNavLinks();
    initPage();
    document.querySelectorAll('button').forEach(btn => {
        if (btn.textContent.includes('全部重新生成')) btn.addEventListener('click', triggerGenerate);
    });
});

async function renderFinalImage() {
    setGenerationHintVisible(true);
    injectPanelSelectStyles();

    let finalDiv = document.getElementById('final-preview-section');
    if (!finalDiv) {
        finalDiv = document.createElement('div');
        finalDiv.id = 'final-preview-section';
        finalDiv.className = 'col-span-12 mt-8 px-6 py-6 bg-surface-container-low rounded-xl border border-outline-variant/30';
        const grid = document.querySelector('.grid.grid-cols-12');
        if (grid) grid.appendChild(finalDiv);
    }

    let panelsInfo = panelData.panels || [];
    let positions = panelData.panelPositions || [];
    let canvasSize = panelData.canvasSize || { w: 768, h: 1086 };

    if (panelsInfo.length === 0 || positions.length === 0) {
        try {
            const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}/panels?page=${currentViewPage}`);
            const json = await res.json();
            if (json.success && json.data) {
                panelData = json.data;
                panelsInfo = json.data.panels || [];
                positions = json.data.panelPositions || [];
                canvasSize = json.data.canvasSize || canvasSize;
            }
        } catch (e) { console.error('獲取分鏡資料失敗:', e); }
    }

    const cw = canvasSize.w, ch = canvasSize.h;
    let panelOverlaysHtml = '';
    positions.forEach((pos, i) => {
        const panel = panelsInfo[i] || {};
        const panelId = panel.id !== undefined ? panel.id : i;
        const left = (pos.x / cw * 100).toFixed(2), top = (pos.y / ch * 100).toFixed(2);
        const width = (pos.w / cw * 100).toFixed(2), height = (pos.h / ch * 100).toFixed(2);
        const maskUrl = `${API_BASE}/projects/${currentProjectId}/masks/${i}?page=${currentViewPage}`;
        panelOverlaysHtml += `<div class="panel-clickable" data-panel-id="${panelId}" style="left:${left}%;top:${top}%;width:${width}%;height:${height}%;-webkit-mask-image:url('${maskUrl}');mask-image:url('${maskUrl}');"></div>`;
    });

    preloadPanelMasks(positions.length);

    finalDiv.innerHTML = `
        <div class="flex justify-between items-center mb-6">
            <div>
                <h3 class="text-xl font-bold font-headline text-slate-800">最終漫畫預覽${totalPages > 1 ? ` (第 ${currentViewPage + 1}/${totalPages} 頁)` : ''}</h3>
                <p class="text-xs text-slate-500 mt-1">點擊任一格分鏡即可編輯提示詞並重新生成</p>
            </div>
            <button onclick="goToExport()" class="px-5 py-2.5 bg-indigo-600 text-white rounded-lg text-sm font-bold shadow hover:bg-indigo-700 transition flex items-center gap-2">
                <span class="material-symbols-outlined pointer-events-none" style="font-variation-settings: 'FILL' 1;">ios_share</span> 前往導出預覽
            </button>
        </div>
        <div class="w-full bg-slate-200 rounded-lg overflow-hidden flex justify-center p-4 min-h-[400px]">
            <div style="position:relative;max-width:100%;width:fit-content;">
                <img src="${API_BASE}/projects/${currentProjectId}/export/image?page=${currentViewPage}&t=${Date.now()}" alt="最終成果"
                     class="max-w-full lg:max-w-4xl h-auto object-contain comic-shadow bg-white" style="display:block;">
                <div style="position:absolute;inset:0;">${panelOverlaysHtml}</div>
            </div>
        </div>
    `;

    deselectPanel();
    const canvasContainer = finalDiv.querySelector('[style*="position:relative"]');
    if (canvasContainer) setupCanvasClickHandler(canvasContainer);
}

