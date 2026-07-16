import {
  InboxOutlined,
  CopyOutlined,
  DownloadOutlined,
  EditOutlined,
  EllipsisOutlined,
  EyeOutlined,
  FilePdfOutlined,
  PlusOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { ProCard } from '@ant-design/pro-components';
import { App, Button, Dropdown, Space, Tabs, Tag, Tooltip, Typography, Upload } from 'antd';
import type { MenuProps } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';
import { AddTemplateWizard } from '@/features/templates/AddTemplateWizard';
import './TemplatesPage.css';

type TemplateKind = 'email' | 'document';

function TemplateFileUpload({
  templateId,
  label = 'Загрузить свой шаблон',
  primary = false,
  compact = false,
  onUploaded,
}: {
  templateId?: string;
  label?: string;
  primary?: boolean;
  compact?: boolean;
  onUploaded: (template: Template) => void;
}) {
  const { message } = App.useApp();
  const [uploading, setUploading] = useState(false);

  const button = (
    <Button
      type={primary ? 'primary' : 'default'}
      icon={<UploadOutlined />}
      loading={uploading}
      aria-label={label}
      title={compact ? label : undefined}
    >
      {compact ? null : label}
    </Button>
  );

  return (
    <Upload
      accept=".docx,.pdf,.html,.htm"
      maxCount={1}
      showUploadList={false}
      customRequest={async ({ file, onSuccess, onError }) => {
        setUploading(true);
        try {
          const uploaded = await templatesApi.uploadFile(file as File, 'document', {
            template_id: templateId,
          });
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
      {compact ? <Tooltip title={label}>{button}</Tooltip> : button}
    </Upload>
  );
}

function TemplateCard({
  template,
  type,
  onRefresh,
}: {
  template: Template;
  type: TemplateKind;
  onRefresh: () => void;
}) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const isFileTemplate = type !== 'email';
  const variables = template.version?.variables || [];
  const filename = template.version?.filename || '';
  const hasFile = Boolean(filename);
  const extension = filename.split('.').pop()?.toUpperCase();
  const hasDeliveryPdf = Boolean(template.version?.rendered_pdf_filename);

  const openEditor = () => navigate(`/templates/${template.id}/edit`);
  const preview = async () => {
    if (isFileTemplate) {
      window.open(templatesApi.previewFileUrl(template.id), '_blank', 'noopener,noreferrer');
      return;
    }
    const result = await templatesApi.preview(template.id);
    message.info(result.subject);
  };

  const moreItems: MenuProps['items'] = [];
  if (isFileTemplate && hasFile) {
    moreItems.push({
      key: 'source',
      icon: <DownloadOutlined />,
      label: 'Скачать исходник',
      onClick: () => window.open(templatesApi.fileUrl(template.id), '_blank', 'noopener,noreferrer'),
    });
    if (hasDeliveryPdf) {
      moreItems.push({
        key: 'delivery',
        icon: <FilePdfOutlined />,
        label: 'PDF для отправки',
        onClick: () => window.open(templatesApi.deliveryFileUrl(template.id), '_blank', 'noopener,noreferrer'),
      });
    }
    moreItems.push({ type: 'divider' });
  }
  moreItems.push(
    {
      key: 'duplicate',
      icon: <CopyOutlined />,
      label: 'Создать копию',
      onClick: async () => {
        await templatesApi.duplicate(template.id);
        onRefresh();
      },
    },
    {
      key: 'archive',
      icon: <InboxOutlined />,
      label: 'Переместить в архив',
      danger: true,
      onClick: async () => {
        await templatesApi.archive(template.id);
        onRefresh();
      },
    },
  );

  return (
    <ProCard className="template-library-card" bordered>
      <div className="template-card-layout">
        <div className="template-card-header">
          <Typography.Title level={5} ellipsis={{ rows: 2 }} title={template.name}>
            {template.name}
          </Typography.Title>
          <Space size={6} wrap>
            <Tag color={isFileTemplate && !hasFile ? 'orange' : template.status === 'ready' ? 'green' : 'gold'}>
              {isFileTemplate && !hasFile ? 'Требуется файл' : template.status === 'ready' ? 'Готов' : template.status}
            </Tag>
            {extension && <Tag>{extension}</Tag>}
          </Space>
        </div>

        <div className="template-card-content">
          {isFileTemplate ? (
            <>
              <Typography.Text className="template-card-filename" ellipsis={{ tooltip: filename }}>
                {filename || 'Файл ещё не загружен'}
              </Typography.Text>
              <Typography.Text type="secondary">Версия {template.version?.version_number || 1}</Typography.Text>
              <div className="template-card-variables">
                {variables.length > 0 ? (
                  <>
                    {variables.slice(0, 2).map((variable) => <Tag key={variable.name}>{`{{${variable.name}}}`}</Tag>)}
                    {variables.length > 2 && <Tag>+{variables.length - 2}</Tag>}
                  </>
                ) : (
                  <Typography.Text type="secondary">Переменные не найдены</Typography.Text>
                )}
              </div>
            </>
          ) : (
            <Typography.Paragraph type="secondary" ellipsis={{ rows: 3 }}>
              {template.version?.subject || template.status}
            </Typography.Paragraph>
          )}
        </div>

        <div className="template-card-actions">
          <Button type="primary" icon={<EditOutlined />} onClick={openEditor}>
            {isFileTemplate ? 'Редактор' : 'Редактировать'}
          </Button>
          <Tooltip title="Предпросмотр">
            <Button icon={<EyeOutlined />} disabled={isFileTemplate && !hasFile} aria-label="Предпросмотр" onClick={() => void preview()} />
          </Tooltip>
          {isFileTemplate && (
            <TemplateFileUpload
              templateId={template.id}
              label={hasFile ? 'Загрузить новую версию' : 'Загрузить файл'}
              compact
              onUploaded={onRefresh}
            />
          )}
          <Dropdown menu={{ items: moreItems }} trigger={['click']} placement="bottomRight">
            <Tooltip title="Другие действия">
              <Button icon={<EllipsisOutlined />} aria-label="Другие действия" />
            </Tooltip>
          </Dropdown>
        </div>
      </div>
    </ProCard>
  );
}

function TemplateGrid({ type }: { type: TemplateKind }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [wizardOpen, setWizardOpen] = useState(false);
  const isFileTemplate = type === 'document';
  const { data, isLoading } = useQuery({
    queryKey: ['templates', type],
    queryFn: () => templatesApi.list({ template_type: type }),
  });

  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ['templates', type] }); };

  const handleCreated = (template: Template) => {
    refresh();
    navigate(`/templates/${template.id}/edit`);
  };

  return (
    <>
      <div className="template-library-toolbar">
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setWizardOpen(true)}>
          {isFileTemplate ? 'Добавить документ' : 'Добавить письмо'}
        </Button>
        {isFileTemplate && (
          <>
            <TemplateFileUpload primary onUploaded={refresh} />
            <Typography.Text type="secondary">Форматы: DOCX, PDF, HTML</Typography.Text>
          </>
        )}
      </div>

      <div className="template-library-grid" aria-busy={isLoading}>
        {(data || []).map((template) => (
          <TemplateCard key={template.id} template={template} type={type} onRefresh={refresh} />
        ))}
      </div>

      <AddTemplateWizard
        open={wizardOpen}
        templateType={type}
        onClose={() => setWizardOpen(false)}
        onCreated={handleCreated}
      />
    </>
  );
}

export function TemplatesPage() {
  return (
    <Tabs
      items={[
        { key: 'email', label: 'Шаблон письма', children: <TemplateGrid type="email" /> },
        { key: 'document', label: 'Документ', children: <TemplateGrid type="document" /> },
      ]}
    />
  );
}
