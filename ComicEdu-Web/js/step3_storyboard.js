// 分鏡配置頁面 - 支援多頁版面選擇
// 多頁模式：每頁各自推薦並選擇版面，全部選完才能進入下一步

let selectedLayouts = {}; // { pageIndex: layoutName }
let totalPages = 1;
let currentEditingPage = 0;
let allPagesRecommended = {}; // { pageIndex: [layouts] }
let projectData = null;
let layoutRecommendationsReady = false;

function updateNextButtonState() {
    const button = document.getElementById('btnNext');
    if (!button) return;
    const allSelected = layoutRecommendationsReady && Object.keys(selectedLayouts).length >= totalPages;
    button.disabled = !allSelected;
    button.title = allSelected ? '' : (layoutRecommendationsReady ? '請先為所有頁面選擇版面' : '請等待 AI 完成版面推薦');
}

function setRegenButtonBusy(busy) {
    const button = document.getElementById('btnRegenLayouts');
    if (!button) return;
    button.disabled = busy;
    button.classList.toggle('opacity-50', busy);
    button.classList.toggle('cursor-not-allowed', busy);
    button.setAttribute('aria-busy', String(busy));
}
function getProjectId() {
    return new URLSearchParams(window.location.search).get('projectId');
}

function updateNavLinks(projectId) {
    if (!projectId) return;
    const navLinks = document.querySelectorAll('aside nav a');
    const pages = ['1_劇本構思.html', '2_角色設定.html', '3_分鏡配置.html', '4_AI生圖.html', '5_匯出分享.html'];
    navLinks.forEach((link, index) => {
        if (index < pages.length) link.href = `./${pages[index]}?projectId=${projectId}`;
    });
}

// 觸發版面推薦
async function triggerLayoutSelection(projectId) {
    setRegenButtonBusy(true);
    layoutRecommendationsReady = false;
    updateNextButtonState();
    try {
        const resp = await apiFetch(`${API_BASE}/projects/${projectId}/layouts/generate`, { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            pollForLayouts(projectId);
        } else {
            showError('API 返回錯誤');
        }
    } catch (error) {
        showError('無法觸發 AI 版面選擇，請確認伺服器是否正常運作。');
    }
}

function pollForLayouts(projectId) {
    let pollCount = 0;
    const maxPolls = 60;
    const interval = setInterval(async () => {
        pollCount++;
        try {
            const resp = await apiFetch(`${API_BASE}/projects/${projectId}`);
            const data = await resp.json();
            const project = data.data;
            projectData = project;

            if (project.status === 'layouts_selected') {
                clearInterval(interval);
                setRegenButtonBusy(false);
                const layouts = project.recommended_layouts || [];
                const perPage = project.perPageRecommendedLayouts || null;
                handleLayoutsReady(layouts, perPage);
            } else if (project.status === 'failed') {
                clearInterval(interval);
                setRegenButtonBusy(false);
                showError(project.error || 'AI 版面選擇失敗');
            } else if (pollCount > maxPolls) {
                clearInterval(interval);
                setRegenButtonBusy(false);
                showError('AI 版面選擇超時');
            }
        } catch (error) {
            clearInterval(interval);
            setRegenButtonBusy(false);
            showError('與伺服器通訊失敗');
        }
    }, 2000);
}

function handleLayoutsReady(layouts, perPageLayouts) {
    setRegenButtonBusy(false);
    layoutRecommendationsReady = true;
    if (totalPages <= 1) {
        allPagesRecommended[0] = layouts;
        renderSinglePageMode(layouts);
    } else {
        // 多頁模式：使用逐頁推薦結果（若有）
        if (perPageLayouts && perPageLayouts.length === totalPages) {
            for (let i = 0; i < totalPages; i++) {
                allPagesRecommended[i] = perPageLayouts[i] || [];
            }
        } else {
            for (let i = 0; i < totalPages; i++) {
                allPagesRecommended[i] = layouts;
            }
        }
        renderMultiPageMode();
    }
    renderAiSummary(allPagesRecommended[currentEditingPage] || layouts);
    updateNextButtonState();
}

// ===== 單頁模式 =====
function renderSinglePageMode(layouts) {
    renderLayoutCards(layouts, 0);
}

// ===== 多頁模式 =====
function renderMultiPageMode() {
    const grid = document.getElementById('layoutGrid');

    // 頁碼導覽
    let html = `<div class="col-span-full mb-6">
        <div class="flex items-center justify-between mb-4 pb-3 border-b border-slate-200">
            <div class="flex items-center gap-2">
                <span class="material-symbols-outlined text-[#4343d5]">auto_stories</span>
                <span class="text-sm font-bold text-slate-700">共 ${totalPages} 頁，請為每頁選擇版面</span>
            </div>
            <div class="flex items-center gap-1" id="page-selector">
                ${Array.from({length: totalPages}, (_, i) => {
                    const selected = selectedLayouts[i] ? 'bg-green-500 text-white' : (i === currentEditingPage ? 'bg-[#4343d5] text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200');
                    const icon = selectedLayouts[i] ? '<span class="material-symbols-outlined text-xs">check</span>' : (i + 1);
                    return `<button onclick="switchPage(${i})" class="w-9 h-9 rounded-full text-xs font-bold transition-all ${selected} flex items-center justify-center">${icon}</button>`;
                }).join('')}
            </div>
        </div>
        <div class="flex items-center gap-3">
            <button onclick="switchPage(currentEditingPage - 1)" class="p-2 rounded-lg hover:bg-slate-100 transition-colors ${currentEditingPage === 0 ? 'opacity-30 cursor-not-allowed' : ''}" ${currentEditingPage === 0 ? 'disabled' : ''}>
                <span class="material-symbols-outlined">chevron_left</span>
            </button>
            <span class="text-sm font-bold text-[#4343d5]">第 ${currentEditingPage + 1} 頁版面選擇</span>
            ${selectedLayouts[currentEditingPage] ? `<span class="text-xs bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-bold">已選：${selectedLayouts[currentEditingPage].replace('.png','')}</span>` : ''}
            <button onclick="switchPage(currentEditingPage + 1)" class="p-2 rounded-lg hover:bg-slate-100 transition-colors ${currentEditingPage === totalPages - 1 ? 'opacity-30 cursor-not-allowed' : ''}" ${currentEditingPage === totalPages - 1 ? 'disabled' : ''}>
                <span class="material-symbols-outlined">chevron_right</span>
            </button>
        </div>
    </div>`;

    // 版面卡片
    const layouts = allPagesRecommended[currentEditingPage] || [];
    html += layouts.map((layout, index) => {
        const isSelected = selectedLayouts[currentEditingPage] === layout.layout_name;
        return `
        <div class="layout-card group bg-white p-5 rounded-xl comic-panel-shadow transition-all cursor-pointer border-2 ${isSelected ? 'border-[#4343d5] bg-[#4343d5]/5 ring-2 ring-[#4343d5]/20' : 'border-transparent'} hover:border-[#4343d5]/30 hover:shadow-lg"
             data-layout-name="${layout.layout_name}"
             onclick="selectLayoutForPage(${currentEditingPage}, '${layout.layout_name}', this)">
            <div class="flex justify-between items-center mb-3">
                <div class="flex items-center gap-2">
                    <span class="w-7 h-7 rounded-full bg-[#4343d5] text-white text-xs font-bold flex items-center justify-center">${index + 1}</span>
                    <h4 class="font-headline font-bold text-base text-slate-800">${layout.layout_name.replace('.png', '')}</h4>
                </div>
                ${isSelected ? '<span class="text-xs bg-[#4343d5] text-white px-2 py-1 rounded-full font-bold">已選擇</span>' : '<span class="text-xs bg-[#4343d5]/10 text-[#4343d5] px-2 py-1 rounded-full font-bold">AI 推薦</span>'}
            </div>
            <div class="aspect-[4/3] bg-slate-50 rounded-lg overflow-hidden border border-slate-200 mb-3">
                <img src="${apiAssetUrl(`/api/v1/layouts/images/${encodeURIComponent(layout.layout_name)}`)}"
                     alt="${layout.layout_name}" class="w-full h-full object-contain" loading="lazy" />
            </div>
            <div class="bg-slate-50 rounded-lg p-3">
                <p class="text-xs text-slate-600 leading-relaxed">
                    <span class="font-bold text-[#4343d5]">推薦理由：</span>${layout.reason}
                </p>
            </div>
        </div>`;
    }).join('');

    // 底部完成狀態
    const allSelected = Object.keys(selectedLayouts).length === totalPages;
    html += `<div class="col-span-full mt-6 p-4 rounded-xl border ${allSelected ? 'bg-green-50 border-green-200' : 'bg-amber-50 border-amber-200'}">
        <div class="flex items-center gap-2">
            <span class="material-symbols-outlined ${allSelected ? 'text-green-600' : 'text-amber-600'}">${allSelected ? 'check_circle' : 'info'}</span>
            <span class="text-sm font-bold ${allSelected ? 'text-green-800' : 'text-amber-800'}">
                ${allSelected ? '所有頁面已選擇版面，可以進入下一步！' : `還有 ${totalPages - Object.keys(selectedLayouts).length} 頁尚未選擇版面`}
            </span>
        </div>
    </div>`;

    grid.innerHTML = html;
}

function switchPage(pageIdx) {
    if (pageIdx < 0 || pageIdx >= totalPages) return;
    currentEditingPage = pageIdx;
    renderMultiPageMode();
    renderAiSummary(allPagesRecommended[currentEditingPage] || []);
}

function selectLayoutForPage(pageIdx, layoutName, el) {
    selectedLayouts[pageIdx] = layoutName;
    updateNextButtonState();

    if (totalPages > 1) {
        renderMultiPageMode();
        // 自動跳到下一頁（如果還有未選的）
        if (pageIdx < totalPages - 1 && !selectedLayouts[pageIdx + 1]) {
            setTimeout(() => switchPage(pageIdx + 1), 300);
        }
    } else {
        selectLayout(el, layoutName);
    }
}

// 原始單頁選擇（保留向下相容）
function selectLayout(el, layoutName) {
    selectedLayouts[0] = layoutName;
    updateNextButtonState();
    document.querySelectorAll('.layout-card').forEach(card => {
        card.classList.remove('border-[#4343d5]', 'bg-[#4343d5]/5', 'ring-2', 'ring-[#4343d5]/20');
        card.classList.add('border-transparent');
    });
    el.classList.remove('border-transparent');
    el.classList.add('border-[#4343d5]', 'bg-[#4343d5]/5', 'ring-2', 'ring-[#4343d5]/20');
}

function renderLayoutCards(layouts, pageIdx) {
    const grid = document.getElementById('layoutGrid');
    if (!layouts || layouts.length === 0) {
        grid.innerHTML = `<div class="col-span-full text-center py-20">
            <span class="material-symbols-outlined text-4xl text-slate-300 mb-4">sentiment_dissatisfied</span>
            <p class="text-slate-500 font-semibold">AI 未找到適合的版面，請嘗試重新推薦。</p>
        </div>`;
        return;
    }
    grid.innerHTML = layouts.map((layout, index) => `
        <div class="layout-card group bg-white p-5 rounded-xl comic-panel-shadow transition-all cursor-pointer border-2 border-transparent hover:border-[#4343d5]/30 hover:shadow-lg"
             data-layout-name="${layout.layout_name}"
             onclick="selectLayoutForPage(${pageIdx}, '${layout.layout_name}', this)">
            <div class="flex justify-between items-center mb-3">
                <div class="flex items-center gap-2">
                    <span class="w-7 h-7 rounded-full bg-[#4343d5] text-white text-xs font-bold flex items-center justify-center">${index + 1}</span>
                    <h4 class="font-headline font-bold text-base text-slate-800">${layout.layout_name.replace('.png', '')}</h4>
                </div>
                <span class="text-xs bg-[#4343d5]/10 text-[#4343d5] px-2 py-1 rounded-full font-bold">AI 推薦</span>
            </div>
            <div class="aspect-[4/3] bg-slate-50 rounded-lg overflow-hidden border border-slate-200 mb-3">
                <img src="${apiAssetUrl(`/api/v1/layouts/images/${encodeURIComponent(layout.layout_name)}`)}"
                     alt="${layout.layout_name}" class="w-full h-full object-contain" loading="lazy" />
            </div>
            <div class="bg-slate-50 rounded-lg p-3">
                <p class="text-xs text-slate-600 leading-relaxed">
                    <span class="font-bold text-[#4343d5]">推薦理由：</span>${layout.reason}
                </p>
            </div>
        </div>
    `).join('');
}

function renderAiSummary(layouts) {
    const panel = document.getElementById('aiSummaryPanel');
    if (!layouts || layouts.length === 0) {
        panel.innerHTML = '<p class="text-sm text-slate-500">無推薦結果。</p>';
        return;
    }
    let summaryHtml = `<h4 class="text-xs font-bold text-slate-400 uppercase tracking-widest">AI 精選了 ${layouts.length} 個版面</h4>`;
    if (totalPages > 1) {
        summaryHtml += `<div class="bg-indigo-50 p-3 rounded-lg border border-indigo-100 mb-3">
            <p class="text-xs text-indigo-700 font-bold">多頁模式</p>
            <p class="text-xs text-indigo-600">共 ${totalPages} 頁，每頁可選擇不同版面。已選 ${Object.keys(selectedLayouts).length}/${totalPages} 頁。</p>
        </div>`;
    }
    summaryHtml += `<div class="space-y-3">
        ${layouts.map((layout, i) => `
            <div class="bg-secondary-container/10 border border-secondary-container/20 rounded-lg p-3 cursor-pointer hover:bg-secondary-container/20 transition-colors"
                 onclick="document.querySelector('[data-layout-name=\\'${layout.layout_name}\\']')?.click()">
                <p class="text-xs font-bold text-slate-700 mb-1">${i + 1}. ${layout.layout_name.replace('.png', '')}</p>
                <p class="text-xs text-slate-500 leading-relaxed">${layout.reason}</p>
            </div>
        `).join('')}
    </div>`;
    panel.innerHTML = summaryHtml;
}

function showError(msg) {
    setRegenButtonBusy(false);
    const grid = document.getElementById('layoutGrid');
    grid.innerHTML = `<div class="col-span-full text-center py-20">
        <span class="material-symbols-outlined text-4xl text-red-300 mb-4">error</span>
        <p class="text-red-500 font-semibold mb-4">${msg}</p>
        <button onclick="location.reload()" class="px-4 py-2 bg-primary text-on-primary rounded-lg text-sm font-semibold hover:opacity-90">重試</button>
    </div>`;
}

async function goToNext() {
    const projectId = getProjectId();
    if (!projectId) return;
    if (!layoutRecommendationsReady || Object.keys(selectedLayouts).length < totalPages) return;

    if (totalPages > 1) {
        // 多頁模式：檢查所有頁面都已選擇
        if (Object.keys(selectedLayouts).length < totalPages) {
            alert(`請先為所有 ${totalPages} 頁選擇版面再繼續。還有 ${totalPages - Object.keys(selectedLayouts).length} 頁尚未選擇。`);
            return;
        }
        // 儲存多頁版面配置
        const pages = [];
        for (let i = 0; i < totalPages; i++) {
            pages.push({
                pageIndex: i,
                selectedLayoutName: selectedLayouts[i]
            });
        }
        try {
            await apiFetch(`${API_BASE}/projects/${projectId}/storyboard/multi-page`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pages })
            });
            // 也儲存到舊格式以向下相容（第一頁作為主版面）
            await apiFetch(`${API_BASE}/projects/${projectId}/storyboard`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ selectedLayoutName: selectedLayouts[0], pages })
            });
        } catch (error) {
            alert('儲存版面時發生錯誤。');
            return;
        }
    } else {
        // 單頁模式
        if (!selectedLayouts[0]) {
            alert('請先選擇一個版面再繼續。');
            return;
        }
        try {
            await apiFetch(`${API_BASE}/projects/${projectId}/storyboard`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ selectedLayoutName: selectedLayouts[0] })
            });
        } catch (error) {
            alert('儲存版面時發生錯誤。');
            return;
        }
    }

    window.location.href = `./4_AI生圖.html?projectId=${projectId}`;
}

// 頁面初始化
document.addEventListener('DOMContentLoaded', () => {
    const projectId = getProjectId();
    if (!projectId) {
        showError('缺少專案 ID，請從劇本構思步驟開始。');
        return;
    }

    updateNavLinks(projectId);
    setRegenButtonBusy(true);
    updateNextButtonState();

    document.getElementById('btnRegenLayouts')?.addEventListener('click', () => {
        const button = document.getElementById('btnRegenLayouts');
        if (button?.disabled) return;
        setRegenButtonBusy(true);
        const grid = document.getElementById('layoutGrid');
        grid.innerHTML = `<div class="col-span-full flex flex-col items-center justify-center py-20 gap-4">
            <div class="w-12 h-12 border-4 border-primary/20 border-t-primary rounded-full animate-spin"></div>
            <p class="text-sm text-slate-500 font-semibold">AI 正在重新推薦版面...</p>
        </div>`;
        selectedLayouts = {};
        layoutRecommendationsReady = false;
        updateNextButtonState();
        triggerLayoutSelection(projectId);
    });

    document.getElementById('btnNext')?.addEventListener('click', goToNext);

    // 載入專案並判斷頁數
    apiFetch(`${API_BASE}/projects/${projectId}`)
        .then(resp => resp.json())
        .then(data => {
            const project = data.data;
            projectData = project;

            // 判斷多頁模式
            totalPages = project.pageCount || 1;
            if (project.multiPageScript && project.multiPageScript.length > 0) {
                totalPages = project.multiPageScript.length;
            }
            (project.layout?.pages || []).forEach(item => {
                if (item.selectedLayoutName) selectedLayouts[item.pageIndex] = item.selectedLayoutName;
            });

            if (project.recommended_layouts && project.recommended_layouts.length > 0) {
                handleLayoutsReady(project.recommended_layouts, project.perPageRecommendedLayouts || null);
            } else if (project.status === 'selecting_layouts') {
                pollForLayouts(projectId);
            } else if (project.script && project.script.length > 0) {
                triggerLayoutSelection(projectId);
            } else {
                showError('尚未生成劇本，請先回到劇本構思步驟。');
            }
        })
        .catch(err => {
            showError('無法取得專案狀態');
        });
});

