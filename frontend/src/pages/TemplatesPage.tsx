import { DownloadOutlined, EyeOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { ProCard } from '@ant-design/pro-components';
import { App, Button, Drawer, Input, Space, Tabs, Tag, Typography, Upload } from 'antd';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';

type FileTemplateType = 'kp' | 'contract';

function TemplateFileUpload({
  type,
  templateId,
  label = 'Загрузить свой шаблон',
  primary = false,
  onUploaded,
}: {
  type: FileTemplateType;
  templateId?: string;
  label?: string;
  primary?: boolean;
  onUploaded: (template: Template) => void;
}) {
  const { message } = App.useApp();
  const [uploading, setUploading] = useState(false);
  const accept = type === 'kp' ? '.docx,.pdf,.html,.htm' : '.docx';

  return (
    <Upload
      accept={accept}
      maxCount={1}
      showUploadList={false}
      customRequest={async ({ file, onSuccess, onError }) => {
        setUploading(true);
        try {
          const uploaded = await templatesApi.uploadFile(file as File, type, { template_id: templateId });
          message.success(templateId ? 'Новая версия загружена' : 'Шаблон загружен');
          onUploaded(uploaded);
          onSuccess?.(uploaded);
        } catch (error) {
          message.error(error instanceof Error ? error.message : 'Не удалось загрузить шаблон');
          onError?.(error as Error);
        } finally {
          setUploading(false);
        }
      }}
    >
      <Button type={primary ? 'primary' : 'link'} icon={<UploadOutlined />} loading={uploading}>
        {label}
      </Button>
    </Upload>
  );
}

function TemplateEditorDrawer({
  open,
  template,
  onClose,
}: {
  open: boolean;
  template: Template | null;
  onClose: () => void;
}) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [name, setName] = useState(template?.name || '');
  const [subject, setSubject] = useState(template?.version?.subject || '');
  const editor = useEditor({
    extensions: [StarterKit, Placeholder.configure({ placeholder: 'Текст шаблона…' })],
    content: template?.version?.body_html || '<p></p>',
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!template) return;
      return templatesApi.save(template.id, {
        name,
        subject,
        body_html: editor?.getHTML() || '',
      });
    },
    onSuccess: () => {
      message.success('Шаблон сохранён');
      void queryClient.invalidateQueries({ queryKey: ['templates'] });
      onClose();
    },
  });

  return (
    <Drawer
      width={920}
      open={open}
      onClose={onClose}
      title="Редактор шаблона письма"
      extra={
        <Space>
          <Button onClick={() => editor?.chain().focus().insertContent('{{company}}').run()}>
            + company
          </Button>
          <Button onClick={() => editor?.chain().focus().insertContent('{{contact_name}}').run()}>
            + contact_name
          </Button>
          <Button type="primary" loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            Сохранить
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Название" />
        <Input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="Тема" />
        <div style={{ border: '1px solid #DEE2E6', borderRadius: 8, padding: 12, minHeight: 280 }}>
          <EditorContent editor={editor} />
        </div>
        <Typography.Text type="secondary">
          Переменные: {'{{company}}'}, {'{{contact_name}}'}, {'{{email}}'}, {'{{region}}'}
        </Typography.Text>
      </Space>
    </Drawer>
  );
}

function TemplateGrid({ type }: { type: 'email' | FileTemplateType }) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Template | null>(null);
  const isFileTemplate = type !== 'email';
  const { data, isLoading } = useQuery({
    queryKey: ['templates', type],
    queryFn: () => templatesApi.list({ template_type: type }),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['templates', type] });
  const createMutation = useMutation({
    mutationFn: () =>
      templatesApi.create({
        name: 'Новый шаблон письма',
        template_type: 'email',
        subject: 'Тема письма',
        body_html: '<p>Здравствуйте, {{contact_name}}!</p><p>Компания: {{company}}</p>',
      }),
    onSuccess: (template) => {
      void refresh();
      setEditing(template);
    },
  });

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        {isFileTemplate ? (
          <TemplateFileUpload type={type} primary onUploaded={() => void refresh()} />
        ) : (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            loading={createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            Создать письмо
          </Button>
        )}
        {isFileTemplate && (
          <Typography.Text type="secondary">
            {type === 'kp' ? 'Форматы: DOCX, PDF, HTML' : 'Формат: DOCX'}
          </Typography.Text>
        )}
      </Space>

      <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))' }}>
        {(data || []).map((template) => {
          const variables = template.version?.variables || [];
          const hasFile = Boolean(template.version?.filename);
          const actions = isFileTemplate
            ? [
                ...(hasFile
                  ? [
                      <a
                        key="preview"
                        onClick={() =>
                          window.open(templatesApi.previewFileUrl(template.id), '_blank', 'noopener,noreferrer')
                        }
                      >
                        <EyeOutlined /> Предпросмотр
                      </a>,
                      <a key="download" href={templatesApi.fileUrl(template.id)}>
                        <DownloadOutlined /> Скачать
                      </a>,
                    ]
                  : []),
                <TemplateFileUpload
                  key="version"
                  type={type}
                  templateId={template.id}
                  label={hasFile ? 'Новая версия' : 'Загрузить файл'}
                  onUploaded={() => void refresh()}
                />,
              ]
            : [
                <a key="edit" onClick={() => setEditing(template)}>Редактировать</a>,
                <a
                  key="preview"
                  onClick={async () => {
                    const preview = await templatesApi.preview(template.id);
                    message.info(preview.subject);
                  }}
                >
                  Предпросмотр
                </a>,
              ];

          actions.push(
            <a
              key="duplicate"
              onClick={async () => {
                await templatesApi.duplicate(template.id);
                void refresh();
              }}
            >
              Копия
            </a>,
            <a
              key="archive"
              onClick={async () => {
                await templatesApi.archive(template.id);
                void refresh();
              }}
            >
              Архив
            </a>,
          );

          return (
            <ProCard key={template.id} loading={isLoading} title={template.name} bordered actions={actions}>
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                <Tag color={isFileTemplate && !hasFile ? 'orange' : template.status === 'ready' ? 'green' : 'gold'}>
                  {isFileTemplate && !hasFile
                    ? 'Требуется файл'
                    : template.status === 'ready' ? 'Готов' : template.status}
                </Tag>
                {isFileTemplate ? (
                  <>
                    <Typography.Text>{template.version?.filename || 'Файл не загружен'}</Typography.Text>
                    <Typography.Text type="secondary">
                      Версия {template.version?.version_number || 1}
                    </Typography.Text>
                    <div>
                      {variables.length > 0 ? (
                        variables.map((variable) => <Tag key={variable.name}>{`{{${variable.name}}}`}</Tag>)
                      ) : (
                        <Typography.Text type="secondary">Переменные не найдены</Typography.Text>
                      )}
                    </div>
                  </>
                ) : (
                  <Typography.Paragraph ellipsis={{ rows: 3 }}>
                    {template.version?.subject || template.status}
                  </Typography.Paragraph>
                )}
              </Space>
            </ProCard>
          );
        })}
      </div>

      {!isFileTemplate && (
        <TemplateEditorDrawer open={Boolean(editing)} template={editing} onClose={() => setEditing(null)} />
      )}
    </>
  );
}

export function TemplatesPage() {
  return (
    <Tabs
      items={[
        { key: 'email', label: 'Письма', children: <TemplateGrid type="email" /> },
        { key: 'kp', label: 'Коммерческие предложения', children: <TemplateGrid type="kp" /> },
        { key: 'contract', label: 'Договоры', children: <TemplateGrid type="contract" /> },
      ]}
    />
  );
}
