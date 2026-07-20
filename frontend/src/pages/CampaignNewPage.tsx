import {
  ProCard,
  ProForm,
  ProFormDateTimePicker,
  ProFormDigit,
  ProFormSelect,
  ProFormText,
} from '@ant-design/pro-components';
import { App, Button, Col, Collapse, Form, Row, Space, Steps, Table, Tag, Typography, Upload } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { campaignsApi } from '@/api/campaigns';
import { chainsApi } from '@/api/chains';
import { connectionsApi } from '@/api/connections';
import { audiencesApi } from '@/api/audiences';
import { RecipientGenerateModal } from '@/features/campaigns/RecipientGenerateModal';
import { ChainEmailPreviewModal } from '@/features/campaigns/ChainEmailPreviewModal';
import { VariableMappingModal } from '@/features/campaigns/VariableMappingModal';
import { useCampaignDraftStore } from '@/stores/campaignDraftStore';
import { validateCampaignBasics } from '@/utils/validators';
import {
  formValuesToSchedulePayload,
  scheduleToFormValues,
} from '@/utils/scheduleForm';
import { computeLocalSchedulePreview } from '@/utils/schedulePreview';

export function CampaignNewPage() {
  const [params] = useSearchParams();
  const existingId = params.get('id');
  const emailChainIdParam = params.get('email_chain_id');
  const navigate = useNavigate();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { campaignId, draft, setCampaignId, setDraft, replaceDraft, saveState, setSaveState } =
    useCampaignDraftStore();
  const [step, setStep] = useState(0);
  const [basicsForm] = Form.useForm();
  const [senderForm] = Form.useForm();
  const [scheduleForm] = Form.useForm();
  const [generateModalOpen, setGenerateModalOpen] = useState(false);
  const [mappingModalOpen, setMappingModalOpen] = useState(false);
  const [chainPreviewOpen, setChainPreviewOpen] = useState(false);
  const debounceRef = useRef<number | null>(null);
  const hydratedIdRef = useRef<string | null>(null);

  const createMutation = useMutation({
    mutationFn: () => campaignsApi.create({ name: 'Черновик рассылки', send_scenario: 'email_chain' }),
    onSuccess: (camp) => {
      setCampaignId(camp.id);
      replaceDraft(camp);
      navigate(`/campaigns/new?id=${camp.id}`, { replace: true });
    },
  });

  useEffect(() => {
    if (existingId) {
      setCampaignId(existingId);
      void campaignsApi.get(existingId).then((camp) => {
        replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
      });
      return;
    }
    if (!campaignId) {
      createMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existingId]);

  const id = existingId || campaignId;

  useEffect(() => {
    if (!id || !emailChainIdParam) return;
    void campaignsApi
      .update(id, { send_scenario: 'email_chain', email_chain_id: emailChainIdParam })
      .then((camp) => {
        replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
        basicsForm.setFieldsValue({ email_chain_id: emailChainIdParam });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, emailChainIdParam]);

  const watchedChainId = Form.useWatch('email_chain_id', basicsForm);
  const linkedChainId = emailChainIdParam || watchedChainId || draft.email_chain_id;

  useEffect(() => {
    if (!id) return;
    if (hydratedIdRef.current === id) return;
    if (!draft.id && draft.name === undefined) return;
    basicsForm.setFieldsValue(draft);
    senderForm.setFieldsValue({
      ...draft,
      connection_ids:
        draft.connection_ids?.length
          ? draft.connection_ids
          : draft.smtp_mailbox_id
            ? [draft.smtp_mailbox_id]
            : [],
    });
    hydratedIdRef.current = id;
  }, [id, draft.id, draft.name, draft.smtp_mailbox_id, draft.connection_ids, draft, basicsForm, senderForm]);

  const mailboxesQuery = useQuery({ queryKey: ['connections'], queryFn: () => connectionsApi.list() });
  const audiencesQuery = useQuery({ queryKey: ['audiences'], queryFn: () => audiencesApi.list() });
  const chainsQuery = useQuery({
    queryKey: ['chains'],
    queryFn: () => chainsApi.list({ limit: 100 }),
    staleTime: 0,
  });
  const recipientsQuery = useQuery({
    queryKey: ['campaign-recipients', id],
    queryFn: () => campaignsApi.recipients(id!, { limit: 100 }),
    enabled: Boolean(id),
  });
  const scheduleQuery = useQuery({
    queryKey: ['campaign-schedule', id],
    queryFn: () => campaignsApi.getSchedule(id!),
    enabled: Boolean(id),
  });
  const validateQuery = useQuery({
    queryKey: ['campaign-validate', id],
    queryFn: () => campaignsApi.validate(id!),
    enabled: Boolean(id),
    refetchInterval: 15_000,
  });

  const selectedSenderEmails = useMemo(() => {
    const ids =
      draft.connection_ids?.length
        ? draft.connection_ids
        : draft.smtp_mailbox_id
          ? [draft.smtp_mailbox_id]
          : [];
    return ids
      .map((connectionId) => (mailboxesQuery.data || []).find((item) => item.id === connectionId)?.email)
      .filter(Boolean);
  }, [draft.connection_ids, draft.smtp_mailbox_id, mailboxesQuery.data]);

  const recipientCount = recipientsQuery.data?.total || 0;

  const persist = async (patch: Record<string, unknown>) => {
    if (!id) return;
    setSaveState('saving');
    try {
      const updated = await campaignsApi.update(id, {
        ...patch,
        draft_payload: { ...(draft.draft_payload || {}), ...patch },
      });
      setDraft(updated);
      setSaveState('saved');
    } catch {
      setSaveState('error');
    }
  };

  useEffect(() => {
    if (!id) return;
    if (draft.send_scenario === 'email_chain') return;
    void persist({ send_scenario: 'email_chain' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, draft.send_scenario]);

  const autosave = (patch: Record<string, unknown>) => {
    setDraft(patch);
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      void persist(patch);
    }, 700);
  };

  const schedule = scheduleQuery.data;
  const scheduleSyncedIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!schedule || !id) return;
    if (scheduleSyncedIdRef.current && scheduleSyncedIdRef.current !== id) {
      scheduleSyncedIdRef.current = null;
    }
    const formValues = scheduleToFormValues(schedule);
    scheduleForm.setFieldsValue(formValues);
    const payload = formValuesToSchedulePayload(formValues);
    if (!payload) return;
    const needsSync =
      schedule.send_immediately ||
      !schedule.start_at ||
      schedule.interval_seconds !== payload.interval_seconds;
    if (!needsSync) {
      scheduleSyncedIdRef.current = id;
      return;
    }
    if (scheduleSyncedIdRef.current === id) return;
    scheduleSyncedIdRef.current = id;
    void campaignsApi.putSchedule(id, payload).then(() => {
      void queryClient.invalidateQueries({ queryKey: ['campaign-schedule', id] });
    });
  }, [schedule, scheduleForm, id, queryClient]);

  const watchedSchedule = Form.useWatch([], scheduleForm);
  const schedulePreview = useMemo(() => {
    const payload = formValuesToSchedulePayload(watchedSchedule || scheduleToFormValues(schedule));
    return computeLocalSchedulePreview({
      recipientCount: recipientsQuery.data?.total || 0,
      batchSize: payload?.batch_size || schedule?.batch_size || 25,
      intervalSeconds: payload?.interval_seconds || schedule?.interval_seconds || 3600,
    });
  }, [watchedSchedule, schedule, recipientsQuery.data?.total]);
  const batchCountPreview = schedulePreview.batchCount;

  const readinessErrors = [
    ...validateCampaignBasics(draft),
    ...(validateQuery.data?.errors || []),
  ];
  const mappingConfirmed = Boolean(
    validateQuery.data?.mapping_confirmed ??
      (draft.draft_payload as Record<string, unknown> | undefined)?.mapping_confirmed,
  );
  const launchBlocked = readinessErrors.length > 0;

  return (
    <Row gutter={16}>
      <Col xs={24} xl={16}>
        <ProCard bordered title="Создание рассылки" extra={<Tag>{saveState === 'saving' ? 'Сохранение…' : saveState === 'saved' ? 'Сохранено' : 'Черновик'}</Tag>}>
          <Steps
            current={step}
            onChange={setStep}
            items={[
              { title: 'Основное' },
              { title: 'Отправитель' },
              { title: 'Получатели' },
              { title: 'Расписание' },
              { title: 'Запуск' },
            ]}
            style={{ marginBottom: 24 }}
          />

          <Collapse
            accordion
            activeKey={String(step)}
            onChange={(key) => {
              const nextKey = Array.isArray(key) ? key[0] : key;
              if (nextKey !== undefined && nextKey !== '') {
                setStep(Number(nextKey));
              }
            }}
            items={[
              {
                key: '0',
                label: 'Основная информация',
                children: (
                  <ProForm
                    form={basicsForm}
                    submitter={false}
                    initialValues={draft}
                    onValuesChange={(_, values) => autosave(values)}
                  >
                    <ProFormText name="name" label="Название" rules={[{ required: true }]} />
                    {id ? (
                      <>
                        <ProFormSelect
                          name="email_chain_id"
                          label="Цепочка писем"
                          placeholder="Выберите цепочку"
                          options={(chainsQuery.data?.items || []).map((chain) => ({
                            label: chain.name,
                            value: chain.id,
                          }))}
                          fieldProps={{
                            loading: chainsQuery.isLoading,
                            onChange: (value: string) =>
                              autosave({ email_chain_id: value, send_scenario: 'email_chain' }),
                          }}
                          rules={[{ required: true, message: 'Выберите цепочку писем' }]}
                        />
                        <Space wrap>
                          {linkedChainId ? (
                            <Button
                              type="link"
                              onClick={() =>
                                navigate(`/chains/${linkedChainId}`, { state: { campaignId: id } })
                              }
                            >
                              Настроить цепочку писем
                            </Button>
                          ) : null}
                          <Button
                            type="link"
                            onClick={() => navigate('/chains', { state: { campaignId: id } })}
                          >
                            Создать цепочку
                          </Button>
                        </Space>
                      </>
                    ) : null}
                  </ProForm>
                ),
              },
              {
                key: '1',
                label: 'Отправитель',
                children: (
                  <ProForm
                    form={senderForm}
                    submitter={false}
                    initialValues={draft}
                    onValuesChange={(_, values) => autosave(values)}
                  >
                    <ProFormSelect
                      name="connection_ids"
                      label="Подключения отправителя"
                      placeholder="Выберите SMTP, RuSender или MailoPost"
                      fieldProps={{
                        mode: 'multiple',
                        allowClear: true,
                        showSearch: true,
                        optionFilterProp: 'label',
                        onChange: (values: string[]) => {
                          autosave({
                            connection_ids: values,
                            smtp_mailbox_id: values[0] || null,
                          });
                        },
                      }}
                      options={(mailboxesQuery.data || []).map((m) => ({
                        label: `${m.transport === 'smtp' ? 'SMTP' : m.transport === 'rusender' ? 'RuSender' : 'MailoPost'} · ${m.email}${m.is_default ? ' (по умолчанию)' : ''}`,
                        value: m.id,
                      }))}
                      rules={[{ required: true, message: 'Выберите подключение отправителя' }]}
                    />
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      Можно выбрать несколько. Отправка идёт через первое подключение; при достижении лимитов —
                      через следующее.
                    </Typography.Text>
                    <Button onClick={() => navigate('/connections')}>Управлять подключениями</Button>
                  </ProForm>
                ),
              },
              {
                key: '2',
                label: 'Получатели',
                children: (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <ProFormSelect
                      name="audience_id"
                      label="Сохранённая аудитория"
                      options={(audiencesQuery.data || []).map((a) => ({
                        label: `${a.name} (${a.member_count})`,
                        value: a.id,
                      }))}
                      fieldProps={{
                        onChange: async (audienceId: string) => {
                          if (!id || !audienceId) return;
                          await audiencesApi.useInCampaign(audienceId, id);
                          await persist({ audience_id: audienceId });
                          void queryClient.invalidateQueries({ queryKey: ['campaign-recipients', id] });
                          message.success('Аудитория загружена');
                        },
                      }}
                    />
                    <Space wrap>
                      <Upload
                        accept=".csv,.xlsx"
                        showUploadList={false}
                        customRequest={async ({ file, onSuccess, onError }) => {
                          try {
                            if (!id) return;
                            await campaignsApi.importRecipients(id, file as File);
                            void queryClient.invalidateQueries({ queryKey: ['campaign-recipients', id] });
                            message.success('Импорт выполнен');
                            onSuccess?.({});
                          } catch (error) {
                            onError?.(error as Error);
                          }
                        }}
                      >
                        <Button>Загрузить Excel / CSV</Button>
                      </Upload>
                      <Button
                        disabled={!id || !draft.job_id}
                        onClick={() => setGenerateModalOpen(true)}
                      >
                        Сгенерировать список
                      </Button>
                    </Space>
                    <Table
                      rowKey="id"
                      size="small"
                      dataSource={recipientsQuery.data?.items || []}
                      pagination={{ pageSize: 10 }}
                      columns={[
                        { title: 'Компания', dataIndex: 'company' },
                        { title: 'Контакт', dataIndex: 'contact_name' },
                        { title: 'Email', dataIndex: 'email' },
                        {
                          title: 'Проверка',
                          dataIndex: 'validation_status',
                          render: (v) => <Tag color={v === 'valid' ? 'green' : 'red'}>{v}</Tag>,
                        },
                        {
                          title: 'Исключён',
                          dataIndex: 'excluded',
                          render: (v) => (v ? 'да' : 'нет'),
                        },
                      ]}
                    />
                  </Space>
                ),
              },
              {
                key: '3',
                label: 'Расписание',
                children: (
                  <ProForm
                    form={scheduleForm}
                    submitter={false}
                    initialValues={scheduleToFormValues(schedule)}
                    onValuesChange={async (_, values) => {
                      if (!id) return;
                      const payload = formValuesToSchedulePayload(values);
                      if (!payload) return;
                      await campaignsApi.putSchedule(id, payload);
                      void queryClient.invalidateQueries({ queryKey: ['campaign-schedule', id] });
                    }}
                  >
                    <ProFormDigit name="batch_size" label="Размер пакета" min={1} fieldProps={{ precision: 0 }} />
                    <ProFormDateTimePicker
                      name="start_at"
                      label="Дата и время старта"
                      rules={[{ required: true, message: 'Укажите дату и время старта' }]}
                      fieldProps={{ style: { width: '100%' }, format: 'DD.MM.YYYY HH:mm' }}
                    />
                    <Form.Item label="Интервал между пакетами" required>
                      <Space align="start">
                        <ProFormDigit
                          name="interval_value"
                          min={1}
                          width="sm"
                          fieldProps={{ precision: 0 }}
                          rules={[{ required: true, message: 'Укажите интервал' }]}
                          formItemProps={{ style: { marginBottom: 0 } }}
                        />
                        <ProFormSelect
                          name="interval_unit"
                          width="sm"
                          options={[
                            { label: 'часы', value: 'hours' },
                            { label: 'дни', value: 'days' },
                          ]}
                          rules={[{ required: true }]}
                          formItemProps={{ style: { marginBottom: 0 } }}
                        />
                      </Space>
                    </Form.Item>
                    <Typography.Text>
                      Прогноз: {schedulePreview.batchCount} пакетов
                      {schedulePreview.estimatedDurationSeconds > 0
                        ? `, длительность ≈ ${Math.round(schedulePreview.estimatedDurationSeconds / 3600)} ч`
                        : ''}
                    </Typography.Text>
                  </ProForm>
                ),
              },
              {
                key: '4',

                label: 'Проверка и запуск',
                children: (
                  <Space direction="vertical">
                    {(validateQuery.data?.warnings || []).map((w) => (
                      <Tag key={w} color="gold">
                        {w}
                      </Tag>
                    ))}
                    {readinessErrors.map((e) => (
                      <Tag key={e} color="red">
                        {e}
                      </Tag>
                    ))}
                    <Space wrap>
                      <Button
                        type="primary"
                        onClick={() => setMappingModalOpen(true)}
                      >
                        Сохранить
                      </Button>
                      <Button
                        onClick={async () => {
                          if (!id) return;
                          const to = window.prompt('Email для теста');
                          if (!to) return;
                          await campaignsApi.testEmail(id, to);
                          message.success('Тестовое письмо отправлено');
                        }}
                      >
                        Тестовое письмо
                      </Button>
                      {linkedChainId ? (
                        <Button
                          disabled={recipientCount === 0}
                          onClick={() => setChainPreviewOpen(true)}
                        >
                          Предпросмотр цепочки
                        </Button>
                      ) : null}
                      <Button
                        type="primary"
                        disabled={launchBlocked}
                        title={readinessErrors.join('; ') || undefined}
                        onClick={async () => {
                          if (!id) return;
                          await campaignsApi.launch(id, true);
                          message.success('Рассылка запущена');
                          navigate(`/campaigns/${id}`);
                        }}
                      >
                        Запустить сейчас
                      </Button>
                      <Button
                        disabled={launchBlocked}
                        onClick={async () => {
                          if (!id) return;
                          await campaignsApi.launch(id, false);
                          message.success('Рассылка запланирована');
                          navigate(`/campaigns/${id}`);
                        }}
                      >
                        Запланировать
                      </Button>
                    </Space>
                  </Space>
                ),
              },
            ]}
          />
        </ProCard>
      </Col>
      <Col xs={24} xl={8}>
        <ProCard
          title="Готовность"
          bordered
          style={{ position: 'sticky', top: 72 }}
        >
          <Space direction="vertical">
            <Typography.Text>Получателей: {recipientsQuery.data?.total || 0}</Typography.Text>
            <Typography.Text>Исключено: {validateQuery.data?.excluded_recipients || 0}</Typography.Text>
            <Typography.Text>Пакетов (прогноз): {batchCountPreview}</Typography.Text>
            <Typography.Text>
              Отправители:{' '}
              {selectedSenderEmails.length > 0 ? selectedSenderEmails.join(', ') : 'не выбраны'}
            </Typography.Text>
            <Typography.Text>
              Сопоставление переменных:{' '}
              {mappingConfirmed ? (
                <Tag color="green">подтверждено</Tag>
              ) : (
                <Tag color="gold">требуется</Tag>
              )}
            </Typography.Text>
            {readinessErrors.length === 0 ? (
              <Tag color="green">Готово к запуску</Tag>
            ) : (
              <Tag color="red">Есть критические ошибки</Tag>
            )}
          </Space>
        </ProCard>
      </Col>
      {id && draft.job_id ? (
        <RecipientGenerateModal
          open={generateModalOpen}
          campaignId={id}
          jobId={draft.job_id}
          onClose={() => setGenerateModalOpen(false)}
          onImported={() => {
            void queryClient.invalidateQueries({ queryKey: ['campaign-recipients', id] });
            void queryClient.invalidateQueries({ queryKey: ['campaign-validate', id] });
          }}
        />
      ) : null}
      {id ? (
        <VariableMappingModal
          open={mappingModalOpen}
          campaignId={id}
          onClose={() => setMappingModalOpen(false)}
          onConfirmed={() => {
            void queryClient.invalidateQueries({ queryKey: ['campaign-validate', id] });
            void campaignsApi.get(id).then((camp) => {
              replaceDraft({ ...camp, ...(camp.draft_payload || {}) });
            });
            message.success('Сопоставление переменных сохранено');
          }}
        />
      ) : null}
      {id && linkedChainId ? (
        <ChainEmailPreviewModal
          open={chainPreviewOpen}
          campaignId={id}
          onClose={() => setChainPreviewOpen(false)}
        />
      ) : null}
    </Row>
  );
}
