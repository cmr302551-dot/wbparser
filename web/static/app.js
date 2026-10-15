/* ═══════════════════════════════════════════════════════════
   WB Automation — Frontend Logic
   ═══════════════════════════════════════════════════════════ */

const App = (() => {

  // ── State ──────────────────────────────────────────────────
  let tasks = [];
  let accounts = [];
  let currentTaskId = null;
  let currentTaskView = 'table';
  let ws = null;
  let runningTaskIds = new Set();
  let probivRunningTaskIds = new Set();
  let broadcastRunningTaskIds = new Set();
  let sellersRefreshTimer = null;
  let stopRequestedIds = new Set();
  let taskLogs = new Map();
  let selectedInns = new Set();
  let isDragging = false;
  let dragHitRow = false;
  let currentSellers = [];
  let _mouseY = 0;
  let _autoScrollRAF = null;
  let _highlightRAF = null;
  let _sellersPage = 0;
  const SELLERS_PER_PAGE = 50;

  // ── WhatsApp section state ──
  let currentSection = 'wb';        // 'wb' | 'whatsapp'
  let waAccounts = [];
  let currentWaId = null;
  let waStatusTimer = null;
  let _waProxyId = null;

  // null = все выбраны; Set = только эти значения выбраны
  let filterSelections = { query: null, date: null, status: null };

  function _saveFilters() {
    if (!currentTaskId) return;
    const s = {};
    for (const [k, v] of Object.entries(filterSelections))
      s[k] = v === null ? null : [...v];
    localStorage.setItem('wb_filters_' + currentTaskId, JSON.stringify(s));
  }

  function _loadFilters(taskId) {
    // Сначала сбрасываем в «все выбраны»
    filterSelections = { query: null, date: null, status: null };
    if (!taskId) return;
    try {
      const raw = localStorage.getItem('wb_filters_' + taskId);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      for (const [k, v] of Object.entries(parsed))
        filterSelections[k] = v === null ? null : new Set(v);
    } catch (_) {}
  }

  const STATUSES = [
    'В ожидании', 'Пробивается', 'Пробит', 'Не найден', 'Отказ', 'Неполадка',
    'Рассылается', 'Отправлено', 'Нет WhatsApp', 'Много номеров',
    'Переведен', 'Думает', 'Купил', 'В прозвоне'
  ];

  const STATUS_COLORS = {
    'В ожидании':  '#52525f',
    'Пробивается': '#fbbf24',
    'Пробит':      '#22d3ee',
    'Не найден':   '#94a3b8',
    'Отказ':       '#f87171',
    'Неполадка':   '#fb923c',
    'Рассылается': '#fbbf24',
    'Отправлено':  '#34d399',
    'Нет WhatsApp':'#94a3b8',
    'Много номеров':'#c084fc',
    'Переведен':   '#38bdf8',
    'Думает':      '#fbbf24',
    'Купил':       '#34d399',
    'В прозвоне':  '#a78bfa',
  };

  // ── Init ───────────────────────────────────────────────────
  async function init() {
    _renderFilterPanel('status', STATUSES);
    await loadTasks();
    if (!currentTaskId) {
      const savedId = localStorage.getItem('wb_last_task');
      if (savedId && tasks.find(t => t.id === savedId)) selectTask(savedId);
    }
    await loadAccounts();

    // Восстанавливаем выбранный раздел
    if (localStorage.getItem('app_section') === 'whatsapp') {
      switchSection('whatsapp');
    }

    connectWS();
    document.addEventListener('mousemove', (e) => { _mouseY = e.clientY; });
    document.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      isDragging = true;
      dragHitRow = !!e.target.closest('#sellers-body tr');
      _startAutoScroll();
    });
    document.addEventListener('mouseup', (e) => {
      if (e.button !== 0) return;
      if (!dragHitRow) {
        selectedInns.clear();
        updateRowHighlights();
      }
      isDragging = false;
      dragHitRow = false;
      _stopAutoScroll();
    });
    document.addEventListener('click', (e) => {
      hideContextMenu();
      if (!e.target.closest('.filter-dropdown')) _closeAllFilterDrops();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { hideContextMenu(); _closeAllFilterDrops(); }
    });
    document.addEventListener('contextmenu', handleContextMenu);
    document.getElementById('rename-modal').addEventListener('click', (e) => {
      if (e.target.id === 'rename-modal') closeRenameModal();
    });
  }

  // ── Auto-scroll on drag ────────────────────────────────────
  function _startAutoScroll() {
    _stopAutoScroll();
    const ZONE = 80;   // px от края экрана до начала скролла
    const SPEED = 14;  // максимальная скорость (px/кадр)
    const main = document.querySelector('.task-view:not(.hidden)') || document.querySelector('.main');
    if (!main) return;
    function frame() {
      if (!isDragging) return;
      const vh = window.innerHeight;
      let speed = 0;
      if (_mouseY > vh - ZONE)
        speed = Math.ceil(SPEED * (_mouseY - (vh - ZONE)) / ZONE);
      else if (_mouseY < ZONE)
        speed = -Math.ceil(SPEED * (ZONE - _mouseY) / ZONE);
      if (speed !== 0) main.scrollTop += speed;
      _autoScrollRAF = requestAnimationFrame(frame);
    }
    _autoScrollRAF = requestAnimationFrame(frame);
  }

  function _stopAutoScroll() {
    if (_autoScrollRAF) { cancelAnimationFrame(_autoScrollRAF); _autoScrollRAF = null; }
  }

  // ── Filter dropdowns ───────────────────────────────────────
  function toggleFilterDrop(key) {
    const panel = document.getElementById(`fdrop-${key}-panel`);
    const btn   = document.querySelector(`#fdrop-${key} .filter-drop-btn`);
    const isOpen = !panel.classList.contains('hidden');
    _closeAllFilterDrops();
    if (!isOpen) {
      panel.classList.remove('hidden');
      btn && btn.classList.add('active');
    }
  }

  function _closeAllFilterDrops() {
    ['query','date','status'].forEach(k => {
      document.getElementById(`fdrop-${k}-panel`)?.classList.add('hidden');
      document.querySelector(`#fdrop-${k} .filter-drop-btn`)?.classList.remove('active');
    });
  }

  function _renderFilterPanel(key, options) {
    const panel = document.getElementById(`fdrop-${key}-panel`);
    if (!panel) return;
    const sel = filterSelections[key]; // null = all checked
    panel.innerHTML = '';
    for (const opt of options) {
      const checked = sel === null || sel.has(opt);
      const label = document.createElement('label');
      label.className = 'filter-drop-item';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = checked;
      cb.addEventListener('change', () => {
        if (filterSelections[key] === null) {
          filterSelections[key] = new Set(options);
          filterSelections[key].delete(opt);
        } else {
          if (cb.checked) filterSelections[key].add(opt);
          else filterSelections[key].delete(opt);
          if (filterSelections[key].size === options.length) filterSelections[key] = null;
        }
        _saveFilters();
        loadSellers();
      });
      label.appendChild(cb);
      label.appendChild(document.createTextNode(' ' + (opt || '—')));
      panel.appendChild(label);
    }
  }

  // ── WebSocket ──────────────────────────────────────────────
  function connectWS() {
    if (ws) ws.close();
    ws = new WebSocket(`ws://${location.host}/ws`);

    ws.onopen = () => setWsStatus(true);
    ws.onclose = () => {
      setWsStatus(false);
      setTimeout(connectWS, 3000);
    };

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'log') {
        appendLog(msg.task_id, msg.text, msg.source || 'parse');
        // Мгновенно обновляем таблицу когда сохранён новый продавец
        if (msg.text.includes('✅ Сохранен')) {
          const task = tasks.find(t => t.id === msg.task_id);
          if (task) { task.sellers_count = (task.sellers_count || 0) + 1; renderTaskList(); }
          if (msg.task_id === currentTaskId) {
            loadSellers();
            loadFilterOptions();
          }
        }
      } else if (msg.type === 'status') {
        handleStatusChange(msg.status, msg.task_id);
      } else if (msg.type === 'seller_update') {
        const s = currentSellers.find(x => x.inn === msg.inn);
        if (s) Object.assign(s, msg.fields);
        _updateSellerRowDOM(msg.inn, msg.fields);
      } else if (msg.type === 'probiv_status') {
        if (msg.status === 'running') probivRunningTaskIds.add(msg.task_id);
        else probivRunningTaskIds.delete(msg.task_id);
        if (msg.task_id === currentTaskId) updateProbivStopButton();
      } else if (msg.type === 'broadcast_status') {
        if (msg.status === 'running') broadcastRunningTaskIds.add(msg.task_id);
        else broadcastRunningTaskIds.delete(msg.task_id);
        if (msg.task_id === currentTaskId) { updateBroadcastStopButton(); loadBroadcastAccounts(); }
      }
    };
  }

  function setWsStatus(connected) {
    const dot = document.getElementById('ws-dot');
    const lbl = document.getElementById('ws-label');
    if (!dot) return;
    dot.className = 'ws-indicator ' + (connected ? 'connected' : 'disconnected');
    lbl.textContent = connected ? 'Подключено' : 'Нет связи';
  }

  function handleStatusChange(status, taskId) {
    if (stopRequestedIds.has(taskId) && status === 'running') return;

    if (status === 'idle') {
      stopRequestedIds.delete(taskId);
      runningTaskIds.delete(taskId);
    } else if (status === 'running') {
      runningTaskIds.add(taskId);
    }

    if (taskId === currentTaskId) updateStartStopButton();
    renderTaskList();

    // Обновляем список задач (счётчики могут измениться)
    if (status === 'idle') {
      loadTasks();
      if (taskId === currentTaskId) loadSellers();
    }

    // Автообновление продавцов, пока есть хотя бы одна работающая задача
    if (runningTaskIds.size > 0) {
      if (!sellersRefreshTimer) {
        sellersRefreshTimer = setInterval(() => {
          if (currentTaskId && runningTaskIds.has(currentTaskId)) loadSellers();
        }, 10000);
      }
    } else {
      clearInterval(sellersRefreshTimer);
      sellersRefreshTimer = null;
    }
  }

  // ── Log ────────────────────────────────────────────────────
  function _logOutputEl(source) {
    if (source === 'probiv') return document.getElementById('log-output-probiv');
    if (source === 'broadcast') return document.getElementById('log-output-broadcast');
    return document.getElementById('log-output-parse');
  }

  function appendLog(taskId, text, source = 'parse') {
    if (!taskLogs.has(taskId)) taskLogs.set(taskId, { parse: [], probiv: [], broadcast: [] });
    const logs = taskLogs.get(taskId);
    const buf = logs[source] || (logs[source] = []);
    buf.push(text);
    while (buf.length > 300) buf.shift();

    if (taskId !== currentTaskId) return;
    const el = _logOutputEl(source);
    if (!el) return;
    const line = document.createElement('div');
    line.textContent = text;
    el.appendChild(line);
    // Лимит 300 строк — удаляем самые старые
    while (el.children.length > 300) el.removeChild(el.firstChild);
    el.scrollTop = el.scrollHeight;
  }

  function _renderLogPanel(taskId) {
    const logs = taskLogs.get(taskId) || { parse: [], probiv: [], broadcast: [] };
    for (const source of ['parse', 'probiv', 'broadcast']) {
      const el = _logOutputEl(source);
      if (!el) continue;
      el.innerHTML = '';
      for (const text of (logs[source] || [])) {
        const line = document.createElement('div');
        line.textContent = text;
        el.appendChild(line);
      }
      el.scrollTop = el.scrollHeight;
    }
  }

  function clearLog(source) {
    if (currentTaskId) {
      const logs = taskLogs.get(currentTaskId);
      if (logs) logs[source] = [];
    }
    const el = _logOutputEl(source);
    if (el) el.innerHTML = '';
  }

  function copyLog(source) {
    const el = _logOutputEl(source);
    if (!el) return;
    const text = Array.from(el.children).map(d => d.textContent).join('\n');
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => showCopyToast('Лог скопирован'));
  }

  // ── Подразделы задачи (Таблица / Управление / Логи) ───────
  function _applyTaskView(view) {
    ['table', 'manage', 'logs'].forEach(v => {
      const el = document.getElementById('task-view-' + v);
      if (el) el.classList.toggle('hidden', v !== view);
    });
    document.querySelectorAll('.view-tab').forEach(tab => {
      tab.classList.toggle('active', tab.dataset.view === view);
    });
    if (view === 'logs') {
      _renderLogPanel(currentTaskId);
    }
  }

  function selectTaskView(view) {
    if (!currentTaskId || view === currentTaskView) return;
    currentTaskView = view;
    _applyTaskView(view);
  }

  // ── Tasks ──────────────────────────────────────────────────
  async function loadTasks() {
    const res = await fetch('/api/tasks');
    tasks = await res.json();
    renderTaskList();
    // Если текущая задача удалена — сбросить
    if (currentTaskId && !tasks.find(t => t.id === currentTaskId)) {
      selectTask(null);
    }
  }

  function renderTaskList() {
    const el = document.getElementById('task-list');
    el.innerHTML = '';
    for (const t of tasks) {
      const isActive = t.id === currentTaskId;

      const div = document.createElement('div');
      div.className = 'task-item' + (isActive ? ' active' : '') + (runningTaskIds.has(t.id) ? ' running' : '');
      div.dataset.taskId = t.id;
      div.innerHTML = `
        ${runningTaskIds.has(t.id) ? '<span class="task-item-running-dot" title="Выполняется"></span>' : ''}
        <span class="task-item-name">${escHtml(t.name)}</span>
        <span class="task-item-count">${t.sellers_count ?? 0}</span>
      `;
      div.onclick = () => selectTask(t.id);
      div.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        showContextMenuAt(e.clientX, e.clientY, [
          { label: 'Переименовать', action: () => renameTask(t.id) },
          { label: 'Удалить',       action: () => deleteTask(t.id), danger: true },
        ]);
      });
      el.appendChild(div);
    }
  }

  async function createTask() {
    const res = await fetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Новая задача', queries: [] })
    });
    const task = await res.json();
    tasks.push(task);
    renderTaskList();
    selectTask(task.id);
  }

  function selectTask(id) {
    currentTaskId = id;
    currentTaskView = 'table';
    if (id) localStorage.setItem('wb_last_task', id);
    else localStorage.removeItem('wb_last_task');
    _loadFilters(id);
    _renderFilterPanel('status',  STATUSES);
    renderTaskList();

    const detail = document.getElementById('task-detail');
    const empty = document.getElementById('empty-state');

    if (!id) {
      detail.classList.add('hidden');
      empty.style.display = '';
      return;
    }

    empty.style.display = 'none';
    detail.classList.remove('hidden');

    const task = tasks.find(t => t.id === id);
    if (!task) return;

    renderQueryRows(task.queries || []);
    probivSelectedAccountIds = new Set(task.probiv_account_ids || []);
    renderProbivAccountList();
    broadcastSelectedAccountIds = new Set(task.broadcast_account_ids || []);
    _loadBroadcastSettingsIntoUI(task);
    loadBroadcastAccounts();
    updateStartStopButton();
    updateProbivStopButton();
    updateBroadcastStopButton();
    _applyTaskView('table');

    // Мгновенно очищаем данные предыдущей задачи — не ждём fetch
    currentSellers = [];
    document.getElementById('sellers-body').innerHTML = '';
    document.getElementById('sellers-count').textContent = '0';
    _renderFilterPanel('query', []);
    _renderFilterPanel('date',  []);

    loadSellers();
    loadFilterOptions();
  }

  async function saveTaskSettings() {
    if (!currentTaskId) return;
    const task = tasks.find(t => t.id === currentTaskId);
    const name = (task && task.name) || 'Без названия';
    const queries = collectQueryRows();

    await fetch(`/api/tasks/${currentTaskId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, queries })
    });

    // Обновляем локально
    if (task) { task.queries = queries; }
    renderTaskList();
  }

  async function deleteTask(id) {
    await fetch(`/api/tasks/${id}`, { method: 'DELETE' });
    tasks = tasks.filter(t => t.id !== id);
    if (currentTaskId === id) selectTask(null);
    renderTaskList();
  }

  let _renameTaskId = null;

  function renameTask(id) {
    const task = tasks.find(t => t.id === id);
    if (!task) return;
    _renameTaskId = id;
    const input = document.getElementById('rename-input');
    if (input) { input.value = task.name; }
    document.getElementById('rename-modal').classList.remove('hidden');
    setTimeout(() => input && input.select(), 30);
  }

  async function confirmRename() {
    const input = document.getElementById('rename-input');
    const newName = (input ? input.value : '').trim();
    if (!newName || !_renameTaskId) { closeRenameModal(); return; }
    const id = _renameTaskId;
    closeRenameModal();
    const task = tasks.find(t => t.id === id);
    if (!task) return;
    task.name = newName;
    await fetch(`/api/tasks/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: task.name, queries: task.queries })
    });
    renderTaskList();
  }

  function closeRenameModal() {
    document.getElementById('rename-modal').classList.add('hidden');
    _renameTaskId = null;
  }

  // ── Query rows ─────────────────────────────────────────────
  function renderQueryRows(queries) {
    const tbody = document.getElementById('queries-body');
    tbody.innerHTML = '';
    for (const q of queries) addQueryRow(q);
  }

  function addQueryRow(data = {}) {
    const tbody = document.getElementById('queries-body');
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="col-query">
        <input type="text" placeholder="массажер для тела" value="${escHtml(data.query || '')}"
               onblur="App.saveTaskSettings()">
      </td>
      <td class="col-num">
        <input type="number" min="1" max="999" value="${data.kolvo_for_query ?? 20}"
               onblur="App.saveTaskSettings()">
      </td>
      <td class="col-num">
        <input type="number" min="1" max="9999" value="${data.stroke_position ?? 1}"
               onblur="App.saveTaskSettings()">
      </td>
      <td class="col-num">
        <input type="number" min="1" max="9999" value="${data.proverka ?? ''}" placeholder="—"
               onblur="App.saveTaskSettings()">
      </td>
      <td class="col-del">
        <button class="btn-ghost btn-del-query" onclick="this.closest('tr').remove(); App.saveTaskSettings()">Удалить запрос</button>
      </td>
    `;
    tbody.appendChild(tr);
  }

  function collectQueryRows() {
    const rows = document.querySelectorAll('#queries-body tr');
    const result = [];
    for (const row of rows) {
      const inputs = row.querySelectorAll('input');
      const query = inputs[0].value.trim();
      if (!query) continue;
      const proverka = inputs[3].value ? parseInt(inputs[3].value) : null;
      result.push({
        query,
        kolvo_for_query: parseInt(inputs[1].value) || 20,
        stroke_position: parseInt(inputs[2].value) || 1,
        proverka
      });
    }
    return result;
  }

  // ── Start / Stop ───────────────────────────────────────────
  function updateStartStopButton() {
    const btn = document.getElementById('btn-start-stop');
    const badge = document.getElementById('task-status-badge');
    if (!btn || !badge) return;

    const isRunning = runningTaskIds.has(currentTaskId);

    if (isRunning) {
      btn.innerHTML = '<span class="btn-icon">■</span> Остановить';
      btn.className = 'btn-stop';
      badge.textContent = 'Работает';
      badge.className = 'badge badge-running';
    } else {
      btn.innerHTML = '<span class="btn-icon">▶</span> Запустить';
      btn.className = 'btn-start';
      badge.textContent = 'Остановлен';
      badge.className = 'badge badge-idle';
    }
  }

  function updateProbivStopButton() {
    const btn = document.getElementById('btn-probiv-stop');
    if (!btn) return;
    btn.disabled = !probivRunningTaskIds.has(currentTaskId);
  }

  function stopProbiv() {
    if (!currentTaskId) return;
    probivRunningTaskIds.delete(currentTaskId);
    updateProbivStopButton();
    fetch('/api/sellers/probiv/stop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: currentTaskId })
    });
  }

  async function toggleTask() {
    if (!currentTaskId) return;

    const isRunning = runningTaskIds.has(currentTaskId);

    if (isRunning) {
      // Остановить — сразу меняем кнопку, блокируем сброс через WS
      stopRequestedIds.add(currentTaskId);
      runningTaskIds.delete(currentTaskId);
      updateStartStopButton();
      renderTaskList();
      fetch(`/api/tasks/${currentTaskId}/stop`, { method: 'POST' });
    } else {
      // Сохраняем настройки только перед запуском
      await saveTaskSettings();
      const res = await fetch(`/api/tasks/${currentTaskId}/start`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json();
        alert(err.error || 'Ошибка запуска');
        return;
      }
      runningTaskIds.add(currentTaskId);
      updateStartStopButton();
      renderTaskList();
    }
  }

  // ── Sellers ────────────────────────────────────────────────
  async function loadSellers() {
    if (!currentTaskId) return;

    const search = (document.getElementById('filter-search')?.value || '').trim().toLowerCase();

    // Загружаем всех продавцов задачи, фильтрация — на клиенте
    const res = await fetch('/api/sellers?' + new URLSearchParams({ task_id: currentTaskId }));
    let sellers = await res.json();

    const { query: qs, date: ds, status: ss } = filterSelections;
    if (qs) sellers = sellers.filter(s => qs.has(s.query   || ''));
    if (ds) sellers = sellers.filter(s => ds.has(s.date    || ''));
    if (ss) sellers = sellers.filter(s => ss.has(s.status  || ''));

    if (search) {
      sellers = sellers.filter(s => {
        const fullName = [s.last_name, s.name, s.surname].filter(Boolean).join(' ').toLowerCase();
        return (s.shop     || '').toLowerCase().includes(search) ||
               (s.last_name|| '').toLowerCase().includes(search) ||
               (s.name     || '').toLowerCase().includes(search) ||
               (s.surname  || '').toLowerCase().includes(search) ||
               fullName.includes(search) ||
               (s.inn    || '').includes(search) ||
               (s.ogrnip || '').includes(search) ||
               (s.phone  || '').includes(search);
      });
    }

    document.getElementById('sellers-count').textContent = sellers.length;
    _sellersPage = 0;
    renderSellers(sellers);
  }

  async function loadFilterOptions() {
    if (!currentTaskId) return;

    const [qRes, dRes] = await Promise.all([
      fetch(`/api/sellers/queries?task_id=${currentTaskId}`),
      fetch(`/api/sellers/dates?task_id=${currentTaskId}`),
    ]);
    const queries = await qRes.json();
    const dates   = await dRes.json();

    // Новые значения автоматически добавляем в выборку
    if (filterSelections.query !== null)
      queries.forEach(q => filterSelections.query.add(q));
    if (filterSelections.date !== null)
      dates.forEach(d => filterSelections.date.add(d));

    _renderFilterPanel('query', queries);
    _renderFilterPanel('date',  dates);
  }

  function resetFilters() {
    document.getElementById('filter-search').value = '';
    filterSelections = { query: null, date: null, status: null };
    _saveFilters();
    _renderFilterPanel('status',  STATUSES);
    loadFilterOptions();
    loadSellers();
  }

  // ── Row selection ──────────────────────────────────────────
  function rowMousedown(e, inn) {
    if (e.button !== 0) return;
    if (e.target.closest('select, input, .phone-cell-outer, button, a, .cell-copy')) return;
    e.preventDefault();
    if (e.ctrlKey) {
      if (selectedInns.has(inn)) selectedInns.delete(inn);
      else selectedInns.add(inn);
    } else {
      selectedInns.clear();
      selectedInns.add(inn);
    }
    dragHitRow = true;
    updateRowHighlights();
  }

  function rowMouseover(inn) {
    if (!isDragging) return;
    selectedInns.add(inn);
    dragHitRow = true;
    updateRowHighlights();
  }

  function updateRowHighlights() {
    if (_highlightRAF) return;
    _highlightRAF = requestAnimationFrame(() => {
      document.querySelectorAll('#sellers-body tr').forEach(tr => {
        tr.classList.toggle('row-selected', selectedInns.has(tr.dataset.inn));
      });
      _highlightRAF = null;
    });
  }

  function _renderPagination(total) {
    let pg = document.getElementById('sellers-pagination');
    if (!pg) {
      pg = document.createElement('div');
      pg.id = 'sellers-pagination';
      pg.className = 'pagination-row';
      const tw = document.querySelector('.table-wrapper');
      if (tw) tw.after(pg);
    }
    if (total <= SELLERS_PER_PAGE) { pg.innerHTML = ''; return; }
    const start = _sellersPage * SELLERS_PER_PAGE;
    const end = Math.min(start + SELLERS_PER_PAGE, total);
    const isFirst = _sellersPage === 0;
    const isLast  = end >= total;
    pg.innerHTML = `
      <button class="btn-ghost-sm" ${isFirst ? 'disabled' : ''} onclick="App.sellersPagePrev()">← Пред.</button>
      <span class="pg-info">${start + 1}–${end} из ${total}</span>
      <button class="btn-ghost-sm" ${isLast ? 'disabled' : ''} onclick="App.sellersPageNext()">След. →</button>
    `;
  }

  function sellersPagePrev() {
    if (_sellersPage > 0) { _sellersPage--; renderSellers(currentSellers); }
  }

  function sellersPageNext() {
    if ((_sellersPage + 1) * SELLERS_PER_PAGE < currentSellers.length) {
      _sellersPage++;
      renderSellers(currentSellers);
    }
  }

  function renderSellers(sellers) {
    currentSellers = sellers;
    const tbody = document.getElementById('sellers-body');

    // Сохраняем телефоны которые редактируются прямо сейчас (ещё не ушли на сервер)
    const pendingPhones = {};
    tbody.querySelectorAll('.cell-phone').forEach(el => {
      const inn = el.dataset.inn;
      const val = el.textContent.trim();
      if (inn && val) pendingPhones[inn] = val;
    });

    tbody.innerHTML = '';

    // Залогиненные WhatsApp-аккаунты — для выпадающего списка «Аккаунт»
    const waLogged = waAccounts.filter(a => a.logged_in)
      .map(a => a.name || a.phone || ('WA#' + a.id));

    // Отображаем только текущую страницу
    const start = _sellersPage * SELLERS_PER_PAGE;
    const pageSlice = sellers.slice(start, start + SELLERS_PER_PAGE);

    for (const s of pageSlice) {
      const phone = s.phone || pendingPhones[s.inn] || '';
      const accCur = s.account || '';
      const accOpts = (accCur && !waLogged.includes(accCur)) ? [accCur, ...waLogged] : waLogged;
      const tr = document.createElement('tr');
      tr.dataset.inn = s.inn;
      tr.addEventListener('mousedown', (e) => rowMousedown(e, s.inn));
      tr.addEventListener('mouseover', () => rowMouseover(s.inn));
      if (selectedInns.has(s.inn)) tr.classList.add('row-selected');

      const statusClass = statusCssClass(s.status);

      tr.innerHTML = `
        <td class="text-muted">${escHtml(s.query || '')}</td>
        <td><span class="cell-copy" onclick="App.copyCell(this)" title="Скопировать">${escHtml([s.name, s.surname].filter(Boolean).join(' ') || '—')}</span></td>
        <td><span class="cell-copy" onclick="App.copyCell(this)" title="Скопировать">${escHtml(s.inn || '—')}</span></td>
        <td><span class="cell-copy" onclick="App.copyCell(this)" title="Скопировать">${escHtml(s.ogrnip || '—')}</span></td>
        <td><span class="cell-copy" onclick="App.copyCell(this)" title="Скопировать">${escHtml(s.shop || '—')}</span></td>
        <td class="link-cell">${s.seller_url
          ? `<a href="${escHtml(s.seller_url)}" target="_blank">Магазин ↗</a>`
          : '<span class="text-muted">—</span>'}</td>
        <td class="link-cell">${s.product_url
          ? `<a href="${escHtml(s.product_url)}" target="_blank">Артикул ↗</a>`
          : '<span class="text-muted">—</span>'}</td>
        <td>
          <div class="phone-cell-outer" onclick="this.querySelector('.cell-phone').focus()">
            <div class="phone-scroll-box">
              <span class="cell-phone" contenteditable="true"
                    data-inn="${escHtml(s.inn)}"
                    onblur="App.updateSeller('${escHtml(s.inn)}', 'phone', this.textContent.trim()); this.closest('.phone-scroll-box').scrollLeft=0;"
                    onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur();}"
              >${escHtml(phone)}</span>
            </div>
          </div>
        </td>
        <td class="${statusClass}">
          <div class="status-wrap">
            <span class="status-dot" style="background:${statusColor(s.status)}"></span>
            <select class="cell-select" data-field="status"
                    onchange="App.updateSeller('${escHtml(s.inn)}','status',this.value);App.applyStatusStyle(this)">
              ${STATUSES.map(st =>
                `<option value="${st}" style="color:${statusColor(st)}" ${st === s.status ? 'selected' : ''}>${st}</option>`
              ).join('')}
            </select>
          </div>
        </td>
        <td>
          <select class="cell-select" data-field="account"
                  onchange="App.updateSeller('${escHtml(s.inn)}', 'account', this.value)">
            <option value="" ${!accCur ? 'selected' : ''}>—</option>
            ${accOpts.map(a =>
              `<option value="${escHtml(a)}" ${a === accCur ? 'selected' : ''}>${escHtml(a)}</option>`
            ).join('')}
          </select>
        </td>
        <td class="text-muted">${escHtml(s.date || '')}</td>
        <td>
          <button class="btn-del-seller"
                  onclick="App.deleteSeller('${escHtml(s.inn)}', this)"
                  title="Удалить">✕</button>
        </td>
      `;

      tbody.appendChild(tr);
    }

    _renderPagination(sellers.length);
  }

  async function updateSeller(inn, field, value) {
    // Телефон — всегда только одна ячейка
    if (field === 'phone') {
      fetch(`/api/sellers/${encodeURIComponent(inn)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: value })
      });
      return;
    }

    // status / account — применяем ко всем выделенным строкам
    const inns = (selectedInns.size > 1 && selectedInns.has(inn))
      ? [...selectedInns]
      : [inn];

    inns.forEach(id =>
      fetch(`/api/sellers/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: value })
      })
    );

    // Мгновенно обновляем UI остальных выделенных строк
    if (inns.length > 1) {
      document.querySelectorAll('#sellers-body tr.row-selected').forEach(tr => {
        if (tr.dataset.inn === inn) return;
        const sel = tr.querySelector(`[data-field="${field}"]`);
        if (!sel) return;
        sel.value = value;
        if (field === 'status') applyStatusStyle(sel);
      });
    }
  }

  async function deleteSeller(inn, btn) {
    const res = await fetch(`/api/sellers/${encodeURIComponent(inn)}`, { method: 'DELETE' });
    if (res.ok) {
      btn.closest('tr').remove();
      currentSellers = currentSellers.filter(s => s.inn !== inn);
      const countEl = document.getElementById('sellers-count');
      if (countEl) countEl.textContent = currentSellers.length;
      const task = tasks.find(t => t.id === currentTaskId);
      if (task) { task.sellers_count = Math.max(0, (task.sellers_count || 1) - 1); renderTaskList(); }
      // Если страница стала пустой — откатиться назад
      const pageStart = _sellersPage * SELLERS_PER_PAGE;
      if (pageStart >= currentSellers.length && _sellersPage > 0) {
        _sellersPage--;
        renderSellers(currentSellers);
      } else {
        _renderPagination(currentSellers.length);
      }
      loadFilterOptions();
    }
  }

  // ── Accounts ───────────────────────────────────────────────

  // tgAccounts — Telegram аккаунты для пробива (с авторизацией)
  // accounts   — простые метки для дропдауна в таблице продавцов
  let tgAccounts = [];

  async function loadAccounts() {
    const [r1, r2] = await Promise.all([
      fetch('/api/accounts'),
      fetch('/api/tg-accounts'),
    ]);
    accounts = await r1.json();
    tgAccounts = await r2.json();
    renderProbivAccountList();
  }

  // ── Пробив селлеров: выбор аккаунтов для текущей задачи ────
  let probivSelectedAccountIds = new Set();

  function toggleProbivAccounts() {
    const body = document.getElementById('probiv-accounts-body');
    const chevron = document.getElementById('probiv-accounts-chevron');
    if (!body) return;
    const isNowCollapsed = body.classList.toggle('collapsed');
    chevron.classList.toggle('collapsed', isNowCollapsed);
  }

  function renderProbivAccountList() {
    const el = document.getElementById('probiv-account-list');
    if (!el) return;
    el.innerHTML = '';
    for (const a of tgAccounts) {
      const selected = probivSelectedAccountIds.has(a.id);
      const nameParts = [a.first_name, a.last_name].filter(Boolean).join(' ');
      const nameHtml   = nameParts ? `<span class="acc-name">${escHtml(nameParts)}</span>` : '';
      const userHtml   = a.username ? `<span class="acc-username">@${escHtml(a.username)}</span>` : '';
      const phoneHtml  = `<span class="acc-phone">${escHtml(a.phone)}</span>`;
      const proxyHtml  = a.proxy ? `<span class="acc-proxy">${escHtml(a.proxy)}</span>` : '';
      const div = document.createElement('div');
      div.className = 'probiv-account-item' + (selected ? ' selected' : '');
      div.innerHTML = `
        <span class="probiv-account-mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>
        </span>
        <div class="acc-info">${nameHtml}${userHtml}${phoneHtml}${proxyHtml}</div>
      `;
      div.onclick = () => toggleProbivAccountSelection(a.id);
      div.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        showContextMenuAt(e.clientX, e.clientY, [
          { label: 'Изменить прокси', action: () => openChangeProxyModal(a.id) },
          { label: 'Удалить',         action: () => removeTgAccount(a.id), danger: true },
        ]);
      });
      el.appendChild(div);
    }
    _updateProbivAccountsSummary();
  }

  function _updateProbivAccountsSummary() {
    const el = document.getElementById('probiv-accounts-summary');
    if (!el) return;
    const n = probivSelectedAccountIds.size;
    el.textContent = n === 0 ? 'Аккаунты не выбраны' : `Выбрано аккаунтов: ${n}`;
  }

  function toggleProbivAccountSelection(id) {
    if (probivSelectedAccountIds.has(id)) probivSelectedAccountIds.delete(id);
    else probivSelectedAccountIds.add(id);
    renderProbivAccountList();
    _saveProbivAccounts();
  }

  async function _saveProbivAccounts() {
    if (!currentTaskId) return;
    const task = tasks.find(t => t.id === currentTaskId);
    if (!task) return;
    task.probiv_account_ids = [...probivSelectedAccountIds];
    await fetch(`/api/tasks/${currentTaskId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: task.name, queries: task.queries, probiv_account_ids: task.probiv_account_ids })
    });
  }

  // ── Рассылка WhatsApp: настройки, аккаунты, запуск ─────────
  let broadcastSelectedAccountIds = new Set();
  const BROADCAST_VARS = ['name','last_name','patronymic','surname','shop','scu','query','inn','ogrnip','phone','seller_url','product_url','date'];
  let _bcSaveTimer = null;

  function _loadBroadcastSettingsIntoUI(task) {
    const msg = document.getElementById('broadcast-message');
    if (msg) msg.value = task.broadcast_message || '';
    const lim = document.getElementById('broadcast-daily-limit');
    if (lim) lim.value = (task.broadcast_daily_limit != null ? task.broadcast_daily_limit : 30);
    const mn = document.getElementById('broadcast-min-delay');
    if (mn) mn.value = (task.broadcast_min_delay != null ? task.broadcast_min_delay : 4);
    const mx = document.getElementById('broadcast-max-delay');
    if (mx) mx.value = (task.broadcast_max_delay != null ? task.broadcast_max_delay : 9);
    _renderBroadcastVars();
    _renderBroadcastMediaName(task.broadcast_media || '');
  }

  function _renderBroadcastVars() {
    const el = document.getElementById('broadcast-vars');
    if (!el) return;
    el.innerHTML = '';
    for (const v of BROADCAST_VARS) {
      const chip = document.createElement('span');
      chip.className = 'broadcast-var-chip';
      chip.textContent = '{' + v + '}';
      chip.onclick = () => _insertBroadcastVar('{' + v + '}');
      el.appendChild(chip);
    }
  }

  function _insertBroadcastVar(token) {
    const ta = document.getElementById('broadcast-message');
    if (!ta) return;
    const s = ta.selectionStart != null ? ta.selectionStart : ta.value.length;
    const e = ta.selectionEnd != null ? ta.selectionEnd : ta.value.length;
    ta.value = ta.value.slice(0, s) + token + ta.value.slice(e);
    ta.focus();
    ta.selectionStart = ta.selectionEnd = s + token.length;
    onBroadcastMessageInput();
  }

  function _renderBroadcastMediaName(path) {
    const el = document.getElementById('broadcast-media-name');
    const clr = document.getElementById('broadcast-media-clear');
    const name = path ? path.split(/[\\/]/).pop() : '';
    if (el) el.textContent = name || 'Без вложения';
    if (clr) clr.classList.toggle('hidden', !path);
  }

  function onBroadcastMessageInput() {
    clearTimeout(_bcSaveTimer);
    _bcSaveTimer = setTimeout(_saveBroadcastSettings, 500);
  }
  function onBroadcastLimitInput() {
    renderBroadcastAccountList();
    _saveBroadcastSettings();
  }

  function toggleBroadcastAccounts() {
    const body = document.getElementById('broadcast-accounts-body');
    const chevron = document.getElementById('broadcast-accounts-chevron');
    if (!body) return;
    const collapsed = body.classList.toggle('collapsed');
    chevron.classList.toggle('collapsed', collapsed);
  }

  async function loadBroadcastAccounts() {
    try {
      const res = await fetch('/api/wa-accounts');
      waAccounts = await res.json();
    } catch (_) { waAccounts = []; }
    renderBroadcastAccountList();
    // Обновить выпадающие списки «Аккаунт» в таблице, когда список WA готов
    if (currentSection === 'wb' && currentSellers.length) renderSellers(currentSellers);
  }

  function renderBroadcastAccountList() {
    const el = document.getElementById('broadcast-account-list');
    if (!el) return;
    const limEl = document.getElementById('broadcast-daily-limit');
    const limit = parseInt(limEl && limEl.value || '0') || 0;
    el.innerHTML = '';
    if (!waAccounts.length) {
      el.innerHTML = '<div class="broadcast-accounts-hint">Нет WhatsApp-аккаунтов. Добавьте в разделе «WhatsApp».</div>';
      _updateBroadcastAccountsSummary();
      return;
    }
    for (const a of waAccounts) {
      const selected = broadcastSelectedAccountIds.has(a.id);
      const sent = a.sent_today || 0;
      const loginHtml = a.logged_in ? '' : '<span class="acc-proxy" style="color:var(--orange)">не авторизован</span>';
      const todayHtml = `<span class="acc-proxy">сегодня: ${sent}${limit ? ' / ' + limit : ''}</span>`;
      const div = document.createElement('div');
      div.className = 'probiv-account-item' + (selected ? ' selected' : '');
      div.innerHTML = `
        <span class="probiv-account-mark">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>
        </span>
        <div class="acc-info">
          <span class="acc-name">${escHtml(a.name || a.phone || 'WhatsApp')}</span>
          ${loginHtml}${todayHtml}
        </div>`;
      div.onclick = () => toggleBroadcastAccountSelection(a.id);
      el.appendChild(div);
    }
    _updateBroadcastAccountsSummary();
  }

  function _updateBroadcastAccountsSummary() {
    const el = document.getElementById('broadcast-accounts-summary');
    if (!el) return;
    const n = broadcastSelectedAccountIds.size;
    el.textContent = n === 0 ? 'Аккаунты не выбраны' : `Выбрано аккаунтов: ${n}`;
  }

  function toggleBroadcastAccountSelection(id) {
    if (broadcastSelectedAccountIds.has(id)) broadcastSelectedAccountIds.delete(id);
    else broadcastSelectedAccountIds.add(id);
    renderBroadcastAccountList();
    _saveBroadcastSettings();
  }

  async function _saveBroadcastSettings() {
    if (!currentTaskId) return;
    const task = tasks.find(t => t.id === currentTaskId);
    if (!task) return;
    const limEl = document.getElementById('broadcast-daily-limit');
    const msgEl = document.getElementById('broadcast-message');
    const mnEl = document.getElementById('broadcast-min-delay');
    const mxEl = document.getElementById('broadcast-max-delay');
    task.broadcast_account_ids = [...broadcastSelectedAccountIds];
    task.broadcast_message = msgEl ? msgEl.value : (task.broadcast_message || '');
    task.broadcast_daily_limit = parseInt(limEl && limEl.value || '0') || 0;
    task.broadcast_min_delay = parseFloat(mnEl && mnEl.value || '0') || 0;
    task.broadcast_max_delay = parseFloat(mxEl && mxEl.value || '0') || 0;
    await fetch(`/api/tasks/${currentTaskId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        broadcast_account_ids: task.broadcast_account_ids,
        broadcast_message: task.broadcast_message,
        broadcast_media: task.broadcast_media || '',
        broadcast_daily_limit: task.broadcast_daily_limit,
        broadcast_min_delay: task.broadcast_min_delay,
        broadcast_max_delay: task.broadcast_max_delay,
      })
    });
  }

  async function onBroadcastMediaChange(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
      const res = await fetch(`/api/broadcast/media?filename=${encodeURIComponent(file.name)}`, {
        method: 'POST', body: file,
      });
      if (!res.ok) { alert('Не удалось загрузить файл'); return; }
      const data = await res.json();
      const task = tasks.find(t => t.id === currentTaskId);
      if (task) task.broadcast_media = data.path;
      _renderBroadcastMediaName(data.path);
      _saveBroadcastSettings();
    } catch (_) {
      alert('Ошибка загрузки файла');
    }
    input.value = '';
  }

  function clearBroadcastMedia() {
    const task = tasks.find(t => t.id === currentTaskId);
    if (task) task.broadcast_media = '';
    _renderBroadcastMediaName('');
    _saveBroadcastSettings();
  }

  function updateBroadcastStopButton() {
    const btn = document.getElementById('btn-broadcast-stop');
    if (!btn) return;
    btn.disabled = !broadcastRunningTaskIds.has(currentTaskId);
  }

  function stopBroadcast() {
    if (!currentTaskId) return;
    broadcastRunningTaskIds.delete(currentTaskId);
    updateBroadcastStopButton();
    fetch('/api/sellers/broadcast/stop', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: currentTaskId })
    });
  }

  async function startBroadcast(inns) {
    const task = tasks.find(t => t.id === currentTaskId);
    if (!task || !(task.broadcast_account_ids || []).length) {
      alert('Сначала выберите WhatsApp-аккаунты для рассылки в разделе «Управление»');
      return;
    }
    if (!(task.broadcast_message || '').trim()) {
      alert('Сначала задайте текст сообщения рассылки в разделе «Управление»');
      return;
    }
    const isRunning = broadcastRunningTaskIds.has(currentTaskId);
    const endpoint = isRunning ? '/api/sellers/broadcast/add' : '/api/sellers/broadcast/start';
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: currentTaskId, inns })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || 'Ошибка запуска рассылки');
      return;
    }
    inns.forEach(inn => _updateSellerRowDOM(inn, { status: 'Рассылается' }));
    inns.forEach(inn => {
      const s = currentSellers.find(x => x.inn === inn);
      if (s) s.status = 'Рассылается';
    });
    if (!isRunning) {
      broadcastRunningTaskIds.add(currentTaskId);
      updateBroadcastStopButton();
    }
  }

  async function removeTgAccount(id) {
    const res = await fetch(`/api/tg-accounts/${id}`, { method: 'DELETE' });
    if (res.ok) {
      const r = await fetch('/api/tg-accounts');
      tgAccounts = await r.json();
      probivSelectedAccountIds.delete(id);
      renderProbivAccountList();
    }
  }

  function openChangeProxyModal(id) {
    _changeProxyAccountId = id;
    document.getElementById('cp-proxy-address').value = '';
    document.getElementById('cp-proxy-port').value = '';
    document.getElementById('cp-proxy-type').value = 'socks5';
    document.getElementById('cp-proxy-user').value = '';
    document.getElementById('cp-proxy-pass').value = '';
    document.getElementById('cp-error').classList.add('hidden');
    document.getElementById('change-proxy-modal').classList.remove('hidden');
    setTimeout(() => document.getElementById('cp-proxy-address').focus(), 50);
  }

  function closeChangeProxyModal() {
    document.getElementById('change-proxy-modal').classList.add('hidden');
    _changeProxyAccountId = null;
  }

  async function submitChangeProxy() {
    const btn    = document.getElementById('cp-submit-btn');
    const errEl  = document.getElementById('cp-error');
    errEl.classList.add('hidden');

    const address = document.getElementById('cp-proxy-address').value.trim();
    const port    = document.getElementById('cp-proxy-port').value.trim();
    const type    = document.getElementById('cp-proxy-type').value;
    const user    = document.getElementById('cp-proxy-user').value.trim();
    const pass    = document.getElementById('cp-proxy-pass').value.trim();

    if (!address || !port) {
      errEl.textContent = 'Укажите адрес и порт прокси';
      errEl.classList.remove('hidden');
      return;
    }

    btn.disabled = true;
    btn.textContent = 'Сохранение...';
    try {
      const res = await fetch(`/api/tg-accounts/${_changeProxyAccountId}/proxy`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          proxy_address: address,
          proxy_port: parseInt(port),
          proxy_type: type,
          proxy_username: user || null,
          proxy_password: pass || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        errEl.textContent = data.error || 'Ошибка';
        errEl.classList.remove('hidden');
        return;
      }
      closeChangeProxyModal();
      tgAccounts = await (await fetch('/api/tg-accounts')).json();
      renderProbivAccountList();
    } catch (e) {
      errEl.textContent = String(e);
      errEl.classList.remove('hidden');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Сохранить';
    }
  }

  // ── Auth modal ─────────────────────────────────────────────

  let _authStep = 1;
  let _authSessionId = null;
  let _changeProxyAccountId = null;

  function openAddAccount() {
    _authStep = 1;
    _authSessionId = null;
    _showAuthStep(1);
    _authSetError('');
    _authSetSubmitLabel('Получить код');
    document.getElementById('auth-modal-title').textContent = 'Добавить аккаунт';
    document.getElementById('auth-phone').value = '';
    document.getElementById('auth-proxy-address').value = '';
    document.getElementById('auth-proxy-port').value = '';
    document.getElementById('auth-proxy-user').value = '';
    document.getElementById('auth-proxy-pass').value = '';
    document.getElementById('auth-proxy-type').value = 'socks5';
    document.getElementById('auth-code').value = '';
    document.getElementById('auth-password').value = '';
    document.getElementById('tg-auth-modal').classList.remove('hidden');
    setTimeout(() => document.getElementById('auth-phone').focus(), 50);
  }

  function closeAuthModal() {
    document.getElementById('tg-auth-modal').classList.add('hidden');
    _authSetSubmitLabel('Получить код', false);
    _authSetError('');
    _authStep = 1;
    _authSessionId = null;
  }

  function _showAuthStep(n) {
    [1, 2, 3].forEach(i => {
      document.getElementById(`auth-step-${i}`).classList.toggle('hidden', i !== n);
    });
    _authStep = n;
  }

  function _authSetError(msg) {
    const el = document.getElementById('auth-error');
    el.textContent = msg;
    el.classList.toggle('hidden', !msg);
  }

  function _authSetSubmitLabel(label, loading = false) {
    const btn = document.getElementById('auth-submit-btn');
    btn.disabled = loading;
    btn.textContent = loading ? '...' : label;
  }

  async function authSubmit() {
    _authSetError('');
    if (_authStep === 1) await _authStep1();
    else if (_authStep === 2) await _authStep2();
    else if (_authStep === 3) await _authStep3();
  }

  async function _authStep1() {
    const phone = document.getElementById('auth-phone').value.trim();
    if (!phone) { _authSetError('Введите номер телефона'); return; }

    const body = {
      phone,
      proxy_address: document.getElementById('auth-proxy-address').value.trim(),
      proxy_port: parseInt(document.getElementById('auth-proxy-port').value) || 0,
      proxy_username: document.getElementById('auth-proxy-user').value.trim(),
      proxy_password: document.getElementById('auth-proxy-pass').value.trim(),
      proxy_type: document.getElementById('auth-proxy-type').value,
    };

    _authSetSubmitLabel('Отправка...', true);
    try {
      const res = await fetch('/api/tg-accounts/start-auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) { _authSetError(data.error || 'Ошибка'); _authSetSubmitLabel('Получить код'); return; }

      if (data.status === 'already_authorized') {
        closeAuthModal();
        await loadAccounts();
        return;
      }

      // code_sent
      _authSessionId = data.session_id;
      _showAuthStep(2);
      _authSetSubmitLabel('Подтвердить');
      document.getElementById('auth-modal-title').textContent = 'Введите код из Telegram';
      setTimeout(() => document.getElementById('auth-code').focus(), 50);
    } catch (e) {
      _authSetError('Ошибка соединения');
      _authSetSubmitLabel('Получить код');
    }
  }

  async function _authStep2() {
    const code = document.getElementById('auth-code').value.trim();
    if (!code) { _authSetError('Введите код'); return; }

    _authSetSubmitLabel('Проверка...', true);
    try {
      const res = await fetch('/api/tg-accounts/confirm-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: _authSessionId, code }),
      });
      const data = await res.json();
      if (!res.ok) { _authSetError(data.error || 'Ошибка'); _authSetSubmitLabel('Подтвердить'); return; }

      if (data.status === 'need_password') {
        _showAuthStep(3);
        _authSetSubmitLabel('Войти');
        document.getElementById('auth-modal-title').textContent = 'Введите облачный пароль (2FA)';
        setTimeout(() => document.getElementById('auth-password').focus(), 50);
        return;
      }

      // ok
      closeAuthModal();
      await loadAccounts();
    } catch (e) {
      _authSetError('Ошибка соединения');
      _authSetSubmitLabel('Подтвердить');
    }
  }

  async function _authStep3() {
    const password = document.getElementById('auth-password').value.trim();
    if (!password) { _authSetError('Введите пароль'); return; }

    _authSetSubmitLabel('Вход...', true);
    try {
      const res = await fetch('/api/tg-accounts/confirm-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: _authSessionId, password }),
      });
      const data = await res.json();
      if (!res.ok) { _authSetError(data.error || 'Ошибка'); _authSetSubmitLabel('Войти'); return; }

      closeAuthModal();
      await loadAccounts();
    } catch (e) {
      _authSetError('Ошибка соединения');
      _authSetSubmitLabel('Войти');
    }
  }

  // ── Context menu ───────────────────────────────────────────

  function showContextMenuAt(x, y, items) {
    const menu = document.getElementById('ctx-menu');
    if (!menu) return;
    menu.innerHTML = '';
    for (const item of items) {
      const div = document.createElement('div');
      div.className = 'ctx-item' + (item.danger ? ' ctx-item-danger' : '');
      div.textContent = item.label;
      div.addEventListener('click', (e) => {
        e.stopPropagation();
        hideContextMenu();
        item.action();
      });
      menu.appendChild(div);
    }
    menu.style.left = x + 'px';
    menu.style.top  = y + 'px';
    menu.classList.add('visible');
    requestAnimationFrame(() => {
      const r = menu.getBoundingClientRect();
      if (r.right  > window.innerWidth)  menu.style.left = (x - r.width)  + 'px';
      if (r.bottom > window.innerHeight) menu.style.top  = (y - r.height) + 'px';
    });
  }

  function hideContextMenu() {
    const menu = document.getElementById('ctx-menu');
    if (menu) menu.classList.remove('visible');
  }

  function handleContextMenu(e) {
    if (!e.target.closest('.table-wrapper')) { hideContextMenu(); return; }
    e.preventDefault();

    const tr = e.target.closest('#sellers-body tr');
    const clickedInn = tr ? tr.dataset.inn : null;

    const innsToDelete = (selectedInns.size > 0 && clickedInn && selectedInns.has(clickedInn))
      ? [...selectedInns]
      : (clickedInn ? [clickedInn] : []);

    const items = [];

    if (selectedInns.size > 0) {
      const phones = [];
      document.querySelectorAll('#sellers-body tr.row-selected .cell-phone').forEach(el => {
        const v = el.textContent.trim();
        if (!v) return;
        v.split(/[,\n]+/).map(p => p.trim()).filter(Boolean).forEach(p => {
          phones.push(p.startsWith('+') ? p : '+' + p);
        });
      });
      if (phones.length) {
        items.push({
          label: `Скопировать телефоны (${phones.length})`,
          action: () => navigator.clipboard.writeText(phones.join('\n')).then(() => showCopyToast(`Скопировано ${phones.length} номеров`)),
        });
      }
    }

    if (innsToDelete.length) {
      const probivLabel = probivRunningTaskIds.has(currentTaskId)
        ? (innsToDelete.length > 1 ? `Добавить в пробив (${innsToDelete.length})` : 'Добавить в пробив')
        : (innsToDelete.length > 1 ? `Запустить пробив (${innsToDelete.length})` : 'Запустить пробив');
      items.push({ label: probivLabel, action: () => startProbiv(innsToDelete) });

      const broadcastLabel = broadcastRunningTaskIds.has(currentTaskId)
        ? (innsToDelete.length > 1 ? `Добавить в рассылку (${innsToDelete.length})` : 'Добавить в рассылку')
        : (innsToDelete.length > 1 ? `Запустить рассылку (${innsToDelete.length})` : 'Запустить рассылку');
      items.push({ label: broadcastLabel, action: () => startBroadcast(innsToDelete) });

      const delLabel = innsToDelete.length > 1
        ? `Удалить (${innsToDelete.length})`
        : 'Удалить продавца';
      items.push({ label: delLabel, action: () => deleteSelectedSellers(innsToDelete), danger: true });
    }

    if (!items.length) return;
    showContextMenuAt(e.clientX, e.clientY, items);
  }

  async function startProbiv(inns) {
    const task = tasks.find(t => t.id === currentTaskId);
    if (!task || !(task.probiv_account_ids || []).length) {
      alert('Сначала выберите аккаунты для пробива в разделе "Управление"');
      return;
    }
    const isRunning = probivRunningTaskIds.has(currentTaskId);
    const endpoint = isRunning ? '/api/sellers/probiv/add' : '/api/sellers/probiv/start';
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_id: currentTaskId, inns })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(err.error || 'Ошибка запуска пробива');
      return;
    }
    inns.forEach(inn => _updateSellerRowDOM(inn, { status: 'Пробивается' }));
    inns.forEach(inn => {
      const s = currentSellers.find(x => x.inn === inn);
      if (s) s.status = 'Пробивается';
    });
    if (!isRunning) {
      probivRunningTaskIds.add(currentTaskId);
      updateProbivStopButton();
    }
  }

  async function deleteSelectedSellers(inns) {
    await Promise.all(inns.map(inn =>
      fetch(`/api/sellers/${encodeURIComponent(inn)}`, { method: 'DELETE' })
    ));
    inns.forEach(inn => {
      const row = Array.from(document.querySelectorAll('#sellers-body tr'))
        .find(r => r.dataset.inn === inn);
      if (row) row.remove();
    });
    const countEl = document.getElementById('sellers-count');
    if (countEl) countEl.textContent = Math.max(0, parseInt(countEl.textContent) - inns.length);
    const task = tasks.find(t => t.id === currentTaskId);
    if (task) { task.sellers_count = Math.max(0, (task.sellers_count || 0) - inns.length); renderTaskList(); }
    currentSellers = currentSellers.filter(s => !inns.includes(s.inn));
    const countEl2 = document.getElementById('sellers-count');
    if (countEl2) countEl2.textContent = currentSellers.length;
    // Если страница стала пустой — откатиться назад
    const pageStart = _sellersPage * SELLERS_PER_PAGE;
    if (pageStart >= currentSellers.length && _sellersPage > 0) {
      _sellersPage--;
      renderSellers(currentSellers);
    } else {
      _renderPagination(currentSellers.length);
    }
    selectedInns.clear();
    updateRowHighlights();
    loadFilterOptions();
  }

  // ── Copy ───────────────────────────────────────────────────
  let _toastTimer = null;

  function copyCell(el) {
    const text = el.textContent.trim();
    if (!text || text === '—') return;
    navigator.clipboard.writeText(text).then(() => {
      el.classList.add('copied');
      setTimeout(() => el.classList.remove('copied'), 700);
      showCopyToast(text);
    });
  }

  function showCopyToast(text) {
    const toast = document.getElementById('copy-toast');
    if (!toast) return;
    toast.textContent = '✓ Скопировано: ' + (text.length > 30 ? text.slice(0, 30) + '…' : text);
    toast.classList.add('show');
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(() => toast.classList.remove('show'), 1800);
  }

  // ── Utils ──────────────────────────────────────────────────
  function escHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function statusCssClass(status) {
    const map = {
      'В ожидании':  'status-waiting',
      'Пробивается': 'status-probing',
      'Пробит':      'status-found',
      'Не найден':   'status-not-found',
      'Отказ':       'status-reject',
      'Неполадка':   'status-problem',
      'Рассылается': 'status-probing',
      'Отправлено':  'status-bought',
      'Нет WhatsApp':'status-not-found',
      'Переведен':   'status-moved',
      'Думает':      'status-thinking',
      'Купил':       'status-bought',
      'В прозвоне':  'status-calling',
    };
    return map[status] || '';
  }

  function statusColor(status) {
    return STATUS_COLORS[status] || '#52525f';
  }

  function applyStatusStyle(select) {
    const dot = select.previousElementSibling;
    const td = select.closest('td');
    if (dot) dot.style.background = statusColor(select.value);
    if (td) td.className = statusCssClass(select.value);
  }

  function _updateSellerRowDOM(inn, fields) {
    const tr = document.querySelector(`#sellers-body tr[data-inn="${inn}"]`);
    if (!tr) return;
    if ('phone' in fields) {
      const phoneEl = tr.querySelector('.cell-phone');
      if (phoneEl) phoneEl.textContent = fields.phone;
    }
    if ('status' in fields) {
      const sel = tr.querySelector('[data-field="status"]');
      if (sel) { sel.value = fields.status; applyStatusStyle(sel); }
    }
    if ('account' in fields) {
      const sel = tr.querySelector('[data-field="account"]');
      if (sel) {
        const val = fields.account || '';
        if (val && !Array.from(sel.options).some(o => o.value === val)) {
          const o = document.createElement('option');
          o.value = val; o.textContent = val;
          sel.appendChild(o);
        }
        sel.value = val;
      }
    }
  }

  // ── Разделы (WB / WhatsApp) ────────────────────────────────
  function switchSection(section) {
    if (section === currentSection) {
      // Первый вызов при восстановлении: section мог совпасть с 'wb' по умолчанию
    }
    currentSection = section;
    localStorage.setItem('app_section', section);

    document.querySelectorAll('.section-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.section === section));

    const isWb = section === 'wb';
    document.getElementById('sidebar-wb').classList.toggle('hidden', !isWb);
    document.getElementById('sidebar-wa').classList.toggle('hidden', isWb);
    document.getElementById('wb-main').classList.toggle('hidden', !isWb);
    document.getElementById('wa-section').classList.toggle('hidden', isWb);

    if (section === 'whatsapp') {
      loadWaAccounts();
      _startWaStatusPolling();
    } else {
      _stopWaStatusPolling();
    }
  }

  // ── WhatsApp accounts ──────────────────────────────────────
  async function loadWaAccounts() {
    try {
      const res = await fetch('/api/wa-accounts');
      waAccounts = await res.json();
    } catch (_) {
      waAccounts = [];
    }
    if (currentWaId && !waAccounts.find(a => a.id === currentWaId)) currentWaId = null;
    renderWaAccounts();
    renderWaDetail();
  }

  function _waStatusInfo(a) {
    // Пульсация (pulse) = окно РЕАЛЬНО открыто и что-то «живёт».
    // Цвет: зелёный = вход сохранён, оранжевый = открыто без входа, серый = ничего.
    if (a.open && a.logged_in) return { label: 'Открыто, вход есть', cls: 'badge badge-running', dot: 'var(--green)',    pulse: true };
    if (a.open)               return { label: 'Открыто, нужен QR',  cls: 'badge badge-warn',    dot: 'var(--orange)',   pulse: true };
    if (a.logged_in)          return { label: 'Авторизован',        cls: 'badge badge-ok',      dot: 'var(--green)',    pulse: false };
    return { label: 'Нужен вход', cls: 'badge badge-idle', dot: 'var(--text-dim)', pulse: false };
  }

  function renderWaAccounts() {
    const el = document.getElementById('wa-account-list');
    if (!el) return;
    el.innerHTML = '';
    for (const a of waAccounts) {
      const isActive = a.id === currentWaId;
      const info = _waStatusInfo(a);
      const div = document.createElement('div');
      div.className = 'task-item' + (isActive ? ' active' : '');
      div.innerHTML = `
        <span class="task-item-running-dot${info.pulse ? ' wa-dot--pulse' : ''}" style="background:${info.dot}"></span>
        <span class="task-item-name">${escHtml(a.name || a.phone || 'WhatsApp')}</span>
      `;
      div.onclick = () => selectWaAccount(a.id);
      div.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const toggleItem = a.open
          ? { label: 'Закрыть переписку', action: () => { selectWaAccount(a.id); _waClose(a); } }
          : { label: 'Открыть переписку', action: () => { selectWaAccount(a.id); _waOpen(a); } };
        showContextMenuAt(e.clientX, e.clientY, [
          toggleItem,
          { label: 'Изменить прокси', action: () => { selectWaAccount(a.id); changeWaProxyCurrent(); } },
          { label: 'Удалить',         action: () => { selectWaAccount(a.id); deleteWaAccountCurrent(); }, danger: true },
        ]);
      });
      el.appendChild(div);
    }
  }

  function selectWaAccount(id) {
    currentWaId = id;
    renderWaAccounts();
    renderWaDetail();
  }

  function renderWaDetail() {
    const empty  = document.getElementById('wa-empty');
    const detail = document.getElementById('wa-detail');
    if (!empty || !detail) return;

    const a = waAccounts.find(x => x.id === currentWaId);
    if (!a) {
      detail.classList.add('hidden');
      empty.style.display = '';
      return;
    }
    empty.style.display = 'none';
    detail.classList.remove('hidden');

    document.getElementById('wa-detail-name').textContent  = a.name || a.phone || 'WhatsApp';
    document.getElementById('wa-detail-phone').textContent = a.phone || '—';
    document.getElementById('wa-detail-proxy').textContent = a.proxy || '—';

    const info  = _waStatusInfo(a);
    const badge = document.getElementById('wa-detail-status');
    badge.textContent = info.label;
    badge.className = info.cls;

    // Кнопка-тогл: открыть ↔ закрыть переписку в зависимости от состояния окна
    const btn = document.getElementById('wa-toggle-btn');
    if (btn) {
      if (a.open) {
        btn.className = 'btn-stop';
        btn.innerHTML = '<span class="btn-icon">■</span> Закрыть переписку';
      } else {
        btn.className = 'btn-start';
        btn.innerHTML = '<span class="btn-icon">▶</span> Открыть переписку';
      }
    }

    // Подсказку про QR показываем, когда окно открыто, но вход ещё не выполнен
    document.getElementById('wa-detail-hint').classList.toggle('hidden', !(a.open && !a.logged_in));
  }

  // Опрос статуса открытых окон (вход по QR появляется асинхронно)
  function _startWaStatusPolling() {
    _stopWaStatusPolling();
    waStatusTimer = setInterval(_pollWaStatuses, 3000);
  }
  function _stopWaStatusPolling() {
    if (waStatusTimer) { clearInterval(waStatusTimer); waStatusTimer = null; }
  }
  async function _pollWaStatuses() {
    if (currentSection !== 'whatsapp') return;
    const ids = new Set();
    waAccounts.forEach(a => { if (a.open) ids.add(a.id); });
    if (currentWaId) ids.add(currentWaId);
    if (!ids.size) return;

    let changed = false;
    for (const id of ids) {
      try {
        const res = await fetch(`/api/wa-accounts/${id}/status`);
        const st = await res.json();
        const a = waAccounts.find(x => x.id === id);
        if (a && (a.open !== st.open || a.logged_in !== st.logged_in)) {
          a.open = st.open;
          a.logged_in = st.logged_in;
          changed = true;
        }
      } catch (_) {}
    }
    if (changed) { renderWaAccounts(); renderWaDetail(); }
  }

  // Открытие окна с оптимистичным UI: кнопка переключается СРАЗУ, до ответа сервера.
  async function _waOpen(a) {
    if (!a) return;
    a.open = true;                 // оптимистично: сразу показываем «Закрыть»
    renderWaAccounts();
    renderWaDetail();
    try {
      const res = await fetch(`/api/wa-accounts/${a.id}/open`, { method: 'POST' });
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        a.open = false;            // откат, если открыть не удалось
        renderWaAccounts();
        renderWaDetail();
        alert(e.error || 'Не удалось открыть окно');
      }
    } catch (_) {
      a.open = false;
      renderWaAccounts();
      renderWaDetail();
      alert('Ошибка соединения');
    }
  }

  // Закрытие окна с оптимистичным UI: кнопка возвращается в «Открыть» сразу.
  async function _waClose(a) {
    if (!a) return;
    a.open = false;
    renderWaAccounts();
    renderWaDetail();
    try {
      await fetch(`/api/wa-accounts/${a.id}/close`, { method: 'POST' });
    } catch (_) { /* окно всё равно считаем закрытым; поллер сверит статус */ }
  }

  function toggleWaWindow() {
    const a = waAccounts.find(x => x.id === currentWaId);
    if (!a) return;
    if (a.open) _waClose(a); else _waOpen(a);
  }

  async function deleteWaAccountCurrent() {
    if (!currentWaId) return;
    if (!confirm('Удалить аккаунт WhatsApp? Профиль и вход в этот аккаунт будут стёрты.')) return;
    const id = currentWaId;
    const res = await fetch(`/api/wa-accounts/${id}`, { method: 'DELETE' });
    if (res.ok) {
      waAccounts = waAccounts.filter(a => a.id !== id);
      if (currentWaId === id) currentWaId = null;
      renderWaAccounts();
      renderWaDetail();
    }
  }

  // ── Модалка добавления WhatsApp-аккаунта ───────────────────
  function _waAddError(msg) {
    const el = document.getElementById('wa-add-error');
    el.textContent = msg;
    el.classList.toggle('hidden', !msg);
  }

  function openAddWaAccount() {
    ['wa-name', 'wa-proxy-address', 'wa-proxy-port', 'wa-proxy-user', 'wa-proxy-pass']
      .forEach(id => { document.getElementById(id).value = ''; });
    document.getElementById('wa-proxy-type').value = 'socks5';
    _waAddError('');
    const btn = document.getElementById('wa-add-submit');
    btn.disabled = false; btn.textContent = 'Добавить';
    document.getElementById('wa-add-modal').classList.remove('hidden');
    setTimeout(() => document.getElementById('wa-name').focus(), 50);
  }

  function closeAddWaAccount() {
    document.getElementById('wa-add-modal').classList.add('hidden');
  }

  async function submitAddWaAccount() {
    const address = document.getElementById('wa-proxy-address').value.trim();
    const port    = document.getElementById('wa-proxy-port').value.trim();
    if (!address || !port) { _waAddError('Прокси обязателен: укажите адрес и порт'); return; }

    const body = {
      name: document.getElementById('wa-name').value.trim(),
      proxy_address: address,
      proxy_port: parseInt(port),
      proxy_type: document.getElementById('wa-proxy-type').value,
      proxy_username: document.getElementById('wa-proxy-user').value.trim() || null,
      proxy_password: document.getElementById('wa-proxy-pass').value.trim() || null,
    };

    const btn = document.getElementById('wa-add-submit');
    btn.disabled = true; btn.textContent = 'Открываю окно...';
    try {
      const res = await fetch('/api/wa-accounts/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) { _waAddError(data.error || 'Ошибка'); btn.disabled = false; btn.textContent = 'Добавить'; return; }

      closeAddWaAccount();
      await loadWaAccounts();
      if (data.id) selectWaAccount(data.id);
      if (data.warning) showCopyToast('Окно не открылось: ' + data.warning);
    } catch (e) {
      _waAddError('Ошибка соединения');
      btn.disabled = false; btn.textContent = 'Добавить';
    }
  }

  // ── Модалка смены прокси WhatsApp-аккаунта ─────────────────
  function _waProxyError(msg) {
    const el = document.getElementById('wa-proxy-error');
    el.textContent = msg;
    el.classList.toggle('hidden', !msg);
  }

  function changeWaProxyCurrent() {
    if (!currentWaId) return;
    _waProxyId = currentWaId;
    ['wap-address', 'wap-port', 'wap-user', 'wap-pass']
      .forEach(id => { document.getElementById(id).value = ''; });
    document.getElementById('wap-type').value = 'socks5';
    _waProxyError('');
    document.getElementById('wa-proxy-modal').classList.remove('hidden');
    setTimeout(() => document.getElementById('wap-address').focus(), 50);
  }

  function closeWaProxyModal() {
    document.getElementById('wa-proxy-modal').classList.add('hidden');
    _waProxyId = null;
  }

  async function submitWaProxy() {
    const address = document.getElementById('wap-address').value.trim();
    const port    = document.getElementById('wap-port').value.trim();
    if (!address || !port) { _waProxyError('Укажите адрес и порт прокси'); return; }

    const btn = document.getElementById('wa-proxy-submit');
    btn.disabled = true; btn.textContent = 'Сохранение...';
    try {
      const res = await fetch(`/api/wa-accounts/${_waProxyId}/proxy`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          proxy_address: address,
          proxy_port: parseInt(port),
          proxy_type: document.getElementById('wap-type').value,
          proxy_username: document.getElementById('wap-user').value.trim() || null,
          proxy_password: document.getElementById('wap-pass').value.trim() || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) { _waProxyError(data.error || 'Ошибка'); btn.disabled = false; btn.textContent = 'Сохранить'; return; }
      closeWaProxyModal();
      await loadWaAccounts();
    } catch (e) {
      _waProxyError('Ошибка соединения');
      btn.disabled = false; btn.textContent = 'Сохранить';
    }
  }

  // ── Public API ─────────────────────────────────────────────
  return {
    init,
    switchSection,
    openAddWaAccount,
    closeAddWaAccount,
    submitAddWaAccount,
    toggleWaWindow,
    deleteWaAccountCurrent,
    changeWaProxyCurrent,
    closeWaProxyModal,
    submitWaProxy,
    createTask,
    selectTask,
    saveTaskSettings,
    addQueryRow,
    toggleTask,
    loadSellers,
    resetFilters,
    updateSeller,
    deleteSeller,
    toggleProbivAccounts,
    openAddAccount,
    closeAuthModal,
    authSubmit,
    removeTgAccount,
    openChangeProxyModal,
    closeChangeProxyModal,
    submitChangeProxy,
    toggleFilterDrop,
    clearLog,
    copyLog,
    selectTaskView,
    statusColor,
    applyStatusStyle,
    copyCell,
    rowMousedown,
    rowMouseover,
    deleteSelectedSellers,
    startProbiv,
    stopProbiv,
    stopBroadcast,
    toggleBroadcastAccounts,
    onBroadcastMessageInput,
    onBroadcastLimitInput,
    onBroadcastMediaChange,
    clearBroadcastMedia,
    renameTask,
    confirmRename,
    closeRenameModal,
    deleteTask,
    sellersPagePrev,
    sellersPageNext,
  };

})();

// Запуск
document.addEventListener('DOMContentLoaded', App.init);
