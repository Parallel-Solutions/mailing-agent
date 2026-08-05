import {
  InboxOutlined,
  DeleteOutlined,
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
import { App, Button, Checkbox, Dropdown, Modal, Space, Tabs, Tag, Tooltip, Typography, Upload } from 'antd';
import type { MenuProps } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';
import {
  useActiveOnboardingStep,
} from '@/features/onboarding/events';
import { AddTemplateWizard, type WizardStep } from '@/features/templates/AddTemplateWizard';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { readBoolParam, readEnumParam } from '@/utils/urlState';
import { TemplatePreviewImage } from '@/features/templates/TemplatePreviewImage';
import { showDocumentUploadError } from '@/features/templates/documentUploadError';
import {
  buildEmailPreviewDocument,
  downloadEmailHtml,
  getEmailFormat,
} from '@/features/templates/emailTemplateUtils';
import './TemplatesPage.css';
import { statusLabel } from '@/utils/presentation';

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
  const { message, modal } = App.useApp();
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
      accept=".docx,.pdf,.pptx,.html,.htm"
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
          showDocumentUploadError(modal, error);
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
  selectable = false,
  selected = false,
  onSelectedChange,
  onPreview,
}: {
  template: Template;
  type: TemplateKind;
  onRefresh: () => void;
  selectable?: boolean;
  selected?: boolean;
  onSelectedChange?: (selected: boolean) => void;
  onPreview?: (templateId: string) => void;
}) {
  const navigate = useNavigate();
  const { message, modal } = App.useApp();
  const [archiving, setArchiving] = useState(false);
  const isFileTemplate = type !== 'email';
  const variables = template.version?.variables || [];
  const filename = template.version?.filename || '';
  const deliveryFilename = template.version?.rendered_pdf_filename || '';
  const hasFile = Boolean(filename);
  const extension = filename.split('.').pop()?.toUpperCase();
  const isPptx = filename.toLowerCase().endsWith('.pptx');
  const canPreviewFile = !isPptx;
  const hasDeliveryPdf = Boolean(template.version?.rendered_pdf_filename);
  const emailFormat = !isFileTemplate ? getEmailFormat(template) : null;
  const canShowPreviewImage = isFileTemplate ? hasFile && canPreviewFile : Boolean(template.version?.body_html?.trim());

  const openEditor = () => navigate(`/templates/${template.id}/edit`);
  const preview = async () => {
    if (isFileTemplate) {
      window.open(templatesApi.previewFileUrl(template.id), '_blank', 'noopener,noreferrer');
      return;
    }
    onPreview?.(template.id);
  };
  const archiveDocument = () => {
    modal.confirm({
      title: `Удалить документ «${template.name}»?`,
      content: 'Документ будет перемещён в архив и исчезнет из списка.',
      okText: 'Удалить',
      okType: 'danger',
      cancelText: 'Отмена',
      onOk: async () => {
        setArchiving(true);
        try {
          await templatesApi.archive(template.id);
          message.success('Документ удалён');
          onSelectedChange?.(false);
          onRefresh();
        } catch (error) {
          message.error(
            error instanceof Error ? error.message : 'Не удалось удалить документ',
          );
          throw error;
        } finally {
          setArchiving(false);
        }
      },
    });
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
  } else if (!isFileTemplate && template.version?.body_html?.trim()) {
    moreItems.push(
      {
        key: 'html',
        icon: <DownloadOutlined />,
        label: 'Скачать HTML',
        onClick: () => downloadEmailHtml(template.name, template.version?.body_html || ''),
      },
      { type: 'divider' },
    );
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
    !isFileTemplate ? {
      key: 'archive',
      icon: <InboxOutlined />,
      label: 'Удалить',
      danger: true,
      onClick: async () => {
        await templatesApi.archive(template.id);
        onSelectedChange?.(false);
        onRefresh();
      },
    } : null,
  );

  return (
    <ProCard className={`template-library-card${selected ? ' template-library-card--selected' : ''}`} bordered>
      <div className="template-card-layout">
        {selectable && (
          <div className="template-card-select">
            <Checkbox
              checked={selected}
              aria-label={`Выбрать ${template.name}`}
              onChange={(event) => onSelectedChange?.(event.target.checked)}
            />
          </div>
        )}
        {canShowPreviewImage && (
          <TemplatePreviewImage templateId={template.id} alt={template.name} />
        )}

        <div className="template-card-header">
          <Typography.Title level={5} ellipsis={{ rows: 2 }} title={template.name}>
            {template.name}
          </Typography.Title>
          <Space size={6} wrap>
            <Tag color={isFileTemplate && !hasFile ? 'orange' : template.status === 'ready' ? 'green' : 'gold'}>
              {isFileTemplate && !hasFile ? 'Требуется файл' : template.status === 'ready' ? 'Готов' : statusLabel(template.status)}
            </Tag>
            {emailFormat && <Tag color={emailFormat === 'visual' ? 'blue' : 'default'}>{emailFormat === 'visual' ? 'HTML' : 'Текст'}</Tag>}
            {extension && <Tag>{extension}</Tag>}
          </Space>
        </div>

        <div className="template-card-content">
          {isFileTemplate ? (
            <>
              <Typography.Text className="template-card-filename" ellipsis={{ tooltip: filename }}>
                {filename || 'Файл ещё не загружен'}
              </Typography.Text>
              {deliveryFilename && deliveryFilename !== filename && (
                <Typography.Text type="secondary" ellipsis={{ tooltip: deliveryFilename }}>
                  Имя в письме: {deliveryFilename}
                </Typography.Text>
              )}
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
              {template.version?.subject || statusLabel(template.status)}
            </Typography.Paragraph>
          )}
        </div>

        <div className="template-card-actions">
          {isPptx ? (
            <Button type="primary" icon={<DownloadOutlined />} href={templatesApi.fileUrl(template.id)}>
              Скачать
            </Button>
          ) : (
            <Button type="primary" icon={<EditOutlined />} onClick={openEditor}>
              {isFileTemplate ? 'Редактор' : 'Редактировать'}
            </Button>
          )}
          <Tooltip title={canPreviewFile ? 'Предпросмотр' : 'PPTX отправляется оригиналом без предпросмотра'}>
            <Button
              icon={<EyeOutlined />}
              disabled={(isFileTemplate && (!hasFile || !canPreviewFile)) || (!isFileTemplate && !template.version?.body_html?.trim())}
              aria-label="Предпросмотр"
              onClick={() => void preview()}
            />
          </Tooltip>
          {isFileTemplate && (
            <TemplateFileUpload
              templateId={template.id}
              label={hasFile ? 'Загрузить новую версию' : 'Загрузить файл'}
              compact
              onUploaded={onRefresh}
            />
          )}
          {isFileTemplate && (
            <Tooltip title="Удалить документ">
              <Button
                danger
                icon={<DeleteOutlined />}
                loading={archiving}
                aria-label={`Удалить документ ${template.name}`}
                onClick={archiveDocument}
              />
            </Tooltip>
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
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { searchParams, pushParams } = useUrlNavigation();
  const currentTab = searchParams.get('tab') || 'email';
  const wizardOpen = readBoolParam(searchParams, 'wizard') && currentTab === type;
  const defaultWizardStep: WizardStep = type === 'email' ? 'format' : 'gallery';
  const wizardStep = wizardOpen
    ? readEnumParam(searchParams, 'wizard_step', ['format', 'gallery', 'custom'] as const, defaultWizardStep)
    : defaultWizardStep;
  const previewTemplateId = searchParams.get('preview');
  const [previewHtml, setPreviewHtml] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkArchiving, setBulkArchiving] = useState(false);
  const activeOnboardingStep = useActiveOnboardingStep();
  const previousOnboardingStepRef = useRef<string | null>(null);
  const isFileTemplate = type === 'document';
  const canBulkSelect = type === 'email';
  const { data, isLoading } = useQuery({
    queryKey: ['templates', type],
    queryFn: () => templatesApi.list({ template_type: type }),
  });
  const templates = data || [];
  const selectedCount = selectedIds.size;
  const allSelected = templates.length > 0 && selectedCount === templates.length;

  const refresh = () => { void queryClient.invalidateQueries({ queryKey: ['templates', type] }); };

  useEffect(() => {
    const previousStep = previousOnboardingStepRef.current;
    previousOnboardingStepRef.current = activeOnboardingStep;
    const stepId = activeOnboardingStep || '';

    if (type === 'document') {
      const documentSteps = new Set([
        'document-source',
        'document-upload',
        'document-fields',
        'document-preview',
        'document-chain-use',
      ]);
      if (documentSteps.has(stepId)) {
        pushParams({ tab: 'document', wizard: '1' }, ['wizard_step']);
      } else if (
        stepId === 'template-document'
        || stepId === 'document-library'
        || stepId === 'document-add'
        || stepId === 'document-formats'
      ) {
        pushParams({ tab: 'document' }, ['wizard', 'wizard_step']);
      } else if (previousStep?.startsWith('document-')) {
        pushParams({}, ['wizard', 'wizard_step']);
      }
      return;
    }

    if (!stepId.startsWith('template-')) {
      if (!previousStep?.startsWith('template-')) return;
      pushParams({}, ['wizard', 'wizard_step']);
      return;
    }

    if (
      stepId === 'template-open'
      || stepId === 'template-library'
      || stepId === 'template-actions'
    ) {
      pushParams({ tab: 'email' }, ['wizard', 'wizard_step']);
      return;
    }

    const nextStep: WizardStep | undefined =
      stepId === 'template-format'
        ? 'format'
        : stepId === 'template-source'
          ? 'gallery'
          : stepId === 'template-custom'
            ? 'custom'
            : undefined;
    if (!nextStep) return;
    pushParams({
      tab: 'email',
      wizard: '1',
      wizard_step: nextStep === 'format' ? null : nextStep,
    });
  }, [activeOnboardingStep, pushParams, type]);

  useEffect(() => {
    if (!previewTemplateId || type !== 'email') {
      setPreviewHtml('');
      return;
    }
    let cancelled = false;
    void templatesApi.preview(previewTemplateId).then((result) => {
      if (!cancelled) {
        setPreviewHtml(buildEmailPreviewDocument(result.body_html));
      }
    }).catch(() => {
      if (!cancelled) setPreviewHtml('');
    });
    return () => {
      cancelled = true;
    };
  }, [previewTemplateId, type]);

  const previewTemplate = templates.find((template) => template.id === previewTemplateId) || null;

  const handleCreated = (template: Template) => {
    refresh();
    if (template.version?.filename?.toLowerCase().endsWith('.pptx')) {
      pushParams({ tab: 'document' }, ['wizard', 'wizard_step']);
      return;
    }
    navigate(`/templates/${template.id}/edit`);
  };

  const toggleSelected = (templateId: string, selected: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selected) next.add(templateId);
      else next.delete(templateId);
      return next;
    });
  };

  const selectAll = () => {
    setSelectedIds(new Set(templates.map((template) => template.id)));
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
  };

  const archiveSelected = () => {
    if (selectedCount === 0) return;
    const ids = Array.from(selectedIds);
    modal.confirm({
      title: `Удалить выбранные письма (${ids.length})?`,
      content: 'Шаблоны будут перемещены в архив и исчезнут из списка.',
      okText: 'Удалить',
      okType: 'danger',
      cancelText: 'Отмена',
      onOk: async () => {
        setBulkArchiving(true);
        try {
          for (const id of ids) {
            await templatesApi.archive(id);
          }
          message.success(
            ids.length === 1 ? 'Письмо перемещено в архив' : `Перемещено в архив: ${ids.length}`,
          );
          clearSelection();
          refresh();
        } catch (error) {
          message.error(error instanceof Error ? error.message : 'Не удалось удалить выбранные письма');
          refresh();
          throw error;
        } finally {
          setBulkArchiving(false);
        }
      },
    });
  };

  return (
    <>
      <div data-onboarding-id="template-library">
        <div
          className="template-library-toolbar"
          data-onboarding-id={isFileTemplate ? 'document-library-toolbar' : 'template-library-toolbar'}
        >
          <Button
            type="primary"
            icon={<PlusOutlined />}
            data-onboarding-id={isFileTemplate ? 'document-add' : 'add-template'}
            onClick={() => {
              pushParams({ tab: type, wizard: '1' });
            }}
          >
            {isFileTemplate ? 'Добавить документ' : 'Добавить письмо'}
          </Button>
          {isFileTemplate && (
            <Typography.Text type="secondary" data-onboarding-id="document-formats">
              Форматы: DOCX, PDF, PPTX, HTML
            </Typography.Text>
          )}
          {canBulkSelect && selectedCount > 0 && (
            <div className="template-library-bulk">
              <Typography.Text type="secondary">Выбрано: {selectedCount}</Typography.Text>
              {!allSelected && (
                <Button size="small" onClick={selectAll}>
                  Выбрать все
                </Button>
              )}
              <Button size="small" onClick={clearSelection}>
                Снять
              </Button>
              <Button danger size="small" loading={bulkArchiving} onClick={archiveSelected}>
                Удалить выбранные
              </Button>
            </div>
          )}
        </div>

        <div className="template-library-grid" aria-busy={isLoading}>
          {templates.map((template) => (
            <TemplateCard
              key={template.id}
              template={template}
              type={type}
              onRefresh={refresh}
              selectable={canBulkSelect}
              selected={selectedIds.has(template.id)}
              onSelectedChange={(selected) => toggleSelected(template.id, selected)}
              onPreview={(templateId) => pushParams({ tab: 'email', preview: templateId })}
            />
          ))}
        </div>
      </div>

      <AddTemplateWizard
        open={wizardOpen}
        templateType={type}
        step={wizardStep}
        onStepChange={(next) =>
          pushParams({ wizard_step: next === defaultWizardStep ? null : next })
        }
        onClose={() => pushParams({}, ['wizard', 'wizard_step'])}
        onCreated={handleCreated}
      />

      <Modal
        open={Boolean(previewTemplateId && previewHtml && type === 'email')}
        title={previewTemplate?.version?.subject || previewTemplate?.name || 'Предпросмотр письма'}
        onCancel={() => pushParams({}, ['preview'])}
        footer={null}
        width={760}
        destroyOnClose
      >
        <iframe
          title="Предпросмотр письма"
          sandbox=""
          srcDoc={previewHtml}
          style={{ width: '100%', minHeight: 480, border: 'none', background: '#f4f6f5' }}
        />
      </Modal>
    </>
  );
}

export function TemplatesPage() {
  const { searchParams, pushParams } = useUrlNavigation();
  const activeTab = readEnumParam(searchParams, 'tab', ['email', 'document'] as const, 'email');

  return (
    <Tabs
      activeKey={activeTab}
      onChange={(key) => pushParams({ tab: key === 'email' ? null : key }, ['preview', 'wizard', 'wizard_step'])}
      items={[
        { key: 'email', label: 'Шаблон письма', children: <TemplateGrid type="email" /> },
        {
          key: 'document',
          label: <span data-onboarding-id="template-document-tab">Документ</span>,
          children: <TemplateGrid type="document" />,
        },
      ]}
    />
  );
}
