import { CheckCircleOutlined, ReloadOutlined, ToolOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Col, Empty, Modal, Row, Space, Tabs, Tag, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { campaignsApi } from '@/api/campaigns';
import type { DocumentLayoutReviewItem } from '@/api/types';
import { OperationProgress } from '@/components/OperationProgress';
import { invalidateCampaignDerivedData } from './campaignQueryUtils';
import './CampaignDocumentLayoutReview.css';

type Props = {
  open: boolean;
  campaignId: string;
  onClose: () => void;
  onApplied?: () => void;
};

function statusTag(document: DocumentLayoutReviewItem, applied: boolean) {
  if (applied || document.status === 'already_applied') {
    return <Tag color="green">Исправление применено</Tag>;
  }
  if (document.status === 'candidate') return <Tag color="blue">Есть улучшение</Tag>;
  if (document.status === 'preview_only') return <Tag color="cyan">{'\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440'}</Tag>;
  if (document.status === 'fallback') return <Tag color="gold">Исходный макет</Tag>;
  if (document.status === 'error') return <Tag color="red">Ошибка проверки</Tag>;
  return <Tag>Без изменений</Tag>;
}

export function CampaignDocumentLayoutReview({
  open,
  campaignId,
  onClose,
  onApplied,
}: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [selectedTemplateId, setSelectedTemplateId] = useState('');
  const [appliedTemplateIds, setAppliedTemplateIds] = useState<Set<string>>(new Set());

  const reviewQuery = useQuery({
    queryKey: ['campaign-document-layout', campaignId],
    queryFn: () => campaignsApi.inspectDocumentLayout(campaignId),
    enabled: open,
    staleTime: 0,
    refetchOnMount: 'always',
  });

  const documents = useMemo(() => reviewQuery.data?.documents || [], [reviewQuery.data]);
  const selectedDocument =
    documents.find((document) => document.template_id === selectedTemplateId) || documents[0];

  useEffect(() => {
    if (!open) {
      setSelectedTemplateId('');
      setAppliedTemplateIds(new Set());
      return;
    }
    if (!documents.length) return;
    if (!documents.some((document) => document.template_id === selectedTemplateId)) {
      setSelectedTemplateId(documents[0].template_id);
    }
  }, [documents, open, selectedTemplateId]);

  const applyMutation = useMutation({
    mutationFn: (templateId: string) =>
      campaignsApi.applyDocumentLayout(campaignId, templateId),
    onSuccess: (result) => {
      setAppliedTemplateIds((current) => new Set(current).add(result.template_id));
      invalidateCampaignDerivedData(queryClient, campaignId);
      void queryClient.invalidateQueries({ queryKey: ['templates'] });
      message.success('Исправленная разметка сохранена новой версией шаблона');
      onApplied?.();
    },
    onError: (error) => {
      message.error(
        error instanceof Error ? error.message : 'Не удалось применить исправление',
      );
    },
  });

  const busy = applyMutation.isPending;
  const selectedApplied = selectedDocument
    ? appliedTemplateIds.has(selectedDocument.template_id)
    : false;
  const canApply = Boolean(selectedDocument?.can_apply && !selectedApplied);

  return (
    <Modal
      open={open}
      title="Проверка вёрстки документов"
      width={1180}
      destroyOnClose
      closable={!busy}
      maskClosable={!busy}
      onCancel={() => {
        if (!busy) onClose();
      }}
      footer={
        <Space>
          <Button onClick={onClose} disabled={busy}>
            Закрыть
          </Button>
          <Button
            icon={<ReloadOutlined />}
            disabled={busy || reviewQuery.isFetching}
            onClick={() => void reviewQuery.refetch()}
          >
            Проверить заново
          </Button>
          <Button
            type="primary"
            icon={selectedApplied ? <CheckCircleOutlined /> : <ToolOutlined />}
            disabled={!canApply || busy}
            loading={busy}
            onClick={() => {
              if (selectedDocument) applyMutation.mutate(selectedDocument.template_id);
            }}
          >
            {selectedApplied ? 'Сохранено в шаблон' : 'Сохранить разметку в шаблон'}
          </Button>
        </Space>
      }
    >
      <OperationProgress
        active={reviewQuery.isFetching && !reviewQuery.data}
        title="Сравниваем документы"
        stages={[
          'Получаем текущий PDF',
          'Определяем поля и исходные шрифты',
          'Строим исправленную разметку',
          'Готовим изображения «до» и «после»',
        ]}
        estimatedSeconds={[8, 30]}
      />
      <OperationProgress
        active={busy}
        title="Сохраняем исправленную разметку"
        stages={[
          'Проверяем активную версию шаблона',
          'Формируем исправленный PDF',
          'Создаём новую версию шаблона',
          'Обновляем предпросмотр',
        ]}
        estimatedSeconds={[5, 15]}
      />

      {reviewQuery.isError && !reviewQuery.isFetching ? (
        <Alert
          type="error"
          showIcon
          message="Не удалось сравнить документы"
          description={
            reviewQuery.error instanceof Error
              ? reviewQuery.error.message
              : 'Повторите проверку.'
          }
        />
      ) : null}

      {!reviewQuery.isFetching && !reviewQuery.isError && documents.length === 0 ? (
        <Empty description="В цепочке нет PDF-шаблонов с подставляемыми полями" />
      ) : null}

      {documents.length > 0 && !busy ? (
        <div className="document-layout-review">
          <Alert
            type={selectedDocument?.status === 'fallback' ? 'warning' : 'info'}
            showIcon
            message={`Пример для: ${reviewQuery.data?.recipient.company || 'первого получателя'}`}
            description={
              selectedDocument?.status === 'fallback'
                ? 'Подстановка выполнена в исходном макете. Автоматическая коррекция не будет сохранена и не блокирует отправку.'
                : 'Документы для отправки формируются по варианту «После». Сохранение закрепит эту разметку в новой версии шаблона.'
            }
          />
          <Tabs
            activeKey={selectedDocument?.template_id}
            onChange={setSelectedTemplateId}
            items={documents.map((document) => ({
              key: document.template_id,
              label: (
                <Space size={6}>
                  <span>{document.template_name}</span>
                  {statusTag(document, appliedTemplateIds.has(document.template_id))}
                </Space>
              ),
            }))}
          />

          {selectedDocument ? (
            <>
              {selectedDocument.status === 'error' ? (
                <Alert
                  type="error"
                  showIcon
                  message="Документ не удалось обработать"
                  description={selectedDocument.message}
                />
              ) : null}
              {selectedDocument.status === 'preview_only' ? (
                <Alert
                  type="info"
                  showIcon
                  message={'\u041f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0439 \u043f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440'}
                  description={selectedDocument.message}
                />
              ) : null}
              {selectedDocument.status === 'fallback' ? (
                <Alert
                  type="warning"
                  showIcon
                  message="Автоматическая компоновка пропущена"
                  description={
                    <Space direction="vertical" size={6}>
                      <Typography.Text>{selectedDocument.message}</Typography.Text>
                      {(selectedDocument.issues || []).map((issue, issueIndex) => (
                        <Typography.Text
                          key={`${issue.page}-${issue.source_text}-${issueIndex}`}
                          type="secondary"
                        >
                          {`Страница ${issue.page}: строка «${issue.source_text || 'без текста'}». `}
                          {issue.variables.length > 0
                            ? `Переменные: ${issue.variables.join(', ')}. `
                            : ''}
                          {issue.rendered_value
                            ? `Подставленное значение: «${issue.rendered_value}».`
                            : ''}
                        </Typography.Text>
                      ))}
                    </Space>
                  }
                />
              ) : null}
              {selectedDocument.status === 'skipped' ? (
                <Alert
                  type="info"
                  showIcon
                  message="Автоматическая коррекция не требуется"
                  description={selectedDocument.message}
                />
              ) : null}
              {(selectedDocument.status === 'preview_only' ||
                selectedDocument.status === 'fallback') &&
              selectedDocument.before_image &&
              !selectedDocument.after_image ? (
                <div className="document-layout-review__preview">
                  <div className="document-layout-review__preview-title">
                    <Typography.Text strong>Документ для отправки</Typography.Text>
                    {selectedDocument.status === 'fallback' ? (
                      <Typography.Text type="secondary">
                        Подстановка в исходном макете
                      </Typography.Text>
                    ) : null}
                  </div>
                  <img
                    src={selectedDocument.before_image}
                    alt={`\u041f\u0440\u0435\u0434\u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440 ${selectedDocument.template_name}`}
                  />
                </div>
              ) : null}
              {selectedDocument.before_image && selectedDocument.after_image ? (
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={12}>
                    <div className="document-layout-review__preview">
                      <div className="document-layout-review__preview-title">
                        <Typography.Text strong>До</Typography.Text>
                        <Typography.Text type="secondary">Без корпоративной коррекции</Typography.Text>
                      </div>
                      <img
                        src={selectedDocument.before_image}
                        alt={`Текущая вёрстка ${selectedDocument.template_name}`}
                      />
                    </div>
                  </Col>
                  <Col xs={24} lg={12}>
                    <div className="document-layout-review__preview document-layout-review__preview--after">
                      <div className="document-layout-review__preview-title">
                        <Typography.Text strong>После</Typography.Text>
                        <Typography.Text type="secondary">Корпоративная компоновка</Typography.Text>
                      </div>
                      <img
                        src={selectedDocument.after_image}
                        alt={`Исправленная вёрстка ${selectedDocument.template_name}`}
                      />
                    </div>
                  </Col>
                </Row>
              ) : null}
              {selectedDocument.changes.length > 0 ? (
                <div className="document-layout-review__changes">
                  <Typography.Text strong>Что будет исправлено</Typography.Text>
                  <ul>
                    {selectedDocument.changes.map((change) => (
                      <li key={change}>{change}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
    </Modal>
  );
}
