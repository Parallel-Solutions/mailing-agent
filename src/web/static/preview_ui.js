(function () {
  const DOCX_SCRIPT_URLS = [
    '/public/vendor/jszip.min.js',
    '/public/vendor/docx-preview.min.js',
  ];
  let docxScriptsPromise = null;
  let currentPreviewState = null;

  function resolveApiUrl(path) {
    if (typeof window.apiUrl === 'function') {
      return window.apiUrl(path);
    }
    return path;
  }

  async function requestJson(url, options) {
    const fetchFn = typeof window.fetchWithTimeout === 'function' ? window.fetchWithTimeout : fetch;
    const response = await fetchFn(url, { credentials: 'same-origin', ...(options || {}) }, 20000);
    const payload = typeof window.readJsonOrText === 'function'
      ? await window.readJsonOrText(response)
      : await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof window.formatApiErrorDetail === 'function'
        ? window.formatApiErrorDetail(payload.detail, 'Не удалось загрузить предпросмотр.')
        : (payload.detail || 'Не удалось загрузить предпросмотр.');
      throw new Error(String(detail));
    }
    return payload.result || payload;
  }

  function showToastMessage(message) {
    if (typeof window.showToast === 'function') {
      window.showToast(message);
    }
  }

  function getModalElements() {
    return {
      modal: document.getElementById('file-preview-modal'),
      title: document.getElementById('file-preview-modal-title'),
      subtitle: document.getElementById('file-preview-modal-sub'),
      downloadLink: document.getElementById('file-preview-download-link'),
      archiveLayout: document.getElementById('file-preview-archive-layout'),
      archiveList: document.getElementById('file-preview-archive-list'),
      archiveViewer: document.getElementById('file-preview-archive-viewer'),
      archiveSearch: document.getElementById('file-preview-archive-search'),
      pdfFrame: document.getElementById('file-preview-pdf-frame'),
      docxContainer: document.getElementById('file-preview-docx-container'),
      tableWrap: document.getElementById('file-preview-table-wrap'),
      sheetTabs: document.getElementById('file-preview-sheet-tabs'),
      tableScroll: document.getElementById('file-preview-table-scroll'),
      tablePagination: document.getElementById('file-preview-table-pagination'),
      textPre: document.getElementById('file-preview-text'),
      loading: document.getElementById('file-preview-loading'),
    };
  }

  const PREVIEW_PANELS = {
    loading: 'file-preview-loading',
    archive: 'file-preview-archive-layout',
    pdf: 'file-preview-pdf-frame',
    docx: 'file-preview-docx-container',
    table: 'file-preview-table-wrap',
    text: 'file-preview-text',
  };

  function resetArchivePanel(elements) {
    if (elements.archiveList) elements.archiveList.innerHTML = '';
    if (elements.archiveViewer) elements.archiveViewer.innerHTML = '';
    if (elements.archiveSearch) {
      elements.archiveSearch.value = '';
      elements.archiveSearch.oninput = null;
      elements.archiveSearch.onchange = null;
    }
  }

  function setVisiblePreviewPanel(elements, panelName) {
    Object.entries(PREVIEW_PANELS).forEach(([name, id]) => {
      const node = document.getElementById(id);
      if (!node) return;
      const visible = name === panelName;
      node.classList.toggle('is-visible', visible);
      node.hidden = !visible;
    });
    if (panelName !== 'archive') {
      resetArchivePanel(elements);
    }
  }

  function hideAllPreviewPanels(elements) {
    setVisiblePreviewPanel(elements, null);
    if (elements.pdfFrame) elements.pdfFrame.removeAttribute('src');
    if (elements.docxContainer) elements.docxContainer.innerHTML = '';
    if (elements.tableScroll) elements.tableScroll.innerHTML = '';
    if (elements.sheetTabs) elements.sheetTabs.innerHTML = '';
    if (elements.tablePagination) elements.tablePagination.innerHTML = '';
    if (elements.textPre) elements.textPre.textContent = '';
  }

  function openModalShell(title, subtitle, downloadUrl) {
    const elements = getModalElements();
    if (!elements.modal) return elements;
    hideAllPreviewPanels(elements);
    if (elements.title) elements.title.textContent = title || 'Предпросмотр файла';
    if (elements.subtitle) elements.subtitle.textContent = subtitle || '';
    if (elements.downloadLink) {
      elements.downloadLink.href = downloadUrl || '#';
      elements.downloadLink.hidden = !downloadUrl;
    }
    elements.modal.classList.add('open');
    return elements;
  }

  function closeFilePreviewModal() {
    const elements = getModalElements();
    if (elements.modal) elements.modal.classList.remove('open');
    hideAllPreviewPanels(elements);
    currentPreviewState = null;
  }

  function loadScript(url) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[data-preview-src="${url}"]`);
      if (existing) {
        existing.addEventListener('load', () => resolve(), { once: true });
        existing.addEventListener('error', () => reject(new Error(`Script load failed: ${url}`)), { once: true });
        if (existing.dataset.loaded === 'true') resolve();
        return;
      }
      const script = document.createElement('script');
      script.src = url;
      script.dataset.previewSrc = url;
      script.onload = () => {
        script.dataset.loaded = 'true';
        resolve();
      };
      script.onerror = () => reject(new Error(`Script load failed: ${url}`));
      document.head.appendChild(script);
    });
  }

  async function ensureDocxPreviewLibs() {
    if (window.docx && window.JSZip) return;
    if (!docxScriptsPromise) {
      docxScriptsPromise = DOCX_SCRIPT_URLS.reduce(
        (chain, url) => chain.then(() => loadScript(url)),
        Promise.resolve(),
      );
    }
    await docxScriptsPromise;
  }

  async function renderPdfPreview(elements, url, subtitle) {
    hideAllPreviewPanels(elements);
    if (elements.subtitle && subtitle) elements.subtitle.textContent = subtitle;
    setVisiblePreviewPanel(elements, 'pdf');
    if (elements.pdfFrame) {
      const separator = String(url).includes('?') ? '&' : '?';
      elements.pdfFrame.src = `${url}${separator}v=${Date.now()}#toolbar=1&navpanes=0&scrollbar=1&view=FitH`;
    }
  }

  async function renderDocxPreview(elements, url, subtitle, options) {
    const inline = !!(options && options.inline);
    if (!inline) {
      hideAllPreviewPanels(elements);
      if (elements.subtitle && subtitle) elements.subtitle.textContent = subtitle;
      if (!elements.docxContainer) return;
      setVisiblePreviewPanel(elements, 'docx');
    } else if (!elements.docxContainer) {
      return;
    }
    elements.docxContainer.innerHTML = '<div class="file-preview-loading-inline">Загружаю DOCX…</div>';
    await ensureDocxPreviewLibs();
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error('Не удалось загрузить DOCX.');
    const buffer = await response.arrayBuffer();
    elements.docxContainer.innerHTML = '<div id="file-preview-docx-style"></div><div id="file-preview-docx-body"></div>';
    await window.docx.renderAsync(
      buffer,
      document.getElementById('file-preview-docx-body'),
      document.getElementById('file-preview-docx-style'),
      {
        className: 'docx',
        inWrapper: true,
        ignoreWidth: false,
        ignoreHeight: false,
        breakPages: true,
      },
    );
  }

  function renderTable(elements, payload, state, onPageChange) {
    hideAllPreviewPanels(elements);
    if (!elements.tableWrap || !elements.tableScroll) return;
    setVisiblePreviewPanel(elements, 'table');
    const columns = Array.isArray(payload.columns) ? payload.columns : [];
    const rows = Array.isArray(payload.rows) ? payload.rows : [];
    elements.tableScroll.innerHTML = '';
    if (!columns.length && !rows.length) {
      elements.tableScroll.innerHTML = '<div class="file-preview-empty">Таблица пуста или не удалось загрузить данные.</div>';
      return;
    }
    const table = document.createElement('table');
    table.className = 'file-preview-table';
    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    columns.forEach((column) => {
      const th = document.createElement('th');
      th.textContent = column;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      columns.forEach((_, index) => {
        const td = document.createElement('td');
        td.textContent = row[index] != null ? String(row[index]) : '';
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    elements.tableScroll.appendChild(table);

    if (elements.sheetTabs) {
      elements.sheetTabs.innerHTML = '';
      const sheetNames = Array.isArray(payload.sheet_names) ? payload.sheet_names : [];
      if (sheetNames.length > 1) {
        sheetNames.forEach((name, index) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'file-preview-sheet-tab';
          button.textContent = name;
          if (index === Number(payload.sheet_index || state.sheet || 0)) {
            button.classList.add('active');
          }
          button.addEventListener('click', () => onPageChange({ sheet: index, offset: 0 }));
          elements.sheetTabs.appendChild(button);
        });
      }
    }

    if (elements.tablePagination) {
      elements.tablePagination.innerHTML = '';
      const totalRows = Number(payload.total_rows || 0);
      const pageSize = Number(state.limit || 100);
      const offset = Number(state.offset || 0);
      const info = document.createElement('span');
      info.className = 'file-preview-pagination-info';
      info.textContent = `Строки ${Math.min(offset + 1, totalRows || 1)}–${Math.min(offset + rows.length, totalRows)} из ${totalRows}`;
      elements.tablePagination.appendChild(info);
      const prev = document.createElement('button');
      prev.type = 'button';
      prev.className = 'btn-outline';
      prev.textContent = 'Назад';
      prev.disabled = offset <= 0;
      prev.addEventListener('click', () => onPageChange({ offset: Math.max(0, offset - pageSize) }));
      elements.tablePagination.appendChild(prev);
      const next = document.createElement('button');
      next.type = 'button';
      next.className = 'btn-outline';
      next.textContent = 'Дальше';
      next.disabled = offset + pageSize >= totalRows;
      next.addEventListener('click', () => onPageChange({ offset: offset + pageSize }));
      elements.tablePagination.appendChild(next);
    }
  }

  function renderTextPreview(elements, payload) {
    hideAllPreviewPanels(elements);
    if (!elements.textPre) return;
    setVisiblePreviewPanel(elements, 'text');
    elements.textPre.textContent = String(payload.content || '');
  }

  async function loadTablePreview(state) {
    const query = new URLSearchParams({
      kind: state.kind,
      offset: String(state.offset || 0),
      limit: String(state.limit || 100),
      sheet: String(state.sheet || 0),
    });
    if (state.jobId) query.set('job_id', state.jobId);
    return requestJson(resolveApiUrl(`/api/preview/table?${query.toString()}`));
  }

  async function loadTextPreview(state) {
    const query = new URLSearchParams({
      kind: state.kind,
      offset: String(state.offset || 0),
      limit: String(state.limit || 500),
    });
    if (state.jobId) query.set('job_id', state.jobId);
    return requestJson(resolveApiUrl(`/api/preview/text?${query.toString()}`));
  }

  async function loadArchiveEntries(state) {
    const query = new URLSearchParams({
      kind: state.kind,
      offset: String(state.offset || 0),
      limit: String(state.limit || 100),
    });
    if (state.query) query.set('q', state.query);
    if (state.jobId) query.set('job_id', state.jobId);
    return requestJson(resolveApiUrl(`/api/preview/archive?${query.toString()}`));
  }

  function archiveFileUrl(kind, path, jobId) {
    const query = new URLSearchParams({ kind, path });
    if (jobId) query.set('job_id', jobId);
    return resolveApiUrl(`/api/preview/file?${query.toString()}`);
  }

  async function renderArchivePreview(elements, state) {
    hideAllPreviewPanels(elements);
    if (!elements.archiveLayout || !elements.archiveList) return;
    setVisiblePreviewPanel(elements, 'archive');
    elements.archiveList.innerHTML = '<div class="file-preview-loading-inline">Загружаю список файлов…</div>';
    const payload = await loadArchiveEntries(state);
    const entries = Array.isArray(payload.entries) ? payload.entries : [];
    elements.archiveList.innerHTML = '';
    if (elements.archiveSearch) {
      elements.archiveSearch.value = state.query || '';
      elements.archiveSearch.onchange = null;
      elements.archiveSearch.oninput = null;
      let searchTimer = null;
      elements.archiveSearch.oninput = () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(async () => {
          currentPreviewState = { ...state, query: elements.archiveSearch.value.trim(), offset: 0 };
          await renderArchivePreview(elements, currentPreviewState);
        }, 300);
      };
    }
    if (!entries.length) {
      elements.archiveList.innerHTML = '<div class="file-preview-empty">Файлы не найдены.</div>';
      return;
    }
    entries.forEach((entry) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'file-preview-archive-item';
      button.dataset.path = entry.path || '';
      button.textContent = entry.label || entry.name || entry.path;
      button.addEventListener('click', async () => {
        elements.archiveList.querySelectorAll('.file-preview-archive-item.active').forEach((node) => {
          node.classList.remove('active');
        });
        button.classList.add('active');
        if (!elements.archiveViewer) return;
        elements.archiveViewer.innerHTML = '';
        const ext = String(entry.ext || '').toLowerCase();
        if (ext === '.pdf') {
          const frame = document.createElement('iframe');
          frame.className = 'file-preview-archive-frame';
          frame.title = entry.label || entry.name || 'PDF';
          frame.src = `${archiveFileUrl(state.kind, entry.path, state.jobId)}&v=${Date.now()}#toolbar=1&navpanes=0&scrollbar=1&view=FitH`;
          elements.archiveViewer.appendChild(frame);
          return;
        }
        if (ext === '.docx') {
          const docxHost = document.createElement('div');
          docxHost.className = 'file-preview-docx-container';
          elements.archiveViewer.appendChild(docxHost);
          const temp = { ...elements, docxContainer: docxHost };
          await renderDocxPreview(
            temp,
            archiveFileUrl(state.kind, entry.path, state.jobId),
            entry.label || entry.name,
            { inline: true },
          );
          return;
        }
        elements.archiveViewer.innerHTML = '<div class="file-preview-empty">Предпросмотр доступен только для PDF и DOCX. Скачайте файл целиком.</div>';
      });
      elements.archiveList.appendChild(button);
    });
    const firstPreviewable = entries.find((entry) => ['.pdf', '.docx'].includes(String(entry.ext || '').toLowerCase()));
    if (firstPreviewable) {
      const target = elements.archiveList.querySelector(
        `[data-path="${CSS.escape(firstPreviewable.path || '')}"]`,
      );
      target?.click();
    }
  }

  async function openFilePreview(kind, options) {
    const normalizedKind = String(kind || '').trim();
    if (!normalizedKind) return;
    const jobId = options && options.jobId != null
      ? options.jobId
      : (typeof window.currentJobId === 'string' ? window.currentJobId : '');
    const elements = openModalShell(
      options && options.label ? options.label : 'Предпросмотр файла',
      'Загружаю предпросмотр…',
      '',
    );
    if (elements.loading) {
      setVisiblePreviewPanel(elements, 'loading');
    }
    try {
      const metaQuery = new URLSearchParams({ kind: normalizedKind });
      if (jobId) metaQuery.set('job_id', jobId);
      const meta = await requestJson(resolveApiUrl(`/api/preview/meta?${metaQuery.toString()}`));
      if (elements.loading) setVisiblePreviewPanel(elements, null);
      if (elements.title) elements.title.textContent = meta.title || (options && options.label) || 'Предпросмотр файла';
      if (elements.subtitle) elements.subtitle.textContent = meta.filename || '';
      if (elements.downloadLink) {
        elements.downloadLink.href = resolveApiUrl(meta.download_url || '');
        elements.downloadLink.hidden = false;
      }
      const state = {
        kind: normalizedKind,
        jobId,
        offset: 0,
        limit: Number(meta.page_size || 100),
        sheet: 0,
        query: '',
        previewMode: meta.preview_mode,
      };
      currentPreviewState = state;

      if (meta.preview_mode === 'archive') {
        await renderArchivePreview(elements, state);
        return;
      }
      if (meta.preview_mode === 'table' || meta.preview_mode === 'ndjson') {
        const renderPagedTable = async (patch) => {
          Object.assign(state, patch || {});
          const payload = meta.preview_mode === 'ndjson'
            ? await loadTextPreview(state)
            : await loadTablePreview(state);
          renderTable(elements, payload, state, renderPagedTable);
        };
        await renderPagedTable({});
        return;
      }
      if (meta.preview_mode === 'text') {
        const payload = await loadTextPreview(state);
        renderTextPreview(elements, payload);
        return;
      }
      throw new Error('Этот тип файла пока не поддерживает предпросмотр.');
    } catch (error) {
      closeFilePreviewModal();
      showToastMessage(error.message || 'Не удалось открыть предпросмотр.');
    }
  }

  function previewKindFromDownloadUrl(url) {
    const raw = String(url || '').trim();
    let path = raw.split('?')[0].split('#')[0];
    try {
      if (/^https?:\/\//i.test(raw)) {
        path = new URL(raw).pathname;
      }
    } catch (e) {}
    const mapping = {
      '/api/download/output': 'output',
      '/api/download/data-xlsx': 'data-xlsx',
      '/api/parser/download-result': 'parser-result',
      '/api/parser/download-failed': 'parser-failed',
      '/api/download/correction-report': 'correction-report',
      '/api/download/sender-delivery-report': 'sender-delivery-report',
      '/api/download/sent-mail-log': 'sent-mail-log',
      '/api/download/inflection-log': 'inflection-log',
      '/api/download/inflection-report': 'inflection-report',
      '/api/download/agent-memory': 'agent-memory',
      '/api/download/agent-quarantine': 'agent-quarantine',
      '/api/download/agent-report': 'agent-report',
    };
    return mapping[path] || '';
  }

  window.openFilePreview = openFilePreview;
  window.closeFilePreviewModal = closeFilePreviewModal;
  window.previewKindFromDownloadUrl = previewKindFromDownloadUrl;

  if (typeof window.upgradeAllChatPreviewButtons === 'function') {
    window.upgradeAllChatPreviewButtons();
  }
}());
