(function () {
  function fmt(value) {
    return Number(value || 0).toLocaleString('ru');
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value ?? '');
  }

  function senderActionReady(options = {}, helpers = {}) {
    if (typeof helpers.isSenderActionReady === 'function') {
      return helpers.isSenderActionReady(options);
    }
    return true;
  }

  function setSenderButtonsState({
    status = 'idle',
    mode = 'dry_run',
    canConfirm = false,
    hasPendingRows = false,
    errorRows = 0,
    total = 0,
    ready = 0,
  } = {}, helpers = {}) {
    const runButton = document.getElementById('s-run');
    const transportSelect = document.getElementById('sender-transport');
    const sendModeSelect = document.getElementById('sender-send-mode');
    const recipientStrategySelect = document.getElementById('sender-recipient-strategy');
    const disabledReason = helpers.senderDisabledReason || 'Сначала подготовьте документы.';
    const hasFailedRows = Number(errorRows || 0) > 0;
    if (transportSelect) transportSelect.disabled = status === 'running';
    if (sendModeSelect) sendModeSelect.disabled = status === 'running';
    if (recipientStrategySelect) recipientStrategySelect.disabled = status === 'running';
    if (!runButton) return;

    runButton.dataset.action = 'preview';
    runButton.className = 'btn-primary';
    runButton.disabled = false;
    runButton.title = '';

    if (status === 'running') {
      runButton.dataset.action = 'stop';
      runButton.textContent = mode === 'send' ? 'Остановить отправку' : 'Остановить проверку';
      runButton.className = 'btn-danger';
      return;
    }

    if (status === 'completed' && mode === 'dry_run' && canConfirm) {
      const isReady = senderActionReady({ status, mode, total, ready, canConfirm, hasPendingRows, errorRows }, helpers);
      runButton.dataset.action = 'send';
      runButton.textContent = 'Отправить письма';
      runButton.className = 'btn-primary';
      runButton.disabled = !isReady;
      runButton.title = isReady ? '' : disabledReason;
      return;
    }

    if (status === 'stopped') {
      const isReady = senderActionReady({ status, mode, total, ready, canConfirm, hasPendingRows, errorRows }, helpers);
      runButton.dataset.action = mode === 'send' ? 'send' : 'preview';
      runButton.textContent = mode === 'send' ? 'Продолжить отправку' : 'Проверить ещё раз';
      runButton.className = 'btn-primary';
      runButton.disabled = !isReady;
      runButton.title = isReady ? '' : disabledReason;
      return;
    }

    if (status === 'completed' && mode === 'send') {
      if (hasPendingRows || hasFailedRows) {
        const isReady = senderActionReady({ status, mode, total, ready, canConfirm, hasPendingRows, errorRows }, helpers);
        runButton.dataset.action = 'send';
        runButton.textContent = hasPendingRows ? 'Отправить неотправленные' : 'Повторить отправку';
        runButton.className = 'btn-primary';
        runButton.disabled = !isReady;
        runButton.title = isReady
          ? (hasPendingRows
            ? 'Есть письма, которые ещё не отправлены. Нажмите, чтобы продолжить.'
            : 'Есть письма с ошибкой. Нажмите, чтобы попробовать отправить снова.')
          : disabledReason;
        return;
      }
      runButton.dataset.action = 'done';
      runButton.textContent = 'Отправка завершена';
      runButton.className = 'btn-outline';
      runButton.disabled = true;
      runButton.title = 'Письма уже отправлены.';
      return;
    }

    if (status === 'error') {
      const isReady = senderActionReady({ status, mode, total, ready, canConfirm, hasPendingRows, errorRows }, helpers);
      runButton.dataset.action = mode === 'send' ? 'send' : 'preview';
      runButton.textContent = mode === 'send' ? 'Продолжить отправку' : 'Проверить ещё раз';
      runButton.className = 'btn-primary';
      runButton.disabled = !isReady;
      runButton.title = isReady ? '' : disabledReason;
      return;
    }

    const isReady = senderActionReady({ status, mode, total, ready, canConfirm, hasPendingRows, errorRows }, helpers);
    runButton.dataset.action = 'preview';
    runButton.textContent = 'Проверить перед отправкой';
    runButton.className = 'btn-primary';
    runButton.disabled = !isReady;
    runButton.title = isReady ? '' : disabledReason;
  }

  function updateStepTrack(status = 'idle', mode = 'dry_run') {
    const steps = [
      { id: 's-step-check' },
      { id: 's-step-confirm' },
      { id: 's-step-send' },
    ];
    const normalizedStatus = String(status || 'idle');
    const normalizedMode = String(mode || 'dry_run');
    let activeIndex = -1;
    if (normalizedStatus === 'running') {
      activeIndex = normalizedMode === 'send' ? 2 : 0;
    } else if (normalizedStatus === 'completed') {
      activeIndex = normalizedMode === 'send' ? 2 : 1;
    } else if (normalizedStatus === 'stopped' || normalizedStatus === 'error') {
      activeIndex = normalizedMode === 'send' ? 2 : 0;
    }

    steps.forEach((step, index) => {
      const el = document.getElementById(step.id);
      if (!el) return;
      el.classList.remove('is-active', 'is-done', 'is-error');
      if (normalizedStatus === 'completed' && normalizedMode === 'send') {
        el.classList.add('is-done');
        return;
      }
      if ((normalizedStatus === 'error' || normalizedStatus === 'stopped') && index === activeIndex) {
        el.classList.add(normalizedStatus === 'error' ? 'is-error' : 'is-active');
        return;
      }
      if (index < activeIndex) {
        el.classList.add('is-done');
      } else if (index === activeIndex) {
        el.classList.add('is-active');
      }
    });
  }

  function updateProcessPanel({
    status = 'idle',
    mode = 'dry_run',
    total = 0,
    processed = 0,
    ready = 0,
    sent = 0,
    errors = 0,
    pending = 0,
  } = {}) {
    updateStepTrack(status, mode);
    setText('s-process-checked', `${fmt(processed)}/${fmt(total)}`);
    setText('s-process-ready', fmt(ready));
    setText('s-process-sent', fmt(sent));
    setText('s-process-errors', fmt(errors));
    setText('s-process-left', fmt(Math.max(0, pending)));

    const setPanelText = ({ title, main, detail, next }) => {
      setText('s-process-title', title);
      setText('s-process-main', main);
      setText('s-process-detail', detail);
      setText('s-process-next', next);
    };

    if (status === 'running') {
      if (mode === 'send') {
        setPanelText({
          title: 'Идёт отправка',
          main: 'Отправляем письма.',
          detail: total > 0 ? `Отправлено: ${fmt(sent)}. Обработано: ${fmt(processed)} из ${fmt(total)}.` : 'Письма уходят получателям.',
          next: 'Дождитесь завершения. Уже отправленные письма повторно не уйдут.',
        });
        return;
      }
      setPanelText({
        title: 'Идёт проверка',
        main: 'Проверяем письма перед отправкой.',
        detail: total > 0 ? `Проверено: ${fmt(processed)} из ${fmt(total)}. Готово к отправке: ${fmt(ready)}.` : 'Проверяем адреса и вложения.',
        next: 'После проверки появится отдельная кнопка отправки.',
      });
      return;
    }

    if (status === 'completed') {
      if (mode === 'send') {
        setPanelText({
          title: errors > 0 ? 'Отправка завершена с ошибками' : 'Готово',
          main: errors > 0 ? 'Часть писем не удалось отправить.' : 'Письма отправлены.',
          detail: `Отправлено: ${fmt(sent)}. Ошибок: ${fmt(errors)}.`,
          next: 'Можно перейти к статистике отправок.',
        });
        return;
      }
      setPanelText({
        title: 'Проверка завершена',
        main: errors > 0 ? 'Есть строки, которые нужно проверить.' : 'Письма готовы к отправке.',
        detail: `Готово к отправке: ${fmt(ready)}. Ошибок: ${fmt(errors)}.`,
        next: ready > 0 ? 'Нажмите «Отправить письма», когда будете готовы.' : 'Проверьте ошибки перед отправкой.',
      });
      return;
    }

    if (status === 'stopped') {
      setPanelText({
        title: 'Процесс остановлен',
        main: mode === 'send' ? 'Отправка остановлена.' : 'Проверка остановлена.',
        detail: 'Прогресс сохранён.',
        next: 'Можно продолжить с сохранённого места.',
      });
      return;
    }

    if (status === 'error') {
      setPanelText({
        title: 'Есть ошибка',
        main: mode === 'send' ? 'Не удалось завершить отправку.' : 'Не удалось завершить проверку.',
        detail: errors > 0 ? `Ошибок: ${fmt(errors)}.` : 'Проверьте журнал отправки.',
        next: 'Исправьте проблему или повторите действие.',
      });
      return;
    }

    setPanelText({
      title: 'Готово к проверке',
      main: 'Сервис проверит письма перед отправкой.',
      detail: 'Реальная отправка начнётся только после вашего подтверждения.',
      next: 'После проверки появится кнопка отправки.',
    });
  }

  function renderSenderState(nextState = {}, helpers = {}) {
    const processed = Number(nextState.processed_rows || 0);
    const stats = nextState.stats || {};
    const totalRaw = Math.max(Number(nextState.total_rows || 0), Number(stats.total || 0));
    const total = totalRaw > 0 ? totalRaw : (processed > 0 ? processed : 0);
    const ready = Number(nextState.ready_rows || 0);
    const errors = Math.max(Number(nextState.error_rows || 0), Number(stats.error || 0));
    const sent = Math.max(Number(nextState.sent_rows || 0), Number(stats.sent || 0));
    const percent = total > 0 ? Math.round((processed / total) * 100) : 0;
    const status = nextState.status || 'idle';
    const mode = nextState.mode || 'dry_run';
    const pending = Number(helpers.pending || 0);
    const hasPendingRows = Math.max(0, pending) > 0;
    const hasReadyRows = ready > 0;
    const hasPreviewRows = !!helpers.hasPreviewRows;
    const isDryRunReady = status === 'completed' && mode === 'dry_run' && hasReadyRows;
    const canResumeStopped = status === 'stopped' && hasPendingRows;
    const canConfirm = hasPreviewRows || isDryRunReady || canResumeStopped;
    const canDownloadResultTable = status === 'completed' && (mode === 'dry_run' || sent > 0 || errors > 0 || total > 0);
    const canDownloadDeliveryReport = status === 'completed' && mode === 'send' && sent > 0;
    const humanize = helpers.humanizeSenderMessage || ((value, fallback) => String(value || fallback || ''));

    if (typeof helpers.setProgressVisual === 'function') {
      helpers.setProgressVisual(document.getElementById('s-fill'), {
        percent,
        running: status === 'running' && total > 0,
        indeterminate: false,
      });
    }
    setText('s-sent', fmt(sent));
    setText('s-left', fmt(Math.max(0, pending)));
    setText('s-err2', fmt(errors));

    if (typeof helpers.setSenderDownloadEnabled === 'function') {
      helpers.setSenderDownloadEnabled({
        data: canDownloadResultTable,
        report: canDownloadDeliveryReport,
      });
    }
    updateProcessPanel({ status, mode, total, processed, ready, sent, errors, pending });
    if (typeof helpers.renderJobSenderStatusScreen === 'function') {
      helpers.renderJobSenderStatusScreen(nextState);
    }

    const badge = document.getElementById('s-badge');
    const label = document.getElementById('s-label');
    const actionsHint = document.getElementById('s-actions-hint');
    let senderRunning = false;

    if (status === 'running') {
      senderRunning = true;
      if (badge) {
        badge.textContent = mode === 'send' ? 'Отправка' : 'Проверка';
        badge.className = 'badge badge-running';
      }
      if (label) {
        if (total > 0) {
          const base = mode === 'send'
            ? `Отправляю письма: ${fmt(processed)} из ${fmt(total)}. Отправлено: ${fmt(sent)}.`
            : `Проверяю перед отправкой: ${fmt(processed)} из ${fmt(total)}. Готово к отправке: ${fmt(ready)}.`;
          label.textContent = errors > 0 ? `${base} Ошибок: ${fmt(errors)}.` : base;
        } else {
          label.textContent = mode === 'send' ? 'Начинаю отправку писем.' : 'Проверяю адреса и вложения.';
        }
      }
      if (actionsHint) actionsHint.textContent = mode === 'send'
        ? 'Идёт отправка. Просто дождитесь завершения.'
        : 'Идёт проверка. Реальная отправка ещё не началась.';
      setSenderButtonsState({ status: 'running', mode, total, ready, canConfirm: false, hasPendingRows, errorRows: errors }, helpers);
      return { senderRunning, canConfirm, hasPendingRows };
    }

    if (status === 'stopped') {
      if (badge) {
        badge.textContent = 'Остановлено';
        badge.className = 'badge badge-wait';
      }
      if (label) label.textContent = humanize(nextState.summary_text, 'Отправка остановлена. Можно продолжить позже.');
      if (actionsHint) actionsHint.textContent = 'Прогресс сохранён. Можно продолжить с этого места.';
      setSenderButtonsState({ status: 'stopped', mode, total, ready, canConfirm, hasPendingRows, errorRows: errors }, helpers);
      return { senderRunning, canConfirm, hasPendingRows };
    }

    setSenderButtonsState({ status, mode, total, ready, canConfirm, hasPendingRows, errorRows: errors }, helpers);

    if (status === 'completed') {
      const isFailedSend = mode === 'send' && sent === 0 && errors > 0;
      if (badge) {
        badge.textContent = mode === 'dry_run' ? 'Проверено' : (isFailedSend ? 'Ошибка' : 'Готово');
        badge.className = isFailedSend ? 'badge badge-error' : 'badge badge-done';
      }
      if (label) {
        label.textContent = total > 0
          ? humanize(
              nextState.summary_text,
              mode === 'send'
                ? (
                  errors > 0
                    ? `Отправка завершена не полностью. Отправлено: ${fmt(sent)}. Не отправлено: ${fmt(errors)}.`
                    : `Отправка завершена: все письма ушли. Отправлено: ${fmt(sent)}.`
                )
                : (
                  errors > 0
                    ? `Проверка завершена, но есть проблемные строки: ${fmt(errors)}. Готово к отправке: ${fmt(ready)}.`
                    : `Проверка завершена: всё в порядке, письма готовы к отправке. Готово писем: ${fmt(ready)}.`
                )
            )
          : 'Обработка завершена';
      }
      if (actionsHint) actionsHint.textContent = mode === 'send'
        ? (errors > 0 ? 'Отправка завершена с ошибками. Можно повторить отправку после исправления проблемы.' : 'Отправка завершена. Можно перейти к статистике или скачать отчёт.')
        : 'Проверка завершена. Отправка начнётся только после нажатия «Отправить письма».';
      return { senderRunning, canConfirm, hasPendingRows };
    }

    if (status === 'error') {
      if (badge) {
        badge.textContent = 'Ошибка';
        badge.className = 'badge badge-error';
      }
      if (label) label.textContent = humanize(nextState.summary_text, 'Не удалось завершить проверку или отправку.');
      if (actionsHint) actionsHint.textContent = 'Проверьте журнал отправки и повторите действие.';
      return { senderRunning, canConfirm, hasPendingRows };
    }

    if (badge) {
      badge.textContent = 'Готов к проверке';
      badge.className = 'badge badge-idle';
    }
    if (label) {
      label.textContent = total > 0
        ? `Готово к проверке: ${fmt(total)}.`
        : 'Проверка перед отправкой ещё не запускалась.';
    }
    if (actionsHint) actionsHint.textContent = 'Сначала запустите проверку. Реальная отправка начнётся только после подтверждения.';
    return { senderRunning, canConfirm, hasPendingRows };
  }

  window.SenderUi = {
    renderSenderState,
    setSenderButtonsState,
  };
}());
