import {
  AlignCenterOutlined,
  AlignLeftOutlined,
  AlignRightOutlined,
  ArrowLeftOutlined,
  BoldOutlined,
  CheckCircleFilled,
  CopyOutlined,
  DownloadOutlined,
  EyeOutlined,
  FilePdfOutlined,
  FileWordOutlined,
  ItalicOutlined,
  OrderedListOutlined,
  PictureOutlined,
  RedoOutlined,
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
  StrikethroughOutlined,
  TableOutlined,
  UndoOutlined,
  UnorderedListOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { EditorContent, useEditor } from '@tiptap/react';
import type { Editor } from '@tiptap/react';
import Link from '@tiptap/extension-link';
import Paragraph from '@tiptap/extension-paragraph';
import Placeholder from '@tiptap/extension-placeholder';
import StarterKit from '@tiptap/starter-kit';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Breadcrumb,
  Button,
  Card,
  Divider,
  Empty,
  Input,
  Modal,
  Popover,
  Select,
  Skeleton,
  Space,
  Tag,
  Tooltip,
  Typography,
  Upload,
} from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { templatesApi } from '@/api/templates';
import type { OfficeEditorConfig } from '@/api/templates';
import type { PdfEditorField, Template } from '@/api/types';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { readBoolParam } from '@/utils/urlState';
import { VisualEmailEditor } from '@/features/templates/VisualEmailEditor';
import { PersonalizationSetting } from '@/features/templates/PersonalizationSetting';
import { DeliveryFilenameField } from '@/features/templates/DeliveryFilenameField';
import { showDocumentUploadError } from '@/features/templates/documentUploadError';
import {
  downloadEmailHtml,
  getEmailFormat,
  preserveParagraphIndents,
} from '@/features/templates/emailTemplateUtils';
import {
  applyParagraphIndentToAll,
  buildEmailEditorHtml,
  bulkParagraphIndentActive,
  cancelParagraphIndentNormalization,
  collectParagraphIndentStates,
  handleParagraphIndentKeydown,
  paragraphIndentActive,
  scheduleParagraphIndentNormalization,
  toggleCurrentParagraphIndent,
} from '@/features/templates/emailEditorIndent';
import './TemplateEditorPage.css';

const { Text, Title } = Typography;

function isDocumentTemplateType(templateType: string): boolean {
  return templateType === 'document' || templateType === 'kp' || templateType === 'contract';
}

type EditorVariable = { name: string; label: string; source: string };
type CanvasController = {
  command: (name: string, value?: string) => void;
  insertHtml: (html: string) => void;
  getHtml: () => string;
};
type OnlyOfficeInstance = { destroyEditor?: () => void };

declare global {
  interface Window {
    DocsAPI?: { DocEditor: new (id: string, config: Record<string, unknown>) => OnlyOfficeInstance };
  }
}

const EMAIL_VARIABLES: EditorVariable[] = [
  { name: 'company', label: 'Компания', source: 'Получатель' },
  { name: 'contact_name', label: 'Контактное лицо', source: 'Получатель' },
  { name: 'Имя', label: 'Имя (из ФИО)', source: 'Получатель' },
  { name: 'Отчество', label: 'Отчество (из ФИО)', source: 'Получатель' },
  { name: 'Имя Отчество', label: 'Имя и отчество (без фамилии)', source: 'Получатель' },
  { name: 'email', label: 'Email', source: 'Получатель' },
  { name: 'region', label: 'Регион', source: 'Получатель' },
  { name: 'campaign_name', label: 'Название рассылки', source: 'Рассылка' },
];

const DOCUMENT_VARIABLES: EditorVariable[] = [
  { name: 'OUTGOING_NUMBER', label: 'Исходящий номер', source: 'Система' },
  { name: 'DATE', label: 'Дата документа', source: 'Система' },
  { name: 'VALID_UNTIL', label: 'Срок действия', source: 'Система' },
  { name: 'DIRECTOR_NAME', label: 'Подписант', source: 'Система' },
  { name: 'ADM_NAME', label: 'Получатель', source: 'Получатель' },
  { name: 'WORK_TITLE', label: 'Вид работ', source: 'Система' },
  { name: 'MUN_R_SCOPE_FRAGMENT', label: 'Муниципальное образование', source: 'Система' },
  { name: 'PRICE_TOTAL', label: 'Стоимость', source: 'Система' },
];

const SAMPLE_VALUES: Record<string, string> = {
  company: 'ООО «Вектор»',
  contact_name: 'Анна Сергеевна',
  Имя: 'Анна',
  Отчество: 'Сергеевна',
  'Имя Отчество': 'Анна Сергеевна',
  email: 'anna@vector.ru',
  region: 'Московская область',
  campaign_name: 'КП — июль 2026',
};

const DEFAULT_KP_HTML = `<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<style>
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; width: 210mm; min-height: 297mm; background: #fff; }
body { font-family: Arial, sans-serif; color: #303633; }
.page { position: relative; width: 210mm; min-height: 297mm; padding: 15mm 17mm 12mm; overflow: hidden; }
.brand { color: #236348; font-size: 10pt; font-weight: 700; letter-spacing: .08em; border-bottom: 2px solid #2d6a4f; padding-bottom: 5mm; }
.meta { display: grid; grid-template-columns: 1fr 78mm; gap: 12mm; margin: 11mm 0; font-size: 10pt; }
.recipient { font-weight: 700; line-height: 1.5; }
h1 { margin: 0 0 9mm; text-align: center; color: #174d38; font-size: 17pt; }
p { margin: 0 0 5mm; font-size: 10.5pt; line-height: 1.55; text-align: justify; }
table { width: 100%; border-collapse: collapse; margin: 8mm 0; font-size: 10pt; }
th, td { border: 1px solid #747b77; padding: 3mm; }
th { background: #edf5f0; color: #174d38; }
.price { width: 38mm; text-align: right; }
.footer { display: grid; grid-template-columns: 1fr 62mm; gap: 10mm; margin-top: 18mm; align-items: end; }
.signature { color: #174d38; font-weight: 700; }
.variable-token { display: inline; padding: 1px 3px; border-radius: 3px; background: #e7f4ec; color: #145c3e; font-family: Consolas, monospace; font-size: .92em; }
</style>
</head>
<body>
<section class="page">
  <div class="brand">AI-OFFER · КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ</div>
  <div class="meta">
    <div>№ <span class="variable-token">{{OUTGOING_NUMBER}}</span> от <span class="variable-token">{{DATE}}</span></div>
    <div class="recipient">Руководителю<br><span class="variable-token">{{ADM_NAME}}</span></div>
  </div>
  <h1>КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ</h1>
  <p>Предлагаем выполнить работы по <strong><span class="variable-token">{{WORK_TITLE}}</span></strong> для <span class="variable-token">{{MUN_R_SCOPE_FRAGMENT}}</span>.</p>
  <p>В стоимость включены подготовка материалов, консультационное сопровождение и передача итогового комплекта документов.</p>
  <table>
    <thead><tr><th>Вид работ</th><th class="price">Стоимость, руб.</th></tr></thead>
    <tbody>
      <tr><td><span class="variable-token">{{WORK_TITLE}}</span></td><td class="price"><span class="variable-token">{{PRICE_TOTAL}}</span></td></tr>
      <tr><td><strong>ИТОГО</strong></td><td class="price"><strong><span class="variable-token">{{PRICE_TOTAL}}</span></strong></td></tr>
    </tbody>
  </table>
  <p>Срок действия предложения: до <span class="variable-token">{{VALID_UNTIL}}</span>.</p>
  <div class="footer">
    <div><div class="signature">С уважением,<br>генеральный директор</div></div>
    <div><span class="variable-token">{{DIRECTOR_NAME}}</span></div>
  </div>
</section>
</body>
</html>`;

function PersonalizationSettingPanel({ template }: { template: Template }) {
  return <PersonalizationSetting template={template} />;
}

function DocumentDeliverySettingsRow({ template }: { template: Template }) {
  return (
    <div className="document-delivery-settings-row">
      <PersonalizationSettingPanel template={template} />
      <DeliveryFilenameField template={template} />
    </div>
  );
}

function EditorHeader({
  template,
  dirty,
  saving,
  onBack,
  onPreview,
  onSave,
  onDownloadHtml,
  format,
  saveLabel = 'Сохранить версию',
  saveDisabled = false,
}: {
  template: Template;
  dirty: boolean;
  saving: boolean;
  onBack: () => void;
  onPreview: () => void;
  onSave: () => void;
  onDownloadHtml?: () => void;
  format?: 'PDF' | 'DOCX';
  saveLabel?: string;
  saveDisabled?: boolean;
}) {
  const section = template.template_type === 'email' ? 'Письма' : template.template_type === 'kp' ? 'Коммерческие предложения' : 'Документы';
  return (
    <>
      <Breadcrumb items={[{ title: 'Шаблоны и документы' }, { title: section }, { title: 'Редактирование' }]} />
      <div className="template-editor-heading">
        <Space align="start">
          <Button icon={<ArrowLeftOutlined />} onClick={onBack} aria-label="Назад" />
          <div>
            <Space wrap>
              <Title level={3} style={{ margin: 0 }}>{template.name}</Title>
              {format && <Tag color="green">Итоговый формат: {format}</Tag>}
              <Tag>Версия {template.version?.version_number || 1}</Tag>
            </Space>
            <Text type="secondary">{dirty ? 'Есть несохранённые изменения' : 'Все изменения сохранены'}</Text>
          </div>
        </Space>
        <Space wrap>
          {onDownloadHtml && (
            <Button icon={<DownloadOutlined />} onClick={onDownloadHtml}>Скачать HTML</Button>
          )}
          <Button icon={<EyeOutlined />} onClick={onPreview}>Предпросмотр</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={saveDisabled} onClick={onSave}>{saveLabel}</Button>
        </Space>
      </div>
    </>
  );
}

function VariablePanel({ variables, query, onQuery, onInsert, insertLabel = 'Вставить' }: {
  variables: EditorVariable[];
  query: string;
  onQuery: (value: string) => void;
  onInsert: (name: string) => void;
  insertLabel?: string;
}) {
  const filtered = variables.filter((variable) => `${variable.name} ${variable.label}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <Card className="template-side-panel" title="Переменные">
      <Input allowClear prefix={<SearchOutlined />} placeholder="Найти переменную" value={query} onChange={(event) => onQuery(event.target.value)} />
      <div className="template-variable-list">
        {filtered.map((variable) => (
          <button key={variable.name} type="button" className="template-variable-row" onClick={() => onInsert(variable.name)} title={`${insertLabel} ${variable.name}`}>
            <span><code>{`{{${variable.name}}}`}</code><small>{variable.label} · {variable.source}</small></span>
            <span className="template-variable-add">+</span>
          </button>
        ))}
      </div>
    </Card>
  );
}

function Checks({ pdf = false, kpDocx = false }: { pdf?: boolean; kpDocx?: boolean }) {
  const checks = kpDocx
    ? ['Исходный DOCX сохранён без изменений', 'PDF-копия создана для отправки', 'В архиве доступны DOCX и PDF']
    : pdf
      ? ['Макет помещается на страницу A4', 'Все переменные распознаны', 'PDF собирается из текущего HTML']
      : ['Исходный DOCX сохранён', 'Автосохранение включено', 'Новая версия создаётся без изменения оригинала'];
  return (
    <Card className="template-side-panel" title="Проверка">
      <div className="template-check-list">{checks.map((check) => <span key={check}><CheckCircleFilled />{check}</span>)}</div>
    </Card>
  );
}


const EmailParagraph = Paragraph.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      textIndent: {
        default: null,
        keepOnSplit: true,
        parseHTML: (element) => {
          const style = element.getAttribute('style') || '';
          const match = style.match(/text-indent\s*:\s*([^;]+)/i);
          return match?.[1]?.trim() || null;
        },
        renderHTML: (attributes) => {
          if (!attributes.textIndent) return {};
          return { style: `text-indent:${attributes.textIndent}` };
        },
      },
    };
  },
});

function EmailToolbar({ editor, refreshKey }: { editor: Editor | null; refreshKey: number }) {
  void refreshKey;
  const paragraphAttrs = editor?.getAttributes('paragraph') || {};
  const indentActive = paragraphIndentActive(paragraphAttrs);
  const bulkIndentActive = editor
    ? bulkParagraphIndentActive(collectParagraphIndentStates(editor), { skipFirst: true })
    : false;
  return (
    <div className="template-editor-toolbar">
      <Button type="text" size="small" icon={<UndoOutlined />} onClick={() => editor?.chain().focus().undo().run()} />
      <Button type="text" size="small" icon={<RedoOutlined />} onClick={() => editor?.chain().focus().redo().run()} />
      <Divider type="vertical" />
      <Button type={editor?.isActive('bold') ? 'primary' : 'text'} size="small" icon={<BoldOutlined />} onClick={() => editor?.chain().focus().toggleBold().run()} />
      <Button type={editor?.isActive('italic') ? 'primary' : 'text'} size="small" icon={<ItalicOutlined />} onClick={() => editor?.chain().focus().toggleItalic().run()} />
      <Button type={editor?.isActive('strike') ? 'primary' : 'text'} size="small" icon={<StrikethroughOutlined />} onClick={() => editor?.chain().focus().toggleStrike().run()} />
      <Button type={editor?.isActive('bulletList') ? 'primary' : 'text'} size="small" icon={<UnorderedListOutlined />} onClick={() => editor?.chain().focus().toggleBulletList().run()} />
      <Button type={editor?.isActive('orderedList') ? 'primary' : 'text'} size="small" icon={<OrderedListOutlined />} onClick={() => editor?.chain().focus().toggleOrderedList().run()} />
      <Divider type="vertical" />
      <Tooltip title="Красная строка (Tab)">
        <Button
          type={indentActive ? 'primary' : 'text'}
          size="small"
          onClick={() => {
            if (!editor) return;
            toggleCurrentParagraphIndent(editor);
          }}
        >
          ¶
        </Button>
      </Tooltip>
      <Tooltip title="Красная строка во всех абзацах (кроме первого)">
        <Button
          type={bulkIndentActive ? 'primary' : 'text'}
          size="small"
          onClick={() => {
            if (!editor) return;
            applyParagraphIndentToAll(editor, { skipFirst: true });
          }}
        >
          ¶¶
        </Button>
      </Tooltip>
    </div>
  );
}

function EmailTemplateEditor({ template }: { template: Template }) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { searchParams, pushParams } = useUrlNavigation();
  const previewOpen = readBoolParam(searchParams, 'preview');
  const [name, setName] = useState(template.name);
  const [subject, setSubject] = useState(template.version?.subject || '');
  const [dirty, setDirty] = useState(false);
  const [variableQuery, setVariableQuery] = useState('');
  const [toolbarRefreshKey, setToolbarRefreshKey] = useState(0);
  const initialBodyHtml = useMemo(
    () => preserveParagraphIndents(template.version?.body_html || '<p>Здравствуйте, {{contact_name}}!</p>'),
    [template.version?.body_html],
  );
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ paragraph: false }),
      EmailParagraph,
      Link.configure({ openOnClick: false }),
      Placeholder.configure({ placeholder: 'Начните писать письмо…' }),
    ],
    content: initialBodyHtml,
    onUpdate: ({ editor: activeEditor }) => {
      setDirty(true);
      scheduleParagraphIndentNormalization(activeEditor);
    },
  });
  useEffect(() => {
    if (!editor) return;
    const refreshToolbar = () => setToolbarRefreshKey((value) => value + 1);
    const handleIndentKeys = (event: KeyboardEvent) => {
      handleParagraphIndentKeydown(editor, event);
    };
    editor.on('selectionUpdate', refreshToolbar);
    editor.on('transaction', refreshToolbar);
    editor.view.dom.addEventListener('keydown', handleIndentKeys);
    return () => {
      cancelParagraphIndentNormalization(editor);
      editor.off('selectionUpdate', refreshToolbar);
      editor.off('transaction', refreshToolbar);
      editor.view.dom.removeEventListener('keydown', handleIndentKeys);
    };
  }, [editor]);
  const variables = template.version?.variables?.length ? template.version.variables : EMAIL_VARIABLES;
  const saveMutation = useMutation({
    mutationFn: () => {
      if (!editor) throw new Error('Редактор ещё не готов');
      return templatesApi.save(template.id, {
        name,
        subject,
        body_html: buildEmailEditorHtml(editor),
        body_text: editor.getText({ blockSeparator: '\n\n' }) || '',
      });
    },
    onSuccess: () => {
      setDirty(false);
      message.success('Создана новая версия шаблона');
      void queryClient.invalidateQueries({ queryKey: ['template', template.id] });
    },
  });
  const renderedHtml = useMemo(() => {
    let html = preserveParagraphIndents(editor?.getHTML() || '');
    Object.entries(SAMPLE_VALUES).forEach(([key, value]) => { html = html.replaceAll(`{{${key}}}`, value); });
    return `<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;padding:28px;line-height:1.55}</style></head><body>${html}</body></html>`;
  }, [editor, previewOpen]);
  return (
    <div className="template-editor-page">
      <EditorHeader
        template={{ ...template, name }}
        dirty={dirty}
        saving={saveMutation.isPending}
        onBack={() => navigate('/templates')}
        onDownloadHtml={() => downloadEmailHtml(name, editor?.getHTML() || template.version?.body_html || '')}
        onPreview={() => pushParams({ preview: '1' })}
        onSave={() => saveMutation.mutate()}
      />
      <div className="template-editor-fields">
        <label><Text>Название шаблона</Text><Input value={name} onChange={(event) => { setName(event.target.value); setDirty(true); }} /></label>
        <label><Text>Тема письма</Text><Input value={subject} onChange={(event) => { setSubject(event.target.value); setDirty(true); }} /></label>
      </div>
      <div className="template-editor-grid">
        <main className="template-editor-main"><Card className="template-editor-surface" styles={{ body: { padding: 0 } }}><EmailToolbar editor={editor} refreshKey={toolbarRefreshKey} /><EditorContent editor={editor} className="template-email-canvas" /></Card></main>
        <aside className="template-editor-aside">
          <PersonalizationSettingPanel template={template} />
          <VariablePanel variables={variables} query={variableQuery} onQuery={setVariableQuery} onInsert={(nameValue) => editor?.chain().focus().insertContent(`{{${nameValue}}}`).run()} />
        </aside>
      </div>
      <Modal open={previewOpen} width={820} title="Предпросмотр письма" onCancel={() => pushParams({}, ['preview'])} footer={null}><div className="template-email-preview desktop"><iframe title="Предпросмотр письма" sandbox="" srcDoc={renderedHtml} /></div></Modal>
    </div>
  );
}

function KpCanvas({ initialHtml, controllerRef, onChange }: {
  initialHtml: string;
  controllerRef: React.MutableRefObject<CanvasController | null>;
  onChange: (html: string) => void;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const handleLoad = useCallback(() => {
    const frame = iframeRef.current;
    const doc = frame?.contentDocument;
    if (!doc) return;
    doc.designMode = 'on';
    const sync = () => onChange(`<!doctype html>\n${doc.documentElement.outerHTML}`);
    const command = (name: string, value?: string) => {
      doc.execCommand(name, false, value);
      doc.body.focus();
      sync();
    };
    controllerRef.current = {
      command,
      insertHtml: (html: string) => command('insertHTML', html),
      getHtml: () => `<!doctype html>\n${doc.documentElement.outerHTML}`,
    };
    doc.addEventListener('input', sync);
    doc.addEventListener('keyup', sync);
  }, [controllerRef, onChange]);
  return <iframe ref={iframeRef} className="kp-canvas-frame" title="Редактируемый макет коммерческого предложения" sandbox="allow-same-origin" srcDoc={initialHtml} onLoad={handleLoad} />;
}

function KpToolbar({ controller }: { controller: React.MutableRefObject<CanvasController | null> }) {
  const run = (command: string, value?: string) => controller.current?.command(command, value);
  return (
    <div className="kp-ribbon">
      <div className="kp-ribbon-tabs"><button className="active">Главная</button><button>Вставка</button><button>Макет</button><button>Переменные</button><button>Проверка</button></div>
      <div className="kp-ribbon-tools">
        <Tooltip title="Отменить"><Button type="text" icon={<UndoOutlined />} onClick={() => run('undo')} /></Tooltip>
        <Tooltip title="Повторить"><Button type="text" icon={<RedoOutlined />} onClick={() => run('redo')} /></Tooltip>
        <Divider type="vertical" />
        <Select size="small" defaultValue="Arial" style={{ width: 122 }} options={[{ value: 'Arial' }, { value: 'Inter' }, { value: 'Times New Roman' }]} onChange={(value) => run('fontName', value)} />
        <Select size="small" defaultValue="3" style={{ width: 70 }} options={[{ value: '2', label: '10' }, { value: '3', label: '12' }, { value: '4', label: '14' }, { value: '5', label: '18' }]} onChange={(value) => run('fontSize', value)} />
        <Button type="text" icon={<BoldOutlined />} onClick={() => run('bold')} />
        <Button type="text" icon={<ItalicOutlined />} onClick={() => run('italic')} />
        <Button type="text" icon={<StrikethroughOutlined />} onClick={() => run('strikeThrough')} />
        <Divider type="vertical" />
        <Button type="text" icon={<AlignLeftOutlined />} onClick={() => run('justifyLeft')} />
        <Button type="text" icon={<AlignCenterOutlined />} onClick={() => run('justifyCenter')} />
        <Button type="text" icon={<AlignRightOutlined />} onClick={() => run('justifyRight')} />
        <Button type="text" icon={<UnorderedListOutlined />} onClick={() => run('insertUnorderedList')} />
        <Button type="text" icon={<OrderedListOutlined />} onClick={() => run('insertOrderedList')} />
        <Divider type="vertical" />
        <Button type="text" icon={<TableOutlined />} onClick={() => controller.current?.insertHtml('<table style="width:100%;border-collapse:collapse"><tbody><tr><td style="border:1px solid #888;padding:8px">Ячейка</td><td style="border:1px solid #888;padding:8px">Ячейка</td></tr></tbody></table><p></p>')}>Таблица</Button>
        <Upload accept="image/*" showUploadList={false} beforeUpload={(file) => { const reader = new FileReader(); reader.onload = () => run('insertImage', String(reader.result)); reader.readAsDataURL(file); return false; }}><Button type="text" icon={<PictureOutlined />}>Изображение</Button></Upload>
      </div>
    </div>
  );
}

function KpTemplateEditor({ template }: { template: Template }) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const controller = useRef<CanvasController | null>(null);
  const initialHtml = template.version?.body_html || DEFAULT_KP_HTML;
  const [html, setHtml] = useState(initialHtml);
  const [name] = useState(template.name);
  const [dirty, setDirty] = useState(false);
  const [variableQuery, setVariableQuery] = useState('');
  const [previewUrl, setPreviewUrl] = useState('');
  const variables = template.version?.variables?.length ? template.version.variables : DOCUMENT_VARIABLES;
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);
  const saveMutation = useMutation({
    mutationFn: () => templatesApi.save(template.id, { name, body_html: controller.current?.getHtml() || html }),
    onSuccess: () => {
      setDirty(false);
      message.success('PDF-версия КП сохранена');
      void queryClient.invalidateQueries({ queryKey: ['template', template.id] });
      void queryClient.invalidateQueries({ queryKey: ['templates', 'kp'] });
    },
  });
  const previewMutation = useMutation({
    mutationFn: () => templatesApi.previewKpPdf(template.id, controller.current?.getHtml() || html),
    onSuccess: (blob) => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl(URL.createObjectURL(blob));
    },
    onError: (error) => message.error(error instanceof Error ? error.message : 'Не удалось собрать PDF'),
  });
  const insertVariable = (variable: string) => {
    controller.current?.insertHtml(`<span class="variable-token" style="background:#e7f4ec;color:#145c3e;border-radius:3px;padding:1px 3px;font-family:Consolas,monospace">{{${variable}}}</span>`);
    setDirty(true);
  };
  return (
    <div className="template-editor-page kp-editor-page">
      <EditorHeader template={{ ...template, name }} dirty={dirty} saving={saveMutation.isPending} format="PDF" onBack={() => navigate('/templates')} onPreview={() => previewMutation.mutate()} onSave={() => saveMutation.mutate()} />
      {template.version?.filename && !template.version.filename.toLowerCase().endsWith('.pdf') && <Alert type="warning" showIcon message="Это старый шаблон другого формата" description="После сохранения редактор создаст новую версию PDF. Следующие загрузки для КП принимаются только в PDF." />}
      <KpToolbar controller={controller} />
      <div className="kp-workspace">
        <aside className="kp-pages-panel"><div className="kp-panel-title">Страницы</div><button className="kp-page-thumb active"><span>1</span><div className="kp-page-mini">КП</div></button><Button type="dashed" block size="small" disabled>КП всегда 1 страница</Button></aside>
        <main className="kp-stage"><div className="kp-ruler horizontal" /><KpCanvas initialHtml={initialHtml} controllerRef={controller} onChange={(value) => { setHtml(value); setDirty(true); }} /></main>
        <aside className="template-editor-aside kp-inspector">
          <PersonalizationSettingPanel template={template} />
          <VariablePanel variables={variables} query={variableQuery} onQuery={setVariableQuery} onInsert={insertVariable} />
          <Card className="template-side-panel" title="Параметры документа"><div className="kp-settings"><span><Text type="secondary">Формат</Text><strong>A4 · 210 × 297 мм</strong></span><span><Text type="secondary">Результат</Text><strong>PDF</strong></span><span><Text type="secondary">Страниц</Text><strong>1</strong></span></div></Card>
          <Checks pdf />
          <Button block type="primary" icon={<FilePdfOutlined />} loading={previewMutation.isPending} onClick={() => previewMutation.mutate()}>Собрать тестовый PDF</Button>
        </aside>
      </div>
      <div className="editor-statusbar"><span>Страница 1 из 1</span><span><CheckCircleFilled /> HTML-макет будет преобразован в PDF</span><span>100%</span></div>
      <Modal open={Boolean(previewUrl)} width={900} title="Тестовый PDF" onCancel={() => setPreviewUrl('')} footer={null}>{previewUrl && <iframe className="kp-pdf-preview" title="Тестовый PDF" src={previewUrl} />}</Modal>
    </div>
  );
}

function PdfOverlayEditor({ template }: { template: Template }) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [fields, setFields] = useState<PdfEditorField[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [pageIndex, setPageIndex] = useState(0);
  const [dirty, setDirty] = useState(false);
  const zoom = typeof window !== 'undefined' && window.innerWidth >= 1600 ? 1.02 : 0.82;
  const editorQuery = useQuery({
    queryKey: ['pdf-editor', template.id, template.version?.id],
    queryFn: () => templatesApi.pdfEditor(template.id),
  });

  useEffect(() => {
    if (!editorQuery.data) return;
    setFields(editorQuery.data.fields);
    setSelectedId(editorQuery.data.fields[0]?.id || '');
    setDirty(false);
  }, [editorQuery.data]);

  const selected = fields.find((field) => field.id === selectedId);
  const page = editorQuery.data?.pages.find((item) => item.index === pageIndex);
  const pageFields = fields.filter((field) => field.page === pageIndex);
  const updateField = (id: string, patch: Partial<PdfEditorField>) => {
    setFields((current) => current.map((field) => (field.id === id ? { ...field, ...patch } : field)));
    setDirty(true);
  };
  const updateFieldValue = (id: string, value: string) => {
    const original = editorQuery.data?.fields.find((field) => field.id === id);
    const ratio = original && value.length > original.source_text.length
      ? Math.max(0.55, original.source_text.length / value.length)
      : 1;
    updateField(id, {
      value,
      font_size: original ? Math.max(6, Number((original.font_size * ratio).toFixed(2))) : undefined,
    });
  };
  const saveMutation = useMutation({
    mutationFn: () => templatesApi.savePdfEditor(
      template.id,
      fields.map(({ id, value, font_size }) => ({ id, value, font_size })),
    ),
    onSuccess: async () => {
      message.success('Новая PDF-версия сохранена, исходник остался в архиве');
      setDirty(false);
      await queryClient.invalidateQueries({ queryKey: ['template', template.id] });
      await queryClient.invalidateQueries({ queryKey: ['templates'] });
    },
    onError: (error) => message.error(error instanceof Error ? error.message : 'Не удалось сохранить PDF'),
  });

  return (
    <div className="template-editor-page pdf-overlay-page">
      <EditorHeader
        template={template}
        dirty={dirty}
        saving={saveMutation.isPending}
        format="PDF"
        saveDisabled={!dirty || saveMutation.isPending}
        onBack={() => navigate('/templates')}
        onPreview={() => window.open(templatesApi.previewFileUrl(template.id), '_blank', 'noopener,noreferrer')}
        onSave={() => saveMutation.mutate()}
      />
      <Alert
        type="success"
        showIcon
        message="Редактируются только поля поверх исходного PDF"
        description="Нажмите на жёлтое поле в документе и введите новое значение. Логотипы, печать, подпись, шрифты и остальная вёрстка не перестраиваются. При сохранении создаётся новая PDF-версия, исходный файл остаётся в архиве."
      />
      <div className="docx-commandbar">
        <Space>
          <Tag icon={<FilePdfOutlined />} color="green">PDF</Tag>
          <Text strong>{template.version?.filename}</Text>
          <Tag color="gold">{fields.length} редактируемых полей</Tag>
        </Space>
        <Space wrap>
          <Button href={templatesApi.fileUrl(template.id)} icon={<DownloadOutlined />}>Скачать исходник</Button>
          <Button href={templatesApi.deliveryFileUrl(template.id)} icon={<FilePdfOutlined />}>Текущая PDF-версия</Button>
        </Space>
      </div>

      <DocumentDeliverySettingsRow template={template} />

      {editorQuery.isLoading && <Skeleton active paragraph={{ rows: 14 }} />}
      {editorQuery.isError && <Alert type="error" showIcon message="Не удалось открыть редактор PDF" action={<Button onClick={() => void editorQuery.refetch()}>Повторить</Button>} />}
      {editorQuery.data && page && (
        <div className="pdf-overlay-workspace">
          <aside className="pdf-overlay-pages">
            <div className="kp-panel-title">Страницы</div>
            {editorQuery.data.pages.map((item) => (
              <button key={item.index} type="button" className={`pdf-page-thumb ${item.index === pageIndex ? 'active' : ''}`} onClick={() => setPageIndex(item.index)}>
                <img src={templatesApi.pdfEditorPageUrl(template.id, item.index)} alt={`Страница ${item.index + 1}`} />
                <span>{item.index + 1}</span>
              </button>
            ))}
          </aside>
          <main className="pdf-overlay-stage">
            <div className="pdf-overlay-hint">Нажмите на жёлтое поле, чтобы изменить его</div>
            <div className="pdf-overlay-sheet" style={{ width: page.width * zoom, height: page.height * zoom }}>
              <img src={templatesApi.pdfEditorPageUrl(template.id, pageIndex)} alt={`Страница PDF ${pageIndex + 1}`} />
              {pageFields.map((field) => (
                <input
                  key={field.id}
                  className={`pdf-overlay-input ${selectedId === field.id ? 'selected' : ''}`}
                  aria-label={field.label}
                  title={`${field.label}: ${field.variable}`}
                  value={field.value}
                  onFocus={() => setSelectedId(field.id)}
                  onChange={(event) => updateFieldValue(field.id, event.target.value)}
                  style={{
                    left: field.x * zoom,
                    top: field.y * zoom,
                    width: Math.max(field.width * zoom, Math.min((page.width - field.x - 12) * zoom, (field.value.length * field.font_size * 0.62 + 8) * zoom), 38),
                    height: Math.max(field.height * zoom, 18),
                    paddingLeft: Math.max((field.text_x - field.x) * zoom, 1),
                    fontSize: field.font_size * zoom,
                    fontWeight: field.bold ? 700 : 400,
                    color: field.text_color,
                    backgroundColor: field.background,
                  }}
                />
              ))}
            </div>
          </main>
          <aside className="template-editor-aside pdf-overlay-inspector">
            <Card className="template-side-panel" title="Поля документа">
              <div className="pdf-field-list">
                {fields.map((field) => (
                  <button key={field.id} type="button" className={selectedId === field.id ? 'active' : ''} onClick={() => { setSelectedId(field.id); setPageIndex(field.page); }}>
                    <span><strong>{field.label}</strong><code>{`{{${field.variable}}}`}</code></span>
                    <small>{field.value || 'Пустое значение'}</small>
                  </button>
                ))}
              </div>
            </Card>
            {selected && (
              <Card className="template-side-panel" title="Выбранное поле">
                <div className="pdf-field-settings">
                  <label><span>Значение</span><Input value={selected.value} onChange={(event) => updateFieldValue(selected.id, event.target.value)} /></label>
                  <label><span>Переменная</span><Input value={`{{${selected.variable}}}`} readOnly /></label>
                  <label><span>Размер текста</span><Input type="number" min={6} max={36} step={0.5} value={selected.font_size} onChange={(event) => updateField(selected.id, { font_size: Number(event.target.value) || selected.font_size })} /></label>
                  <Button onClick={() => updateField(selected.id, { value: selected.source_text, font_size: editorQuery.data.fields.find((item) => item.id === selected.id)?.font_size || selected.font_size })}>Вернуть исходное значение</Button>
                </div>
              </Card>
            )}
            {fields.length === 0 && <Alert type="warning" showIcon message="Жёлтые поля не найдены" description="В этом PDF нет распознаваемых выделенных полей. Загрузите PDF с жёлтыми маркерами или DOCX-исходник." />}
            <Card className="template-side-panel" title="Что сохранится">
              <div className="template-check-list">
                <span><CheckCircleFilled />Оригинальный PDF без изменений</span>
                <span><CheckCircleFilled />Новая PDF-копия с введёнными значениями</span>
                <span><CheckCircleFilled />Обе версии доступны в архиве</span>
              </div>
            </Card>
            <Button type="primary" block icon={<SaveOutlined />} disabled={!dirty} loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>Сохранить новую PDF-версию</Button>
          </aside>
        </div>
      )}
      <div className="editor-statusbar"><span>Страница {pageIndex + 1} из {editorQuery.data?.page_count || 1}</span><span><CheckCircleFilled />Исходная вёрстка защищена от изменений</span><span>{dirty ? 'Есть изменения' : 'Сохранено'}</span></div>
    </div>
  );
}

function OnlyOfficeEditor({ data, templateId, onDirty, onError }: {
  data: OfficeEditorConfig;
  templateId: string;
  onDirty: (dirty: boolean) => void;
  onError: (message: string) => void;
}) {
  const containerId = `onlyoffice-editor-${templateId}`;
  useEffect(() => {
    let disposed = false;
    let instance: OnlyOfficeInstance | undefined;
    const mount = () => {
      if (disposed || !window.DocsAPI) return;
      const config = {
        ...data.config,
        events: {
          // Fast mode synchronizes the editor's working copy in the background.
          // A false event means the sync completed, not that our archived
          // version was saved, so only the explicit app save clears dirty.
          onDocumentStateChange: (event: { data?: boolean }) => {
            if (event.data) onDirty(true);
          },
          onError: (event: { data?: { errorDescription?: string } }) => onError(event.data?.errorDescription || 'Ошибка DOCX-редактора'),
        },
      } as Record<string, unknown>;
      instance = new window.DocsAPI.DocEditor(containerId, config);
    };
    const editorUrl = new URL(data.editor_url, window.location.origin);
    const localOnlyOfficeHosts = new Set(['localhost', '127.0.0.1', '0.0.0.0']);
    if (localOnlyOfficeHosts.has(editorUrl.hostname) && !localOnlyOfficeHosts.has(window.location.hostname)) {
      editorUrl.hostname = window.location.hostname;
    }
    const src = `${editorUrl.toString().replace(/\/$/, '')}/web-apps/apps/api/documents/api.js`;
    const existing = document.querySelector<HTMLScriptElement>(`script[data-onlyoffice-src="${src}"]`);
    if (window.DocsAPI) mount();
    else if (existing) existing.addEventListener('load', mount, { once: true });
    else {
      const script = document.createElement('script');
      script.src = src;
      script.dataset.onlyofficeSrc = src;
      script.onload = mount;
      script.onerror = () => onError('ONLYOFFICE не запущен. Проверьте контейнер documentserver.');
      document.head.appendChild(script);
    }
    return () => {
      disposed = true;
      instance?.destroyEditor?.();
    };
  }, [containerId, data, onDirty, onError]);
  return <div id={containerId} className="onlyoffice-editor" />;
}

function DocxTemplateEditor({ template }: { template: Template }) {
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [dirty, setDirty] = useState(false);
  const [variableQuery, setVariableQuery] = useState('');
  const handleOfficeError = useCallback((value: string) => message.error(value), [message]);
  const [uploading, setUploading] = useState(false);
  const filename = template.version?.filename || '';
  const hasDocx = filename.toLowerCase().endsWith('.docx');
  const buildsDeliveryPdf = isDocumentTemplateType(template.template_type);
  const variables = template.version?.variables?.length ? template.version.variables : DOCUMENT_VARIABLES;
  const filteredVariables = variables.filter((variable) => `${variable.name} ${variable.label}`.toLowerCase().includes(variableQuery.toLowerCase()));
  const officeQuery = useQuery({ queryKey: ['office-config', template.id, template.version?.id], queryFn: () => templatesApi.officeConfig(template.id), enabled: hasDocx, retry: false });
  const saveMutation = useMutation({
    mutationFn: async () => {
      const currentVersionId = template.version?.id || '';
      await templatesApi.forceSaveOffice(
        template.id,
        currentVersionId,
        officeQuery.data?.document_key || '',
      );
      for (let attempt = 0; attempt < 30; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        const latest = await templatesApi.get(template.id);
        if (latest.version?.id && latest.version.id !== currentVersionId) return latest;
      }
      throw new Error('ONLYOFFICE принял документ, но новая версия не появилась. Попробуйте сохранить ещё раз.');
    },
    onSuccess: (latest) => {
      setDirty(false);
      queryClient.setQueryData(['template', template.id], latest);
      message.success(`Версия ${latest.version?.version_number || ''} сохранена`);
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось сохранить DOCX');
    },
  });


  const upload = (
    <Upload accept=".docx" maxCount={1} showUploadList={false} customRequest={async ({ file, onSuccess, onError }) => {
      setUploading(true);
      try {
        await templatesApi.uploadFile(file as File, 'document', { template_id: template.id });
        message.success(buildsDeliveryPdf ? 'DOCX и PDF-версия сохранены' : 'Новая версия DOCX загружена');
        await queryClient.invalidateQueries({ queryKey: ['template', template.id] });
        await queryClient.invalidateQueries({ queryKey: ['office-config', template.id] });
        onSuccess?.({});
      } catch (error) {
        showDocumentUploadError(modal, error);
        onError?.(error as Error);
      } finally { setUploading(false); }
    }}><Button icon={<UploadOutlined />} loading={uploading}>Новая версия</Button></Upload>
  );
  const copyVariable = async (name: string) => {
    await navigator.clipboard.writeText(`{{${name}}}`);
    message.success(`{{${name}}} скопирована — вставьте её сочетанием Ctrl+V`);
  };
  const variablePicker = (
    <div className="docx-variable-picker">
      <Input allowClear prefix={<SearchOutlined />} placeholder="Найти поле" value={variableQuery} onChange={(event) => setVariableQuery(event.target.value)} />
      <div className="docx-variable-picker-list">
        {filteredVariables.map((variable) => (
          <button key={variable.name} type="button" onClick={() => void copyVariable(variable.name)}>
            <span><strong>{variable.label}</strong><small>{variable.source}</small></span>
            <code>{`{{${variable.name}}}`}</code>
          </button>
        ))}
      </div>
      <Text type="secondary">Нажмите на поле, затем вставьте его в документ сочетанием Ctrl+V.</Text>
    </div>
  );

  return (
    <div className="template-editor-page docx-editor-page focused-document-editor">
      <EditorHeader
        template={template}
        dirty={dirty}
        saving={saveMutation.isPending}
        format={buildsDeliveryPdf ? 'PDF' : 'DOCX'}
        saveLabel="Сохранить версию"
        saveDisabled={!dirty || !officeQuery.data}
        onBack={() => navigate('/templates')}
        onPreview={() => window.open(templatesApi.previewFileUrl(template.id), '_blank', 'noopener,noreferrer')}
        onSave={() => saveMutation.mutate()}
      />

      <DocumentDeliverySettingsRow template={template} />

      <div className="focused-editor-bar">
        <div className="focused-editor-file">
          <Tag icon={<FileWordOutlined />} color="blue">DOCX</Tag>
          <div>
            <Text strong ellipsis={{ tooltip: filename }}>{filename || 'Файл не загружен'}</Text>
            <small>Новая версия создаётся только по кнопке «Сохранить версию»</small>
          </div>
        </div>
        <Space wrap>
          <Popover content={variablePicker} title="Поля документа" trigger="click" placement="bottomRight">
            <Button icon={<CopyOutlined />}>Вставить поле</Button>
          </Popover>
          <Tooltip title="Скачать исходный DOCX"><Button href={templatesApi.fileUrl(template.id)} icon={<DownloadOutlined />} aria-label="Скачать исходный DOCX" /></Tooltip>
          {buildsDeliveryPdf && <Tooltip title="Скачать PDF для отправки"><Button href={templatesApi.deliveryFileUrl(template.id)} icon={<FilePdfOutlined />} aria-label="Скачать PDF для отправки" /></Tooltip>}
          {upload}
        </Space>
      </div>

      {!hasDocx ? (
        <Alert type="warning" showIcon message="Для редактирования нужен DOCX" description={<Space direction="vertical"><Text>Загрузите исходник — оформление сохранится, система отдельно создаст PDF для отправки.</Text>{upload}</Space>} />
      ) : (
        <div className="focused-docx-workspace">
          <main className="docx-editor-shell">
            {officeQuery.isLoading && <Skeleton active paragraph={{ rows: 12 }} />}
            {officeQuery.isError && <Alert type="error" showIcon message="Редактор документов недоступен" description="Проверьте локальный сервис документов и попробуйте подключиться ещё раз." action={<Button icon={<ReloadOutlined />} onClick={() => void officeQuery.refetch()}>Повторить</Button>} />}
            {officeQuery.data && <OnlyOfficeEditor data={officeQuery.data} templateId={template.id} onDirty={setDirty} onError={handleOfficeError} />}
          </main>
        </div>
      )}
      <div className="editor-statusbar">
        <span><PictureOutlined />Текст и изображения</span>
        <span><CheckCircleFilled />{buildsDeliveryPdf ? 'Исходный DOCX и PDF-копия сохраняются отдельно' : 'Исходный DOCX сохраняется новой версией'}</span>
        <span>{saveMutation.isPending ? 'Сохраняем…' : dirty ? 'Не сохранено' : 'Сохранено'}</span>
      </div>
    </div>
  );
}

function resolveTemplateEditor(template: Template) {
  if (template.template_type === 'email') {
    if (getEmailFormat(template) === 'visual') {
      return <VisualEmailEditor key={template.version?.id} template={template} />;
    }
    return <EmailTemplateEditor key={template.version?.id} template={template} />;
  }

  const filename = template.version?.filename?.toLowerCase() || '';
  if (filename.endsWith('.docx')) {
    return <DocxTemplateEditor key={template.version?.id} template={template} />;
  }
  if (filename.endsWith('.pdf')) {
    return <PdfOverlayEditor key={template.version?.id} template={template} />;
  }
  if (template.template_type === 'kp' && template.version?.body_html) {
    return <KpTemplateEditor key={template.version?.id} template={template} />;
  }
  if (isDocumentTemplateType(template.template_type)) {
    return <DocxTemplateEditor key={template.version?.id} template={template} />;
  }
  return <DocxTemplateEditor key={template.version?.id} template={template} />;
}

export function TemplateEditorPage() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const { data: template, isLoading, isError } = useQuery({ queryKey: ['template', id], queryFn: () => templatesApi.get(id), enabled: Boolean(id) });
  if (isLoading) return <Skeleton active paragraph={{ rows: 12 }} />;
  if (isError || !template) return <Empty description="Шаблон не найден"><Button onClick={() => navigate('/templates')}>Вернуться к шаблонам</Button></Empty>;
  return resolveTemplateEditor(template);
}
