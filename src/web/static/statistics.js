(function () {
  const AUTO_REFRESH_MS = 20 * 60 * 1000;

  const PAGE_TITLES = {
    dashboard: 'Статистика рассылки',
    campaigns: 'Рассылки',
    recipients: 'Получатели и статусы',
    'campaign-analytics': 'Детальная аналитика рассылки',
    consents: 'Согласия и интерес',
    problems: 'Проблемы с email',
    reports: 'Отчёты и выгрузки',
  };

  const RECIPIENT_CHIPS = [
    ['', 'Все'],
    ['delivered', 'Доставлено'],
    ['opened', 'Открыто'],
    ['clicked', 'Клик'],
    ['problems', 'Проблемы'],
    ['pending', 'Ожидают'],
    ['action', 'Нужно действие'],
  ];

  const ACTION_TYPES = [
    ['call', 'Перезвонить'],
    ['resend', 'Повторить отправку'],
    ['find_another_email', 'Найти другой email'],
    ['create_task', 'Создать задачу'],
  ];

  const state = {
    page: 'dashboard',
    filters: {},
    pagination: { recipients: 1, consents: 1, problems: 1 },
    perPage: 10,
    selectedCampaign: '',
    selectedRecipient: null,
    selectedProblem: null,
    actionRecipient: null,
    actionType: 'call',
    charts: {},
    campaigns: [],
    campaignsLoaded: false,
    userName: '',
    pollTimer: null,
    autoRefreshTimer: null,
    busyDepth: 0,
    searchTimers: {},
  };

  let pendingRecipientQuickFilter = null;

  function debounceSearch(key, callback, delay = 300) {
    if (state.searchTimers[key]) clearTimeout(state.searchTimers[key]);
    state.searchTimers[key] = setTimeout(callback, delay);
  }

  function qs(id) { return document.getElementById(id); }
  function badge(status) {
    const tone = status?.tone || 'neutral';
    return `<span class="badge badge-${tone}">${escapeHtml(status?.label || '—')}</span>`;
  }
  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }
  function fmt(value) { return Number(value || 0).toLocaleString('ru'); }

  function readGlobalFiltersFromDom() {
    return {
      period_from: qs('filter-from')?.value || '',
      period_to: qs('filter-to')?.value || '',
      campaign: qs('filter-campaign')?.value || '',
      provider: qs('filter-provider')?.value || '',
    };
  }

  function recipientFilterParams() {
    const params = {};
    if (state.filters.quick_filter) params.quick_filter = state.filters.quick_filter;
    const status = qs('filter-status')?.value || state.filters.status || '';
    if (status) params.status = status;
    if (state.filters.manager_action) params.manager_action = state.filters.manager_action;
    if (state.filters.organization) params.organization = state.filters.organization;
    if (state.filters.problems_only) params.problems_only = 'true';
    return params;
  }

  function consentFilterParams() {
    const params = {};
    if (state.filters.consent_status) params.consent_status = state.filters.consent_status;
    if (state.filters.organization) params.organization = state.filters.organization;
    return params;
  }

  function problemFilterParams() {
    const params = {};
    if (state.filters.organization) params.organization = state.filters.organization;
    if (state.filters.quick_filter) params.quick_filter = state.filters.quick_filter;
    return params;
  }

  function buildQueryParams(extra = {}) {
    const params = { ...readGlobalFiltersFromDom(), ...extra };
    if (state.filters.providers) {
      params.providers = state.filters.providers;
      delete params.provider;
    }
    return params;
  }

  // Embedded inside the main SPA (index.html): the statistics section keeps its
  // navigation/filter state in-memory only and must not touch the browser URL,
  // otherwise it would fight the host app's own screen persistence.
  function syncFiltersToUrl() {}

  function initFilterDefaults() {
    state.page = 'dashboard';
    if (qs('filter-from')) qs('filter-from').value = state.filters.period_from || '';
    if (qs('filter-to')) qs('filter-to').value = state.filters.period_to || '';
    if (qs('filter-campaign')) qs('filter-campaign').value = state.filters.campaign || '';
    if (qs('filter-provider')) qs('filter-provider').value = state.filters.provider || '';
    if (qs('filter-status')) qs('filter-status').value = state.filters.status || '';
  }

  function syncGlobalFiltersToDom() {
    if (qs('filter-from')) qs('filter-from').value = state.filters.period_from || '';
    if (qs('filter-to')) qs('filter-to').value = state.filters.period_to || '';
    if (qs('filter-campaign')) qs('filter-campaign').value = state.filters.campaign || '';
    if (qs('filter-provider')) qs('filter-provider').value = state.filters.provider || '';
    if (qs('filter-status')) qs('filter-status').value = state.filters.status || '';
  }

  function clearAllFilters() {
    state.filters = {};
    syncGlobalFiltersToDom();
    ['adv-from', 'adv-to', 'adv-organization'].forEach((id) => { if (qs(id)) qs(id).value = ''; });
    ['adv-campaign', 'adv-consent-status', 'adv-manager-action'].forEach((id) => { if (qs(id)) qs(id).value = ''; });
    if (qs('adv-problems-only')) qs('adv-problems-only').checked = false;
    if (qs('adv-providers')) Array.from(qs('adv-providers').options).forEach((option) => { option.selected = false; });
  }

  function clearTabFiltersForPage(page) {
    if (page !== 'recipients') {
      state.filters.quick_filter = '';
      state.filters.status = '';
      state.filters.manager_action = '';
      if (qs('filter-status')) qs('filter-status').value = '';
    }
    if (page !== 'consents') {
      state.filters.consent_status = '';
    }
    if (page !== 'recipients' && page !== 'problems') {
      state.filters.organization = '';
      state.filters.problems_only = false;
    }
    if (page === 'recipients' && pendingRecipientQuickFilter !== null) {
      state.filters.quick_filter = pendingRecipientQuickFilter;
      pendingRecipientQuickFilter = null;
    }
  }

  function updateFilterBarForPage(page) {
    qs('btn-advanced-filters')?.classList.toggle('hidden', page === 'reports');
  }

  function closeSidePanels() {
    qs('recipient-card')?.classList.add('hidden');
    qs('problem-card')?.classList.add('hidden');
    state.selectedRecipient = null;
    state.selectedProblem = null;
  }

  function queryString(extra = {}) {
    const filters = buildQueryParams(extra);
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== '' && value !== false && value != null) params.set(key, String(value));
    });
    const query = params.toString();
    return query ? `?${query}` : '';
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options });
    if (response.status === 401) {
      window.location.href = '/login';
      throw new Error('Unauthorized');
    }
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) throw new Error(data.detail || 'Не удалось загрузить данные.');
    return data.result ?? data;
  }

  function setBusy(isBusy) {
    state.busyDepth = Math.max(0, state.busyDepth + (isBusy ? 1 : -1));
    qs('stats-status')?.classList.toggle('hidden', state.busyDepth === 0);
  }

  function showError(message) {
    const el = qs('stats-error');
    if (!el) return;
    const text = el.querySelector('.stats-error-text');
    if (text) text.textContent = message || 'Не удалось загрузить данные.';
    el.classList.remove('hidden');
  }

  function clearError() {
    qs('stats-error')?.classList.add('hidden');
  }

  function setRefreshBadge(inProgress) {
    qs('refresh-badge')?.classList.toggle('hidden', !inProgress);
  }

  function clearPoll() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function schedulePoll(inProgress) {
    clearPoll();
    setRefreshBadge(inProgress);
    if (inProgress) {
      state.pollTimer = setTimeout(() => { loadCurrentPage(); }, 5000);
    }
  }

  function stopAutoRefresh() {
    if (state.autoRefreshTimer) {
      clearInterval(state.autoRefreshTimer);
      state.autoRefreshTimer = null;
    }
  }

  function startAutoRefresh() {
    stopAutoRefresh();
    state.autoRefreshTimer = setInterval(() => { loadCurrentPage(true); }, AUTO_REFRESH_MS);
  }

  function destroyChart(id) {
    if (state.charts[id]) {
      state.charts[id].destroy();
      delete state.charts[id];
    }
  }

  function renderKpis(containerId, items) {
    const container = qs(containerId);
    if (!container) return;
    container.innerHTML = items.map((item) => `
      <div class="kpi-card">
        <div class="label">${escapeHtml(item.title)}</div>
        <div class="value">${escapeHtml(item.value)}</div>
      </div>
    `).join('');
  }

  function renderFunnel(containerId, funnel) {
    const container = qs(containerId);
    if (!container) return;
    container.innerHTML = (funnel || []).map((step) => `
      <div class="funnel-step">
        <div class="label">${escapeHtml(step.label)}</div>
        <div class="value">${fmt(step.value)}</div>
        <div class="label">${step.percent ?? 0}%</div>
      </div>
    `).join('');
  }

  function renderPagination(containerId, pagination, key) {
    const container = qs(containerId);
    if (!container || !pagination) return;
    container.innerHTML = `
      <span>Показано ${Math.min((pagination.page - 1) * pagination.per_page + 1, pagination.total)}–${Math.min(pagination.page * pagination.per_page, pagination.total)} из ${fmt(pagination.total)}</span>
      <span>
        <button class="btn-outline" data-page="${pagination.page - 1}" ${pagination.page <= 1 ? 'disabled' : ''}>Назад</button>
        <span> ${pagination.page} / ${pagination.pages} </span>
        <button class="btn-outline" data-page="${pagination.page + 1}" ${pagination.page >= pagination.pages ? 'disabled' : ''}>Вперёд</button>
      </span>
    `;
    container.querySelectorAll('button[data-page]').forEach((button) => {
      button.addEventListener('click', () => {
        state.pagination[key] = Number(button.dataset.page);
        syncFiltersToUrl();
        loadCurrentPage();
      });
    });
  }

  function renderDashboard(result) {
    renderKpis('dashboard-kpis', [
      { title: 'Отправлено', value: fmt(result.summary?.sent) },
      { title: 'Доставлено', value: fmt(result.summary?.delivered) },
      { title: 'Открыто', value: fmt(result.summary?.opened) },
      { title: 'Переходы', value: fmt(result.summary?.clicked) },
      { title: 'Ошибки', value: fmt(result.summary?.errors) },
      { title: 'Ожидают статуса', value: fmt(result.summary?.pending) },
      { title: 'Согласия', value: fmt(result.summary?.consents) },
      { title: 'Материалы отправлены', value: fmt(result.summary?.materials_sent) },
    ]);
    renderKpis('dashboard-rates', [
      { title: 'Доставляемость', value: `${result.rates?.delivery_rate ?? 0}%` },
      { title: 'Открываемость', value: `${result.rates?.open_rate ?? 0}%` },
      { title: 'Переходы (CTR)', value: `${result.rates?.ctr ?? 0}%` },
      { title: 'Доля ошибок', value: `${result.rates?.error_rate ?? 0}%` },
    ]);
    renderFunnel('dashboard-funnel', result.funnels);
    qs('dashboard-empty')?.classList.toggle('hidden', !result.empty);
    qs('refresh-meta').textContent = `Обновлено: ${result.generated_at_label || 'сейчас'}`;

    destroyChart('chart-statuses');
    state.charts['chart-statuses'] = new Chart(qs('chart-statuses'), {
      type: 'doughnut',
      data: {
        labels: (result.statuses || []).map((item) => item.label),
        datasets: [{ data: (result.statuses || []).map((item) => item.count), backgroundColor: ['#22c55e', '#8b5cf6', '#2563eb', '#ef4444', '#f59e0b', '#64748b'] }],
      },
      options: { maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
    });
    destroyChart('chart-providers');
    state.charts['chart-providers'] = new Chart(qs('chart-providers'), {
      type: 'bar',
      data: {
        labels: (result.providers || []).map((item) => item.label),
        datasets: [{ label: 'Отправлено', data: (result.providers || []).map((item) => item.count), backgroundColor: '#2563eb' }],
      },
      options: { maintainAspectRatio: false, indexAxis: 'y' },
    });
    destroyChart('chart-roles');
    const roles = result.roles || [];
    const rolesCard = qs('card-chart-roles');
    // Splitting by address role only helps when a fallback address actually
    // exists; with a single role the chart is noise, so hide it.
    if (rolesCard) rolesCard.classList.toggle('hidden', roles.length < 2);
    if (roles.length >= 2) {
      state.charts['chart-roles'] = new Chart(qs('chart-roles'), {
        type: 'doughnut',
        data: {
          labels: roles.map((item) => item.label),
          datasets: [{ data: roles.map((item) => item.count), backgroundColor: ['#5a9e1f', '#c9b98a'] }],
        },
        options: { maintainAspectRatio: false },
      });
    }

    const worklists = result.work_lists || {};
    qs('dashboard-worklists').innerHTML = [
      ['Заинтересованные', worklists.interested || [], 'recipients', 'opened'],
      ['Проблемы с email', worklists.email_problems || [], 'problems'],
      ['Нужно перезвонить', worklists.need_call || [], 'recipients', 'action'],
    ].map(([title, items, page, quick]) => `
      <div class="panel">
        <h3>${escapeHtml(title)}</h3>
        ${items.length ? items.map((item) => `<div class="worklist-item"><span>${escapeHtml(item.organization)}</span><strong>${fmt(item.count)}</strong></div>`).join('') : '<div class="empty-state">Нет данных</div>'}
        <button class="btn-outline" data-nav="${page}" data-quick="${quick || ''}">Посмотреть все</button>
      </div>
    `).join('');
    qs('dashboard-worklists').querySelectorAll('[data-nav]').forEach((button) => {
      button.addEventListener('click', () => {
        pendingRecipientQuickFilter = button.dataset.quick || '';
        activatePage(button.dataset.nav);
      });
    });
    qs('dashboard-insights').innerHTML = (result.insights || []).map((item) => `<li><strong>${escapeHtml(item.title)}:</strong> ${escapeHtml(item.text)}</li>`).join('');
  }

  function syncCampaignSelects() {
    const options = ['<option value="">Все рассылки</option>'].concat(
      state.campaigns.map((item) => `<option value="${escapeHtml(item.job_id)}">${escapeHtml(item.title)}</option>`)
    );
    const analyticsOptions = ['<option value="">Выберите рассылку</option>'].concat(
      state.campaigns.map((item) => `<option value="${escapeHtml(item.job_id)}">${escapeHtml(item.title)}</option>`)
    );
    const select = qs('filter-campaign');
    const exportSelect = qs('export-campaign');
    const analyticsSelect = qs('analytics-campaign');
    if (select) select.innerHTML = options.join('');
    if (exportSelect) exportSelect.innerHTML = ['<option value="">Текущая / первая доступная</option>'].concat(
      state.campaigns.map((item) => `<option value="${escapeHtml(item.job_id)}">${escapeHtml(item.title)}</option>`)
    ).join('');
    if (qs('adv-campaign')) qs('adv-campaign').innerHTML = options.join('');
    if (analyticsSelect) analyticsSelect.innerHTML = analyticsOptions.join('');
    const selectedCampaign = state.filters.campaign || state.selectedCampaign || '';
    if (selectedCampaign) {
      if (select) select.value = selectedCampaign;
      if (analyticsSelect) analyticsSelect.value = selectedCampaign;
    }
  }

  function renderCampaigns(result) {
    state.campaigns = result.campaigns || [];
    syncCampaignSelects();
    renderKpis('campaigns-kpis', [
      { title: 'Всего рассылок', value: fmt(result.summary?.total) },
      { title: 'Активные', value: fmt(result.summary?.active) },
      { title: 'Завершённые', value: fmt(result.summary?.completed) },
      { title: 'Черновики', value: fmt(result.summary?.draft) },
      { title: 'Средняя доставляемость', value: `${result.summary?.avg_delivery_rate ?? 0}%` },
      { title: 'Средний Open Rate', value: `${result.summary?.avg_open_rate ?? 0}%` },
    ]);
    const search = (qs('campaigns-search')?.value || '').toLowerCase();
    const rows = state.campaigns.filter((item) => !search || item.title.toLowerCase().includes(search));
    qs('campaigns-table').innerHTML = rows.map((item) => `
      <tr data-job-id="${escapeHtml(item.job_id)}">
        <td>${escapeHtml(item.title)}</td>
        <td>${escapeHtml(item.period_label)}</td>
        <td>${escapeHtml(item.provider_label)}</td>
        <td>${fmt(item.sent)}</td>
        <td>${fmt(item.delivered)} / ${item.delivery_rate}%</td>
        <td>${fmt(item.opened)} / ${item.open_rate}%</td>
        <td>${fmt(item.clicked)} / ${item.ctr}%</td>
        <td>${fmt(item.consents)}</td>
        <td>${escapeHtml(item.status_label)}</td>
        <td><button class="btn-outline" data-open-analytics="${escapeHtml(item.job_id)}">Аналитика</button></td>
      </tr>
    `).join('') || '<tr><td colspan="10" class="empty-state">Пока нет рассылок с отправками</td></tr>';
    qs('campaigns-table').querySelectorAll('tr[data-job-id]').forEach((row) => {
      row.addEventListener('click', () => selectCampaign(row.dataset.jobId));
    });
    qs('campaigns-table').querySelectorAll('[data-open-analytics]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        state.selectedCampaign = button.dataset.openAnalytics;
        state.filters.campaign = state.selectedCampaign;
        if (qs('analytics-campaign')) qs('analytics-campaign').value = state.selectedCampaign;
        activatePage('campaign-analytics');
      });
    });
  }

  function selectCampaign(jobId) {
    state.selectedCampaign = jobId;
    const item = state.campaigns.find((campaign) => campaign.job_id === jobId);
    if (!item) return;
    qs('campaign-summary-empty')?.classList.add('hidden');
    const body = qs('campaign-summary-body');
    body.classList.remove('hidden');
    body.innerHTML = `
      <h4>${escapeHtml(item.title)}</h4>
      <p>${escapeHtml(item.period_label)}</p>
      <p>Доставляемость: <strong>${item.delivery_rate}%</strong></p>
      <p>Open Rate: <strong>${item.open_rate}%</strong></p>
      <p>CTR: <strong>${item.ctr}%</strong></p>
      <button class="btn-primary" id="campaign-open-analytics">Открыть аналитику</button>
      <button class="btn-outline" id="campaign-download-report">Скачать отчёт</button>
    `;
    qs('campaign-open-analytics')?.addEventListener('click', () => {
      state.filters.campaign = jobId;
      if (qs('analytics-campaign')) qs('analytics-campaign').value = jobId;
      activatePage('campaign-analytics');
    });
    qs('campaign-download-report')?.addEventListener('click', () => {
      window.location.href = `/api/download/sender-delivery-report?job_id=${encodeURIComponent(jobId)}`;
    });
  }

  function renderRecipients(result) {
    renderKpis('recipients-kpis', [
      { title: 'Всего получателей', value: fmt(result.summary?.total) },
      { title: 'Активные', value: fmt(result.summary?.active) },
      { title: 'Проблемные', value: fmt(result.summary?.problematic) },
      { title: 'Нужно перезвонить', value: fmt(result.summary?.need_call) },
    ]);
    qs('recipient-chips').innerHTML = RECIPIENT_CHIPS.map(([value, label]) => `
      <button class="chip ${state.filters.quick_filter === value ? 'active' : ''}" data-quick="${value}">${label}</button>
    `).join('');
    qs('recipient-chips').querySelectorAll('.chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        state.filters.quick_filter = chip.dataset.quick;
        state.pagination.recipients = 1;
        syncFiltersToUrl();
        loadRecipients();
      });
    });
    qs('recipients-table').innerHTML = (result.items || []).map((item) => `
      <tr data-row-key="${escapeHtml(item.row_key)}">
        <td>${escapeHtml(item.organization)}</td>
        <td>${escapeHtml(item.recipient_name)}<div>${escapeHtml(item.email)}</div></td>
        <td>${escapeHtml(item.role_label)}</td>
        <td>${badge(item.manager_status)}</td>
        <td>${escapeHtml(item.last_event_label)}<div>${escapeHtml(item.last_event_at)}</div></td>
        <td>${escapeHtml(item.interest?.label)}</td>
        <td>${escapeHtml(item.next_action?.label)}</td>
        <td><button class="btn-outline" data-action="${escapeHtml(item.row_key)}">⋯</button></td>
      </tr>
    `).join('') || '<tr><td colspan="8" class="empty-state">Нет получателей за выбранный период</td></tr>';
    qs('recipients-table').querySelectorAll('tr[data-row-key]').forEach((row) => {
      row.addEventListener('click', () => openRecipientCard(row.dataset.rowKey));
    });
    qs('recipients-table').querySelectorAll('[data-action]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        openActionModal(button.dataset.action);
      });
    });
    renderPagination('recipients-pagination', result.pagination, 'recipients');
  }

  async function openRecipientCard(rowKey) {
    const detail = await api(`/api/sender/recipients/${encodeURIComponent(rowKey)}`);
    state.selectedRecipient = detail;
    qs('recipient-card').classList.remove('hidden');
    qs('recipient-card-body').innerHTML = `
      <p><strong>Организация:</strong> ${escapeHtml(detail.organization)}</p>
      <p><strong>Email:</strong> ${escapeHtml(detail.email)}</p>
      <p><strong>Получатель:</strong> ${escapeHtml(detail.recipient_name)}</p>
      <p><strong>Роль:</strong> ${escapeHtml(detail.role_label)}</p>
      <p><strong>Статус:</strong> ${badge(detail.manager_status)}</p>
      <h4>История статусов</h4>
      ${(detail.status_history || []).map((item) => `<div class="timeline-item">${badge({ label: item.label, tone: item.tone })}<div>${escapeHtml(item.at)}</div></div>`).join('') || '<div class="empty-state">События провайдера ещё не получены</div>'}
      <h4>Рекомендуемое действие</h4>
      <p>${escapeHtml(detail.recommended_action?.label)}</p>
    `;
    qs('recipient-action-btn').onclick = () => openActionModal(rowKey);
  }

  function renderCampaignAnalytics(result) {
    const campaign = result.campaign || {};
    const meta = qs('analytics-campaign-meta');
    if (meta) {
      meta.innerHTML = `
        <strong>${escapeHtml(campaign.title || 'Рассылка')}</strong>
        <span>${escapeHtml(result.period_from || '')}${result.period_to ? ` — ${escapeHtml(result.period_to)}` : ''}</span>
      `;
    }
    qs('analytics-empty')?.classList.add('hidden');
    qs('analytics-content')?.classList.remove('hidden');
    renderKpis('analytics-kpis', [
      { title: 'Отправлено', value: fmt(result.summary?.sent) },
      { title: 'Доставлено', value: `${fmt(result.summary?.delivered)} / ${result.rates?.delivery_rate ?? 0}%` },
      { title: 'Открыто', value: `${fmt(result.summary?.opened)} / ${result.rates?.open_rate ?? 0}%` },
      { title: 'Переходы', value: `${fmt(result.summary?.clicked)} / ${result.rates?.ctr ?? 0}%` },
      { title: 'Недоставлено', value: fmt(result.summary?.errors) },
      { title: 'Отписки и спам', value: fmt((result.summary?.unsubscribed || 0) + (result.summary?.spam || 0)) },
    ]);
    renderFunnel('analytics-funnel', result.funnel);
    destroyChart('chart-daily');
    state.charts['chart-daily'] = new Chart(qs('chart-daily'), {
      type: 'line',
      data: {
        labels: (result.daily || []).map((item) => item.date),
        datasets: [
          { label: 'Отправлено', data: (result.daily || []).map((item) => item.sent), borderColor: '#2563eb' },
          { label: 'Доставлено', data: (result.daily || []).map((item) => item.delivered), borderColor: '#16a34a' },
          { label: 'Открыто', data: (result.daily || []).map((item) => item.opened), borderColor: '#8b5cf6' },
        ],
      },
      options: { maintainAspectRatio: false },
    });
    destroyChart('chart-reasons');
    state.charts['chart-reasons'] = new Chart(qs('chart-reasons'), {
      type: 'bar',
      data: {
        labels: (result.undelivery_reasons || []).map((item) => item.label),
        datasets: [{ data: (result.undelivery_reasons || []).map((item) => item.count), backgroundColor: '#ef4444' }],
      },
      options: { maintainAspectRatio: false, indexAxis: 'y' },
    });
    destroyChart('chart-provider-eff');
    state.charts['chart-provider-eff'] = new Chart(qs('chart-provider-eff'), {
      type: 'bar',
      data: {
        labels: (result.provider_effectiveness || []).map((item) => item.provider),
        datasets: [
          { label: 'Доставляемость', data: (result.provider_effectiveness || []).map((item) => item.delivery_rate), backgroundColor: '#16a34a' },
          { label: 'Open Rate', data: (result.provider_effectiveness || []).map((item) => item.open_rate), backgroundColor: '#8b5cf6' },
        ],
      },
      options: { maintainAspectRatio: false },
    });
    qs('analytics-high-interest').innerHTML = (result.high_interest_companies || []).map((item) => `
      <tr><td>${escapeHtml(item.organization)}</td><td>${fmt(item.sent)}</td><td>${item.open_rate}%</td><td>${item.clicked}</td></tr>
    `).join('') || '<tr><td colspan="4" class="empty-state">Нет компаний с высоким интересом</td></tr>';
    qs('analytics-problems').innerHTML = (result.problem_addresses || []).map((item) => `
      <tr><td>${escapeHtml(item.email)}</td><td>${escapeHtml(item.reason_label)}</td><td>${escapeHtml(item.provider_label)}</td><td>${fmt(item.attempts)}</td></tr>
    `).join('') || '<tr><td colspan="4" class="empty-state">Нет проблемных адресов</td></tr>';
    qs('analytics-recommendations').innerHTML = (result.recommendations || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
  }

  function renderConsents(result) {
    renderKpis('consents-kpis', [
      { title: 'Дали согласие', value: fmt(result.summary?.confirmed) },
      { title: 'Материалы отправлены', value: fmt(result.summary?.materials_sent) },
      { title: 'Открыли после согласия', value: fmt(result.summary?.opened_after_consent) },
      { title: 'Нужно перезвонить', value: fmt(result.summary?.need_call) },
    ]);
    renderFunnel('consents-funnel', result.funnel);
    qs('consents-table').innerHTML = (result.items || []).map((item) => `
      <tr>
        <td>${escapeHtml(item.organization)}</td>
        <td>${escapeHtml(item.contact)}</td>
        <td>${escapeHtml(item.email)}</td>
        <td>${escapeHtml(item.consent_status_label)}</td>
        <td>${escapeHtml(item.materials_label)}</td>
        <td>${escapeHtml(item.last_action_label)}<div>${escapeHtml(item.last_action_at)}</div></td>
        <td>${escapeHtml(item.interest?.label)}</td>
        <td>${escapeHtml(item.next_action?.label)}</td>
      </tr>
    `).join('') || '<tr><td colspan="8" class="empty-state">Нет данных по согласиям</td></tr>';
    qs('consents-priority').innerHTML = (result.priority_contacts || []).map((item, index) => `
      <div class="worklist-item"><span>${index + 1}. ${escapeHtml(item.organization)}</span><span>${escapeHtml(item.contact)}</span></div>
    `).join('') || '<div class="empty-state">Нет приоритетных контактов</div>';
    renderPagination('consents-pagination', result.pagination, 'consents');
  }

  function renderProblems(result) {
    renderKpis('problems-kpis', [
      { title: 'Проблемные адреса', value: fmt(result.summary?.problem_addresses) },
      { title: 'Hard bounce', value: fmt(result.summary?.hard_bounce) },
      { title: 'Soft bounce', value: fmt(result.summary?.soft_bounce) },
      { title: 'Требуют проверки', value: fmt(result.summary?.need_check) },
      { title: 'Повторить позже', value: fmt(result.summary?.retry_later) },
    ]);
    destroyChart('chart-problem-reasons');
    state.charts['chart-problem-reasons'] = new Chart(qs('chart-problem-reasons'), {
      type: 'doughnut',
      data: {
        labels: (result.reasons || []).map((item) => item.label),
        datasets: [{ data: (result.reasons || []).map((item) => item.count) }],
      },
      options: { maintainAspectRatio: false },
    });
    destroyChart('chart-problem-domains');
    state.charts['chart-problem-domains'] = new Chart(qs('chart-problem-domains'), {
      type: 'bar',
      data: {
        labels: (result.domains || []).map((item) => item.provider),
        datasets: [{ data: (result.domains || []).map((item) => item.count), backgroundColor: '#f97316' }],
      },
      options: { maintainAspectRatio: false, indexAxis: 'y' },
    });
    qs('problems-table').innerHTML = (result.items || []).map((item) => `
      <tr data-row-key="${escapeHtml(item.row_key)}">
        <td>${escapeHtml(item.organization)}</td>
        <td>${escapeHtml(item.email)}</td>
        <td>${escapeHtml(item.bounce_reason_label)}</td>
        <td>${escapeHtml(item.provider)}</td>
        <td>${fmt(item.attempts)}</td>
        <td>${escapeHtml(item.last_event_at)}</td>
        <td>${escapeHtml(item.recommended_action?.label)}</td>
      </tr>
    `).join('') || '<tr><td colspan="7" class="empty-state">Нет проблемных адресов</td></tr>';
    qs('problems-table').querySelectorAll('tr[data-row-key]').forEach((row) => {
      row.addEventListener('click', () => openProblemCard(row.dataset.rowKey, result.items));
    });
    renderPagination('problems-pagination', result.pagination, 'problems');
  }

  function openProblemCard(rowKey, items) {
    const item = (items || []).find((entry) => entry.row_key === rowKey);
    if (!item) return;
    state.selectedProblem = item;
    qs('problem-card').classList.remove('hidden');
    qs('problem-card-body').innerHTML = `
      <p><strong>${escapeHtml(item.email)}</strong></p>
      <p>${badge(item.manager_status)}</p>
      <p>Причина: ${escapeHtml(item.bounce_reason_label)}</p>
      <p>Провайдер: ${escapeHtml(item.provider)}</p>
      <p>Попыток: ${fmt(item.attempts)}</p>
      <p>Последнее событие: ${escapeHtml(item.last_event_at)}</p>
      <p>Рекомендация: ${escapeHtml(item.recommended_action?.label)}</p>
      <button class="btn-danger" id="problem-create-task">Создать задачу</button>
    `;
    qs('problem-create-task').onclick = () => openActionModal(rowKey, 'create_task');
  }

  function showAnalyticsEmpty(message) {
    qs('analytics-empty')?.classList.remove('hidden');
    if (qs('analytics-empty')) qs('analytics-empty').textContent = message;
    qs('analytics-content')?.classList.add('hidden');
    qs('analytics-campaign-meta').innerHTML = '';
    schedulePoll(false);
  }

  function renderReports(result) {
    renderKpis('reports-kpis', [
      { title: 'Сформировано отчётов', value: fmt(result.summary?.generated) },
      { title: 'Excel выгрузки', value: fmt(result.summary?.xlsx) },
      { title: 'CSV выгрузки', value: fmt(result.summary?.csv) },
      { title: 'NDJSON журналы', value: fmt(result.summary?.ndjson) },
    ]);
    qs('reports-available').innerHTML = (result.available || []).map((item) => `
      <div class="report-card kpi-card">
        <h3>${escapeHtml(item.title)}</h3>
        <p>${escapeHtml(item.description)}</p>
        <button class="btn-outline" data-export-type="${escapeHtml(item.id)}">Сформировать отчёт</button>
      </div>
    `).join('');
    qs('reports-available').querySelectorAll('[data-export-type]').forEach((button) => {
      button.addEventListener('click', () => {
        qs('export-type').value = button.dataset.exportType;
        openModal('modal-export');
      });
    });
    qs('reports-history').innerHTML = (result.history || []).map((item) => `
      <tr>
        <td>${escapeHtml(item.report_type)}</td>
        <td>${escapeHtml(item.period_from)} — ${escapeHtml(item.period_to)}</td>
        <td>${escapeHtml(item.format)}</td>
        <td>${escapeHtml(item.created_at)}</td>
        <td>${escapeHtml(item.author)}</td>
        <td>${escapeHtml(item.status)}</td>
        <td><a class="btn-outline" href="/api/sender/reports/download/${encodeURIComponent(item.report_id)}">Скачать</a></td>
      </tr>
    `).join('') || '<tr><td colspan="7" class="empty-state">Отчёты ещё не формировались</td></tr>';
  }

  async function loadDashboard(refresh = false) {
    const result = await api(`/api/sender/manager-dashboard${queryString({ refresh: refresh ? 'true' : '' })}`);
    renderDashboard(result);
    schedulePoll(!!result.refresh_in_progress);
  }

  async function loadCampaigns() {
    const result = await api(`/api/sender/campaigns${queryString({ q: qs('campaigns-search')?.value || '' })}`);
    renderCampaigns(result);
    state.campaignsLoaded = true;
  }

  async function loadRecipients() {
    const result = await api(`/api/sender/recipients${queryString({
      ...recipientFilterParams(),
      q: qs('recipients-search')?.value || '',
      page: state.pagination.recipients,
      per_page: state.perPage,
    })}`);
    renderRecipients(result);
    if (state.selectedRecipient?.row_key) {
      openRecipientCard(state.selectedRecipient.row_key).catch((error) => console.error(error));
    }
  }

  async function loadCampaignAnalytics(refresh = false) {
    const preselected = state.filters.campaign || state.selectedCampaign || '';
    if (preselected && qs('analytics-campaign')) qs('analytics-campaign').value = preselected;
    const jobId = qs('analytics-campaign')?.value || preselected || '';
    if (!jobId) {
      showAnalyticsEmpty('Выберите рассылку, чтобы посмотреть детальную аналитику');
      return;
    }
    state.filters.campaign = jobId;
    state.selectedCampaign = jobId;
    if (qs('filter-campaign')) qs('filter-campaign').value = jobId;
    const result = await api(`/api/sender/campaign-analytics/${encodeURIComponent(jobId)}${refresh ? '?refresh=true' : ''}`);
    renderCampaignAnalytics(result);
    schedulePoll(!!result.refresh_in_progress);
  }

  async function loadConsents() {
    const result = await api(`/api/sender/consents${queryString({
      ...consentFilterParams(),
      q: qs('consents-search')?.value || '',
      page: state.pagination.consents,
      per_page: state.perPage,
    })}`);
    renderConsents(result);
  }

  async function loadProblems() {
    const result = await api(`/api/sender/email-problems${queryString({
      ...problemFilterParams(),
      page: state.pagination.problems,
      per_page: state.perPage,
    })}`);
    renderProblems(result);
    if (state.selectedProblem?.row_key) {
      openProblemCard(state.selectedProblem.row_key, result.items);
    }
  }

  async function loadReports() {
    const result = await api(`/api/sender/reports${queryString()}`);
    renderReports(result);
  }

  async function loadCurrentPage(refresh = false) {
    clearError();
    clearPoll();
    setRefreshBadge(false);
    setBusy(true);
    try {
      if (state.page === 'dashboard') await loadDashboard(refresh);
      else if (state.page === 'campaigns') await loadCampaigns();
      else if (state.page === 'recipients') await loadRecipients();
      else if (state.page === 'campaign-analytics') await loadCampaignAnalytics(refresh);
      else if (state.page === 'consents') await loadConsents();
      else if (state.page === 'problems') await loadProblems();
      else if (state.page === 'reports') await loadReports();
    } catch (error) {
      console.error(error);
      if (error && error.message !== 'Unauthorized') {
        showError(error.message || 'Не удалось загрузить данные.');
      }
    } finally {
      setBusy(false);
    }
  }

  function activatePage(page) {
    const prevPage = state.page;
    if (prevPage !== page) {
      clearTabFiltersForPage(page);
      closeSidePanels();
    }
    state.page = page;
    document.querySelectorAll('.stx-tab[data-page]').forEach((item) => {
      item.classList.toggle('active', item.dataset.page === page);
    });
    document.querySelectorAll('.stats-page').forEach((section) => {
      section.classList.toggle('active', section.id === `page-${page}`);
    });
    qs('page-title').textContent = PAGE_TITLES[page] || 'Статистика';
    updateFilterBarForPage(page);
    syncFiltersToUrl();
    loadCurrentPage();
  }

  function openModal(id) { qs(id)?.classList.add('open'); }
  function closeModal(id) { qs(id)?.classList.remove('open'); }

  async function openActionModal(rowKey, defaultType = 'call') {
    const detail = await api(`/api/sender/recipients/${encodeURIComponent(rowKey)}`);
    state.actionRecipient = detail;
    state.actionType = defaultType;
    qs('action-recipient-summary').innerHTML = `
      <p><strong>${escapeHtml(detail.organization)}</strong> · ${escapeHtml(detail.recipient_name)}</p>
      <p>${escapeHtml(detail.email)} · ${badge(detail.manager_status)} · ${escapeHtml(detail.interest?.label)}</p>
    `;
    qs('action-type-cards').innerHTML = ACTION_TYPES.map(([value, label]) => `
      <div class="action-card ${state.actionType === value ? 'active' : ''}" data-action-type="${value}">${label}</div>
    `).join('');
    qs('action-type-cards').querySelectorAll('.action-card').forEach((card) => {
      card.addEventListener('click', () => {
        state.actionType = card.dataset.actionType;
        qs('action-type-cards').querySelectorAll('.action-card').forEach((node) => node.classList.toggle('active', node.dataset.actionType === state.actionType));
      });
    });
    qs('action-manager').value = state.userName || '';
    openModal('modal-action');
  }

  async function saveAction() {
    if (!state.actionRecipient) return;
    const dueDate = qs('action-date').value;
    const dueTime = qs('action-time').value;
    const dueAt = dueDate ? `${dueDate}${dueTime ? `T${dueTime}` : ''}` : '';
    const savedRowKey = state.actionRecipient.row_key;
    await api(`/api/sender/recipients/${encodeURIComponent(savedRowKey)}/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action_type: state.actionType,
        responsible_manager: qs('action-manager').value,
        due_at: dueAt,
        comment: qs('action-comment').value,
        priority: qs('action-priority').checked,
      }),
    });
    closeModal('modal-action');
    const reopenRecipientCard = state.page === 'recipients' && !qs('recipient-card')?.classList.contains('hidden');
    if (reopenRecipientCard) state.selectedRecipient = { row_key: savedRowKey };
    await loadCurrentPage();
  }

  async function submitExport() {
    const payload = {
      report_type: qs('export-type').value,
      period_from: qs('export-from').value,
      period_to: qs('export-to').value,
      job_id: qs('export-campaign').value || null,
      fmt: qs('export-format').value,
      options: {
        include_statuses: qs('export-include-statuses').checked,
        include_consents: qs('export-include-consents').checked,
        active_only: qs('export-active-only').checked,
      },
    };
    const result = await api('/api/sender/reports/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    closeModal('modal-export');
    if (result.report_id) {
      window.location.href = `/api/sender/reports/download/${encodeURIComponent(result.report_id)}`;
    }
    loadReports();
  }

  function applyAdvancedFilters() {
    state.filters.period_from = qs('adv-from').value;
    state.filters.period_to = qs('adv-to').value;
    state.filters.campaign = qs('adv-campaign').value;
    state.filters.consent_status = qs('adv-consent-status').value;
    state.filters.manager_action = qs('adv-manager-action').value;
    state.filters.organization = qs('adv-organization').value;
    state.filters.problems_only = qs('adv-problems-only').checked;
    const providers = Array.from(qs('adv-providers').selectedOptions).map((option) => option.value);
    state.filters.providers = providers.join(',');
    syncGlobalFiltersToDom();
    if (state.filters.campaign && qs('analytics-campaign')) qs('analytics-campaign').value = state.filters.campaign;
    closeModal('modal-filters');
    syncFiltersToUrl();
    loadCurrentPage(true);
  }

  function bindEvents() {
    document.querySelectorAll('.stx-tab[data-page]').forEach((item) => {
      item.addEventListener('click', () => activatePage(item.dataset.page));
    });
    qs('btn-refresh')?.addEventListener('click', () => { loadCurrentPage(true); startAutoRefresh(); });
    qs('stats-error-retry')?.addEventListener('click', () => { clearError(); loadCurrentPage(); });
    qs('btn-advanced-filters')?.addEventListener('click', () => openModal('modal-filters'));
    qs('btn-export-report')?.addEventListener('click', () => openModal('modal-export'));
    ['filter-from', 'filter-to', 'filter-campaign', 'filter-provider'].forEach((id) => {
      qs(id)?.addEventListener('change', () => {
        const key = id.replace('filter-', '');
        state.filters[key] = qs(id).value;
        if (key === 'campaign') {
          state.selectedCampaign = qs(id).value;
          if (qs('analytics-campaign')) qs('analytics-campaign').value = qs(id).value;
        }
        syncFiltersToUrl();
        loadCurrentPage();
      });
    });
    qs('filter-status')?.addEventListener('change', () => {
      state.filters.status = qs('filter-status').value;
      state.pagination.recipients = 1;
      syncFiltersToUrl();
      loadRecipients();
    });
    qs('analytics-campaign')?.addEventListener('change', () => {
      const jobId = qs('analytics-campaign').value;
      state.filters.campaign = jobId;
      state.selectedCampaign = jobId;
      if (qs('filter-campaign')) qs('filter-campaign').value = jobId;
      loadCampaignAnalytics();
    });
    qs('campaigns-search')?.addEventListener('input', () => {
      debounceSearch('campaigns', () => loadCampaigns());
    });
    qs('recipients-search')?.addEventListener('input', () => {
      debounceSearch('recipients', () => { state.pagination.recipients = 1; loadRecipients(); });
    });
    qs('consents-search')?.addEventListener('input', () => {
      debounceSearch('consents', () => { state.pagination.consents = 1; loadConsents(); });
    });
    qs('recipient-card-close')?.addEventListener('click', () => qs('recipient-card').classList.add('hidden'));
    qs('problem-card-close')?.addEventListener('click', () => qs('problem-card').classList.add('hidden'));
    qs('adv-cancel')?.addEventListener('click', () => closeModal('modal-filters'));
    qs('adv-apply')?.addEventListener('click', applyAdvancedFilters);
    qs('adv-reset')?.addEventListener('click', () => {
      clearAllFilters();
      closeModal('modal-filters');
      syncFiltersToUrl();
      loadCurrentPage(true);
    });
    qs('export-cancel')?.addEventListener('click', () => closeModal('modal-export'));
    qs('export-submit')?.addEventListener('click', submitExport);
    qs('action-cancel')?.addEventListener('click', () => closeModal('modal-action'));
    qs('action-save')?.addEventListener('click', saveAction);
    document.querySelectorAll('.stx-modal').forEach((modal) => {
      modal.addEventListener('click', (event) => {
        if (event.target === modal) modal.classList.remove('open');
      });
    });
  }

  let initialized = false;

  async function init() {
    if (initialized) return;
    initialized = true;
    try {
      const response = await fetch('/api/auth/me', { credentials: 'same-origin' });
      if (response.ok) {
        const data = await response.json();
        state.userName = data.result?.user?.username || data.user?.username || '';
        if (qs('stats-user-name')) qs('stats-user-name').textContent = state.userName || 'Менеджер';
      }
    } catch (_) {
      /* section still works with the cookie session */
    }
    initFilterDefaults();
    bindEvents();
    activatePage(state.page);
    // The campaigns endpoint also fills the campaign dropdowns. The campaigns
    // page already loads it via activatePage; for other pages fetch it once in
    // the background (reuses the server-side cache) without blocking first paint.
    if (state.page !== 'campaigns' && !state.campaignsLoaded) {
      loadCampaigns().catch((error) => console.error(error));
    }
  }

  // Lazy entry point used by the host SPA (index.html). Nothing runs until the
  // statistics screen is opened, and polling is suspended while it is hidden.
  function show() {
    const wasInitialized = initialized;
    init();
    if (wasInitialized) loadCurrentPage();
    startAutoRefresh();
  }

  function hide() {
    stopAutoRefresh();
    clearPoll();
  }

  window.StatsEmbed = { show, hide };
}());
