import { PlusOutlined } from '@ant-design/icons';
import { ProCard } from '@ant-design/pro-components';
import { App, Button, Drawer, Input, Space, Tabs, Typography } from 'antd';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';

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
      title="Редактор шаблона"
      extra={
        <Space>
          <Button
            onClick={() => editor?.chain().focus().insertContent('{{company}}').run()}
          >
            + company
          </Button>
          <Button
            onClick={() => editor?.chain().focus().insertContent('{{contact_name}}').run()}
          >
            + contact_name
          </Button>
          <Button type="primary" loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            Сохранить
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Название" />
        <Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Тема" />
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

function TemplateGrid({ type }: { type: string }) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<Template | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['templates', type],
    queryFn: () => templatesApi.list({ template_type: type }),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      templatesApi.create({
        name: `Новый шаблон ${type}`,
        template_type: type,
        subject: 'Тема письма',
        body_html: '<p>Здравствуйте, {{contact_name}}!</p><p>Компания: {{company}}</p>',
      }),
    onSuccess: (tmpl) => {
      void queryClient.invalidateQueries({ queryKey: ['templates', type] });
      setEditing(tmpl);
    },
  });

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} loading={createMutation.isPending} onClick={() => createMutation.mutate()}>
          Создать
        </Button>
      </Space>
      <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
        {(data || []).map((tmpl) => (
          <ProCard
            key={tmpl.id}
            loading={isLoading}
            title={tmpl.name}
            bordered
            actions={[
              <a key="edit" onClick={() => setEditing(tmpl)}>Редактировать</a>,
              <a
                key="preview"
                onClick={async () => {
                  const preview = await templatesApi.preview(tmpl.id);
                  message.info(preview.subject);
                }}
              >
                Preview
              </a>,
              <a
                key="dup"
                onClick={async () => {
                  await templatesApi.duplicate(tmpl.id);
                  void queryClient.invalidateQueries({ queryKey: ['templates', type] });
                }}
              >
                Копия
              </a>,
              <a
                key="arch"
                onClick={async () => {
                  await templatesApi.archive(tmpl.id);
                  void queryClient.invalidateQueries({ queryKey: ['templates', type] });
                }}
              >
                Архив
              </a>,
            ]}
          >
            <Typography.Paragraph ellipsis={{ rows: 3 }}>
              {tmpl.version?.subject || tmpl.status}
            </Typography.Paragraph>
          </ProCard>
        ))}
      </div>
      <TemplateEditorDrawer open={Boolean(editing)} template={editing} onClose={() => setEditing(null)} />
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
