import {
  ArrowLeftOutlined,
  DesktopOutlined,
  DownloadOutlined,
  EyeOutlined,
  MobileOutlined,
  SaveOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App, Alert, Breadcrumb, Button, Card, Input, Modal, Space, Tag, Typography } from 'antd';
import type { Editor } from 'grapesjs';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { EditorSideAccordion } from '@/features/assistants';
import { PersonalizationSetting } from '@/features/templates/PersonalizationSetting';
import { templatesApi, invalidateTemplateCaches } from '@/api/templates';
import type { EmailEditorState, Template } from '@/api/types';
import { EMAIL_VARIABLES, SAMPLE_EMAIL_VALUES } from './emailConstants';
import {
  buildEmailPreviewDocument,
  downloadEmailHtml,
  htmlToPlainText,
  substituteChainButtonsPreview,
  substitutePreviewValues,
} from './emailTemplateUtils';
import {
  createVisualEmailEditor,
  exportVisualEmailHtml,
  insertMergeVariable,
  isFixedLayoutHtml,
} from './grapesjsConfig';
import {
  buildImportMetadataDescription,
  formatImportScore,
  importSourceLabel,
  importStopReasonLabel,
} from './importMetadata';
import './VisualEmailEditor.css';

const { Text, Title } = Typography;

type EditorVariable = { name: string; label: string; source: string };

function VariablePanel({
  variables,
  query,
  onQuery,
  onInsert,
}: {
  variables: EditorVariable[];
  query: string;
  onQuery: (value: string) => void;
  onInsert: (name: string) => void;
}) {
  const filtered = variables.filter((variable) =>
    `${variable.name} ${variable.label}`.toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <Card className="template-side-panel" title="Переменные">
      <Input
        allowClear
        prefix={<SearchOutlined />}
        placeholder="Найти переменную"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
      />
      <div className="template-variable-list">
        {filtered.map((variable) => (
          <button
            key={variable.name}
            type="button"
            className="template-variable-row"
            onClick={() => onInsert(variable.name)}
            title={`Вставить ${variable.name}`}
          >
            <span>
              <code>{`{{${variable.name}}}`}</code>
              <small>
                {variable.label} · {variable.source}
              </small>
            </span>
            <span className="template-variable-add">+</span>
          </button>
        ))}
      </div>
    </Card>
  );
}

type Props = {
  template: Template;
};

export function VisualEmailEditor({ template }: Props) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<Editor | null>(null);

  const editorState = (template.version?.editor_state || {}) as EmailEditorState;
  const importedLayout = Boolean(editorState.imported_layout || template.tags?.includes('import'));
  const fixedLayoutImport = isFixedLayoutHtml(
    template.version?.body_html || '',
    editorState.import_source,
  );
  const importRefinement = editorState.import_refinement;
  const importBannerKey = `import-draft-banner:${template.id}`;
  const [name, setName] = useState(template.name);
  const [subject, setSubject] = useState(template.version?.subject || '');
  const [dirty, setDirty] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewDevice, setPreviewDevice] = useState<'desktop' | 'mobile'>('desktop');
  const [variableQuery, setVariableQuery] = useState('');
  const [editorDevice, setEditorDevice] = useState<'Desktop' | 'Mobile'>('Desktop');
  const [initError, setInitError] = useState<string | null>(null);
  const [initAttempt, setInitAttempt] = useState(0);
  const [importBannerOpen, setImportBannerOpen] = useState(() => {
    if (!importedLayout) return false;
    try {
      return sessionStorage.getItem(importBannerKey) !== '1';
    } catch {
      return true;
    }
  });

  const variables = template.version?.variables?.length ? template.version.variables : EMAIL_VARIABLES;

  useEffect(() => {
    setName(template.name);
    setSubject(template.version?.subject || '');
  }, [template.id, template.name, template.version?.id, template.version?.subject]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    setInitError(null);
    container.innerHTML = '';
    let editor: Editor | null = null;

    try {
      editor = createVisualEmailEditor({
        container,
        bodyHtml: template.version?.body_html || '',
        projectData: editorState.grapesjs_project,
        importedLayout,
        importSource: editorState.import_source,
        onChange: () => setDirty(true),
        uploadAsset: (file) => templatesApi.uploadAsset(template.id, file),
        onUploadError: (error) => {
          message.error(error instanceof Error ? error.message : 'Не удалось загрузить изображение');
        },
      });
      editorRef.current = editor;
    } catch (error) {
      editorRef.current = null;
      setInitError(error instanceof Error ? error.message : 'Не удалось инициализировать HTML-конструктор');
      return;
    }

    return () => {
      editor?.destroy();
      editorRef.current = null;
    };
  }, [template.id, template.version?.id, template.version?.body_html, editorState.grapesjs_project, initAttempt, message]);

  const saveMutation = useMutation({
    mutationFn: () => {
      const editor = editorRef.current;
      if (!editor) throw new Error('Редактор ещё не готов');
      const body_html = exportVisualEmailHtml(editor, {
        canonicalHtml: template.version?.body_html || '',
        importSource: editorState.import_source,
      });
      const nextEditorState: EmailEditorState = {
        email_format: 'visual',
        grapesjs_project: editor.getProjectData(),
        brand: editorState.brand,
        imported_layout: editorState.imported_layout,
        import_source: editorState.import_source,
        import_as_draft: editorState.import_as_draft,
        import_refinement: editorState.import_refinement,
      };
      return templatesApi.save(template.id, {
        name,
        subject,
        body_html,
        body_text: htmlToPlainText(body_html),
        editor_state: nextEditorState,
      });
    },
    onSuccess: () => {
      setDirty(false);
      message.success('Создана новая версия шаблона');
      invalidateTemplateCaches(queryClient, template.id);
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось сохранить шаблон');
    },
  });

  const regenerateMutation = useMutation({
    mutationFn: () => templatesApi.regenerateImport(template.id),
    onSuccess: () => {
      setDirty(false);
      message.success('Шаблон перегенерирован с AI');
      invalidateTemplateCaches(queryClient, template.id);
      setInitAttempt((value) => value + 1);
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось перегенерировать шаблон');
    },
  });

  const importMetadataDescription = useMemo(
    () => buildImportMetadataDescription(editorState.import_source, importRefinement),
    [editorState.import_source, importRefinement],
  );

  const previewHtml = useMemo(() => {
    const editor = editorRef.current;
    const html = editor ? exportVisualEmailHtml(editor) : template.version?.body_html || '';
    const withVariables = substitutePreviewValues(html, SAMPLE_EMAIL_VALUES);
    const withChainButtons = substituteChainButtonsPreview(withVariables);
    return buildEmailPreviewDocument(withChainButtons);
  }, [previewOpen, previewDevice, template.version?.body_html, dirty]);

  const switchEditorDevice = (device: 'Desktop' | 'Mobile') => {
    setEditorDevice(device);
    editorRef.current?.setDevice(device);
  };

  const buildAssistantSnapshot = useCallback(() => {
    const editor = editorRef.current;
    const bodyHtml = editor ? exportVisualEmailHtml(editor) : template.version?.body_html || '';
    return {
      name,
      subject,
      body_html: bodyHtml,
      variables,
      is_template: template.is_template,
      email_format: 'visual',
      grapesjs_project: editor?.getProjectData?.() || editorState.grapesjs_project || null,
    };
  }, [editorState.grapesjs_project, name, subject, template.is_template, template.version?.body_html, variables]);

  const assistantHandlers = useMemo(
    () => ({
      setSubject: (value: string) => {
        setSubject(value);
        setDirty(true);
      },
      setHtml: (html: string) => {
        const editor = editorRef.current;
        if (!editor) return;
        editor.setComponents(html);
        setDirty(true);
      },
      insertComponents: (html: string) => {
        const editor = editorRef.current;
        if (!editor) return;
        editor.addComponents(html);
        setDirty(true);
      },
      loadGrapesProject: (project: Record<string, unknown>) => {
        const editor = editorRef.current;
        if (!editor) return;
        editor.loadProjectData(project);
        setDirty(true);
      },
      setPersonalization: () => {
        void queryClient.invalidateQueries({ queryKey: ['template', template.id] });
      },
      markDirty: () => setDirty(true),
    }),
    [queryClient, template.id],
  );

  return (
    <div className={`template-editor-page visual-email-editor${fixedLayoutImport ? ' visual-email-editor--fixed-layout' : ''}`}>
      <Breadcrumb
        items={[
          { title: 'Шаблоны и документы' },
          { title: 'Письма' },
          { title: 'HTML-конструктор' },
        ]}
      />
      <div className="template-editor-heading">
        <Space align="start">
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/templates')} aria-label="Назад" />
          <div>
            <Space wrap>
              <Title level={3} style={{ margin: 0 }}>
                {name}
              </Title>
              <Tag color="blue">HTML-конструктор</Tag>
              <Tag>Версия {template.version?.version_number || 1}</Tag>
            </Space>
            <Text type="secondary">
              {dirty ? 'Есть несохранённые изменения' : 'Все изменения сохранены'}
            </Text>
          </div>
        </Space>
        <Space wrap>
          {importedLayout && (
            <Button
              icon={<ThunderboltOutlined />}
              loading={regenerateMutation.isPending}
              onClick={() => regenerateMutation.mutate()}
            >
              Перегенерировать с AI
            </Button>
          )}
          <Button
            icon={<DownloadOutlined />}
            onClick={() => {
              const editor = editorRef.current;
              const html = editor
                ? exportVisualEmailHtml(editor, {
                    canonicalHtml: template.version?.body_html || '',
                    importSource: editorState.import_source,
                  })
                : template.version?.body_html || '';
              downloadEmailHtml(name, html);
            }}
          >
            Скачать HTML
          </Button>
          <Button icon={<EyeOutlined />} onClick={() => setPreviewOpen(true)}>
            Предпросмотр
          </Button>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            Сохранить версию
          </Button>
        </Space>
      </div>

      <div className="template-editor-fields">
        <label>
          <Text>Название шаблона</Text>
          <Input
            value={name}
            onChange={(event) => {
              setName(event.target.value);
              setDirty(true);
            }}
          />
        </label>
        <label>
          <Text>Тема письма</Text>
          <Input
            value={subject}
            onChange={(event) => {
              setSubject(event.target.value);
              setDirty(true);
            }}
          />
        </label>
      </div>

      {importedLayout && importBannerOpen && (
        <Alert
          type="info"
          showIcon
          closable
          style={{ marginBottom: 12 }}
          message="Импорт — стартовая вёрстка"
          description={
            <div className="visual-email-import-meta">
              <p>
                Письмо собрано из документа как редактируемый HTML. Проверьте блоки, кнопки и
                плейсхолдеры перед отправкой.
              </p>
              <p>{importMetadataDescription}</p>
              <Space wrap size={[8, 8]}>
                <Tag>{importSourceLabel(editorState.import_source)}</Tag>
                {importRefinement?.best_score !== undefined && (
                  <Tag color="blue">Score: {formatImportScore(importRefinement.best_score)}</Tag>
                )}
                {importRefinement?.stop_reason && (
                  <Tag>{importStopReasonLabel(importRefinement.stop_reason)}</Tag>
                )}
                {importRefinement?.available === false && (
                  <Tag color="default">AI vision не запускался</Tag>
                )}
              </Space>
            </div>
          }
          onClose={() => {
            setImportBannerOpen(false);
            try {
              sessionStorage.setItem(importBannerKey, '1');
            } catch {
              /* ignore quota / private mode */
            }
          }}
        />
      )}

      {importedLayout && !importBannerOpen && (
        <Card size="small" className="visual-email-import-summary" style={{ marginBottom: 12 }}>
          <Space wrap size={[8, 8]} align="center">
            <Text type="secondary">Импорт:</Text>
            <Tag>{importSourceLabel(editorState.import_source)}</Tag>
            {importRefinement?.best_score !== undefined && (
              <Tag color="blue">Score: {formatImportScore(importRefinement.best_score)}</Tag>
            )}
            {importRefinement?.stop_reason && (
              <Tag>{importStopReasonLabel(importRefinement.stop_reason)}</Tag>
            )}
          </Space>
        </Card>
      )}

      <div className="visual-email-editor-grid">
        <main className="visual-email-editor-main">
          {initError && (
            <Alert
              type="error"
              showIcon
              message="HTML-конструктор не загрузился"
              description={initError}
              action={
                <Button onClick={() => setInitAttempt((value) => value + 1)}>
                  Повторить
                </Button>
              }
              style={{ margin: 12 }}
            />
          )}
          <div className="visual-email-device-bar" hidden={Boolean(initError)}>
            <Button
              size="small"
              type={editorDevice === 'Desktop' ? 'primary' : 'default'}
              icon={<DesktopOutlined />}
              onClick={() => switchEditorDevice('Desktop')}
            >
              Desktop
            </Button>
            <Button
              size="small"
              type={editorDevice === 'Mobile' ? 'primary' : 'default'}
              icon={<MobileOutlined />}
              onClick={() => switchEditorDevice('Mobile')}
            >
              Mobile
            </Button>
          </div>
          <div ref={containerRef} hidden={Boolean(initError)} />
        </main>
        <EditorSideAccordion
          editorKind="visual_email"
          resourceId={template.id}
          buildSnapshot={buildAssistantSnapshot}
          handlers={assistantHandlers}
          settings={
            <>
              <PersonalizationSetting template={template} />
              <Alert
                type="info"
                showIcon
                message="Кнопки цепочки"
                description='Перетащите блок «Кнопки цепочки» на холст — при отправке цепочки сюда подставятся ветки из конструктора.'
              />
              <VariablePanel
                variables={variables}
                query={variableQuery}
                onQuery={setVariableQuery}
                onInsert={(variableName) => {
                  const editor = editorRef.current;
                  if (!editor) return;
                  insertMergeVariable(editor, variableName);
                  setDirty(true);
                }}
              />
            </>
          }
        />
      </div>

      <Modal
        open={previewOpen}
        width={860}
        title="Предпросмотр письма"
        onCancel={() => setPreviewOpen(false)}
        footer={
          <Space>
            <Button
              icon={<DesktopOutlined />}
              type={previewDevice === 'desktop' ? 'primary' : 'default'}
              onClick={() => setPreviewDevice('desktop')}
            >
              Desktop
            </Button>
            <Button
              icon={<MobileOutlined />}
              type={previewDevice === 'mobile' ? 'primary' : 'default'}
              onClick={() => setPreviewDevice('mobile')}
            >
              Mobile
            </Button>
          </Space>
        }
      >
        <div className={`visual-email-preview ${previewDevice}`}>
          <iframe title="Предпросмотр письма" sandbox="" srcDoc={previewHtml} />
        </div>
      </Modal>
    </div>
  );
}
