import { CheckCircleOutlined, EyeOutlined, FileDoneOutlined, SyncOutlined, UploadOutlined } from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, App, Button, Progress, Select, Space, Steps, Tag, Typography, Upload } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { campaignsApi } from '@/api/campaigns';
import { templatesApi } from '@/api/templates';
import type { Campaign, DocumentTemplatePreview } from '@/api/types';

type Props = {
  campaignId?: string | null;
  campaign: Partial<Campaign>;
};

export function CampaignGenerationStep({ campaignId, campaign }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [preview, setPreview] = useState<DocumentTemplatePreview | null>(null);
  const [documentMode, setDocumentMode] = useState(campaign.document_mode || 'kp');
  const [kpTemplateId, setKpTemplateId] = useState<string | null>(campaign.kp_template_id || null);
  const [contractTemplateId, setContractTemplateId] = useState<string | null>(campaign.contract_template_id || null);

  useEffect(() => {
    setDocumentMode(campaign.document_mode || 'kp');
    setKpTemplateId(campaign.kp_template_id || null);
    setContractTemplateId(campaign.contract_template_id || null);
  }, [campaign.document_mode, campaign.kp_template_id, campaign.contract_template_id]);

  const documentTemplatesQuery = useQuery({
    queryKey: ['templates', 'document'],
    queryFn: () => templatesApi.list({ template_type: 'document' }),
  });
  const documentTemplates = documentTemplatesQuery.data || [];
  const kpOptions = documentTemplates
    .filter((template) => /\.(docx|pdf)$/i.test(template.version?.filename || ''))
    .map((template) => ({
      label: `${template.name}${template.version?.filename ? ` — ${template.version.filename}` : ''}`,
      value: template.id,
    }));
  const contractOptions = documentTemplates
    .filter((template) => /\.docx$/i.test(template.version?.filename || ''))
    .map((template) => ({
      label: `${template.name}${template.version?.filename ? ` — ${template.version.filename}` : ''}`,
      value: template.id,
    }));

  const saveDocumentSettings = async (patch: Partial<Campaign>) => {
    if (!campaignId) return;
    await campaignsApi.update(campaignId, patch);
    if (patch.document_mode) setDocumentMode(patch.document_mode);
    if ('kp_template_id' in patch) setKpTemplateId(patch.kp_template_id || null);
    if ('contract_template_id' in patch) setContractTemplateId(patch.contract_template_id || null);
    setPreview(null);
    await queryClient.invalidateQueries({ queryKey: ['campaign-generation', campaignId] });
    await queryClient.invalidateQueries({ queryKey: ['campaign-validate', campaignId] });
  };

  const generationQuery = useQuery({
    queryKey: ['campaign-generation', campaignId],
    queryFn: () => campaignsApi.generation(campaignId!),
    enabled: Boolean(campaignId),
    refetchInterval: (query) => {
      const status = query.state.data?.documents?.status;
      return status === 'running' || status === 'waiting_review' ? 2_000 : false;
    },
  });
  const generation = generationQuery.data;

  useEffect(() => {
    if (!generation?.ready || !campaignId) return;
    void queryClient.invalidateQueries({ queryKey: ['campaign-validate', campaignId] });
  }, [campaignId, generation?.ready, queryClient]);

  const prepareMutation = useMutation({
    mutationFn: () => campaignsApi.prepareGeneration(campaignId!),
    onSuccess: (result) => {
      queryClient.setQueryData(['campaign-generation', campaignId], result);
      setPreview(null);
      void queryClient.invalidateQueries({ queryKey: ['campaign-validate', campaignId] });
      message.success('Данные и шаблоны подготовлены');
    },
    onError: (error) => message.error(error instanceof Error ? error.message : 'Не удалось подготовить документы'),
  });

  const previewMutation = useMutation({
    mutationFn: () =>
      campaignsApi.previewDocuments({
        job_id: generation?.job_id || String(campaign.job_id || ''),
        document_mode: generation?.document_mode || campaign.document_mode || 'kp',
        work_type: generation?.work_type || campaign.work_type,
      }),
    onSuccess: (result) => {
      setPreview(result);
      if (result.status === 'ready') message.success('Тестовый пример собран');
    },
    onError: (error) => message.error(error instanceof Error ? error.message : 'Не удалось собрать пример'),
  });

  const startMutation = useMutation({
    mutationFn: () =>
      campaignsApi.startDocuments({
        job_id: generation?.job_id || String(campaign.job_id || ''),
        document_mode: generation?.document_mode || campaign.document_mode || 'kp',
        work_type: generation?.work_type || campaign.work_type,
        template_analysis_confirmed: true,
        mode: 'fast',
      }),
    onSuccess: async () => {
      message.success('Формирование документов запущено');
      await queryClient.invalidateQueries({ queryKey: ['campaign-generation', campaignId] });
      await generationQuery.refetch();
    },
    onError: (error) => message.error(error instanceof Error ? error.message : 'Не удалось запустить формирование'),
  });

  const currentStage = useMemo(() => {
    if (generation?.ready) return 3;
    if (generation?.documents?.status === 'running' || generation?.documents?.status === 'waiting_review') return 2;
    if (preview?.status === 'ready') return 2;
    if (generation?.prepared && !generation.stale) return 1;
    return 0;
  }, [generation, preview]);

  const progress = Number(generation?.documents?.progress_percent || (generation?.ready ? 100 : 0));
  const isRunning = generation?.documents?.status === 'running' || generation?.documents?.status === 'waiting_review';

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {campaign.send_scenario === 'email_chain' && (
        <Alert
          showIcon
          type="info"
          message="Для цепочки генерация документов необязательна"
          description="Если письма цепочки должны содержать КП или договор, выберите шаблоны ниже и сформируйте комплект."
        />
      )}
      <Space direction="vertical" style={{ width: '100%' }}>
        <Typography.Text strong>Какие документы формируем</Typography.Text>
        <Select
          value={documentMode}
          style={{ width: '100%', maxWidth: 420 }}
          options={[
            { label: 'Только КП', value: 'kp' },
            { label: 'КП и договор', value: 'both' },
            { label: 'Только договор', value: 'contract' },
          ]}
          onChange={(value) => void saveDocumentSettings({ document_mode: value })}
        />
        {documentMode !== 'contract' && (
          <Space wrap>
            <Select
              value={kpTemplateId || undefined}
              loading={documentTemplatesQuery.isLoading}
              placeholder="Выберите шаблон КП (DOCX или PDF)"
              style={{ width: 420, maxWidth: '100%' }}
              options={kpOptions}
              onChange={(value) => void saveDocumentSettings({ kp_template_id: value })}
            />
            <Upload
              accept=".docx,.pdf"
              showUploadList={false}
              customRequest={async ({ file, onSuccess, onError }) => {
                try {
                  const uploaded = await templatesApi.uploadFile(file as File, 'document');
                  await saveDocumentSettings({ kp_template_id: uploaded.id });
                  await queryClient.invalidateQueries({ queryKey: ['templates', 'document'] });
                  message.success('Шаблон КП загружен и выбран');
                  onSuccess?.(uploaded);
                } catch (error) {
                  onError?.(error as Error);
                }
              }}
            >
              <Button icon={<UploadOutlined />}>Загрузить шаблон КП</Button>
            </Upload>
          </Space>
        )}
        {documentMode !== 'kp' && (
          <Space wrap>
            <Select
              value={contractTemplateId || undefined}
              loading={documentTemplatesQuery.isLoading}
              placeholder="Выберите шаблон договора (DOCX)"
              style={{ width: 420, maxWidth: '100%' }}
              options={contractOptions}
              onChange={(value) => void saveDocumentSettings({ contract_template_id: value })}
            />
            <Upload
              accept=".docx"
              showUploadList={false}
              customRequest={async ({ file, onSuccess, onError }) => {
                try {
                  const uploaded = await templatesApi.uploadFile(file as File, 'document');
                  await saveDocumentSettings({ contract_template_id: uploaded.id });
                  await queryClient.invalidateQueries({ queryKey: ['templates', 'document'] });
                  message.success('Шаблон договора загружен и выбран');
                  onSuccess?.(uploaded);
                } catch (error) {
                  onError?.(error as Error);
                }
              }}
            >
              <Button icon={<UploadOutlined />}>Загрузить шаблон договора</Button>
            </Upload>
          </Space>
        )}
      </Space>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
        Система подставит данные каждого получателя в выбранные шаблоны. КП будет подготовлено в PDF,
        договор — в DOCX. Исходники и результаты сохраняются отдельно.
      </Typography.Paragraph>

      <Steps
        current={currentStage}
        responsive
        items={[
          { title: 'Подготовить данные' },
          { title: 'Проверить пример' },
          { title: 'Сформировать' },
          { title: 'Готово' },
        ]}
      />

      {generation?.stale && (
        <Alert
          showIcon
          type="warning"
          message="Документы нужно пересобрать"
          description="После последней сборки изменились получатели, вид работ или шаблоны."
        />
      )}
      {generation?.ready && (
        <Alert
          showIcon
          type="success"
          message="Документы готовы к отправке"
          description={`Сформировано файлов: ${generation.documents?.output_file_count || 'готовый комплект'}.`}
        />
      )}
      {isRunning && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text>{generation?.documents?.stage_text || 'Формируем документы…'}</Typography.Text>
          <Progress percent={progress} status="active" />
        </Space>
      )}
      {generation?.documents?.status === 'error' && (
        <Alert
          showIcon
          type="error"
          message="Не удалось сформировать документы"
          description={generation.documents.error || generation.documents.summary_text || 'Проверьте шаблон и повторите подготовку.'}
        />
      )}
      {preview?.status === 'ready' && (
        <Alert
          showIcon
          type="info"
          message="Проверьте тестовый пример"
          description={preview.row_label ? `Пример собран для: ${preview.row_label}` : 'Откройте файл и убедитесь, что подстановка выглядит правильно.'}
          action={
            <Space wrap>
              {preview.pdf_url && (
                <Button icon={<EyeOutlined />} onClick={() => window.open(preview.pdf_url, '_blank', 'noopener,noreferrer')}>
                  Открыть PDF
                </Button>
              )}
              {preview.docx_url && (
                <Button onClick={() => window.open(preview.docx_url, '_blank', 'noopener,noreferrer')}>
                  Скачать DOCX
                </Button>
              )}
            </Space>
          }
        />
      )}
      {preview && preview.status !== 'ready' && (
        <Alert showIcon type="error" message="Пример не прошёл проверку" description={preview.failed_message || 'Исправьте шаблон и соберите пример повторно.'} />
      )}

      <Space wrap>
        <Button
          icon={<SyncOutlined />}
          loading={prepareMutation.isPending}
          disabled={!campaignId || isRunning}
          onClick={() => prepareMutation.mutate()}
        >
          {generation?.prepared ? 'Обновить данные и шаблоны' : 'Подготовить данные и шаблоны'}
        </Button>
        <Button
          icon={<EyeOutlined />}
          loading={previewMutation.isPending}
          disabled={!generation?.prepared || generation.stale || isRunning}
          onClick={() => previewMutation.mutate()}
        >
          Собрать тестовый пример
        </Button>
        <Button
          type="primary"
          icon={generation?.ready ? <CheckCircleOutlined /> : <FileDoneOutlined />}
          loading={startMutation.isPending}
          disabled={preview?.status !== 'ready' || isRunning || generation?.ready}
          onClick={() => startMutation.mutate()}
        >
          {generation?.ready ? 'Документы готовы' : 'Сформировать для всех получателей'}
        </Button>
        {generation?.manifest?.recipient_count !== undefined && (
          <Tag>Получателей: {generation.manifest.recipient_count}</Tag>
        )}
      </Space>
    </Space>
  );
}
