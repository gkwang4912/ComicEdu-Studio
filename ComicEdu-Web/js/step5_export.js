const currentProjectId = new URLSearchParams(window.location.search).get('projectId');
let exportPages = [];
let currentExportPage = 0;

function centerExportOptions() {
    const panel = document.getElementById('export-options-panel');
    const mainContent = document.querySelector('main > div');
    if (!panel || !mainContent) return;
    panel.className = 'w-full max-w-2xl mx-auto mt-8 bg-white rounded-2xl border border-slate-200 p-6 shadow-sm';
    mainContent.appendChild(panel);
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

async function loadProjectExport() {
    if (!currentProjectId) return;
    try {
        const res = await apiFetch(`${API_BASE}/projects/${currentProjectId}`);
        const json = await res.json();
        if (!json.success) return;
        const proj = json.data;
        // 更新頁面標題
        const h1 = document.querySelector('main header h1');
        if (h1 && proj.settings) {
            h1.textContent = `成果預覽：${proj.settings.topic || '我的教材'}`;
        }
        exportPages = Array.isArray(proj.exportPages) && proj.exportPages.length > 0
            ? proj.exportPages
            : (proj.export ? [proj.export] : []);
        // 若有 export 路徑，顯示資訊
        if (exportPages.length > 0) {
            const container = document.getElementById('final-image-container');
            if (container) {
                container.className = "relative w-full max-w-4xl bg-slate-100 rounded-xl overflow-hidden flex justify-center p-6 border border-outline-variant/30";
                renderExportPage(container);
            }
            const statusBar = document.querySelector('.animate-pulse-slow');
            if (statusBar) {
                statusBar.innerHTML = `<span class="material-symbols-outlined text-indigo-600" style="font-variation-settings: 'FILL' 1;">check_circle</span><span class="text-sm font-semibold text-indigo-900">教材生成成功！您可以預覽並導出您的作品。</span>`;
            }
        }
        if (proj.status === 'failed') {
            const statusBar = document.querySelector('.animate-pulse-slow');
            if (statusBar) {
                statusBar.className = statusBar.className.replace('indigo-600/5', 'error/5').replace('indigo-100', 'error/20');
                statusBar.innerHTML = `<span class="material-symbols-outlined text-error">error</span><span class="text-sm font-semibold text-error">生成失敗：${proj.error || '未知錯誤'}</span>`;
            }
        }
    } catch (e) { console.error(e); }
}

function renderExportPage(container) {
    container.innerHTML = `
        <div class="w-full flex flex-col items-center gap-4">
            <div class="flex items-center justify-between w-full max-w-3xl">
                <button type="button" onclick="switchExportPage(-1)" class="px-3 py-2 rounded-lg bg-white border border-slate-200 text-sm font-semibold disabled:opacity-30" ${currentExportPage === 0 ? 'disabled' : ''}>上一頁</button>
                <span class="text-sm font-bold text-slate-700">第 ${currentExportPage + 1} / ${exportPages.length} 頁</span>
                <button type="button" onclick="switchExportPage(1)" class="px-3 py-2 rounded-lg bg-white border border-slate-200 text-sm font-semibold disabled:opacity-30" ${currentExportPage === exportPages.length - 1 ? 'disabled' : ''}>下一頁</button>
            </div>
            <img src="${API_BASE}/projects/${currentProjectId}/export/image?page=${currentExportPage}&t=${Date.now()}" alt="第 ${currentExportPage + 1} 頁最終成果" class="max-w-full h-auto comic-shadow border border-slate-300">
        </div>`;
}

function switchExportPage(delta) {
    const next = currentExportPage + delta;
    if (next < 0 || next >= exportPages.length) return;
    currentExportPage = next;
    const container = document.getElementById('final-image-container');
    if (container) renderExportPage(container);
}

function downloadProject(format) {
    if (!currentProjectId || exportPages.length === 0) return;
    const link = document.createElement('a');
    link.href = `${API_BASE}/projects/${currentProjectId}/export?format=${encodeURIComponent(format)}`;
    link.download = '';
    document.body.appendChild(link);
    link.click();
    link.remove();
}

document.addEventListener('DOMContentLoaded', () => {
    centerExportOptions();
    updateNavLinks();
    loadProjectExport();
    document.querySelectorAll('[data-export-format]').forEach(button => {
        button.addEventListener('click', () => downloadProject(button.dataset.exportFormat));
    });
});

