import grapesjs, { usePlugin } from 'grapesjs';

import grapesjsPresetNewsletterImport from 'grapesjs-preset-newsletter';

import 'grapesjs/dist/css/grapes.min.css';

import type { Editor } from 'grapesjs';



type NewsletterPresetPlugin = (editor: Editor, options?: Record<string, unknown>) => void;



type CreateEditorOptions = {
  container: HTMLElement;
  bodyHtml: string;
  projectData?: Record<string, unknown> | null;
  importedLayout?: boolean;
  importSource?: string | null;
  onChange: () => void;
  uploadAsset: (file: File) => Promise<string>;
  onUploadError?: (error: unknown) => void;
};



const BLOCK_LABELS: Record<string, string> = {

  sect100: 'Секция 100%',

  sect50: '2 колонки 50/50',

  sect30: '3 колонки',

  sect37: 'Колонки 30/70',

  button: 'Кнопка',

  divider: 'Разделитель',

  text: 'Текст',

  'text-sect': 'Заголовок + текст',

  image: 'Изображение',

  quote: 'Цитата',

  'grid-items': 'Сетка 2×1',

  'list-items': 'Список',

  'merge-tag': 'Переменная',

  'chain-buttons': 'Кнопки цепочки',

};



const CHAIN_BUTTONS_ALIGN_OPTIONS = [

  { id: 'left', name: 'Слева' },

  { id: 'center', name: 'По центру' },

  { id: 'right', name: 'Справа' },

];



function buildChainButtonsStubInnerHtml(): string {

  return (

    '<div class="ma-chain-buttons-stub-inner" style="display:inline-block;border:2px dashed #bfbfbf;'

    + 'border-radius:8px;padding:12px 16px;background:#fafafa;text-align:center">'

    + '<p style="margin:0 0 8px">'

    + '<span style="display:inline-block;padding:8px 16px;background:#d9d9d9;color:#595959;'

    + 'border-radius:4px;margin:0 4px">Вариант 1</span>'

    + '<span style="display:inline-block;padding:8px 16px;background:#d9d9d9;color:#595959;'

    + 'border-radius:4px;margin:0 4px">Вариант 2</span>'

    + '</p>'

    + '<p style="margin:0;font-size:12px;color:#8c8c8c">Кнопки цепочки</p>'

    + '</div>'

  );

}



function registerChainButtonsComponent(editor: Editor): void {

  const domComponents = editor.DomComponents;

  const defaultType = domComponents.getType('default');

  const defaultModel = defaultType.model;

  const defaultView = defaultType.view;



  domComponents.addType('ma-chain-buttons', {

    isComponent(el) {

      if (typeof el === 'object' && el !== null && 'getAttribute' in el) {

        const element = el as HTMLElement;

        if (element.getAttribute?.('data-ma-chain-buttons') === '1') {

          return { type: 'ma-chain-buttons' };

        }

      }

      return false;

    },

    model: {

      defaults: {

        ...defaultModel.prototype.defaults,

        name: 'Кнопки цепочки',

        tagName: 'div',

        attributes: {

          'data-ma-chain-buttons': '1',

          class: 'ma-chain-buttons-stub',

        },

        droppable: false,

        draggable: true,

        copyable: true,

        removable: true,

        traits: [

          {

            type: 'select',

            label: 'Выравнивание',

            name: 'align',

            options: CHAIN_BUTTONS_ALIGN_OPTIONS,

            changeProp: true,

          },

        ],

        align: 'center',

        style: {

          'text-align': 'center',

          padding: '8px 0',

        },

        components: buildChainButtonsStubInnerHtml(),

      },

      init(this: { on: (event: string, handler: () => void) => void; updateAlign: () => void }) {

        this.on('change:align', this.updateAlign);

        this.updateAlign();

      },

      updateAlign(this: { get: (key: string) => string; addStyle: (style: Record<string, string>) => void }) {

        const align = this.get('align') || 'center';

        this.addStyle({ 'text-align': align });

      },

    },

    view: defaultView,

  });

}



function resolveNewsletterPreset(): NewsletterPresetPlugin {

  let candidate: unknown = grapesjsPresetNewsletterImport;

  while (candidate && typeof candidate !== 'function') {

    candidate = (candidate as { default?: unknown }).default;

  }

  if (typeof candidate !== 'function') {

    throw new Error('Не удалось загрузить плагин grapesjs-preset-newsletter');

  }

  return candidate as NewsletterPresetPlugin;

}



const FIXED_LAYOUT_RE = /data-layout=["']fixed["']/i;

export function isFixedLayoutHtml(html: string, importSource?: string | null): boolean {
  if (importSource === 'fixed_layout') return true;
  return FIXED_LAYOUT_RE.test(html || '');
}

function extractStyleBlocks(html: string): string[] {
  const blocks: string[] = [];
  const pattern = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
  let match = pattern.exec(html);
  while (match) {
    blocks.push(match[0]);
    match = pattern.exec(html);
  }
  return blocks;
}

function injectFixedLayoutCanvasStyles(editor: Editor, styleBlocks: string[]): void {
  const frame = editor.Canvas.getFrameEl() as HTMLIFrameElement | null;
  const doc = frame?.contentDocument;
  if (!doc) return;
  const head = doc.head || doc.getElementsByTagName('head')[0];
  if (!head) return;
  head.querySelectorAll('[data-ma-fixed-layout="1"]').forEach((node) => node.remove());
  for (const block of styleBlocks) {
    const css = block.replace(/^<style\b[^>]*>/i, '').replace(/<\/style>$/i, '');
    const el = doc.createElement('style');
    el.setAttribute('data-ma-fixed-layout', '1');
    el.textContent = css;
    head.appendChild(el);
  }
  // Suppress GrapesJS dashed/selected chrome on every absolute text chip — it reads
  // as white "highlights" over the decor PNG (especially on teal callouts).
  const chrome = doc.createElement('style');
  chrome.setAttribute('data-ma-fixed-layout', '1');
  chrome.textContent = `
    .ma-fixed-layout-root .fixed-text,
    .ma-fixed-layout-root .fixed-text *,
    .ma-fixed-layout-root .fixed-text div,
    .ma-fixed-layout-root .fixed-text span,
    .ma-fixed-layout-root .fixed-text a {
      background: transparent !important;
      background-color: transparent !important;
      box-shadow: none !important;
    }
    .ma-fixed-layout-root .fixed-text [data-gjs-highlightable],
    .ma-fixed-layout-root .fixed-text *[data-gjs-highlightable] {
      outline: none !important;
      box-shadow: none !important;
      background: transparent !important;
      background-color: transparent !important;
    }
    .ma-fixed-layout-root .fixed-text .gjs-selected,
    .ma-fixed-layout-root .fixed-text .gjs-hovered,
    .ma-fixed-layout-root .fixed-page .gjs-selected,
    .ma-fixed-layout-root .fixed-page .gjs-hovered {
      outline: none !important;
      box-shadow: none !important;
      background: transparent !important;
      background-color: transparent !important;
    }
    .gjs-dashed .ma-fixed-layout-root .fixed-text *[data-gjs-highlightable] {
      outline: none !important;
      background: transparent !important;
    }
  `;
  head.appendChild(chrome);
}

function registerFixedLayoutComponent(editor: Editor): void {
  const domComponents = editor.DomComponents;
  const defaultType = domComponents.getType('default');
  const defaultModel = defaultType.model;
  const defaultView = defaultType.view;

  domComponents.addType('ma-fixed-layout-page', {
    isComponent(el) {
      if (typeof el === 'object' && el !== null && 'getAttribute' in el) {
        const element = el as HTMLElement;
        if (element.getAttribute?.('data-layout') === 'fixed') {
          return { type: 'ma-fixed-layout-page' };
        }
      }
      return false;
    },
    model: {
      defaults: {
        ...defaultModel.prototype.defaults,
        name: 'Fixed layout',
        tagName: 'div',
        droppable: false,
        editable: false,
        draggable: false,
        copyable: false,
        removable: false,
        layerable: true,
        highlightable: true,
        selectable: true,
        hoverable: true,
        attributes: {
          'data-layout': 'fixed',
          class: 'fixed-page',
        },
      },
    },
    view: defaultView,
  });

  // Absolute text chips inside fixed layout: keep editable text but no selection chrome.
  domComponents.addType('ma-fixed-layout-chip', {
    isComponent(el) {
      if (typeof el !== 'object' || el === null || !('closest' in el)) return false;
      const element = el as HTMLElement;
      if (!element.closest?.('.fixed-text')) return false;
      const style = (element.getAttribute?.('style') || '').toLowerCase();
      if (style.includes('position:absolute') || style.includes('position: absolute')) {
        return { type: 'ma-fixed-layout-chip' };
      }
      return false;
    },
    model: {
      defaults: {
        ...defaultModel.prototype.defaults,
        name: 'Fixed text',
        droppable: false,
        draggable: false,
        copyable: false,
        removable: false,
        highlightable: false,
        hoverable: false,
        selectable: true,
        editable: true,
      },
    },
    view: defaultView,
  });
}

const NEWSLETTER_PRESET_OPTS = {
  modalTitleImport: 'Импорт HTML',
  modalTitleExport: 'Экспорт HTML',
  modalBtnImport: 'Импорт',
  textCleanCanvas: 'Очистить холст? Все блоки будут удалены.',
  inlineCss: true,
  showBlocksOnLoad: true,
  showStylesOnChange: true,
};

function wrapImportedHtml(html: string): string {
  const trimmed = html.trim();
  if (!trimmed) return trimmed;
  if (/class=["'][^"']*main-body/i.test(trimmed)) {
    return trimmed;
  }
  const widthMatch = trimmed.match(/data-content-width=["'](\d+)["']/i)
    || trimmed.match(/max-width\s*:\s*(\d+)px/i)
    || trimmed.match(/width:(\d+)px/i);
  const maxWidth = Math.max(480, Math.min(800, Number(widthMatch?.[1] || 600)));
  const isFixedLayout = /data-layout=["']fixed["']/i.test(trimmed);
  const cellStyle = isFixedLayout ? 'padding:0;overflow:hidden' : 'padding:0';
  return (
    '<table class="main-body" width="100%" cellpadding="0" cellspacing="0" role="presentation" '
    + `style="width:100%;max-width:${maxWidth}px;margin:0 auto;background:#ffffff">`
    + `<tr><td class="cell" style="${cellStyle}">`
    + trimmed
    + '</td></tr></table>'
  );
}



export function createVisualEmailEditor(options: CreateEditorOptions): Editor {
  const fixedLayoutMode = isFixedLayoutHtml(options.bodyHtml, options.importSource);
  const presetOpts = {
    ...NEWSLETTER_PRESET_OPTS,
    inlineCss: !fixedLayoutMode,
    showStylesOnChange: !fixedLayoutMode,
  };
  const fixedLayoutStyleBlocks = fixedLayoutMode ? extractStyleBlocks(options.bodyHtml) : [];

  const editor = grapesjs.init({

    container: options.container,

    height: '100%',

    width: 'auto',

    storageManager: false,

    plugins: [usePlugin(resolveNewsletterPreset(), presetOpts)],

    deviceManager: {

      devices: [

        { name: 'Desktop', width: '' },

        { name: 'Mobile', width: '320px', widthMedia: '480px' },

      ],

    },

    assetManager: {

      autoAdd: true,

    },

  });



  registerChainButtonsComponent(editor);
  registerFixedLayoutComponent(editor);

  if (fixedLayoutMode) {
    editor.on('canvas:frame:load', () => {
      injectFixedLayoutCanvasStyles(editor, fixedLayoutStyleBlocks);
    });
  }



  editor.BlockManager.add('merge-tag', {

    label: 'Переменная',

    category: 'Письмо',

    attributes: { class: 'fa fa-code' },

    content: '<span>{{contact_name}}</span>',

  });



  editor.BlockManager.add('chain-buttons', {

    label: 'Кнопки цепочки',

    category: 'Письмо',

    attributes: { class: 'fa fa-link' },

    content: { type: 'ma-chain-buttons' },

  });



  editor.on('load', () => {

    editor.BlockManager.getAll().forEach((block: { get(key: string): unknown; set(key: string, value: string): void }) => {

      const id = String(block.get('id') || '');

      const label = BLOCK_LABELS[id];

      if (label) block.set('label', label);

    });



    if (options.projectData && Object.keys(options.projectData).length > 0) {

      editor.loadProjectData(options.projectData);

    } else if (options.bodyHtml.trim()) {

      const html = options.importedLayout ? wrapImportedHtml(options.bodyHtml) : options.bodyHtml;

      editor.setComponents(html);
      if (fixedLayoutMode) {
        injectFixedLayoutCanvasStyles(editor, fixedLayoutStyleBlocks);
        editor.getWrapper()?.addClass('ma-fixed-layout-root');
      }

    }

  });



  editor.AssetManager.config.uploadFile = async (event: Event) => {

    const input = event.target as HTMLInputElement | null;

    const dragEvent = event as DragEvent;

    const files = input?.files || dragEvent.dataTransfer?.files;

    if (!files?.length) return;

    for (const file of Array.from(files)) {

      try {

        const url = await options.uploadAsset(file);

        editor.AssetManager.add({ src: url, type: 'image' });

      } catch (error) {

        options.onUploadError?.(error);

      }

    }

  };



  editor.on('update', options.onChange);

  editor.on('component:add', options.onChange);

  editor.on('component:remove', options.onChange);



  return editor;

}



export function exportVisualEmailHtml(
  editor: Editor,
  options?: { canonicalHtml?: string; importSource?: string | null },
): string {
  const canonical = options?.canonicalHtml || '';
  if (canonical && isFixedLayoutHtml(canonical, options?.importSource)) {
    return canonical;
  }

  const inlined = editor.runCommand('gjs-get-inlined-html');

  if (typeof inlined === 'string' && inlined.trim()) {

    return inlined;

  }

  const css = editor.getCss() || '';

  const html = editor.getHtml();

  if (!css.trim()) return html;

  return `<style>${css}</style>${html}`;

}



export function insertMergeVariable(editor: Editor, variableName: string): void {

  const token = `{{${variableName}}}`;

  const selected = editor.getSelected();

  if (selected?.is('text')) {

    selected.append(token);

    return;

  }

  editor.addComponents(`<span>${token}</span>`);

}


