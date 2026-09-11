const knowledgeBooks = [
    { title: '自然', description: '自然主題知識讀本，共四頁。', pages: ['./assets/showcase/主題知識/自然/1-1.png', './assets/showcase/主題知識/自然/1-2.png', './assets/showcase/主題知識/自然/1-3.png', './assets/showcase/主題知識/自然/1-4.png'] },
    { title: '社會', description: '社會主題知識讀本，共四頁。', pages: ['./assets/showcase/主題知識/社會/1.png', './assets/showcase/主題知識/社會/2.png', './assets/showcase/主題知識/社會/3.png', './assets/showcase/主題知識/社會/4.png'] },
    { title: '英文', description: '英文主題知識讀本，共四頁。', pages: ['./assets/showcase/主題知識/英文/1.png', './assets/showcase/主題知識/英文/2.png', './assets/showcase/主題知識/英文/3.png', './assets/showcase/主題知識/英文/4.png'] }
];

const funExplorations = [
    { title: '分數在生活中的應用', image: './assets/showcase/分數在生活中的應用.png', description: '從日常情境探索分數的實際應用。' },
    { title: '古文明的神祕訊息', image: './assets/showcase/古文明的神祕訊息.png', description: '從線索中認識古文明留下的訊息。' },
    { title: '未來新聞編輯室', image: './assets/showcase/未來新聞編輯室.png', description: '用新聞視角想像未來世界。' },
    { title: '光合作用的奧祕', image: './assets/showcase/光合作用的奧秘.png', description: '認識植物如何運用陽光製造養分。' },
    { title: '色彩魔法實驗室', image: './assets/showcase/色彩魔法實驗室.png', description: '觀察色彩混合產生的變化。' },
    { title: '城市裡的老故事', image: './assets/showcase/城市裡的老故事.png', description: '從城市景物發現地方歷史。' },
    { title: '神話與傳說的祕密', image: './assets/showcase/神話與傳說的祕密.png', description: '探索神話與傳說背後的文化想像。' }
];

const showcaseState = { collection: 'knowledge', itemIndex: 0, pageIndex: 0, trigger: null };

function showcaseItems() { return showcaseState.collection === 'knowledge' ? knowledgeBooks : funExplorations; }
function showcaseItem() { return showcaseItems()[showcaseState.itemIndex]; }
function showcasePages(item) { return showcaseState.collection === 'knowledge' ? (item.pages || []) : [item.image].filter(Boolean); }

function artworkImage(source, alt) {
    const image = document.createElement('img');
    image.src = source;
    image.alt = alt;
    return image;
}

function renderShowcaseViewer() {
    const modal = document.getElementById('showcase-modal');
    const item = showcaseItem();
    if (!modal || !item) return;
    const pages = showcasePages(item);
    const isBook = showcaseState.collection === 'knowledge';
    showcaseState.pageIndex = Math.min(showcaseState.pageIndex, Math.max(pages.length - 1, 0));
    modal.classList.toggle('is-book-reader', isBook);
    document.getElementById('showcase-modal-category').textContent = isBook ? '主題知識' : '趣味探索';
    document.getElementById('showcase-modal-counter').textContent = isBook ? '' : `${showcaseState.itemIndex + 1} / ${showcaseItems().length}`;
    document.getElementById('showcase-modal-title').textContent = item.title;
    document.getElementById('showcase-modal-description').textContent = item.description;
    const pageCounter = document.getElementById('showcase-page-counter');
    pageCounter.hidden = !isBook;
    pageCounter.textContent = isBook ? `第 ${showcaseState.pageIndex + 1} / ${pages.length} 頁` : '';

    const media = document.getElementById('showcase-modal-media');
    media.replaceChildren();
    if (isBook) {
        const book = document.createElement('div');
        book.className = 'showcase-reader-book';
        const page = document.createElement('div');
        page.className = 'showcase-reader-page';
        page.appendChild(artworkImage(pages[showcaseState.pageIndex], `${item.title}，第 ${showcaseState.pageIndex + 1} 頁`));
        const spine = document.createElement('span');
        spine.className = 'showcase-reader-spine';
        book.append(page, spine);
        media.appendChild(book);
    } else {
        media.appendChild(artworkImage(pages[0], item.title));
    }

    const previous = document.getElementById('showcase-previous');
    const next = document.getElementById('showcase-next');
    previous.hidden = !isBook;
    next.hidden = !isBook;
    previous.disabled = isBook && showcaseState.pageIndex === 0;
    next.disabled = isBook && showcaseState.pageIndex === pages.length - 1;
    previous.setAttribute('aria-label', isBook ? '上一頁' : '上一個作品');
    next.setAttribute('aria-label', isBook ? '下一頁' : '下一個作品');
}

function openShowcaseViewer(collection, index, trigger) {
    const modal = document.getElementById('showcase-modal');
    if (!modal) return;
    Object.assign(showcaseState, { collection, itemIndex: index, pageIndex: 0, trigger });
    renderShowcaseViewer();
    modal.hidden = false;
    document.body.classList.add('showcase-modal-open');
    document.getElementById('showcase-modal-close')?.focus();
}

function closeShowcaseViewer() {
    const modal = document.getElementById('showcase-modal');
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove('showcase-modal-open');
    showcaseState.trigger?.focus();
}

function moveShowcaseViewer(direction) {
    const item = showcaseItem();
    if (showcaseState.collection !== 'knowledge') return;
    const target = showcaseState.pageIndex + direction;
    if (target < 0 || target >= showcasePages(item).length) return;
    showcaseState.pageIndex = target;
    renderShowcaseViewer();
}

function bindShowcaseViewer() {
    const modal = document.getElementById('showcase-modal');
    if (!modal || modal.dataset.bound === 'true') return;
    modal.dataset.bound = 'true';
    document.getElementById('showcase-modal-close')?.addEventListener('click', closeShowcaseViewer);
    document.getElementById('showcase-previous')?.addEventListener('click', () => moveShowcaseViewer(-1));
    document.getElementById('showcase-next')?.addEventListener('click', () => moveShowcaseViewer(1));
    modal.addEventListener('click', event => { if (event.target.hasAttribute('data-showcase-close')) closeShowcaseViewer(); });
    document.addEventListener('keydown', event => {
        if (modal.hidden) return;
        if (event.key === 'Escape') closeShowcaseViewer();
        if (showcaseState.collection === 'knowledge' && event.key === 'ArrowLeft') { event.preventDefault(); moveShowcaseViewer(-1); }
        if (showcaseState.collection === 'knowledge' && event.key === 'ArrowRight') { event.preventDefault(); moveShowcaseViewer(1); }
    });
}

function loadShowcase() {
    const shelf = document.getElementById('knowledge-books');
    const gallery = document.getElementById('fun-gallery');
    if (!shelf || !gallery) return;
    shelf.innerHTML = knowledgeBooks.map((book, index) => `
        <button type="button" class="showcase-book-card" data-book-index="${index}" aria-label="翻閱主題知識：${escapeHtml(book.title)}">
            <span class="showcase-book-object"><span class="showcase-book-cover"><img src="${book.pages[0]}" alt="${escapeHtml(book.title)}封面" loading="lazy"></span><span class="showcase-book-edges"></span></span>
            <span class="showcase-book-copy"><strong>${escapeHtml(book.title)}</strong><span>${book.pages.length} 頁</span></span>
        </button>`).join('');
    const carouselGroup = duplicate => `<div class="showcase-carousel-group" ${duplicate ? 'aria-hidden="true"' : ''}>
        ${funExplorations.map((item, index) => `
            <button type="button" class="showcase-strip-item" data-fun-index="${index}" aria-label="放大漫畫：${escapeHtml(item.title)}" ${duplicate ? 'tabindex="-1"' : ''}>
                <img src="${item.image}" alt="${escapeHtml(item.title)}" loading="lazy">
            </button>`).join('')}
        </div>`;
    gallery.innerHTML = `<div class="showcase-carousel-track">${carouselGroup(false)}${carouselGroup(true)}</div>`;
    shelf.querySelectorAll('[data-book-index]').forEach(button => button.addEventListener('click', () => openShowcaseViewer('knowledge', Number(button.dataset.bookIndex), button)));
    gallery.querySelectorAll('[data-fun-index]').forEach(button => {
        const open = () => openShowcaseViewer('fun', Number(button.dataset.funIndex), button);
        button.addEventListener('pointerdown', event => { if (event.button === 0) open(); });
        button.addEventListener('click', event => { if (event.detail === 0) open(); });
    });
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
}

function projectRoute(project) {
    const pages = ['1_劇本構思.html', '2_角色設定.html', '3_分鏡配置.html', '4_AI生圖.html', '5_匯出分享.html'];
    const step = Math.max(1, Math.min(5, Number(project.completedStep || 1)));
    return `${pages[step - 1]}?projectId=${encodeURIComponent(project.id)}`;
}

function renderProjectCollection(projects, gridId, countId, completed) {
    const grid = document.getElementById(gridId);
    const count = document.getElementById(countId);
    if (!grid || !count) return;
    count.textContent = `${projects.length} 個專案`;
    if (projects.length === 0) {
        grid.innerHTML = `<div class="sm:col-span-2 min-h-36 rounded-2xl border border-dashed border-slate-300 bg-white/70 flex flex-col items-center justify-center text-center p-6">
            <span class="material-symbols-outlined text-3xl text-slate-300 mb-2">${completed ? 'collections_bookmark' : 'edit_note'}</span>
            <p class="text-sm font-semibold text-slate-600">${completed ? '完成漫畫後會出現在這裡' : '目前沒有進行中的專案'}</p>
        </div>`;
        return;
    }
    grid.innerHTML = projects.map(project => `
        <a href="${projectRoute(project)}" class="group rounded-2xl bg-white border border-slate-200 overflow-hidden hover:border-indigo-300 hover:shadow-lg transition-all">
            ${project.coverUrl ? `<img src="${apiAssetUrl(project.coverUrl)}${project.coverUrl.includes('?') ? '&' : '?'}t=${Date.now()}" alt="${escapeHtml(project.title)}封面" class="w-full h-40 object-cover object-top bg-white">` : `<div class="h-28 bg-slate-100 flex items-center justify-center"><span class="material-symbols-outlined text-4xl text-slate-300">auto_stories</span></div>`}
            <div class="p-4">
                <div class="flex items-start justify-between gap-3"><h3 class="font-bold text-slate-900 leading-snug">${escapeHtml(project.title)}</h3><span class="text-[10px] whitespace-nowrap text-indigo-600 font-bold">${completed ? '已完成' : `步驟 ${project.completedStep}/5`}</span></div>
                <p class="text-xs text-slate-500 mt-2">${escapeHtml(project.subject || '未分類')} · ${escapeHtml(project.gradeLevel || '未設定年級')} · ${project.pageCount} 頁</p>
            </div>
        </a>`).join('');
}

async function loadProjects() {
    try {
        const response = await apiFetch(`${API_BASE}/projects?t=${Date.now()}`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const projects = await response.json();
        if (projects.length === 0 && /(?:^|\/)Projects\.html$/i.test(window.location.pathname)) {
            window.location.replace('1_劇本構思.html');
            return;
        }
        renderProjectCollection(projects.filter(project => !project.completed), 'active-project-grid', 'active-project-count', false);
        renderProjectCollection(projects.filter(project => project.completed), 'completed-project-grid', 'completed-project-count', true);
    } catch (error) {
        console.error('載入專案失敗', error);
        renderProjectCollection([], 'active-project-grid', 'active-project-count', false);
        renderProjectCollection([], 'completed-project-grid', 'completed-project-count', true);
    }
}

// 跳轉到劇本設計頁
function goToCreate() {
    window.location.href = 'Projects.html';
}

function goToNewProject() {
    window.location.href = '1_劇本構思.html';
}

document.addEventListener('DOMContentLoaded', () => {
    bindShowcaseViewer();
    loadShowcase();
    if (document.getElementById('active-project-grid')) loadProjects();
    // 綁定所有「開始創作」按鈕
    document.querySelectorAll('button').forEach(btn => {
        const text = btn.textContent.trim();
        if (text.includes('立即開始創作') || text === '開始創作') {
            btn.addEventListener('click', goToCreate);
        }
    });
});

