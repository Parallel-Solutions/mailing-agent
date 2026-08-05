import { ExclamationCircleOutlined, WarningOutlined } from '@ant-design/icons';
import { Alert, Button, Modal, Space, Spin, Tabs, Typography, message } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { campaignsApi } from '@/api/campaigns';
import type {
  EmailChainPreviewAttachment,
  EmailChainPreviewItem,
  TemplatePlaceholderIssue,
} from '@/api/types';
import { invalidateCampaignDerivedData, showAutoFixResultMessage } from '@/features/campaigns/campaignQueryUtils';
import { ValidationAutoFixButton } from '@/features/campaigns/ValidationAutoFixButton';
import {
  buildEmailPreviewDocument,
  highlightReviewIssues,
} from '@/features/templates/emailTemplateUtils';
import {
  isBlockingPlaceholderIssue,
  isLanguageIssue,
} from '@/features/campaigns/campaignStepValidation';
import './ChainEmailPreviewModal.css';

type Props = {
  open: boolean;
  campaignId: string;
  activeNodeId?: string | null;
  onActiveNodeChange?: (nodeId: string) => void;
  onClose: () => void;
};

function recipientLabel(company?: string, contactName?: string, email?: string): string {
  return [company, contactName, email].filter(Boolean).join(' · ') || '—';
}

function issueLabel(issue: TemplatePlaceholderIssue): string {
  return issue.message || issue.token;
}

function groupIssues(issues: TemplatePlaceholderIssue[]) {
  const placeholders = issues.filter((issue) => isBlockingPlaceholderIssue(issue));
  const language = issues.filter((issue) => isLanguageIssue(issue));
  return { placeholders, language };
}

function IssueAlerts({ issues }: { issues: TemplatePlaceholderIssue[] }) {
  const { placeholders, language } = groupIssues(issues);
  return (
    <>
      {placeholders.length > 0 ? (
        <Alert
          type="error"
          showIcon
          message="Плейсхолдеры и артефакты"
          description={
            <ul className="chain-email-preview-issues">
              {placeholders.map((issue) => (
                <li key={`${issue.field}-${issue.token}-${issue.kind}`}>{issueLabel(issue)}</li>
              ))}
            </ul>
          }
        />
      ) : null}
      {language.length > 0 ? (
        <Alert
          type="warning"
          showIcon
          message="Языковые замечания"
          description={
            <ul className="chain-email-preview-issues">
              {language.map((issue) => (
                <li key={`${issue.field}-${issue.token}-${issue.kind}`}>{issueLabel(issue)}</li>
              ))}
            </ul>
          }
        />
      ) : null}
    </>
  );
}


function attachmentDownloadUrl(
  campaignId: string,
  recipientId: number,
  templateId: string,
  previewVersion: number,
): string {
  const base = campaignsApi.previewEmailChainAttachmentUrl(campaignId, recipientId, templateId, {
    download: true,
  });
  return `${base}&v=${previewVersion}`;
}

function canInlinePreviewAttachment(filename: string): boolean {
  const suffix = filename.split('.').pop()?.toLowerCase() || '';
  return ['pdf', 'docx', 'html', 'htm'].includes(suffix);
}

function TabLabelWithIssues({ label, issues }: { label: string; issues: TemplatePlaceholderIssue[] }) {
  const { hasPlaceholderErrors, hasLanguageIssues } = tabIssueIcons(issues);
  return (
    <span className="chain-email-preview-tab-label">
      {label}
      {hasPlaceholderErrors ? (
        <ExclamationCircleOutlined className="chain-email-preview-tab-error" />
      ) : hasLanguageIssues ? (
        <WarningOutlined className="chain-email-preview-tab-warning" />
      ) : null}
    </span>
  );
}

function AttachmentPdfPreview({
  campaignId,
  recipientId,
  templateId,
  previewVersion,
  filename,
}: {
  campaignId: string;
  recipientId: number;
  templateId: string;
  previewVersion: number;
  filename: string;
}) {
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryVersion, setRetryVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    setLoading(true);
    setError(null);
    setPreviewUrl(null);

    campaignsApi
      .fetchPreviewEmailChainAttachment(campaignId, recipientId, templateId, { signal: controller.signal })
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setPreviewUrl(objectUrl);
      })
      .catch((fetchError) => {
        if (cancelled || (fetchError instanceof Error && fetchError.name === 'AbortError')) return;
        setError(fetchError instanceof Error ? fetchError.message : 'Не удалось загрузить PDF');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [campaignId, recipientId, templateId, previewVersion, retryVersion]);

  if (loading) {
    return (
      <div className="chain-email-preview-loading chain-email-preview-loading--attachment">
        <Spin tip="Загружаем PDF…" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message={error}
        action={
          <Button size="small" onClick={() => setRetryVersion((value) => value + 1)}>
            {'\u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u044c'}
          </Button>
        }
      />
    );
  }

  if (!previewUrl) return null;

  return (
    <div className="chain-email-preview-frame-wrap chain-email-preview-frame-wrap--attachment">
      <iframe title={`Предпросмотр: ${filename}`} src={previewUrl} />
    </div>
  );
}

function AttachmentPreviewTab({
  attachment,
  recipientId,
  campaignId,
  previewVersion,
}: {
  attachment: EmailChainPreviewAttachment;
  recipientId: number;
  campaignId: string;
  previewVersion: number;
}) {
  const attachmentIssues = attachment.issues || [];
  const canPreviewInline = canInlinePreviewAttachment(attachment.filename || '');
  const downloadUrl = attachment.has_content
    ? attachmentDownloadUrl(campaignId, recipientId, attachment.template_id, previewVersion)
    : null;

  return (
    <div className="chain-email-preview-tab">
      {!attachment.has_content ? (
        <Typography.Text type="danger">
          {attachment.filename || attachment.template_id}
          {attachment.error ? `: ${attachment.error}` : ' — файл недоступен'}
        </Typography.Text>
      ) : null}
      <IssueAlerts issues={attachmentIssues} />
      {attachment.has_content ? (
        <>
          {canPreviewInline ? (
            <AttachmentPdfPreview
              campaignId={campaignId}
              recipientId={recipientId}
              templateId={attachment.template_id}
              previewVersion={previewVersion}
              filename={attachment.filename}
            />
          ) : (
            <Alert
              type="info"
              showIcon
              message="PPTX будет отправлен оригиналом без блокирующего предпросмотра."
            />
          )}
          {downloadUrl ? (
            <Typography.Paragraph style={{ marginBottom: 0 }}>
              <a href={downloadUrl} download={attachment.filename || undefined}>
                Скачать {attachment.filename}
              </a>
            </Typography.Paragraph>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function EmailPreviewTab({
  item,
}: {
  item: EmailChainPreviewItem;
}) {
  const emailIssues = (item.issues || []).filter(
    (issue) => issue.field !== 'attachment' && (isLanguageIssue(issue) || isBlockingPlaceholderIssue(issue)),
  );
  const previewHtml = useMemo(() => {
    const highlighted = highlightReviewIssues(item.body_html, emailIssues);
    return buildEmailPreviewDocument(highlighted);
  }, [item.body_html, emailIssues]);

  return (
    <div className="chain-email-preview-tab">
      <IssueAlerts issues={emailIssues} />
      <Typography.Text type="secondary">Тема: {item.subject || '—'}</Typography.Text>
      <div className="chain-email-preview-frame-wrap">
        <iframe title={`Предпросмотр: ${item.node_name}`} sandbox="" srcDoc={previewHtml} />
      </div>
    </div>
  );
}

function NodePreviewContent({
  item,
  recipientId,
  campaignId,
  previewVersion,
}: {
  item: EmailChainPreviewItem;
  recipientId: number;
  campaignId: string;
  previewVersion: number;
}) {
  const emailIssues = (item.issues || []).filter(
    (issue) => issue.field !== 'attachment' && (isLanguageIssue(issue) || isBlockingPlaceholderIssue(issue)),
  );

  if (item.attachments.length === 0) {
    return <EmailPreviewTab item={item} />;
  }

  const innerTabs = [
    {
      key: 'email',
      label: <TabLabelWithIssues label="Письмо" issues={emailIssues} />,
      children: <EmailPreviewTab item={item} />,
    },
    ...item.attachments.map((attachment) => ({
      key: attachment.template_id,
      label: (
        <TabLabelWithIssues
          label={attachment.filename || attachment.template_id}
          issues={attachment.issues || []}
        />
      ),
      children: (
        <AttachmentPreviewTab
          attachment={attachment}
          recipientId={recipientId}
          campaignId={campaignId}
          previewVersion={previewVersion}
        />
      ),
    })),
  ];

  return (
    <Tabs
      className="chain-email-preview-inner-tabs"
      size="small"
      items={innerTabs}
    />
  );
}

function tabIssueIcons(issues: TemplatePlaceholderIssue[]) {
  const hasPlaceholderErrors = issues.some((issue) => isBlockingPlaceholderIssue(issue));
  const hasLanguageIssues = issues.some((issue) => isLanguageIssue(issue));
  return { hasPlaceholderErrors, hasLanguageIssues };
}

function hasBlockingPreviewIssues(issues: TemplatePlaceholderIssue[]): boolean {
  return issues.some((issue) => isBlockingPlaceholderIssue(issue) || isLanguageIssue(issue));
}

export function ChainEmailPreviewModal({
  open,
  campaignId,
  activeNodeId,
  onActiveNodeChange,
  onClose,
}: Props) {
  const queryClient = useQueryClient();
  const previewQuery = useQuery({
    queryKey: ['email-chain-preview', campaignId],
    queryFn: () => campaignsApi.previewEmailChain(campaignId),
    enabled: open && Boolean(campaignId),
  });

  const preview = previewQuery.data;
  const previewVersion = previewQuery.dataUpdatedAt || 0;
  const recipientCaption = preview
    ? recipientLabel(preview.recipient.company, preview.recipient.contact_name, preview.recipient.email)
    : '';
  const hasIssues = useMemo(
    () => (preview?.items || []).some((item) => hasBlockingPreviewIssues(item.issues || [])),
    [preview?.items],
  );

  const autoFixMutation = useMutation({
    mutationFn: () => campaignsApi.autoFixValidation(campaignId),
    onSuccess: async (result) => {
      invalidateCampaignDerivedData(queryClient, campaignId);
      let remainingIssues = result.validation.template_issues?.length || 0;
      try {
        const refreshedPreview = await campaignsApi.previewEmailChain(campaignId);
        remainingIssues = (refreshedPreview.items || []).reduce(
          (count, item) => count + (item.issues?.length || 0),
          0,
        );
        queryClient.setQueryData(['email-chain-preview', campaignId], refreshedPreview);
      } catch {
        // keep validation-based remaining count
      }
      showAutoFixResultMessage(result, message, { remainingIssues });
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось выполнить автоисправление');
    },
  });

  const isRefreshing = autoFixMutation.isPending || (previewQuery.isFetching && !previewQuery.isLoading);

  const tabItems = preview?.items ?? [];
  const resolvedActiveKey =
    activeNodeId && tabItems.some((item) => item.node_id === activeNodeId)
      ? activeNodeId
      : tabItems[0]?.node_id;

  return (
    <Modal
      open={open}
      className="chain-email-preview-modal"
      title="Предпросмотр цепочки писем"
      width="100%"
      centered={false}
      style={{ top: 0, paddingBottom: 0, maxWidth: '100vw' }}
      styles={{
        content: {
          display: 'flex',
          flexDirection: 'column',
          height: '100vh',
          padding: 0,
        },
        body: {
          flex: 1,
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
        },
      }}
      onCancel={onClose}
      destroyOnClose
      footer={
        <Space>
          <Button onClick={onClose}>Закрыть</Button>
          {hasIssues ? (
            <ValidationAutoFixButton
              type="primary"
              loading={autoFixMutation.isPending}
              onClick={() => autoFixMutation.mutate()}
            />
          ) : null}
        </Space>
      }
    >
      <div className="chain-email-preview-body">
        {isRefreshing ? (
          <div className="chain-email-preview-loading chain-email-preview-loading--overlay">
            <Spin tip="Обновляем предпросмотр…" />
          </div>
        ) : null}
        {previewQuery.isLoading ? (
          <div className="chain-email-preview-loading">
            <Spin tip="Формируем препросмотр…" />
          </div>
        ) : null}
        {previewQuery.isError ? (
          <Alert
            type="error"
            showIcon
            message={(previewQuery.error as Error).message || 'Не удалось загрузить препросмотр'}
          />
        ) : null}
        {preview ? (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
              Данные первой строки списка: {recipientCaption}
            </Typography.Paragraph>
            <Tabs
              className="chain-email-preview-outer-tabs"
              activeKey={resolvedActiveKey}
              onChange={(key) => onActiveNodeChange?.(key)}
              items={tabItems.map((item) => {
                const { hasPlaceholderErrors, hasLanguageIssues } = tabIssueIcons(item.issues || []);
                return {
                  key: item.node_id,
                  label: (
                    <span className="chain-email-preview-tab-label">
                      {item.node_name}
                      {hasPlaceholderErrors ? (
                        <ExclamationCircleOutlined className="chain-email-preview-tab-error" />
                      ) : hasLanguageIssues ? (
                        <WarningOutlined className="chain-email-preview-tab-warning" />
                      ) : null}
                    </span>
                  ),
                  children: (
                    <NodePreviewContent
                      item={item}
                      recipientId={preview.recipient.id}
                      campaignId={campaignId}
                      previewVersion={previewVersion}
                    />
                  ),
                };
              })}
            />
          </>
        ) : null}
      </div>
    </Modal>
  );
}
