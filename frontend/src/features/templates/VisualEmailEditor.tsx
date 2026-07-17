import {
  ArrowLeftOutlined,
  DesktopOutlined,
  EyeOutlined,
  MobileOutlined,
  SaveOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { App, Alert, Breadcrumb, Button, Card, Input, Modal, Space, Tag, Typography } from 'antd';
import type { Editor } from 'grapesjs';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PersonalizationSetting } from '@/features/templates/PersonalizationSetting';
import { templatesApi } from '@/api/templates';
import type { EmailEditorState, Template } from '@/api/types';
import { EMAIL_VARIABLES, SAMPLE_EMAIL_VALUES } from './emailConstants';
import {
  buildEmailPreviewDocument,
  htmlToPlainText,
  substituteChainButtonsPreview,
  substitutePreviewValues,
} from './emailTemplateUtils';
import {
  createVisualEmailEditor,
  exportVisualEmailHtml,
  insertMergeVariable,
} from './grapesjsConfig';
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
  const [name, setName] = useState(template.name);
  const [subject, setSubject] = useState(template.version?.subject || '');
  const [dirty, setDirty] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewDevice, setPreviewDevice] = useState<'desktop' | 'mobile'>('desktop');
  const [variableQuery, setVariableQuery] = useState('');
  const [editorDevice, setEditorDevice] = useState<'Desktop' | 'Mobile'>('Desktop');
  const [initError, setInitError] = useState<string | null>(null);
  const [initAttempt, setInitAttempt] = useState(0);

  const variables = template.version?.variables?.length ? template.version.variables : EMAIL_VARIABLES;

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
      const body_html = exportVisualEmailHtml(editor);
      const nextEditorState: EmailEditorState = {
        email_format: 'visual',
        grapesjs_project: editor.getProjectData(),
        brand: editorState.brand,
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
      void queryClient.invalidateQueries({ queryKey: ['template', template.id] });
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось сохранить шаблон');
    },
  });

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

  return (
    <div className="template-editor-page visual-email-editor">
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
        <aside className="template-editor-aside">
          <PersonalizationSetting template={template} />
          <Alert
            type="info"
            showIcon
            message="Кнопки цепочки"
            description='Перетащите блок «Кнопки цепочки» на холст — при отправке цепочки сюда подставятся ветки из конструктора.'
            style={{ marginBottom: 16 }}
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
        </aside>
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
