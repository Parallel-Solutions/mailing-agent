import grapesjs, { usePlugin } from 'grapesjs';

import grapesjsPresetNewsletterImport from 'grapesjs-preset-newsletter';

import 'grapesjs/dist/css/grapes.min.css';

import type { Editor } from 'grapesjs';



type NewsletterPresetPlugin = (editor: Editor, options?: Record<string, unknown>) => void;



type CreateEditorOptions = {

  container: HTMLElement;

  bodyHtml: string;

  projectData?: Record<string, unknown> | null;

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



const NEWSLETTER_PRESET_OPTS = {
  modalTitleImport: 'Импорт HTML',
  modalTitleExport: 'Экспорт HTML',
  modalBtnImport: 'Импорт',
  textCleanCanvas: 'Очистить холст? Все блоки будут удалены.',
  inlineCss: true,
  showBlocksOnLoad: true,
  showStylesOnChange: true,
};



export function createVisualEmailEditor(options: CreateEditorOptions): Editor {

  const editor = grapesjs.init({

    container: options.container,

    height: '100%',

    width: 'auto',

    storageManager: false,

    plugins: [usePlugin(resolveNewsletterPreset(), NEWSLETTER_PRESET_OPTS)],

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

      editor.setComponents(options.bodyHtml);

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



export function exportVisualEmailHtml(editor: Editor): string {

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


