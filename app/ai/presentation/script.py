"""
app/ai/presentation/script.py — клиентский JS шаблона (табы, edit-режим).

Статический ассет — не трогаем при работе с Python-логикой.
"""

JS = """
(function(){
  const tabButtons = [...document.querySelectorAll('.tab-btn')];
  const tabPanels = [...document.querySelectorAll('.tab-panel')];
  const tabsContainer = document.querySelector('.tabs');
  const leftNavBtn = document.querySelector('.tab-nav-btn.left');
  const rightNavBtn = document.querySelector('.tab-nav-btn.right');
  const NAV_ARROW_LEFT = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M10.8 4.2L6 9L10.8 13.8" stroke="#060A0C" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const NAV_ARROW_RIGHT = '<svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M7.2 4.2L12 9L7.2 13.8" stroke="#060A0C" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function showTab(id){
    tabButtons.forEach(btn => btn.classList.toggle('active', btn.dataset.tab === id));
    tabPanels.forEach(panel => panel.classList.toggle('active', panel.id === id));
    try { location.hash = id; } catch(e){}
    scrollTabIntoView(id);
    if(document.body.classList.contains('edit-mode')) setEditableState(true);
  }

  function scrollTabIntoView(tabId){
    if(!tabsContainer) return;
    const activeTab = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
    if(activeTab) activeTab.scrollIntoView({ behavior:'smooth', inline:'center', block:'nearest' });
    setTimeout(updateNavButtons, 250);
  }

  function updateNavButtons(){
    if(!tabsContainer || !leftNavBtn || !rightNavBtn) return;
    const scrollLeft = tabsContainer.scrollLeft;
    const maxScroll = Math.max(tabsContainer.scrollWidth - tabsContainer.clientWidth, 0);
    if(scrollLeft > 10){ leftNavBtn.classList.add('visible'); leftNavBtn.innerHTML = NAV_ARROW_LEFT; }
    else { leftNavBtn.classList.remove('visible'); }
    if(scrollLeft >= maxScroll - 10){
      rightNavBtn.classList.remove('visible');
      rightNavBtn.innerHTML = '<span class="nav-counter"></span><span class="nav-arrow-icon"></span>';
      return;
    }
    const tabs = [...tabsContainer.querySelectorAll('.tab-btn')];
    const containerRight = scrollLeft + tabsContainer.clientWidth;
    let hiddenCount = 0;
    tabs.forEach(tab => {
      const tabRight = tab.offsetLeft + tab.offsetWidth;
      if(tabRight > containerRight + 5) hiddenCount++;
    });
    if(hiddenCount > 0){
      rightNavBtn.classList.add('visible');
      rightNavBtn.innerHTML = `<span class="nav-counter">+${hiddenCount}</span><span class="nav-arrow-icon">${NAV_ARROW_RIGHT}</span>`;
    } else {
      rightNavBtn.classList.remove('visible');
      rightNavBtn.innerHTML = '';
    }
  }

  tabButtons.forEach(btn => btn.addEventListener('click', () => showTab(btn.dataset.tab)));
  if(leftNavBtn) leftNavBtn.addEventListener('click', () => tabsContainer && tabsContainer.scrollBy({ left:-250, behavior:'smooth' }));
  if(rightNavBtn) rightNavBtn.addEventListener('click', () => tabsContainer && tabsContainer.scrollBy({ left:250, behavior:'smooth' }));
  tabsContainer?.addEventListener('scroll', updateNavButtons);

  const start = location.hash ? location.hash.slice(1) : "";
  const initial = tabPanels.some(p => p.id === start) ? start : (tabButtons[0]?.dataset.tab || "");
  if(initial) showTab(initial);

  document.addEventListener('keydown', (e) => {
    if(e.target.isContentEditable) return;
    const idx = tabButtons.findIndex(btn => btn.classList.contains('active'));
    if(idx < 0) return;
    if(e.key === 'ArrowRight'){ e.preventDefault(); showTab(tabButtons[Math.min(idx+1, tabButtons.length-1)].dataset.tab); }
    if(e.key === 'ArrowLeft'){ e.preventDefault(); showTab(tabButtons[Math.max(idx-1, 0)].dataset.tab); }
  });

  document.querySelectorAll('.arrow-btn').forEach(btn => btn.addEventListener('click', (e) => { e.stopPropagation(); const tabId = btn.dataset.tab; if(tabId) showTab(tabId); }));
  document.querySelectorAll('.clickable-row').forEach(row => row.addEventListener('click', () => { const tabId = row.dataset.tab; if(tabId) showTab(tabId); }));

  const viewport = document.querySelector('.viewport');
  function updateScale(){
    if(!viewport) return;
    const baseScale = Math.min(window.innerWidth / 2560, window.innerHeight / 1200, 1);
    viewport.style.transform = `scale(${Math.min(baseScale * 1.06, 1)})`;
  }
  window.addEventListener('resize', updateScale);
  setTimeout(() => { updateScale(); updateNavButtons(); }, 100);

  const editToggle = document.getElementById('editToggle');
  const restoreBtn = document.getElementById('restoreBtn');
  const saveBtn = document.getElementById('saveBtn');
  const panelSnapshots = new Map();
  function activePanel(){ return document.querySelector('.tab-panel.active'); }
  function ensureSnapshot(panel){ if(panel && !panelSnapshots.has(panel.id)) panelSnapshots.set(panel.id, panel.innerHTML); }
  function refreshSectionControls(){
    document.querySelectorAll('[data-section]').forEach(section => {
      if(section.querySelector('.section-controls')) return;
      const box = document.createElement('div');
      box.className = 'section-controls';
      box.innerHTML = '<button class="section-btn" data-act="up">↑</button><button class="section-btn" data-act="down">↓</button><button class="section-btn" data-act="remove">✕</button><button class="section-btn" data-act="img">IMG</button><button class="section-btn" data-act="table">TBL</button>';
      section.appendChild(box);
    });
  }
  function setEditableState(isEditable){ document.querySelectorAll('.editable-text').forEach(el => el.setAttribute('contenteditable', isEditable ? 'true' : 'false')); }
  function moveSection(section, dir){
    const parent = section.parentElement; if(!parent) return;
    if(dir === 'up' && section.previousElementSibling) parent.insertBefore(section, section.previousElementSibling);
    if(dir === 'down' && section.nextElementSibling) parent.insertBefore(section.nextElementSibling, section);
  }
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.section-btn');
    if(!btn) return;
    const section = btn.closest('[data-section]');
    if(!section) return;
    const act = btn.dataset.act;
    if(act === 'remove'){ section.remove(); return; }
    if(act === 'up'){ moveSection(section, 'up'); return; }
    if(act === 'down'){ moveSection(section, 'down'); return; }
    if(act === 'img'){
      const url = prompt('Вставьте URL изображения или data URI');
      if(url){
        const wrap = document.createElement('div');
        wrap.className = 'appendix-image-wrap';
        wrap.innerHTML = `<img class="appendix-image" src="${url}" alt="Добавленное изображение">`;
        section.appendChild(wrap);
      }
      return;
    }
    if(act === 'table'){
      const rows = parseInt(prompt('Количество строк таблицы', '2') || '0', 10);
      const cols = parseInt(prompt('Количество столбцов таблицы', '2') || '0', 10);
      if(rows > 0 && cols > 0){
        let html = '<div class="order-appendix-content"><table class="order-table">';
        for(let r=0; r<rows; r++){
          html += '<tr>';
          for(let c=0; c<cols; c++) html += '<td class="editable-text" contenteditable="true">—</td>';
          html += '</tr>';
        }
        html += '</table></div>';
        section.insertAdjacentHTML('beforeend', html);
        setEditableState(document.body.classList.contains('edit-mode'));
      }
      return;
    }
  });
  if(editToggle){
    editToggle.addEventListener('click', () => {
      const panel = activePanel(); ensureSnapshot(panel);
      const isActive = editToggle.classList.toggle('active');
      document.body.classList.toggle('edit-mode', isActive);
      if(restoreBtn) restoreBtn.style.display = isActive ? 'inline-block' : 'none';
      if(saveBtn) saveBtn.style.display = isActive ? 'inline-block' : 'none';
      if(isActive) refreshSectionControls();
      setEditableState(isActive);
      editToggle.textContent = isActive ? '✓' : 'Ред.';
      document.querySelectorAll('#tab-summary .editable-text').forEach(el => el.setAttribute('contenteditable', isActive ? 'true' : 'false'));
    });
  }
  if(restoreBtn){
    restoreBtn.addEventListener('click', () => {
      const panel = activePanel();
      if(panel && panelSnapshots.has(panel.id)){
        panel.innerHTML = panelSnapshots.get(panel.id);
        refreshSectionControls();
        setEditableState(document.body.classList.contains('edit-mode'));
      }
    });
  }
  if(saveBtn){ saveBtn.addEventListener('click', () => { const panel = activePanel(); if(panel) panelSnapshots.set(panel.id, panel.innerHTML); }); }
})();
"""
