import { PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { ProCard, ProForm, ProFormDigit, ProFormSelect, ProFormSwitch, ProFormText, ProFormTextArea } from '@ant-design/pro-components';
import { App, Button, Col, Collapse, Divider, Form, Modal, Row, Space, Steps, Table, Tag, Typography, Upload } from 'antd';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { campaignsApi } from '@/api/campaigns';
import { connectionsApi } from '@/api/connections';
import { templatesApi } from '@/api/templates';
import { audiencesApi } from '@/api/audiences';
import { workTypesApi, type WorkTypeOption } from '@/api/workTypes';
import { useCampaignDraftStore } from '@/stores/campaignDraftStore';
import { validateCampaignBasics } from '@/utils/validators';
import { computeLocalSchedulePreview } from '@/utils/schedulePreview';

export function CampaignNewPage() {
  const [params] = useSearchParams();
  const existingId = params.get('id');
  const navigate = useNavigate();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { campaignId, draft, setCampaignId, setDraft, replaceDraft, saveState, setSaveState } =
    useCampaignDraftStore();
  const [step, setStep] = useState(0);
  const [basicsForm] = Form.useForm();
  const [senderForm] = Form.useForm();
  const [docsForm] = Form.useForm();
  const [scheduleForm] = Form.useForm();
  const [workTypeForm] = Form.useForm();
  const [workTypeModalOpen, setWorkTypeModalOpen] = useState(false);
  const debounceRef = useRef<number | null>(null);
  const hydratedIdRef = useRef<string | null>(null);

  const createMutation = useMutation({
    mutationFn: () => campaignsApi.create({ name: 'Черновик рассылки' }),
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
    if (!id) return;
    if (hydratedIdRef.current === id) return;
    if (!draft.id && draft.name === undefined) return;
    basicsForm.setFieldsValue(draft);
    senderForm.setFieldsValue(draft);
    docsForm.setFieldsValue(draft);
    hydratedIdRef.current = id;
  }, [id, draft.id, draft.name, draft.mail_subject, draft.smtp_mailbox_id, draft, basicsForm, senderForm, docsForm]);

  const mailboxesQuery = useQuery({ queryKey: ['connections'], queryFn: () => connectionsApi.list() });
  const workTypesQuery = useQuery({
    queryKey: ['work-types'],
    queryFn: () => workTypesApi.list(),
  });
  const templatesQuery = useQuery({
    queryKey: ['templates-email'],
    queryFn: () => templatesApi.list({ template_type: 'email' }),
  });
  const kpTemplatesQuery = useQuery({
    queryKey: ['templates', 'kp'],
    queryFn: () => templatesApi.list({ template_type: 'kp' }),
  });
  const contractTemplatesQuery = useQuery({
    queryKey: ['templates', 'contract'],
    queryFn: () => templatesApi.list({ template_type: 'contract' }),
  });
  const audiencesQuery = useQuery({ queryKey: ['audiences'], queryFn: () => audiencesApi.list() });
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

  const autosave = (patch: Record<string, unknown>) => {
    setDraft(patch);
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      void persist(patch);
    }, 700);
  };

  const createWorkTypeMutation = useMutation({
    mutationFn: (values: { name: string; mail_subject: string }) => workTypesApi.create(values),
    onSuccess: (item) => {
      queryClient.setQueryData<WorkTypeOption[]>(['work-types'], (current = []) => [
        ...current,
        item,
      ]);
      const values = {
        ...basicsForm.getFieldsValue(),
        work_type: item.key,
        mail_subject: item.mail_subject,
      };
      basicsForm.setFieldsValue(values);
      autosave(values);
      setWorkTypeModalOpen(false);
      workTypeForm.resetFields();
      message.success('Вид работ добавлен и выбран');
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось добавить вид работ');
    },
  });

  const schedule = scheduleQuery.data;

  useEffect(() => {
    if (schedule) {
      scheduleForm.setFieldsValue(schedule);
    }
  }, [schedule, scheduleForm]);

  const preview = useMemo(
    () =>
      computeLocalSchedulePreview({
        recipientCount: recipientsQuery.data?.total || 0,
        batchSize: schedule?.batch_size || 25,
        intervalSeconds: schedule?.interval_seconds || 300,
        maxPerHour: schedule?.max_per_hour,
        maxPerDay: schedule?.max_per_day,
      }),
    [recipientsQuery.data?.total, schedule],
  );

  const workTypeOptions = useMemo(() => {
    const items = workTypesQuery.data || [];
    const options = items.map((item) => ({ label: item.name, value: item.key }));
    if (draft.work_type && !items.some((item) => item.key === draft.work_type)) {
      options.unshift({
        label: `${draft.work_type} (ранее введённое значение)`,
        value: draft.work_type,
      });
    }
    return options;
  }, [draft.work_type, workTypesQuery.data]);

  const readinessErrors = [
    ...validateCampaignBasics(draft),
    ...(validateQuery.data?.errors || []),
  ];

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
              { title: 'Документы' },
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
                    onValuesChange={(changed, values) => {
                      if (Object.prototype.hasOwnProperty.call(changed, 'work_type')) {
                        const selected = workTypesQuery.data?.find(
                          (item) => item.key === values.work_type,
                        );
                        if (selected) {
                          const nextValues = {
                            ...values,
                            mail_subject: selected.mail_subject,
                          };
                          basicsForm.setFieldValue('mail_subject', selected.mail_subject);
                          autosave(nextValues);
                          return;
                        }
                      }
                      autosave(values);
                    }}
                  >
                    <ProFormText name="name" label="Название" rules={[{ required: true }]} />
                    <ProFormSelect
                      name="work_type"
                      label="Вид работ"
                      options={workTypeOptions}
                      fieldProps={{
                        showSearch: true,
                        optionFilterProp: 'label',
                        loading: workTypesQuery.isLoading,
                        popupRender: (menu) => (
                          <>
                            {menu}
                            <Divider style={{ margin: '8px 0' }} />
                            <Button
                              type="text"
                              block
                              icon={<PlusOutlined />}
                              onMouseDown={(event) => event.preventDefault()}
                              onClick={() => setWorkTypeModalOpen(true)}
                            >
                              Добавить вид работ
                            </Button>
                          </>
                        ),
                      }}
                    />
                    <ProFormSelect
                      name="document_mode"
                      label="Тип документов"
                      options={[
                        { label: 'Только КП', value: 'kp' },
                        { label: 'КП и договор', value: 'both' },
                        { label: 'Только договор', value: 'contract' },
                      ]}
                    />
                    <ProFormText name="mail_subject" label="Тема письма" rules={[{ required: true }]} />
                    <ProFormTextArea name="description" label="Описание" />
                    <ProFormSelect
                      name="send_scenario"
                      label="Сценарий отправки"
                      options={[
                        { label: 'Запрос согласия и автоотправка материалов', value: 'consent_then_materials' },
                        { label: 'Немедленная отправка материалов', value: 'materials_now' },
                      ]}
                    />
                    <ProFormTextArea name="internal_comment" label="Внутренний комментарий" />
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
                      name="smtp_mailbox_id"
                      label="Подключение отправителя"
                      placeholder="Выберите SMTP, RuSender или MailoPost"
                      options={(mailboxesQuery.data || []).map((m) => ({
                        label: `${m.transport === 'smtp' ? 'SMTP' : m.transport === 'rusender' ? 'RuSender' : 'MailoPost'} · ${m.email}${m.is_default ? ' (по умолчанию)' : ''}`,
                        value: m.id,
                      }))}
                      fieldProps={{
                        onChange: (value: string) => {
                          const connection = (mailboxesQuery.data || []).find((item) => item.id === value);
                          if (!connection) return;
                          senderForm.setFieldValue('transport', connection.transport);
                          autosave({ smtp_mailbox_id: value, transport: connection.transport });
                        },
                      }}
                      rules={[{ required: true, message: 'Выберите подключение отправителя' }]}
                    />
                    <ProFormSelect
                      name="transport"
                      label="Способ отправки"
                      disabled
                      options={[
                        { label: 'SMTP', value: 'smtp' },
                        { label: 'RuSender', value: 'rusender' },
                        { label: 'MailoPost', value: 'mailopost' },
                      ]}
                    />
                    <Button onClick={() => navigate('/connections')}>Управлять подключениями</Button>
                  </ProForm>
                ),
              },
              {
                key: '2',
                label: 'Документы',
                children: (
                  <ProForm
                    form={docsForm}
                    submitter={false}
                    initialValues={draft}
                    onValuesChange={(_, values) => autosave(values)}
                  >
                    <ProFormSelect
                      name="email_template_id"
                      label="Шаблон письма"
                      options={(templatesQuery.data || []).map((template) => ({ label: template.name, value: template.id }))}
                    />
                    <ProFormTextArea
                      name="email_body"
                      label="Текст письма (можно сохранить в черновик)"
                      fieldProps={{
                        onChange: (event) => autosave({ draft_payload: { email_body: event.target.value } }),
                      }}
                    />

                    {draft.document_mode !== 'contract' && (
                      <ProCard title="Коммерческое предложение" bordered size="small">
                        <ProFormSelect
                          name="kp_template_id"
                          label="Шаблон КП"
                          options={(kpTemplatesQuery.data || []).filter((template) => template.version?.filename).map((template) => ({
                            label: template.version?.filename
                              ? `${template.name} — ${template.version.filename}`
                              : template.name,
                            value: template.id,
                          }))}
                        />
                        <Space wrap>
                          <Upload
                            accept=".docx,.pdf,.html,.htm"
                            maxCount={1}
                            showUploadList={false}
                            customRequest={async ({ file, onSuccess, onError }) => {
                              try {
                                const uploaded = await templatesApi.uploadFile(file as File, 'kp');
                                docsForm.setFieldValue('kp_template_id', uploaded.id);
                                await persist({ kp_template_id: uploaded.id });
                                void queryClient.invalidateQueries({ queryKey: ['templates', 'kp'] });
                                message.success('Шаблон КП загружен и выбран');
                                onSuccess?.(uploaded);
                              } catch (error) {
                                message.error(error instanceof Error ? error.message : 'Не удалось загрузить шаблон КП');
                                onError?.(error as Error);
                              }
                            }}
                          >
                            <Button icon={<UploadOutlined />}>Загрузить свой шаблон КП</Button>
                          </Upload>
                          {draft.kp_template_id && (
                            <Button
                              onClick={() =>
                                window.open(
                                  templatesApi.previewFileUrl(String(draft.kp_template_id)),
                                  '_blank',
                                  'noopener,noreferrer',
                                )
                              }
                            >
                              Предпросмотр
                            </Button>
                          )}
                        </Space>
                        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
                          Поддерживаются DOCX, PDF и HTML. Загруженный файл сохранится в библиотеке.
                        </Typography.Paragraph>
                      </ProCard>
                    )}

                    {draft.document_mode !== 'kp' && (
                      <ProCard title="Договор" bordered size="small">
                        <ProFormSelect
                          name="contract_template_id"
                          label="Шаблон договора"
                          options={(contractTemplatesQuery.data || []).filter((template) => template.version?.filename).map((template) => ({
                            label: template.version?.filename
                              ? `${template.name} — ${template.version.filename}`
                              : template.name,
                            value: template.id,
                          }))}
                        />
                        <Space wrap>
                          <Upload
                            accept=".docx"
                            maxCount={1}
                            showUploadList={false}
                            customRequest={async ({ file, onSuccess, onError }) => {
                              try {
                                const uploaded = await templatesApi.uploadFile(file as File, 'contract');
                                docsForm.setFieldValue('contract_template_id', uploaded.id);
                                await persist({ contract_template_id: uploaded.id });
                                void queryClient.invalidateQueries({ queryKey: ['templates', 'contract'] });
                                message.success('Шаблон договора загружен и выбран');
                                onSuccess?.(uploaded);
                              } catch (error) {
                                message.error(
                                  error instanceof Error ? error.message : 'Не удалось загрузить шаблон договора',
                                );
                                onError?.(error as Error);
                              }
                            }}
                          >
                            <Button icon={<UploadOutlined />}>Загрузить свой шаблон договора</Button>
                          </Upload>
                          {draft.contract_template_id && (
                            <Button
                              onClick={() =>
                                window.open(
                                  templatesApi.previewFileUrl(String(draft.contract_template_id)),
                                  '_blank',
                                  'noopener,noreferrer',
                                )
                              }
                            >
                              Предпросмотр
                            </Button>
                          )}
                        </Space>
                        <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
                          Для договора поддерживается формат DOCX.
                        </Typography.Paragraph>
                      </ProCard>
                    )}

                    <Button onClick={() => navigate('/templates')}>Открыть библиотеку шаблонов</Button>
                  </ProForm>
                ),
              },
              {
                key: '3',
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
                key: '4',
                label: 'Расписание',
                children: (
                  <ProForm
                    form={scheduleForm}
                    submitter={false}
                    initialValues={schedule || { send_immediately: true, batch_size: 25, interval_seconds: 300 }}
                    onValuesChange={async (_, values) => {
                      if (!id) return;
                      await campaignsApi.putSchedule(id, values);
                      void queryClient.invalidateQueries({ queryKey: ['campaign-schedule', id] });
                    }}
                  >
                    <ProFormSwitch name="send_immediately" label="Отправить сейчас" />
                    <ProFormDigit name="batch_size" label="Размер пакета" min={1} />
                    <ProFormDigit name="interval_seconds" label="Интервал между пакетами (сек)" min={0} />
                    <ProFormDigit name="max_per_hour" label="Макс. в час (0 = без лимита)" min={0} />
                    <ProFormDigit name="max_per_day" label="Макс. в день (0 = без лимита)" min={0} />
                    <ProFormDigit name="pause_between_messages_ms" label="Пауза между письмами (мс)" min={0} />
                    <ProFormSelect
                      name="on_error"
                      label="Поведение при ошибке"
                      options={[
                        { label: 'Повторить', value: 'retry' },
                        { label: 'Пропустить', value: 'skip' },
                        { label: 'Пауза', value: 'pause' },
                      ]}
                    />
                    <Typography.Paragraph>
                      Прогноз: {preview.batchCount} пакетов, длительность ≈ {preview.estimatedDurationSeconds}с
                    </Typography.Paragraph>
                  </ProForm>
                ),
              },
              {
                key: '5',
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
                        onClick={async () => {
                          if (!id) return;
                          await persist(draft);
                          message.success('Черновик сохранён');
                        }}
                      >
                        Сохранить черновик
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
                      <Button
                        type="primary"
                        disabled={readinessErrors.length > 0}
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
                        disabled={readinessErrors.length > 0}
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
            <Typography.Text>Пакетов (прогноз): {preview.batchCount}</Typography.Text>
            <Typography.Text>
              Отправитель:{' '}
              {(mailboxesQuery.data || []).find((item) => item.id === draft.smtp_mailbox_id)?.email ||
                'не выбран'}
            </Typography.Text>
            <Typography.Text>Тема: {draft.mail_subject || '—'}</Typography.Text>
            {readinessErrors.length === 0 ? (
              <Tag color="green">Готово к запуску</Tag>
            ) : (
              <Tag color="red">Есть критические ошибки</Tag>
            )}
          </Space>
        </ProCard>
      </Col>
      <Modal
        title="Новый вид работ"
        open={workTypeModalOpen}
        confirmLoading={createWorkTypeMutation.isPending}
        okText="Добавить"
        cancelText="Отмена"
        destroyOnHidden
        onCancel={() => {
          setWorkTypeModalOpen(false);
          workTypeForm.resetFields();
        }}
        onOk={async () => {
          try {
            const values = await workTypeForm.validateFields();
            createWorkTypeMutation.mutate(values);
          } catch {
            // Ant Design displays validation errors next to the fields.
          }
        }}
      >
        <Typography.Paragraph type="secondary">
          Тема будет автоматически подставляться при выборе этого вида работ.
        </Typography.Paragraph>
        <ProForm form={workTypeForm} submitter={false} layout="vertical">
          <ProFormText
            name="name"
            label="Название вида работ"
            rules={[{ required: true, message: 'Укажите название' }]}
          />
          <ProFormText
            name="mail_subject"
            label="Тема письма по умолчанию"
            rules={[{ required: true, message: 'Укажите тему письма' }]}
          />
        </ProForm>
      </Modal>
    </Row>
  );
}
