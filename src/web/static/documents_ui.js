(function () {
  function badgeClassFromTone(tone = 'idle') {
    const normalized = String(tone || 'idle');
    if (normalized === 'progress') return 'badge badge-running';
    if (normalized === 'done') return 'badge badge-done';
    if (normalized === 'wait') return 'badge badge-wait';
    if (normalized === 'error') return 'badge badge-error';
    return 'badge badge-idle';
  }

  function countText(done = 0, total = 0) {
    const safeDone = Number(done || 0);
    const safeTotal = Number(total || 0);
    return `${safeDone.toLocaleString('ru')}/${safeTotal.toLocaleString('ru')}`;
  }

  function normalizeDocumentMode(value) {
    const mode = String(value || '').trim();
    return ['kp', 'contract', 'both'].includes(mode) ? mode : 'kp';
  }

  function documentCountPerRow(documentMode) {
    return normalizeDocumentMode(documentMode) === 'both' ? 2 : 1;
  }

  function renderStepTrack(steps = []) {
    const elements = {
      generate: document.getElementById('g-step-generate'),
      review: document.getElementById('g-step-review'),
      ready: document.getElementById('g-step-ready'),
    };
    Object.values(elements).forEach((el) => {
      if (!el) return;
      el.classList.remove('is-active', 'is-done', 'is-error');
    });
    steps.forEach((step) => {
      const el = elements[String(step && step.id || '')];
      if (!el) return;
      const state = String(step && step.state || 'idle');
      if (state === 'active') el.classList.add('is-active');
      if (state === 'done') el.classList.add('is-done');
      if (state === 'error') el.classList.add('is-error');
    });
  }

  function buildFallback(result = {}, helpers = {}) {
    const status = String(result.status || 'idle');
    const totalRows = Number(result.total_rows || 0);
    const processedRows = Number(result.processed_rows || 0);
    const documentMode = normalizeDocumentMode(
      result.document_mode
      || (result.generator && result.generator.document_mode)
      || (typeof helpers.getDocumentMode === 'function' ? helpers.getDocumentMode() : '')
    );
    const expectedDocuments = totalRows > 0 ? totalRows * documentCountPerRow(documentMode) : 0;
    const humanize = helpers.humanizeDocumentsMessage || ((value, fallback) => String(value || fallback || ''));
    const fallbackRestartLocked = status === 'completed'
      && Number(result.error_rows || 0) === 0
      && Number(result.output_file_count || 0) > 0;
    const labelText = status === 'running'
      ? 'Запускаю подготовку документов.'
      : humanize(result.stage_text, 'Подготовка документов ещё не запускалась.');
    return {
      process: {
        title: status === 'running' ? 'Идёт подготовка' : 'Готово к запуску',
        main: status === 'running' ? 'Готовим документы.' : 'Сервис подготовит документы по вашей таблице.',
        detail: '',
        next: status === 'running' ? 'Скоро сервер пришлёт точный статус.' : 'Когда всё будет готово, можно будет скачать результат и перейти дальше.',
        current_item_text: '',
        clients_done: processedRows,
        clients_total: totalRows,
        documents_done: 0,
        documents_total: expectedDocuments,
        review_done: 0,
        review_total: 0,
        show_review: false,
        steps: [
          { id: 'generate', state: status === 'running' ? 'active' : 'idle' },
          { id: 'review', state: 'idle' },
          { id: 'ready', state: 'idle' },
        ],
      },
      module: {
        badge_text: status === 'running' ? 'Подготовка' : 'Готов к запуску',
        badge_tone: status === 'running' ? 'progress' : 'idle',
        run_text: status === 'running' ? 'Документы готовятся' : 'Подготовить документы',
        label_text: labelText,
        actions_hint: status === 'running' ? 'Идёт подготовка документов. Просто дождитесь завершения.' : '',
        philologist_hint: '',
        next_hint: 'Кнопка перехода дальше включится автоматически после завершения подготовки.',
        done_value: processedRows,
        done_label: 'Клиентов',
        error_value: Number(result.error_rows || 0),
        total_value: totalRows,
      },
      progress: {
        percent: status === 'completed' ? 100 : Number(result.progress_percent || 0),
        running: status === 'running',
      },
      actions: {
        can_run: status !== 'running' && !fallbackRestartLocked,
        can_stop: status === 'running',
        can_download_output: false,
        can_download_report: false,
        can_go_next: status === 'completed',
        next_button_text: 'Дальше: проверить отправку',
        next_button_title: status === 'completed' ? 'Перейти к проверке отправки.' : 'Сначала завершите подготовку документов.',
        run_disabled_reason: fallbackRestartLocked ? 'Документы уже успешно подготовлены без ошибок. Повторный запуск для этой сессии заблокирован.' : '',
      },
    };
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value ?? '');
  }

  function renderDocumentsStatus(result = {}, helpers = {}) {
    const ui = result.ui && Object.keys(result.ui || {}).length
      ? result.ui
      : buildFallback(result, helpers);
    const process = ui.process || {};
    const module = ui.module || {};
    const progress = ui.progress || {};
    const actions = ui.actions || {};
    const status = String(result.status || 'idle');

    setText('g-process-title', process.title || '');
    setText('g-process-main', process.main || '');
    setText('g-process-detail', process.detail || '');
    setText('g-current-item', process.current_item_text || '');
    setText('g-process-next', process.next || '');
    setText('g-process-clients', countText(process.clients_done, process.clients_total));
    setText('g-process-files', countText(process.documents_done, process.documents_total));
    setText('g-process-review', countText(process.review_done, process.review_total));
    const reviewRow = document.getElementById('g-process-review-row');
    if (reviewRow) reviewRow.hidden = !process.show_review;

    renderStepTrack(process.steps || []);

    const badge = document.getElementById('g-badge');
    if (badge) {
      badge.textContent = module.badge_text || 'Готов к запуску';
      badge.className = badgeClassFromTone(module.badge_tone);
    }
    const runButton = document.getElementById('g-run');
    if (runButton) {
      runButton.textContent = module.run_text || 'Подготовить документы';
      runButton.disabled = typeof actions.can_run === 'boolean' ? !actions.can_run : status === 'running';
      runButton.title = runButton.disabled
        ? (actions.run_disabled_reason || '')
        : status === 'completed'
          ? 'Запустить подготовку заново с текущими данными.'
          : '';
    }
    const pauseButton = document.getElementById('g-pause');
    if (pauseButton) {
      pauseButton.disabled = typeof actions.can_stop === 'boolean' ? !actions.can_stop : status !== 'running';
      pauseButton.textContent = 'Остановить';
      pauseButton.title = pauseButton.disabled ? 'Остановка станет доступна после запуска подготовки документов.' : '';
    }
    setText('g-total', Number(module.total_value || process.clients_total || 0).toLocaleString('ru'));
    setText('g-done', Number(module.done_value || 0).toLocaleString('ru'));
    setText('g-done-label', module.done_label || 'Клиентов');
    setText('g-err', Number(module.error_value || 0).toLocaleString('ru'));
    setText('g-label', module.label_text || '');

    const reportButton = document.getElementById('ph-download-report');
    if (reportButton) reportButton.disabled = typeof actions.can_download_report === 'boolean'
      ? !actions.can_download_report
      : status !== 'completed';
    const outputButton = document.getElementById('g-download-output');
    if (outputButton) outputButton.disabled = typeof actions.can_download_output === 'boolean'
      ? !actions.can_download_output
      : status !== 'completed';

    if (typeof helpers.updateDocumentsActionHints === 'function') {
      helpers.updateDocumentsActionHints({
        generatorHint: module.actions_hint || module.generator_hint || '',
        philologistHint: module.philologist_hint || '',
        nextHint: module.next_hint || '',
      });
    }
    if (typeof helpers.setProgressVisual === 'function') {
      helpers.setProgressVisual(document.getElementById('g-fill'), {
        percent: progress.percent,
        running: !!progress.running,
        indeterminate: false,
      });
    }
    if (typeof helpers.updateDocumentsNextButton === 'function') {
      helpers.updateDocumentsNextButton(status, actions);
    }
    if (typeof helpers.syncGeneratorStartDescriptionVisibility === 'function') {
      helpers.syncGeneratorStartDescriptionVisibility(status);
    }
  }

  window.DocumentsUi = {
    renderDocumentsStatus,
  };
}());
