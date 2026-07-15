(function () {
  const AUTO_REFRESH_MS = 20 * 60 * 1000;
  const DASHBOARD_CACHE_PREFIX = 'stats-dashboard-v3:';

  const PAGE_TITLES = {
    dashboard: 'Аналитика рассылок',
    campaigns: 'Рассылки',
    recipients: 'Организации и статусы',
    'campaign-analytics': 'Детальная аналитика рассылки',
    consents: 'Согласия и интерес',
    problems: 'Проблемы с email',
    reports: 'Отчёты и выгрузки',
  };

  const RECIPIENT_CHIPS = [
    ['', 'Все'],
    ['delivered', 'Доставлено'],
    ['opened', 'Открыто'],
    ['clicked', 'Переходы'],
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

  function companyField(item, field) {
    return item.company?.fields?.[field]?.display ?? '—';
  }
  function companyEmailsText(item) {
    const emails = (item.emails || []).map((entry) => entry.email).filter(Boolean);
    return emails.length ? emails.join(', ') : (item.email || '—');
  }
  function renderCompanyEmails(item) {
    const emails = item.emails || [];
    if (!emails.length) return escapeHtml(item.email || '—');
    return emails.map((entry) => `<div class="company-email">${escapeHtml(entry.email)}${entry.role_label ? ` <span class="muted">(${escapeHtml(entry.role_label)})</span>` : ''}${entry.manager_status?.label ? ` · ${escapeHtml(entry.manager_status.label)}` : ''}</div>`).join('');
  }

  const DRILLDOWN_RECIPIENT_COLUMNS = [
    ['Компания', (item) => item.organization],
    ['Регион', (item) => companyField(item, 'region')],
    ['ИНН', (item) => companyField(item, 'inn')],
    ['Контакты', (item) => companyEmailsText(item)],
    ['Статус', (item) => item.manager_status?.label],
    ['Последнее событие', (item) => item.last_event_label],
    ['Дата события', (item) => item.last_event_at],
    ['Интерес', (item) => item.interest?.label],
    ['Следующее действие', (item) => item.next_action?.label],
  ];

  const DRILLDOWN_CONSENT_COLUMNS = [
    ['Компания', (item) => item.organization],
    ['Контакт', (item) => item.contact],
    ['Email', (item) => item.email],
    ['Статус согласия', (item) => item.consent_status_label],
    ['Материалы', (item) => item.materials_label],
    ['Последнее действие', (item) => item.last_action_label],
    ['Дата', (item) => item.last_action_at],
    ['Интерес', (item) => item.interest?.label],
    ['Следующее действие', (item) => item.next_action?.label],
  ];

  const DRILLDOWN_CAMPAIGN_COLUMNS = [
    ['Название', (item) => item.title],
    ['Период', (item) => item.period_label],
    ['Провайдер', (item) => item.provider_label],
    ['Отправлено', (item) => item.sent],
    ['Доставлено', (item) => `${item.delivered} / ${item.delivery_rate}%`],
    ['Открыто', (item) => `${item.opened} / ${item.open_rate}%`],
    ['Переходы', (item) => `${item.clicked} / ${item.ctr}%`],
    ['Согласия', (item) => item.consents],
    ['Статус', (item) => item.status_label],
  ];

  const DRILLDOWN_PROBLEM_COLUMNS = [
    ['Компания', (item) => item.organization],
    ['Контакты', (item) => companyEmailsText(item)],
    ['Причина', (item) => item.bounce_reason_label],
    ['Провайдер', (item) => item.provider],
    ['Писем', (item) => item.attempts],
    ['Последнее событие', (item) => item.last_event_at],
    ['Рекомендация', (item) => item.recommended_action?.label],
  ];

  const DRILLDOWN_REPORT_COLUMNS = [
    ['Отчёт', (item) => item.report_type],
    ['Период', (item) => `${item.period_from || ''} — ${item.period_to || ''}`],
    ['Формат', (item) => item.format],
    ['Создан', (item) => item.created_at],
    ['Автор', (item) => item.author],
    ['Статус', (item) => item.status],
  ];

  const DRILLDOWN_CONFIG = {
    sent: { title: 'Компании в рассылке', source: 'recipients', columns: DRILLDOWN_RECIPIENT_COLUMNS, params: {} },
    delivered: { title: 'Доставлено', source: 'recipients', columns: DRILLDOWN_RECIPIENT_COLUMNS, params: { quick_filter: 'delivered' } },
    opened: { title: 'Открыто', source: 'recipients', columns: DRILLDOWN_RECIPIENT_COLUMNS, params: { quick_filter: 'opened' } },
    clicked: { title: 'Переходы', source: 'recipients', columns: DRILLDOWN_RECIPIENT_COLUMNS, params: { quick_filter: 'clicked' } },
    problems: { title: 'Ошибки', source: 'recipients', columns: DRILLDOWN_RECIPIENT_COLUMNS, params: { quick_filter: 'problems' } },
    pending: { title: 'Ожидают статуса', source: 'recipients', columns: DRILLDOWN_RECIPIENT_COLUMNS, params: { quick_filter: 'pending' } },
    consents: { title: 'Согласия', source: 'consents', columns: DRILLDOWN_CONSENT_COLUMNS, params: {} },
    materials: {
      title: 'Материалы отправлены',
      source: 'consents',
      columns: DRILLDOWN_CONSENT_COLUMNS,
      params: {},
      filter: (item) => item.materials_label === 'Материалы отправлены',
    },
    // Аналитика рассылки
    errors: {
      title: 'Недоставлено',
      source: 'recipients',
      columns: DRILLDOWN_RECIPIENT_COLUMNS,
      params: {},
      filter: (i) => ['email_broken', 'soft_bounce', 'delivery_error', 'spam'].includes(i.manager_status?.key),
    },
    unsub_spam: {
      title: 'Отписки и спам',
      source: 'recipients',
      columns: DRILLDOWN_RECIPIENT_COLUMNS,
      params: {},
      filter: (i) => ['unsubscribed', 'spam'].includes(i.manager_status?.key),
    },
    // Получатели
    recipients_active: {
      title: 'Активные получатели',
      source: 'recipients',
      columns: DRILLDOWN_RECIPIENT_COLUMNS,
      params: {},
      filter: (i) => ['opened', 'clicked'].includes(i.manager_status?.key),
    },
    recipients_call: {
      title: 'Нужно перезвонить',
      source: 'recipients',
      columns: DRILLDOWN_RECIPIENT_COLUMNS,
      params: {},
      filter: (i) => i.next_action?.key === 'call',
    },
    // Рассылки
    campaigns_all: { title: 'Все рассылки', source: 'campaigns', columns: DRILLDOWN_CAMPAIGN_COLUMNS, params: {} },
    campaigns_active: {
      title: 'Активные рассылки',
      source: 'campaigns',
      columns: DRILLDOWN_CAMPAIGN_COLUMNS,
      params: {},
      filter: (i) => i.status === 'active',
    },
    campaigns_completed: {
      title: 'Завершённые рассылки',
      source: 'campaigns',
      columns: DRILLDOWN_CAMPAIGN_COLUMNS,
      params: {},
      filter: (i) => i.status === 'completed',
    },
    campaigns_draft: {
      title: 'Черновики',
      source: 'campaigns',
      columns: DRILLDOWN_CAMPAIGN_COLUMNS,
      params: {},
      filter: (i) => i.status === 'draft',
    },
    campaigns_delivery: { title: 'Доставляемость по рассылкам', source: 'campaigns', columns: DRILLDOWN_CAMPAIGN_COLUMNS, params: {} },
    campaigns_open: { title: 'Открываемость по рассылкам', source: 'campaigns', columns: DRILLDOWN_CAMPAIGN_COLUMNS, params: {} },
    // Согласия
    consents_confirmed: { title: 'Дали согласие', source: 'consents', columns: DRILLDOWN_CONSENT_COLUMNS, params: { consent_status: 'confirmed' } },
    consents_opened: {
      title: 'Открыли после согласия',
      source: 'consents',
      columns: DRILLDOWN_CONSENT_COLUMNS,
      params: { consent_status: 'confirmed' },
      filter: (i) => !!i.materials_sent_at,
    },
    consents_call: {
      title: 'Нужно перезвонить',
      source: 'consents',
      columns: DRILLDOWN_CONSENT_COLUMNS,
      params: {},
      filter: (i) => i.interest?.key === 'high',
    },
    // Проблемы с email
    problems_all: { title: 'Проблемные адреса', source: 'email-problems', columns: DRILLDOWN_PROBLEM_COLUMNS, params: {} },
    problems_hard: {
      title: 'Постоянные ошибки',
      source: 'email-problems',
      columns: DRILLDOWN_PROBLEM_COLUMNS,
      params: {},
      filter: (i) => i.manager_status?.key === 'email_broken',
    },
    problems_soft: {
      title: 'Временные ошибки',
      source: 'email-problems',
      columns: DRILLDOWN_PROBLEM_COLUMNS,
      params: {},
      filter: (i) => i.manager_status?.key === 'soft_bounce',
    },
    // Отчёты (клиентская фильтрация уже загруженной истории)
    reports_all: { title: 'Все отчёты', source: 'reports', columns: DRILLDOWN_REPORT_COLUMNS, params: {} },
    reports_xlsx: { title: 'Excel выгрузки', source: 'reports', columns: DRILLDOWN_REPORT_COLUMNS, params: {}, filter: (i) => i.format === 'xlsx' },
    reports_csv: { title: 'CSV выгрузки', source: 'reports', columns: DRILLDOWN_REPORT_COLUMNS, params: {}, filter: (i) => i.format === 'csv' },
    reports_ndjson: { title: 'NDJSON журналы', source: 'reports', columns: DRILLDOWN_REPORT_COLUMNS, params: {}, filter: (i) => i.format === 'ndjson' },
  };

  const drilldown = { columns: [], rows: [], title: '', truncated: false, requestId: 0 };

  const STATS_PAGES = new Set([
    'dashboard',
    'campaigns',
    'recipients',
    'campaign-analytics',
    'consents',
    'problems',
    'reports',
  ]);

  const MODAL_ID_TO_KEY = {
    'modal-company': 'company',
    'modal-drilldown': 'drill',
    'modal-campaign-summary': 'campaign',
    'modal-action': 'action',
    'modal-export': 'export',
    'modal-filters': 'filters',
  };

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
    activeModal: '',
    modalParams: {},
    charts: {},
    campaigns: [],
    campaignsLoaded: false,
    userName: '',
    pollTimer: null,
    autoRefreshTimer: null,
    busyDepth: 0,
    searchTimers: {},
    silentRefresh: false,
    cachedCampaignIds: '',
    lastDashboardKey: '',
    reportsHistory: [],
  };

  let pendingRecipientQuickFilter = null;
  let suppressUrlSync = false;
  let urlReady = false;
  let lastWrittenHash = '';

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

  function isStatisticsScreenActive() {
    return !!document.getElementById('s-statistics')?.classList.contains('active');
  }

  function buildStatsUrlParams() {
    const params = new URLSearchParams();
    const gf = readGlobalFiltersFromDom();
    if (gf.period_from) params.set('from', gf.period_from);
    if (gf.period_to) params.set('to', gf.period_to);
    if (gf.campaign) params.set('campaign', gf.campaign);
    if (gf.provider) params.set('provider', gf.provider);

    const f = state.filters;
    if (f.providers) params.set('providers', f.providers);
    if (f.organization) params.set('org', f.organization);
    if (f.consent_status) params.set('consent_status', f.consent_status);
    if (f.manager_action) params.set('manager_action', f.manager_action);
    if (f.problems_only) params.set('problems_only', '1');
    if (f.quick_filter) params.set('quick_filter', f.quick_filter);
    if (f.status) params.set('status', f.status);

    if (state.pagination.recipients > 1) params.set('rp', String(state.pagination.recipients));
    if (state.pagination.consents > 1) params.set('cp', String(state.pagination.consents));
    if (state.pagination.problems > 1) params.set('pp', String(state.pagination.problems));

    const qCampaigns = qs('campaigns-search')?.value || '';
    const qRecipients = qs('recipients-search')?.value || '';
    const qConsents = qs('consents-search')?.value || '';
    if (qCampaigns) params.set('q_campaigns', qCampaigns);
    if (qRecipients) params.set('q_recipients', qRecipients);
    if (qConsents) params.set('q_consents', qConsents);

    if (state.activeModal) {
      params.set('m', state.activeModal);
      const mp = state.modalParams || {};
      if ((state.activeModal === 'company' || state.activeModal === 'action') && mp.row) {
        params.set('row', mp.row);
      }
      if (state.activeModal === 'action' && mp.at) params.set('at', mp.at);
      if (state.activeModal === 'drill') {
        if (mp.d) params.set('d', mp.d);
        if (mp.d_org) params.set('d_org', mp.d_org);
        if (mp.d_email) params.set('d_email', mp.d_email);
      }
      if (state.activeModal === 'campaign' && mp.cs) params.set('cs', mp.cs);
      if (state.activeModal === 'export' && mp.et) params.set('et', mp.et);
    }

    return params;
  }

  function buildStatsHash() {
    const query = buildStatsUrlParams().toString();
    return `#stats/${state.page}${query ? `?${query}` : ''}`;
  }

  function writeStatsHash(hash, { push = false } = {}) {
    const full = hash.startsWith('#') ? hash : `#${hash}`;
    if (full === lastWrittenHash) return;
    lastWrittenHash = full;
    suppressUrlSync = true;
    if (push) {
      location.hash = full.slice(1);
    } else {
      history.replaceState(null, '', `${location.pathname}${location.search}${full}`);
    }
    queueMicrotask(() => {
      suppressUrlSync = false;
    });
  }

  function syncFiltersToUrl({ push = false } = {}) {
    if (suppressUrlSync || !urlReady || !isStatisticsScreenActive()) return;
    writeStatsHash(buildStatsHash(), { push });
  }

  function parseStatsHash() {
    const raw = (location.hash || '').replace(/^#/, '');
    if (!raw.startsWith('stats/')) return null;
    const rest = raw.slice('stats/'.length);
    const qIdx = rest.indexOf('?');
    const pagePart = qIdx >= 0 ? rest.slice(0, qIdx) : rest;
    const page = pagePart || 'dashboard';
    if (!STATS_PAGES.has(page)) return null;

    const params = new URLSearchParams(qIdx >= 0 ? rest.slice(qIdx + 1) : '');
    const filters = {};
    if (params.get('from')) filters.period_from = params.get('from');
    if (params.get('to')) filters.period_to = params.get('to');
    if (params.get('campaign')) filters.campaign = params.get('campaign');
    if (params.get('provider')) filters.provider = params.get('provider');
    if (params.get('providers')) filters.providers = params.get('providers');
    if (params.get('org')) filters.organization = params.get('org');
    if (params.get('consent_status')) filters.consent_status = params.get('consent_status');
    if (params.get('manager_action')) filters.manager_action = params.get('manager_action');
    if (params.get('problems_only') === '1') filters.problems_only = true;
    if (params.get('quick_filter')) filters.quick_filter = params.get('quick_filter');
    if (params.get('status')) filters.status = params.get('status');

    const pagination = { recipients: 1, consents: 1, problems: 1 };
    const rp = Number(params.get('rp') || 1);
    const cp = Number(params.get('cp') || 1);
    const pp = Number(params.get('pp') || 1);
    if (rp > 1) pagination.recipients = rp;
    if (cp > 1) pagination.consents = cp;
    if (pp > 1) pagination.problems = pp;

    const searches = {};
    if (params.get('q_campaigns')) searches.q_campaigns = params.get('q_campaigns');
    if (params.get('q_recipients')) searches.q_recipients = params.get('q_recipients');
    if (params.get('q_consents')) searches.q_consents = params.get('q_consents');

    const modal = params.get('m') || '';
    const modalParams = {};
    if (modal === 'company' || modal === 'action') {
      if (params.get('row')) modalParams.row = params.get('row');
    }
    if (modal === 'action' && params.get('at')) modalParams.at = params.get('at');
    if (modal === 'drill') {
      if (params.get('d')) modalParams.d = params.get('d');
      if (params.get('d_org')) modalParams.d_org = params.get('d_org');
      if (params.get('d_email')) modalParams.d_email = params.get('d_email');
    }
    if (modal === 'campaign' && params.get('cs')) modalParams.cs = params.get('cs');
    if (modal === 'export' && params.get('et')) modalParams.et = params.get('et');

    return { page, filters, pagination, searches, modal, modalParams };
  }

  function seedStateFromHash(parsed) {
    if (!parsed) return;
    state.page = parsed.page;
    state.filters = { ...parsed.filters };
    state.pagination = { ...state.pagination, ...parsed.pagination };
    state.activeModal = parsed.modal || '';
    state.modalParams = { ...parsed.modalParams };
    if (parsed.filters.campaign) state.selectedCampaign = parsed.filters.campaign;
  }

  function syncSearchInputsToDom(searches = {}) {
    if (searches.q_campaigns && qs('campaigns-search')) qs('campaigns-search').value = searches.q_campaigns;
    if (searches.q_recipients && qs('recipients-search')) qs('recipients-search').value = searches.q_recipients;
    if (searches.q_consents && qs('consents-search')) qs('consents-search').value = searches.q_consents;
  }

  function initFilterDefaults() {
    const parsed = parseStatsHash();
    if (parsed) {
      seedStateFromHash(parsed);
    } else {
      state.page = 'dashboard';
    }
    syncGlobalFiltersToDom();
    syncAdvancedFiltersToDom();
    syncSearchInputsToDom(parsed?.searches || {});
  }

  function syncGlobalFiltersToDom() {
    if (qs('filter-from')) qs('filter-from').value = state.filters.period_from || '';
    if (qs('filter-to')) qs('filter-to').value = state.filters.period_to || '';
    if (qs('filter-campaign')) qs('filter-campaign').value = state.filters.campaign || '';
    if (qs('filter-provider')) qs('filter-provider').value = state.filters.provider || '';
    if (qs('filter-status')) qs('filter-status').value = state.filters.status || '';
  }

  function syncAdvancedFiltersToDom() {
    if (qs('adv-from')) qs('adv-from').value = state.filters.period_from || '';
    if (qs('adv-to')) qs('adv-to').value = state.filters.period_to || '';
    if (qs('adv-campaign')) qs('adv-campaign').value = state.filters.campaign || '';
    if (qs('adv-consent-status')) qs('adv-consent-status').value = state.filters.consent_status || '';
    if (qs('adv-manager-action')) qs('adv-manager-action').value = state.filters.manager_action || '';
    if (qs('adv-organization')) qs('adv-organization').value = state.filters.organization || '';
    if (qs('adv-problems-only')) qs('adv-problems-only').checked = !!state.filters.problems_only;
    if (qs('adv-providers')) {
      const selected = new Set(String(state.filters.providers || '').split(',').filter(Boolean));
      Array.from(qs('adv-providers').options).forEach((option) => {
        option.selected = selected.has(option.value);
      });
    }
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

  function closeSidePanels({ syncUrl = true } = {}) {
    closeModal('modal-company', { syncUrl });
    qs('problem-card')?.classList.add('hidden');
    state.selectedRecipient = null;
    state.selectedProblem = null;
  }

  function closeAllModals({ syncUrl = true } = {}) {
    Object.keys(MODAL_ID_TO_KEY).forEach((id) => {
      if (isModalOpen(id)) closeModal(id, { syncUrl: false });
    });
    state.activeModal = '';
    state.modalParams = {};
    if (syncUrl) syncFiltersToUrl({ push: true });
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

  function dashboardCacheKey() {
    return DASHBOARD_CACHE_PREFIX + JSON.stringify(readGlobalFiltersFromDom());
  }

  function readDashboardCache() {
    try {
      const raw = sessionStorage.getItem(dashboardCacheKey());
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  }

  function writeDashboardCache(result) {
    try {
      sessionStorage.setItem(dashboardCacheKey(), JSON.stringify(result));
    } catch (_) {
      /* sessionStorage full or unavailable */
    }
  }

  function dashboardDataKey(result) {
    if (!result) return '';
    const copy = { ...result };
    delete copy.generated_at_label;
    delete copy.refresh_in_progress;
    delete copy.refresh_started;
    return JSON.stringify(copy);
  }

  function updateRefreshMeta(result) {
    if (qs('refresh-meta')) {
      qs('refresh-meta').textContent = `Обновлено: ${result?.generated_at_label || 'сейчас'}`;
    }
  }

  function dashboardSkeletonPresent() {
    return (qs('dashboard-kpis')?.querySelectorAll('.kpi-card').length || 0) >= 8;
  }

  function dashboardHasInstantPaint() {
    return !!readDashboardCache() || dashboardSkeletonPresent();
  }

  function applyDashboardResult(result, { forceRender = false } = {}) {
    const dataKey = dashboardDataKey(result);
    const unchanged = dataKey === state.lastDashboardKey;
    if (!forceRender && unchanged) {
      updateRefreshMeta(result);
      return false;
    }
    renderDashboard(result);
    state.lastDashboardKey = dataKey;
    return true;
  }

  function paintDashboardFromCacheSync() {
    if (state.page !== 'dashboard') return;
    const cached = readDashboardCache();
    if (cached) {
      applyDashboardResult(cached);
      schedulePoll(!!cached.refresh_in_progress);
    }
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
    qs('stats-status')?.classList.toggle('is-hidden', state.busyDepth === 0);
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

  function clearPoll() {
    if (state.pollTimer) {
      clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function schedulePoll(inProgress) {
    clearPoll();
    if (inProgress) {
      state.pollTimer = setTimeout(() => { loadCurrentPage(false, { silent: true }); }, 5000);
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
    state.autoRefreshTimer = setInterval(() => { loadCurrentPage(false, { silent: true }); }, AUTO_REFRESH_MS);
  }

  function destroyChart(id) {
    if (state.charts[id]) {
      state.charts[id].destroy();
      delete state.charts[id];
    }
  }

  function upsertChart(id, canvas, config) {
    if (!canvas) return null;
    const existing = state.charts[id];
    if (existing) {
      existing.data = config.data;
      if (config.options) existing.options = config.options;
      existing.update('none');
      return existing;
    }
    state.charts[id] = new Chart(canvas, config);
    return state.charts[id];
  }

  function setContainerHtml(container, html, bindFn) {
    if (!container) return false;
    if (container.innerHTML === html) return false;
    container.innerHTML = html;
    if (bindFn) bindFn();
    return true;
  }

  function applyKpiDrill(card, drill) {
    if (drill) {
      card.dataset.drill = drill;
      card.classList.add('clickable');
      card.setAttribute('role', 'button');
      card.setAttribute('tabindex', '0');
    } else {
      delete card.dataset.drill;
      card.classList.remove('clickable');
      card.removeAttribute('role');
      card.removeAttribute('tabindex');
    }
  }

  function bindKpiDrill(container) {
    if (container.dataset.drillBound) return;
    container.dataset.drillBound = '1';
    const trigger = (event) => {
      const card = event.target.closest('.kpi-card[data-drill]');
      if (!card || !container.contains(card)) return;
      openDrilldownModal(card.dataset.drill);
    };
    container.addEventListener('click', trigger);
    container.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const card = event.target.closest('.kpi-card[data-drill]');
      if (!card) return;
      event.preventDefault();
      openDrilldownModal(card.dataset.drill);
    });
  }

  function renderKpis(containerId, items) {
    const container = qs(containerId);
    if (!container) return;
    const cards = container.querySelectorAll('.kpi-card');
    if (cards.length === items.length && items.length > 0) {
      cards.forEach((card, index) => {
        const label = card.querySelector('.label');
        const value = card.querySelector('.value');
        const item = items[index];
        if (label) label.textContent = item.title;
        if (value) value.textContent = String(item.value);
        applyKpiDrill(card, item.drill);
      });
      bindKpiDrill(container);
      return;
    }
    container.innerHTML = items.map((item) => `
      <div class="kpi-card${item.drill ? ' clickable' : ''}"${item.drill ? ` role="button" tabindex="0" data-drill="${escapeHtml(item.drill)}"` : ''}>
        <div class="label">${escapeHtml(item.title)}</div>
        <div class="value">${escapeHtml(item.value)}</div>
      </div>
    `).join('');
    bindKpiDrill(container);
  }

  // The funnel visualises conversion between stages as percentages. Absolute
  // counts live in the KPI cards, so the funnel deliberately shows only the
  // conversion percentage to avoid duplicating the same numbers.
  function renderFunnel(containerId, funnel) {
    const container = qs(containerId);
    if (!container) return;
    const steps = funnel || [];
    const html = steps.map((step) => `
      <div class="funnel-step">
        <div class="value">${step.percent ?? 0}%</div>
        <div class="label">${escapeHtml(step.label)}</div>
      </div>
    `).join('');
    setContainerHtml(container, html);
  }

  function renderPagination(containerId, pagination, key) {
    const container = qs(containerId);
    if (!container || !pagination) return;
    const html = `
      <span>Показано ${Math.min((pagination.page - 1) * pagination.per_page + 1, pagination.total)}–${Math.min(pagination.page * pagination.per_page, pagination.total)} из ${fmt(pagination.total)}</span>
      <span>
        <button class="btn-outline" data-page="${pagination.page - 1}" ${pagination.page <= 1 ? 'disabled' : ''}>Назад</button>
        <span> ${pagination.page} / ${pagination.pages} </span>
        <button class="btn-outline" data-page="${pagination.page + 1}" ${pagination.page >= pagination.pages ? 'disabled' : ''}>Вперёд</button>
      </span>
    `;
    setContainerHtml(container, html, () => {
      container.querySelectorAll('button[data-page]').forEach((button) => {
        button.addEventListener('click', () => {
          state.pagination[key] = Number(button.dataset.page);
          syncFiltersToUrl();
          loadCurrentPage();
        });
      });
    });
  }

  function renderDashboard(result) {
    renderKpis('dashboard-kpis', [
      { title: 'Компаний в рассылке', value: fmt(result.summary?.sent), drill: 'sent' },
      { title: 'Доставлено', value: `${fmt(result.summary?.delivered)} / ${result.rates?.delivery_rate ?? 0}%`, drill: 'delivered' },
      { title: 'Открыто', value: `${fmt(result.summary?.opened)} / ${result.rates?.open_rate ?? 0}%`, drill: 'opened' },
      { title: 'Переходы', value: `${fmt(result.summary?.clicked)} / ${result.rates?.ctr ?? 0}%`, drill: 'clicked' },
      { title: 'Ошибки', value: `${fmt(result.summary?.errors)} / ${result.rates?.error_rate ?? 0}%`, drill: 'problems' },
      { title: 'Ожидают статуса', value: fmt(result.summary?.pending), drill: 'pending' },
      { title: 'Согласия', value: fmt(result.summary?.consents), drill: 'consents' },
      { title: 'Материалы отправлены', value: fmt(result.summary?.materials_sent), drill: 'materials' },
    ]);
    renderFunnel('dashboard-funnel', result.funnels);
    qs('dashboard-empty')?.classList.toggle('hidden', !result.empty);
    updateRefreshMeta(result);

    upsertChart('chart-statuses', qs('chart-statuses'), {
      type: 'doughnut',
      data: {
        labels: (result.statuses || []).map((item) => item.label),
        datasets: [{ data: (result.statuses || []).map((item) => item.count), backgroundColor: ['#22c55e', '#8b5cf6', '#2563eb', '#ef4444', '#f59e0b', '#64748b'] }],
      },
      options: { maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } },
    });
    upsertChart('chart-providers', qs('chart-providers'), {
      type: 'bar',
      data: {
        labels: (result.providers || []).map((item) => item.label),
        datasets: [{ label: 'Отправлено', data: (result.providers || []).map((item) => item.count), backgroundColor: '#2563eb' }],
      },
      options: { maintainAspectRatio: false, indexAxis: 'y' },
    });
    const roles = result.roles || [];
    const rolesPlaceholder = qs('chart-roles-placeholder');
    const rolesCanvas = qs('chart-roles');
    if (roles.length >= 2) {
      rolesPlaceholder?.classList.add('hidden');
      rolesCanvas?.classList.remove('hidden');
      upsertChart('chart-roles', rolesCanvas, {
        type: 'doughnut',
        data: {
          labels: roles.map((item) => item.label),
          datasets: [{ data: roles.map((item) => item.count), backgroundColor: ['#5a9e1f', '#c9b98a'] }],
        },
        options: { maintainAspectRatio: false },
      });
    } else {
      destroyChart('chart-roles');
      rolesCanvas?.classList.add('hidden');
      rolesPlaceholder?.classList.remove('hidden');
    }

    const worklists = result.work_lists || {};
    const worklistsHtml = [
      ['Заинтересованные', worklists.interested || [], 'recipients', 'opened'],
      ['Проблемы с email', worklists.email_problems || [], 'problems'],
    ].map(([title, items, page, quick]) => `
      <div class="panel">
        <h3>${escapeHtml(title)}</h3>
        ${items.length ? items.map((item) => `<div class="worklist-item"><span>${escapeHtml(item.organization)}</span><strong>${fmt(item.count)}</strong></div>`).join('') : '<div class="empty-state">Нет данных</div>'}
        <button class="btn-outline" data-nav="${page}" data-quick="${quick || ''}">Посмотреть все</button>
      </div>
    `).join('');
    setContainerHtml(qs('dashboard-worklists'), worklistsHtml, () => {
      qs('dashboard-worklists').querySelectorAll('[data-nav]').forEach((button) => {
        button.addEventListener('click', () => {
          pendingRecipientQuickFilter = button.dataset.quick || '';
          activatePage(button.dataset.nav);
        });
      });
    });
    setContainerHtml(
      qs('dashboard-insights'),
      (result.insights || []).map((item) => `<li><strong>${escapeHtml(item.title)}:</strong> ${escapeHtml(item.text)}</li>`).join(''),
    );
  }

  function campaignIdsSignature() {
    return state.campaigns.map((item) => item.job_id).join('\u0001');
  }

  function syncCampaignSelects() {
    const signature = campaignIdsSignature();
    const selectedCampaign = state.filters.campaign || state.selectedCampaign || '';
    if (signature === state.cachedCampaignIds) {
      const select = qs('filter-campaign');
      const analyticsSelect = qs('analytics-campaign');
      if (selectedCampaign) {
        if (select && select.value !== selectedCampaign) select.value = selectedCampaign;
        if (analyticsSelect && analyticsSelect.value !== selectedCampaign) analyticsSelect.value = selectedCampaign;
      }
      return;
    }
    state.cachedCampaignIds = signature;
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
    if (selectedCampaign) {
      if (select) select.value = selectedCampaign;
      if (analyticsSelect) analyticsSelect.value = selectedCampaign;
    }
  }

  function renderCampaigns(result) {
    state.campaigns = result.campaigns || [];
    syncCampaignSelects();
    renderKpis('campaigns-kpis', [
      { title: 'Всего рассылок', value: fmt(result.summary?.total), drill: 'campaigns_all' },
      { title: 'Активные', value: fmt(result.summary?.active), drill: 'campaigns_active' },
      { title: 'Завершённые', value: fmt(result.summary?.completed), drill: 'campaigns_completed' },
      { title: 'Черновики', value: fmt(result.summary?.draft), drill: 'campaigns_draft' },
      { title: 'Средняя доставляемость', value: `${result.summary?.avg_delivery_rate ?? 0}%`, drill: 'campaigns_delivery' },
      { title: 'Средняя открываемость', value: `${result.summary?.avg_open_rate ?? 0}%`, drill: 'campaigns_open' },
    ]);
    const search = (qs('campaigns-search')?.value || '').toLowerCase();
    const rows = state.campaigns.filter((item) => !search || item.title.toLowerCase().includes(search));
    const tableHtml = rows.map((item) => `
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
        <td><button class="btn-outline" type="button" data-open-analytics="${escapeHtml(item.job_id)}">Аналитика</button></td>
      </tr>
    `).join('') || '<tr><td colspan="10" class="empty-state">Пока нет рассылок с отправками</td></tr>';
    setContainerHtml(qs('campaigns-table'), tableHtml);
  }

  function openCampaignAnalytics(jobId) {
    if (!jobId) return;
    state.selectedCampaign = jobId;
    state.filters.campaign = jobId;
    if (qs('analytics-campaign')) qs('analytics-campaign').value = jobId;
    if (qs('filter-campaign')) qs('filter-campaign').value = jobId;
    activatePage('campaign-analytics');
  }

  function selectCampaign(jobId) {
    state.selectedCampaign = jobId;
    const item = state.campaigns.find((campaign) => campaign.job_id === jobId);
    if (!item) return;
    if (qs('campaign-summary-title')) qs('campaign-summary-title').textContent = item.title || 'Сводка по рассылке';
    qs('campaign-summary-modal-body').innerHTML = `
      <p>${escapeHtml(item.period_label)}</p>
      <p>Доставляемость: <strong>${item.delivery_rate}%</strong></p>
      <p>Открываемость: <strong>${item.open_rate}%</strong></p>
      <p>Доля переходов: <strong>${item.ctr}%</strong></p>
      <div class="modal-actions">
        <button class="btn-primary" id="campaign-open-analytics" type="button">Открыть аналитику</button>
        <button class="btn-outline" id="campaign-download-report" type="button">Скачать отчёт</button>
      </div>
    `;
    qs('campaign-open-analytics')?.addEventListener('click', () => {
      closeModal('modal-campaign-summary');
      openCampaignAnalytics(jobId);
    });
    qs('campaign-download-report')?.addEventListener('click', () => {
      window.location.href = `/api/download/sender-delivery-report?job_id=${encodeURIComponent(jobId)}`;
    });
    state.modalParams = { cs: jobId };
    openModal('modal-campaign-summary');
  }

  function renderRecipients(result) {
    renderKpis('recipients-kpis', [
      { title: 'Всего компаний', value: fmt(result.summary?.total), drill: 'sent' },
      { title: 'Активные', value: fmt(result.summary?.active), drill: 'recipients_active' },
      { title: 'Проблемные', value: fmt(result.summary?.problematic), drill: 'problems' },
      { title: 'Нужно перезвонить', value: fmt(result.summary?.need_call), drill: 'recipients_call' },
    ]);
    const chipsHtml = RECIPIENT_CHIPS.map(([value, label]) => `
      <button class="chip ${state.filters.quick_filter === value ? 'active' : ''}" data-quick="${value}">${label}</button>
    `).join('');
    setContainerHtml(qs('recipient-chips'), chipsHtml, () => {
      qs('recipient-chips').querySelectorAll('.chip').forEach((chip) => {
        chip.addEventListener('click', () => {
          state.filters.quick_filter = chip.dataset.quick;
          state.pagination.recipients = 1;
          syncFiltersToUrl();
          loadRecipients();
        });
      });
    });
    const tableHtml = (result.items || []).map((item) => `
      <tr data-row-key="${escapeHtml(item.row_key)}">
        <td>${escapeHtml(item.organization)}</td>
        <td>${escapeHtml(companyField(item, 'region'))}</td>
        <td>${escapeHtml(companyField(item, 'inn'))}</td>
        <td>${renderCompanyEmails(item)}</td>
        <td>${badge(item.manager_status)}</td>
        <td>${escapeHtml(item.interest?.label)}</td>
        <td>${escapeHtml(item.next_action?.label)}</td>
        <td><button class="btn-outline" data-action="${escapeHtml(item.row_key)}">⋯</button></td>
      </tr>
    `).join('') || '<tr><td colspan="8" class="empty-state">Нет компаний за выбранный период</td></tr>';
    const tableChanged = setContainerHtml(qs('recipients-table'), tableHtml, () => {
      qs('recipients-table').querySelectorAll('tr[data-row-key]').forEach((row) => {
        row.addEventListener('click', () => openCompanyModal(row.dataset.rowKey));
      });
      qs('recipients-table').querySelectorAll('[data-action]').forEach((button) => {
        button.addEventListener('click', (event) => {
          event.stopPropagation();
          openActionModal(button.dataset.action);
        });
      });
    });
    renderPagination('recipients-pagination', result.pagination, 'recipients');
    return tableChanged;
  }

  async function openCompanyModal(rowKey) {
    let detail;
    try {
      detail = await api(`/api/sender/recipients/${encodeURIComponent(rowKey)}`);
    } catch (error) {
      console.error(error);
      showError('Карточка компании недоступна: запись не найдена среди отправок.');
      return;
    }
    state.selectedRecipient = detail;
    renderCompanyModal(detail);
    state.modalParams = { row: rowKey };
    openModal('modal-company');
  }

  function renderCompanyModal(detail) {
    const fields = detail.company?.fields || {};
    const companyRows = Object.keys(fields).map((key) => {
      const field = fields[key];
      const cls = field.present ? '' : ' class="muted"';
      return `<div class="company-field"><span class="company-field-label">${escapeHtml(field.label)}</span><span${cls}>${escapeHtml(field.display)}</span></div>`;
    }).join('');
    const emailsHtml = (detail.emails || []).map((entry) => `
      <div class="timeline-item">
        <div>${escapeHtml(entry.email)}${entry.role_label ? ` <span class="muted">(${escapeHtml(entry.role_label)})</span>` : ''}</div>
        ${badge(entry.manager_status)}
        ${entry.provider_label ? `<div class="muted">${escapeHtml(entry.provider_label)}</div>` : ''}
        ${entry.bounce_reason_label ? `<div class="muted">${escapeHtml(entry.bounce_reason_label)}</div>` : ''}
        <div class="muted">${escapeHtml(entry.last_event_at || '')}</div>
      </div>
    `).join('') || '<div class="empty-state">Нет отправленных писем</div>';
    const statusHistoryHtml = (detail.status_history || []).map((entry) => `
      <div class="timeline-item">
        <div>${escapeHtml(entry.label)}</div>
        <div class="muted">${escapeHtml(entry.at || '')}</div>
      </div>
    `).join('') || '<div class="empty-state">Нет истории статусов</div>';
    const consentsHtml = (detail.consents || []).map((entry) => `
      <div class="timeline-item">
        <div>${escapeHtml(entry.contact || entry.email || '—')}</div>
        <div>${escapeHtml(entry.consent_status_label || '—')}${entry.materials_label ? ` · ${escapeHtml(entry.materials_label)}` : ''}</div>
        <div class="muted">${escapeHtml(entry.last_action_label || '')}${entry.last_action_at ? ` · ${escapeHtml(entry.last_action_at)}` : ''}</div>
      </div>
    `).join('') || '<div class="empty-state">Нет данных по согласиям</div>';
    const actionsHtml = (detail.action_history || []).map((entry) => `
      <div class="timeline-item">
        <div>${escapeHtml(entry.action_type_label || entry.action_type || '—')}${entry.responsible_manager ? ` · ${escapeHtml(entry.responsible_manager)}` : ''}</div>
        ${entry.comment ? `<div>${escapeHtml(entry.comment)}</div>` : ''}
        <div class="muted">${escapeHtml(entry.due_at || entry.created_at || '')}</div>
      </div>
    `).join('') || '<div class="empty-state">Нет истории действий</div>';

    if (qs('company-modal-title')) qs('company-modal-title').textContent = detail.organization || 'Компания';
    if (qs('company-modal-sub')) {
      qs('company-modal-sub').innerHTML = `${badge(detail.manager_status)} · ${escapeHtml(detail.interest?.label || '')}`;
    }
    qs('company-modal-body').innerHTML = `
      <div class="company-modal-summary">
        <p><strong>Следующее действие:</strong> ${escapeHtml(detail.next_action?.label || '—')}</p>
        <p><strong>Рекомендация:</strong> ${escapeHtml(detail.recommended_action?.label || '—')}</p>
      </div>
      <div class="company-modal-grid">
        <section class="company-modal-section">
          <h4>Данные из документа</h4>
          <div class="company-fields">${companyRows || '<div class="empty-state">Данные появятся позже</div>'}</div>
        </section>
        <section class="company-modal-section">
          <h4>Email-адреса и статусы</h4>
          ${emailsHtml}
        </section>
        <section class="company-modal-section">
          <h4>История статусов</h4>
          ${statusHistoryHtml}
        </section>
        <section class="company-modal-section">
          <h4>Согласия</h4>
          ${consentsHtml}
        </section>
        <section class="company-modal-section">
          <h4>История действий</h4>
          ${actionsHtml}
        </section>
      </div>
    `;
    const actionBtn = qs('company-modal-action-btn');
    if (actionBtn) actionBtn.onclick = () => openActionModal(detail.row_key);
  }

  function renderCampaignAnalytics(result) {
    const campaign = result.campaign || {};
    const meta = qs('analytics-campaign-meta');
    if (meta) {
      const metaHtml = `
        <strong>${escapeHtml(campaign.title || 'Рассылка')}</strong>
        <span>${escapeHtml(result.period_from || '')}${result.period_to ? ` — ${escapeHtml(result.period_to)}` : ''}</span>
      `;
      setContainerHtml(meta, metaHtml);
    }
    qs('analytics-empty')?.classList.add('hidden');
    qs('analytics-content')?.classList.remove('hidden');
    renderKpis('analytics-kpis', [
      { title: 'Отправлено', value: fmt(result.summary?.sent), drill: 'sent' },
      { title: 'Доставлено', value: `${fmt(result.summary?.delivered)} / ${result.rates?.delivery_rate ?? 0}%`, drill: 'delivered' },
      { title: 'Открыто', value: `${fmt(result.summary?.opened)} / ${result.rates?.open_rate ?? 0}%`, drill: 'opened' },
      { title: 'Переходы', value: `${fmt(result.summary?.clicked)} / ${result.rates?.ctr ?? 0}%`, drill: 'clicked' },
      { title: 'Недоставлено', value: fmt(result.summary?.errors), drill: 'errors' },
      { title: 'Отписки и спам', value: fmt((result.summary?.unsubscribed || 0) + (result.summary?.spam || 0)), drill: 'unsub_spam' },
    ]);
    renderFunnel('analytics-funnel', result.funnel);
    upsertChart('chart-daily', qs('chart-daily'), {
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
    upsertChart('chart-reasons', qs('chart-reasons'), {
      type: 'bar',
      data: {
        labels: (result.undelivery_reasons || []).map((item) => item.label),
        datasets: [{ data: (result.undelivery_reasons || []).map((item) => item.count), backgroundColor: '#ef4444' }],
      },
      options: { maintainAspectRatio: false, indexAxis: 'y' },
    });
    upsertChart('chart-provider-eff', qs('chart-provider-eff'), {
      type: 'bar',
      data: {
        labels: (result.provider_effectiveness || []).map((item) => item.provider),
        datasets: [
          { label: 'Доставляемость', data: (result.provider_effectiveness || []).map((item) => item.delivery_rate), backgroundColor: '#16a34a' },
          { label: 'Открываемость', data: (result.provider_effectiveness || []).map((item) => item.open_rate), backgroundColor: '#8b5cf6' },
        ],
      },
      options: { maintainAspectRatio: false },
    });
    setContainerHtml(
      qs('analytics-high-interest'),
      (result.high_interest_companies || []).map((item) => `
      <tr class="row-clickable" data-org="${escapeHtml(item.organization)}"><td>${escapeHtml(item.organization)}</td><td>${fmt(item.sent)}</td><td>${item.open_rate}%</td><td>${item.clicked}</td></tr>
    `).join('') || '<tr><td colspan="4" class="empty-state">Нет компаний с высоким интересом</td></tr>',
      () => {
        qs('analytics-high-interest').querySelectorAll('tr[data-org]').forEach((row) => {
          row.addEventListener('click', () => openOrgDrilldown(row.dataset.org));
        });
      },
    );
    setContainerHtml(
      qs('analytics-problems'),
      (result.problem_addresses || []).map((item) => `
      <tr class="row-clickable" data-org="${escapeHtml(item.organization || '')}" data-email="${escapeHtml(item.email || '')}"><td>${escapeHtml(item.organization || item.email)}</td><td>${escapeHtml(item.reason_label)}</td><td>${escapeHtml(item.provider_label)}</td><td>${fmt(item.attempts)}</td></tr>
    `).join('') || '<tr><td colspan="4" class="empty-state">Нет проблемных адресов</td></tr>',
      () => {
        qs('analytics-problems').querySelectorAll('tr[data-email]').forEach((row) => {
          row.addEventListener('click', () => {
            if (row.dataset.org) openOrgDrilldown(row.dataset.org);
            else openEmailDrilldown(row.dataset.email);
          });
        });
      },
    );
    setContainerHtml(
      qs('analytics-recommendations'),
      (result.recommendations || []).map((item) => `<li>${escapeHtml(item)}</li>`).join(''),
    );
  }

  function renderConsents(result) {
    renderKpis('consents-kpis', [
      { title: 'Дали согласие', value: fmt(result.summary?.confirmed), drill: 'consents_confirmed' },
      { title: 'Материалы отправлены', value: fmt(result.summary?.materials_sent), drill: 'materials' },
      { title: 'Открыли после согласия', value: fmt(result.summary?.opened_after_consent), drill: 'consents_opened' },
      { title: 'Нужно перезвонить', value: fmt(result.summary?.need_call), drill: 'consents_call' },
    ]);
    renderFunnel('consents-funnel', result.funnel);
    setContainerHtml(
      qs('consents-table'),
      (result.items || []).map((item) => `
      <tr${item.row_key ? ` class="row-clickable" data-row-key="${escapeHtml(item.row_key)}"` : ''}>
        <td>${escapeHtml(item.organization)}</td>
        <td>${escapeHtml(item.contact)}</td>
        <td>${escapeHtml(item.email)}</td>
        <td>${escapeHtml(item.consent_status_label)}</td>
        <td>${escapeHtml(item.materials_label)}</td>
        <td>${escapeHtml(item.last_action_label)}<div>${escapeHtml(item.last_action_at)}</div></td>
        <td>${escapeHtml(item.interest?.label)}</td>
        <td>${item.row_key ? `<button class="btn-outline btn-row-action" data-action="${escapeHtml(item.row_key)}" title="Действие по контакту">⋯</button>` : escapeHtml(item.next_action?.label)}</td>
      </tr>
    `).join('') || '<tr><td colspan="8" class="empty-state">Нет данных по согласиям</td></tr>',
      () => {
        qs('consents-table').querySelectorAll('tr[data-row-key]').forEach((row) => {
          row.addEventListener('click', () => openCompanyModal(row.dataset.rowKey).catch((error) => console.error(error)));
        });
        qs('consents-table').querySelectorAll('[data-action]').forEach((button) => {
          button.addEventListener('click', (event) => {
            event.stopPropagation();
            openActionModal(button.dataset.action).catch((error) => console.error(error));
          });
        });
      },
    );
    setContainerHtml(
      qs('consents-priority'),
      (result.priority_contacts || []).map((item, index) => `
      <div class="worklist-item"><span>${index + 1}. ${escapeHtml(item.organization)}</span><span>${escapeHtml(item.contact)}</span></div>
    `).join('') || '<div class="empty-state">Нет приоритетных контактов</div>',
    );
    renderPagination('consents-pagination', result.pagination, 'consents');
  }

  function renderProblems(result) {
    renderKpis('problems-kpis', [
      { title: 'Проблемные адреса', value: fmt(result.summary?.problem_addresses), drill: 'problems_all' },
      { title: 'Постоянные ошибки', value: fmt(result.summary?.hard_bounce), drill: 'problems_hard' },
      { title: 'Временные ошибки', value: fmt(result.summary?.soft_bounce), drill: 'problems_soft' },
      { title: 'Требуют проверки', value: fmt(result.summary?.need_check), drill: 'problems_hard' },
      { title: 'Повторить позже', value: fmt(result.summary?.retry_later), drill: 'problems_soft' },
    ]);
    upsertChart('chart-problem-reasons', qs('chart-problem-reasons'), {
      type: 'doughnut',
      data: {
        labels: (result.reasons || []).map((item) => item.label),
        datasets: [{ data: (result.reasons || []).map((item) => item.count) }],
      },
      options: { maintainAspectRatio: false },
    });
    upsertChart('chart-problem-domains', qs('chart-problem-domains'), {
      type: 'bar',
      data: {
        labels: (result.domains || []).map((item) => item.provider),
        datasets: [{ data: (result.domains || []).map((item) => item.count), backgroundColor: '#f97316' }],
      },
      options: { maintainAspectRatio: false, indexAxis: 'y' },
    });
    const tableHtml = (result.items || []).map((item) => `
      <tr class="row-clickable" data-row-key="${escapeHtml(item.row_key)}">
        <td>${escapeHtml(item.organization)}</td>
        <td>${renderCompanyEmails(item)}</td>
        <td>${escapeHtml(item.bounce_reason_label)}</td>
        <td>${escapeHtml(item.provider)}</td>
        <td>${fmt(item.attempts)}</td>
        <td>${escapeHtml(item.last_event_at)}</td>
        <td class="cell-action"><span>${escapeHtml(item.recommended_action?.label)}</span><button class="btn-outline btn-row-action" data-action="${escapeHtml(item.row_key)}" title="Создать задачу">⋯</button></td>
      </tr>
    `).join('') || '<tr><td colspan="7" class="empty-state">Нет проблемных компаний</td></tr>';
    const tableChanged = setContainerHtml(qs('problems-table'), tableHtml, () => {
      qs('problems-table').querySelectorAll('tr[data-row-key]').forEach((row) => {
        row.addEventListener('click', () => openCompanyModal(row.dataset.rowKey).catch((error) => console.error(error)));
      });
      qs('problems-table').querySelectorAll('[data-action]').forEach((button) => {
        button.addEventListener('click', (event) => {
          event.stopPropagation();
          openActionModal(button.dataset.action, 'create_task').catch((error) => console.error(error));
        });
      });
    });
    renderPagination('problems-pagination', result.pagination, 'problems');
    return tableChanged;
  }

  function openProblemCard(rowKey, items) {
    const item = (items || []).find((entry) => entry.row_key === rowKey);
    if (!item) return;
    state.selectedProblem = item;
    qs('problem-card').classList.remove('hidden');
    qs('problem-card-body').innerHTML = `
      <p><strong>${escapeHtml(item.organization)}</strong></p>
      <p>${badge(item.manager_status)}</p>
      <p>Адреса:</p>
      ${renderCompanyEmails(item)}
      <p>Причина: ${escapeHtml(item.bounce_reason_label)}</p>
      <p>Провайдер: ${escapeHtml(item.provider)}</p>
      <p>Писем: ${fmt(item.attempts)}</p>
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
    state.reportsHistory = result.history || [];
    renderKpis('reports-kpis', [
      { title: 'Сформировано отчётов', value: fmt(result.summary?.generated), drill: 'reports_all' },
      { title: 'Excel выгрузки', value: fmt(result.summary?.xlsx), drill: 'reports_xlsx' },
      { title: 'CSV выгрузки', value: fmt(result.summary?.csv), drill: 'reports_csv' },
      { title: 'NDJSON журналы', value: fmt(result.summary?.ndjson), drill: 'reports_ndjson' },
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
        state.modalParams = { et: button.dataset.exportType || '' };
        openModal('modal-export');
      });
    });
    setContainerHtml(
      qs('reports-history'),
      (result.history || []).map((item) => `
      <tr>
        <td>${escapeHtml(item.report_type)}</td>
        <td>${escapeHtml(item.period_from)} — ${escapeHtml(item.period_to)}</td>
        <td>${escapeHtml(item.format)}</td>
        <td>${escapeHtml(item.created_at)}</td>
        <td>${escapeHtml(item.author)}</td>
        <td>${escapeHtml(item.status)}</td>
        <td><a class="btn-outline" href="/api/sender/reports/download/${encodeURIComponent(item.report_id)}">Скачать</a></td>
      </tr>
    `).join('') || '<tr><td colspan="7" class="empty-state">Отчёты ещё не формировались</td></tr>',
    );
  }

  async function loadDashboard(refresh = false) {
    const useStale = !state.silentRefresh && !refresh;
    if (useStale) {
      const cached = readDashboardCache();
      if (cached) {
        applyDashboardResult(cached);
        schedulePoll(!!cached.refresh_in_progress);
      }
    }
    const result = await api(`/api/sender/manager-dashboard${queryString({ refresh: refresh ? 'true' : '' })}`);
    applyDashboardResult(result, { forceRender: refresh });
    writeDashboardCache(result);
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
    const tableChanged = renderRecipients(result);
    if (state.selectedRecipient?.row_key && isModalOpen('modal-company') && (!state.silentRefresh || tableChanged)) {
      openCompanyModal(state.selectedRecipient.row_key).catch((error) => console.error(error));
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
    const tableChanged = renderProblems(result);
    if (state.selectedProblem?.row_key && (!state.silentRefresh || tableChanged)) {
      openProblemCard(state.selectedProblem.row_key, result.items);
    }
  }

  async function loadReports() {
    const result = await api(`/api/sender/reports${queryString()}`);
    renderReports(result);
  }

  async function loadCurrentPage(refresh = false, { silent = false } = {}) {
    state.silentRefresh = silent;
    let skipBusy = silent;
    if (!silent && state.page === 'dashboard' && !refresh && dashboardHasInstantPaint()) {
      skipBusy = true;
    }
    if (!skipBusy) {
      clearError();
      clearPoll();
      setBusy(true);
    }
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
      if (!silent && error && error.message !== 'Unauthorized') {
        showError(error.message || 'Не удалось загрузить данные.');
      }
    } finally {
      state.silentRefresh = false;
      if (!skipBusy) setBusy(false);
    }
  }

  function activatePage(page, { preserveFilters = false } = {}) {
    const prevPage = state.page;
    if (prevPage !== page) {
      if (!preserveFilters) clearTabFiltersForPage(page);
      closeAllModals({ syncUrl: false });
      closeSidePanels({ syncUrl: false });
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
    syncFiltersToUrl({ push: prevPage !== page });
    return loadCurrentPage();
  }

  function openModal(id, { syncUrl = true } = {}) {
    qs(id)?.classList.add('open');
    const key = MODAL_ID_TO_KEY[id];
    if (key) {
      state.activeModal = key;
      if (syncUrl) syncFiltersToUrl({ push: true });
    }
  }

  function closeModal(id, { syncUrl = true } = {}) {
    qs(id)?.classList.remove('open');
    const key = MODAL_ID_TO_KEY[id];
    if (key && state.activeModal === key) {
      state.activeModal = '';
      state.modalParams = {};
      if (syncUrl) syncFiltersToUrl({ push: true });
    }
  }
  function isModalOpen(id) { return !!qs(id)?.classList.contains('open'); }

  function renderDrilldownTable() {
    const headRow = qs('drilldown-head-row');
    const body = qs('drilldown-body');
    if (!headRow || !body) return;
    headRow.innerHTML = drilldown.columns.map(([header]) => `<th>${escapeHtml(header)}</th>`).join('');
    if (!drilldown.rows.length) {
      body.innerHTML = `<tr><td class="empty-state" colspan="${drilldown.columns.length}">Нет записей</td></tr>`;
    } else {
      body.innerHTML = drilldown.rows.map((item) => {
        const rowKey = item && item.row_key ? escapeHtml(item.row_key) : '';
        const attrs = rowKey ? ` class="row-clickable" data-row-key="${rowKey}"` : '';
        return `<tr${attrs}>${
          drilldown.columns.map(([, accessor]) => `<td>${escapeHtml(accessor(item) ?? '—')}</td>`).join('')
        }</tr>`;
      }).join('');
      body.querySelectorAll('tr[data-row-key]').forEach((row) => {
        row.addEventListener('click', () => openCompanyModal(row.dataset.rowKey).catch((error) => console.error(error)));
      });
    }
    if (qs('drilldown-count')) {
      const base = `Записей: ${fmt(drilldown.rows.length)}`;
      qs('drilldown-count').textContent = drilldown.truncated ? `${base} (показаны первые ${fmt(DRILLDOWN_MAX_ROWS)})` : base;
    }
    if (qs('drilldown-download')) {
      qs('drilldown-download').disabled = !drilldown.rows.length;
    }
  }

  // The list endpoints cap per_page at 100 (server-side validation), so we page
  // through the results and accumulate up to a sane ceiling for the modal/export.
  const DRILLDOWN_PER_PAGE = 100;
  const DRILLDOWN_MAX_ROWS = 2000;

  async function fetchDrilldownRows(source, params, requestId) {
    const rows = [];
    let page = 1;
    let pages = 1;
    do {
      const result = await api(`/api/sender/${source}${queryString({
        ...params,
        page,
        per_page: DRILLDOWN_PER_PAGE,
      })}`);
      if (requestId !== drilldown.requestId) return null;
      (result.items || []).forEach((item) => rows.push(item));
      pages = result.pagination?.pages || 1;
      page += 1;
    } while (page <= pages && rows.length < DRILLDOWN_MAX_ROWS);
    return rows;
  }

  function openOrgDrilldown(organization) {
    if (!organization) return;
    state.modalParams = { d_org: organization };
    openDrilldownModal({
      title: `Получатели · ${organization}`,
      source: 'recipients',
      columns: DRILLDOWN_RECIPIENT_COLUMNS,
      params: { organization },
    });
  }

  function openEmailDrilldown(email) {
    if (!email) return;
    const needle = String(email).toLowerCase();
    state.modalParams = { d_email: email };
    openDrilldownModal({
      title: `Получатель · ${email}`,
      source: 'recipients',
      columns: DRILLDOWN_RECIPIENT_COLUMNS,
      params: { q: email },
      filter: (item) => String(item.email || '').toLowerCase() === needle,
    });
  }

  async function openDrilldownModal(kindOrConfig) {
    const config = typeof kindOrConfig === 'string' ? DRILLDOWN_CONFIG[kindOrConfig] : kindOrConfig;
    if (!config) return;
    if (typeof kindOrConfig === 'string') {
      state.modalParams = { d: kindOrConfig };
    }
    const requestId = ++drilldown.requestId;
    drilldown.title = config.title;
    drilldown.columns = config.columns;
    drilldown.rows = [];
    drilldown.truncated = false;
    if (qs('drilldown-title')) qs('drilldown-title').textContent = config.title;
    if (qs('drilldown-count')) qs('drilldown-count').textContent = 'Загрузка…';
    if (qs('drilldown-download')) qs('drilldown-download').disabled = true;
    if (qs('drilldown-body')) qs('drilldown-body').innerHTML = '';
    // Render the header immediately so the modal never looks empty while loading.
    if (qs('drilldown-head-row')) {
      qs('drilldown-head-row').innerHTML = config.columns.map(([header]) => `<th>${escapeHtml(header)}</th>`).join('');
    }
    openModal('modal-drilldown');
    try {
      let items;
      if (config.source === 'campaigns') {
        const result = await api(`/api/sender/campaigns${queryString()}`);
        if (requestId !== drilldown.requestId) return; // superseded by a newer request
        items = result.campaigns || [];
      } else if (config.source === 'reports') {
        items = (state.reportsHistory || []).slice();
      } else {
        const params = {};
        if (state.filters.organization) params.organization = state.filters.organization;
        Object.assign(params, config.params);
        items = await fetchDrilldownRows(config.source, params, requestId);
        if (items === null) return; // superseded by a newer request
      }
      if (config.filter) items = items.filter(config.filter);
      drilldown.truncated = items.length >= DRILLDOWN_MAX_ROWS;
      drilldown.rows = items;
      renderDrilldownTable();
    } catch (error) {
      if (requestId !== drilldown.requestId) return;
      console.error(error);
      if (qs('drilldown-count')) qs('drilldown-count').textContent = 'Не удалось загрузить данные';
      if (qs('drilldown-download')) qs('drilldown-download').disabled = true;
      if (qs('drilldown-body')) {
        qs('drilldown-body').innerHTML = `<tr><td class="empty-state" colspan="${drilldown.columns.length || 1}">Не удалось загрузить данные. Попробуйте ещё раз.</td></tr>`;
      }
    }
  }

  function csvCell(value) {
    const text = String(value ?? '').replace(/"/g, '""');
    return `"${text}"`;
  }

  function downloadDrilldownCsv() {
    if (!drilldown.rows.length) return;
    const header = drilldown.columns.map(([label]) => csvCell(label)).join(';');
    const lines = drilldown.rows.map((item) => drilldown.columns
      .map(([, accessor]) => csvCell(accessor(item) ?? ''))
      .join(';'));
    const csv = `\ufeff${[header, ...lines].join('\r\n')}`;
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const stamp = new Date().toISOString().slice(0, 10);
    const safeTitle = (drilldown.title || 'таблица').replace(/[^\wа-яё-]+/giu, '_');
    link.href = url;
    link.download = `${safeTitle}_${stamp}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  async function openActionModal(rowKey, defaultType = 'call') {
    let detail;
    try {
      detail = await api(`/api/sender/recipients/${encodeURIComponent(rowKey)}`);
    } catch (error) {
      console.error(error);
      showError('Действие недоступно: получатель не найден среди отправок.');
      return;
    }
    state.actionRecipient = detail;
    state.actionType = defaultType;
    qs('action-recipient-summary').innerHTML = `
      <p><strong>${escapeHtml(detail.organization)}</strong></p>
      <p>${escapeHtml(companyEmailsText(detail))} · ${badge(detail.manager_status)} · ${escapeHtml(detail.interest?.label)}</p>
    `;
    qs('action-type-cards').innerHTML = ACTION_TYPES.map(([value, label]) => `
      <div class="action-card ${state.actionType === value ? 'active' : ''}" data-action-type="${value}">${label}</div>
    `).join('');
    qs('action-type-cards').querySelectorAll('.action-card').forEach((card) => {
      card.addEventListener('click', () => {
        state.actionType = card.dataset.actionType;
        state.modalParams = { row: rowKey, at: state.actionType };
        qs('action-type-cards').querySelectorAll('.action-card').forEach((node) => node.classList.toggle('active', node.dataset.actionType === state.actionType));
        syncFiltersToUrl();
      });
    });
    qs('action-manager').value = state.userName || '';
    state.modalParams = { row: rowKey, at: defaultType };
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
    if (isModalOpen('modal-company')) state.selectedRecipient = { row_key: savedRowKey };
    await loadCurrentPage();
    if (isModalOpen('modal-company')) {
      openCompanyModal(savedRowKey).catch((error) => console.error(error));
    }
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

  async function restoreModal(parsed) {
    if (!parsed?.modal) return;
    const mp = parsed.modalParams || {};
    try {
      switch (parsed.modal) {
        case 'company':
          if (mp.row) await openCompanyModal(mp.row);
          break;
        case 'action':
          if (mp.row) await openActionModal(mp.row, mp.at || 'call');
          break;
        case 'drill':
          if (mp.d_org) openOrgDrilldown(mp.d_org);
          else if (mp.d_email) openEmailDrilldown(mp.d_email);
          else if (mp.d) await openDrilldownModal(mp.d);
          break;
        case 'campaign':
          if (!state.campaignsLoaded) await loadCampaigns();
          if (mp.cs) selectCampaign(mp.cs);
          break;
        case 'export':
          if (mp.et && qs('export-type')) qs('export-type').value = mp.et;
          state.modalParams = { et: mp.et || '' };
          openModal('modal-export');
          break;
        case 'filters':
          syncAdvancedFiltersToDom();
          state.modalParams = {};
          openModal('modal-filters');
          break;
        default:
          break;
      }
    } catch (error) {
      console.error(error);
    }
  }

  async function applyStatsHash(parsed, { fromHistory = false } = {}) {
    if (!parsed || !urlReady || !isStatisticsScreenActive()) return;
    suppressUrlSync = true;
    try {
      seedStateFromHash(parsed);
      syncGlobalFiltersToDom();
      syncAdvancedFiltersToDom();
      syncSearchInputsToDom(parsed.searches || {});

      closeAllModals({ syncUrl: false });
      closeSidePanels({ syncUrl: false });

      await activatePage(parsed.page, { preserveFilters: true });

      if (parsed.modal) {
        await restoreModal(parsed);
      }

      lastWrittenHash = location.hash || buildStatsHash();
      if (!fromHistory) {
        syncFiltersToUrl({ push: false });
      }
    } finally {
      suppressUrlSync = false;
    }
  }

  function onStatsHashChange() {
    if (suppressUrlSync || !urlReady || !isStatisticsScreenActive()) return;
    const parsed = parseStatsHash();
    if (parsed) {
      applyStatsHash(parsed, { fromHistory: true });
    }
  }

  function bindEvents() {
    document.querySelectorAll('.stx-tab[data-page]').forEach((item) => {
      item.addEventListener('click', () => activatePage(item.dataset.page));
    });
    qs('btn-refresh')?.addEventListener('click', () => { loadCurrentPage(true); startAutoRefresh(); });
    qs('stats-error-retry')?.addEventListener('click', () => { clearError(); loadCurrentPage(); });
    qs('btn-advanced-filters')?.addEventListener('click', () => {
      state.modalParams = {};
      openModal('modal-filters');
    });
    qs('btn-export-report')?.addEventListener('click', () => {
      state.modalParams = {};
      openModal('modal-export');
    });
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
      syncFiltersToUrl();
      loadCampaignAnalytics();
    });
    qs('campaigns-table')?.addEventListener('click', (event) => {
      const button = event.target.closest('[data-open-analytics]');
      if (button) {
        event.stopPropagation();
        openCampaignAnalytics(button.getAttribute('data-open-analytics') || '');
        return;
      }
      const row = event.target.closest('tr[data-job-id]');
      if (row) selectCampaign(row.dataset.jobId);
    });
    qs('campaigns-search')?.addEventListener('input', () => {
      debounceSearch('campaigns', () => {
        syncFiltersToUrl();
        loadCampaigns();
      });
    });
    qs('recipients-search')?.addEventListener('input', () => {
      debounceSearch('recipients', () => {
        state.pagination.recipients = 1;
        syncFiltersToUrl();
        loadRecipients();
      });
    });
    qs('consents-search')?.addEventListener('input', () => {
      debounceSearch('consents', () => {
        state.pagination.consents = 1;
        syncFiltersToUrl();
        loadConsents();
      });
    });
    qs('company-modal-close')?.addEventListener('click', () => { closeModal('modal-company'); state.selectedRecipient = null; });
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
    qs('drilldown-close')?.addEventListener('click', () => closeModal('modal-drilldown'));
    qs('drilldown-download')?.addEventListener('click', downloadDrilldownCsv);
    qs('campaign-summary-close')?.addEventListener('click', () => closeModal('modal-campaign-summary'));
    document.querySelectorAll('.stx-modal').forEach((modal) => {
      modal.addEventListener('click', (event) => {
        if (event.target === modal) closeModal(modal.id);
      });
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || !isStatisticsScreenActive()) return;
      const openModalEl = document.querySelector('.stx-modal.open');
      if (openModalEl?.id) closeModal(openModalEl.id);
    });
    window.addEventListener('hashchange', onStatsHashChange);
    window.addEventListener('popstate', onStatsHashChange);
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
    const parsed = parseStatsHash();
    initFilterDefaults();
    bindEvents();
    const pageLoad = activatePage(state.page, { preserveFilters: !!parsed });
    if (parsed?.modal) {
      pageLoad.then(() => restoreModal(parsed)).catch((error) => console.error(error));
    }
    if (!parsed) {
      syncFiltersToUrl({ push: false });
    } else {
      lastWrittenHash = location.hash || buildStatsHash();
    }
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
    urlReady = true;
    const wasInitialized = initialized;
    paintDashboardFromCacheSync();
    init();
    if (wasInitialized) {
      const parsed = parseStatsHash();
      if (parsed) {
        applyStatsHash(parsed).catch((error) => console.error(error));
      } else {
        loadCurrentPage(false, { silent: true });
        syncFiltersToUrl({ push: false });
      }
    }
    startAutoRefresh();
  }

  function hide() {
    stopAutoRefresh();
    clearPoll();
    urlReady = false;
    if (location.hash.startsWith('#stats')) {
      suppressUrlSync = true;
      history.replaceState(null, '', `${location.pathname}${location.search}`);
      lastWrittenHash = '';
      suppressUrlSync = false;
    }
  }

  window.StatsEmbed = { show, hide };
}());
