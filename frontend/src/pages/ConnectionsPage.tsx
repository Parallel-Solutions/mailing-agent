import { PlusOutlined } from '@ant-design/icons';
import { ModalForm, ProFormDigit, ProFormSelect, ProFormText, ProTable } from '@ant-design/pro-components';
import { Alert, App, Button, Form, Input, Popconfirm, Space, Steps, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { connectionsApi } from '@/api/connections';
import type { SmtpSetupAnalysis, SmtpSetupSettings } from '@/api/connections';
import type { DeliveryConnection } from '@/api/types';
import { selectSmtpSetupSettings, smtpSetupSecurity } from '@/utils/smtpSetup';

type ConnectionTransport = 'smtp' | 'rusender' | 'mailopost';
type SmtpSecurity = 'none' | 'tls' | 'starttls';
type SmtpSetupStage = 'email' | 'credentials' | 'manual';

const SECURITY_PORTS: Record<SmtpSecurity, number> = {
  none: 25,
  tls: 465,
  starttls: 587,
};

const PROVIDER_LABELS: Record<ConnectionTransport, string> = {
  smtp: 'SMTP',
  rusender: 'RuSender',
  mailopost: 'MailoPost',
};
const SMTP_PROVIDER_LABELS: Record<string, string> = {
  gmail: 'Gmail',
  outlook: 'Outlook / Microsoft 365',
  yandex: 'Яндекс',
  mailru: 'Почта Mail',
  custom: 'SMTP',
};



function connectionLabel(connection: DeliveryConnection) {
  if (connection.transport === 'smtp') {
    return SMTP_PROVIDER_LABELS[connection.provider || 'custom'] || 'SMTP';
  }
  return PROVIDER_LABELS[connection.transport];
}

function smtpSecurity(connection: DeliveryConnection): SmtpSecurity {
  if (connection.use_ssl) return 'tls';
  if (connection.use_starttls) return 'starttls';
  return 'none';
}


function EditConnectionAction({
  connection,
  onSaved,
}: {
  connection: DeliveryConnection;
  onSaved: () => void;
}) {
  const { message } = App.useApp();
  const [editForm] = Form.useForm();
  const [isSecretEditing, setIsSecretEditing] = useState(false);

  const renderSecretEditor = (
    fieldName: 'password' | 'api_token',
    label: string,
    changeLabel: string,
  ) => {
    if (isSecretEditing) {
      return (
        <ProFormText.Password
          name={fieldName}
          label={label}
          rules={[{ required: true, message: `Введите ${label.toLowerCase()}` }]}
          fieldProps={{ autoComplete: 'new-password' }}
          extra={(
            <Button
              type="link"
              size="small"
              onClick={() => {
                editForm.setFieldValue(fieldName, undefined);
                setIsSecretEditing(false);
              }}
            >
              Отменить изменение
            </Button>
          )}
        />
      );
    }

    return (
      <Form.Item label={label}>
        <Space direction="vertical" size={4} style={{ width: '100%' }}>
          <Space.Compact block>
            <Input.Password
              value={connection.has_secret ? '••••••••••••' : ''}
              placeholder="Секрет не задан"
              readOnly
              visibilityToggle={false}
              aria-label={`${label}: сохранённое значение`}
            />
            <Button onClick={() => setIsSecretEditing(true)}>{changeLabel}</Button>
          </Space.Compact>
          <Typography.Text type="secondary">
            {connection.has_secret ? 'Сохранён и используется' : 'Секрет не задан'}
          </Typography.Text>
        </Space>
      </Form.Item>
    );
  };
  const isMail = connection.transport === 'smtp' && connection.provider === 'mailru';

  return (
    <ModalForm
      form={editForm}
      title={`Редактировать ${connectionLabel(connection)}`}
      modalProps={{ okText: 'Сохранить', cancelText: 'Отмена', destroyOnHidden: true }}
      trigger={<a>Редактировать</a>}
      onOpenChange={(open) => {
        if (!open) {
          editForm.resetFields(['password', 'api_token']);
          setIsSecretEditing(false);
        }
      }}
      initialValues={{
        transport: connection.transport,
        email: connection.email,
        sender_name: connection.sender_name,
        host: connection.host,
        port: connection.port,
        security: smtpSecurity(connection),
        api_base_url: connection.api_base_url,
        password: '',
        api_token: '',
      }}
      onFinish={async (values) => {
        if (isMail) {
          await connectionsApi.update(connection.id, {
            transport: 'smtp',
            email: values.email,
            sender_name: values.sender_name,
            ...(isSecretEditing ? { password: values.password } : {}),
          });
        } else {
          const security = values.security as SmtpSecurity | undefined;
          const {
            security: _security,
            password: _password,
            api_token: _apiToken,
            ...connectionValues
          } = values;
          await connectionsApi.update(connection.id, {
            ...connectionValues,
            transport: connection.transport,
            use_ssl: connection.transport === 'smtp' ? security === 'tls' : undefined,
            use_starttls: connection.transport === 'smtp' ? security === 'starttls' : undefined,
            ...(isSecretEditing && connection.transport === 'smtp'
              ? { password: values.password }
              : {}),
            ...(isSecretEditing && connection.transport !== 'smtp'
              ? { api_token: values.api_token }
              : {}),
          });
        }
        message.success('Подключение обновлено');
        onSaved();
        return true;
      }}
    >
      <ProFormSelect
        name="transport"
        label="Способ отправки"
        disabled
        options={[{ label: connectionLabel(connection), value: connection.transport }]}
      />
      <ProFormText
        name="email"
        label={connection.transport === 'smtp' ? 'Email почтового ящика' : 'Подтверждённый email отправителя'}
        rules={[{ required: true, type: 'email' }]}
      />
      <ProFormText name="sender_name" label="Имя отправителя" />

      {connection.transport === 'smtp' ? (
        isMail ? (
          <>
            {renderSecretEditor(
              'password',
              isSecretEditing ? 'Новый пароль для внешнего приложения' : 'Пароль для внешнего приложения',
              'Изменить пароль',
            )}
            <Alert
              type="info"
              showIcon
              message="Для Почты Mail нужен отдельный пароль"
              description={
                <span>
                  Создайте пароль в разделе «Безопасность → Пароли для внешних приложений».{' '}
                  <Typography.Link
                    href="https://help.mail.ru/mail/login/mailer/"
                    target="_blank"
                  >
                    Открыть инструкцию Mail
                  </Typography.Link>
                </span>
              }
            />
          </>
        ) : (
          <>
          {renderSecretEditor(
            'password',
            isSecretEditing ? 'Новый пароль SMTP' : 'Пароль SMTP',
            'Изменить пароль',
          )}
          <ProFormText name="host" label="SMTP-сервер" rules={[{ required: true }]} />
          <ProFormSelect
            name="security"
            label="Защита соединения"
            options={[
              { label: 'Без шифрования', value: 'none' },
              { label: 'TLS — обычно порт 465', value: 'tls' },
              { label: 'STARTTLS — обычно порт 587', value: 'starttls' },
            ]}
            fieldProps={{
              onChange: (value: SmtpSecurity) => editForm.setFieldValue('port', SECURITY_PORTS[value]),
            }}
            rules={[{ required: true }]}
          />
          <ProFormDigit
            name="port"
            label="Порт SMTP"
            fieldProps={{ min: 1, max: 65535, precision: 0 }}
            rules={[{ required: true }]}
          />
          </>
        )
      ) : (
        <>
          {renderSecretEditor(
            'api_token',
            connection.transport === 'rusender'
              ? `${isSecretEditing ? 'Новый ' : ''}API-ключ RuSender`
              : `${isSecretEditing ? 'Новый ' : ''}API-токен MailoPost`,
            connection.transport === 'rusender' ? 'Изменить ключ' : 'Изменить токен',
          )}
          <ProFormText
            name="api_base_url"
            label="Адрес API"
            rules={[{ required: true, type: 'url' }]}
          />
        </>
      )}
    </ModalForm>
  );
}

export function ConnectionsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const [transport, setTransport] = useState<ConnectionTransport>('smtp');
  const [smtpSetupStage, setSmtpSetupStage] = useState<SmtpSetupStage>('email');
  const [smtpAnalysis, setSmtpAnalysis] = useState<SmtpSetupAnalysis | null>(null);
  const [smtpSetupSettings, setSmtpSetupSettings] = useState<SmtpSetupSettings | null>(null);
  const [smtpSetupError, setSmtpSetupError] = useState('');
  const [isAnalyzingSmtp, setIsAnalyzingSmtp] = useState(false);

  const resetSmtpWizard = () => {
    setSmtpSetupStage('email');
    setSmtpAnalysis(null);
    setSmtpSetupSettings(null);
    setSmtpSetupError('');
    setIsAnalyzingSmtp(false);
  };

  const applySmtpSettings = (settings: SmtpSetupSettings) => {
    setSmtpSetupSettings(settings);
    form.setFieldsValue({
      host: settings.host,
      port: settings.port,
      security: smtpSetupSecurity(settings),
    });
  };

  const analyzeSmtpEmail = async () => {
    const values = await form.validateFields(['email']);
    const email = String(values.email || '').trim().toLowerCase();
    setIsAnalyzingSmtp(true);
    setSmtpSetupError('');
    try {
      const analysis = await connectionsApi.analyzeSmtp(email);
      setSmtpAnalysis(analysis);
      form.setFieldValue('smtp_username', email);
      const recommended = selectSmtpSetupSettings(analysis);
      if (recommended?.host) {
        applySmtpSettings(recommended);
        setSmtpSetupStage('credentials');
      } else {
        const manualSettings: SmtpSetupSettings = {
          provider: 'custom',
          host: '',
          port: 587,
          use_ssl: false,
          use_starttls: true,
        };
        applySmtpSettings(manualSettings);
        setSmtpSetupError('Автоматические настройки не найдены. Укажите данные SMTP-сервера вручную.');
        setSmtpSetupStage('manual');
      }
    } catch (error) {
      const manualSettings: SmtpSetupSettings = {
        provider: 'custom',
        host: '',
        port: 587,
        use_ssl: false,
        use_starttls: true,
      };
      applySmtpSettings(manualSettings);
      form.setFieldValue('smtp_username', email);
      setSmtpSetupError(
        error instanceof Error
          ? error.message
          : 'Не удалось определить провайдера. Укажите настройки вручную.',
      );
      setSmtpSetupStage('manual');
    } finally {
      setIsAnalyzingSmtp(false);
    }
  };
  const { data, isLoading } = useQuery({
    queryKey: ['connections'],
    queryFn: () => connectionsApi.list(),
  });

  const refreshConnections = () => {
    void queryClient.invalidateQueries({ queryKey: ['connections'] });
  };

  const removeMutation = useMutation({
    mutationFn: (id: string) => connectionsApi.remove(id),
    onSuccess: () => {
      message.success('Подключение удалено');
      refreshConnections();
    },
    onError: (error: Error) => message.error(error.message),
  });

  return (
    <ProTable<DeliveryConnection>
      rowKey="id"
      loading={isLoading}
      search={false}
      headerTitle="Подключения отправителей"
      toolBarRender={() => [
        <ModalForm
          key="add"
          form={form}
          title="Добавить подключение"
          modalProps={{ okText: 'Проверить и подключить', cancelText: 'Отмена', destroyOnHidden: true }}
          submitter={{
            searchConfig: { submitText: 'Проверить и подключить' },
            submitButtonProps: {
              disabled: transport === 'smtp' && smtpSetupStage === 'email',
              loading: isAnalyzingSmtp,
            },
          }}
          trigger={
            <Button type="primary" icon={<PlusOutlined />}>
              Добавить
            </Button>
          }
          initialValues={{
            transport: 'smtp',
            email: '',
            sender_name: '',
            smtp_username: '',
            security: 'starttls',
            port: 587,
          }}
          onOpenChange={(open) => {
            if (!open) {
              setTransport('smtp');
              resetSmtpWizard();
              form.resetFields();
            }
          }}
          onFinish={async (values) => {
            if (transport === 'smtp') {
              const security = (values.security || smtpSetupSecurity(
                smtpSetupSettings || {
                  provider: 'custom',
                  host: '',
                  port: 587,
                  use_ssl: false,
                  use_starttls: true,
                },
              )) as SmtpSecurity;
              const attemptedSettings: SmtpSetupSettings = {
                provider: smtpSetupStage === 'manual'
                  ? 'custom'
                  : (smtpSetupSettings?.provider || 'custom'),
                host: String(values.host || smtpSetupSettings?.host || '').trim(),
                port: Number(values.port || smtpSetupSettings?.port || SECURITY_PORTS[security]),
                use_ssl: security === 'tls',
                use_starttls: security === 'starttls',
              };
              if (!attemptedSettings.host) {
                setSmtpSetupStage('manual');
                setSmtpSetupError('Укажите SMTP-сервер.');
                return false;
              }

              let verification;
              try {
                verification = await connectionsApi.verifySmtp({
                  setup_session_id: smtpAnalysis?.setup_session_id || ('manual-' + Date.now()),
                  email: values.email,
                  password: values.password,
                  provider: attemptedSettings.provider,
                  host: attemptedSettings.host,
                  port: attemptedSettings.port,
                  use_ssl: attemptedSettings.use_ssl,
                  use_starttls: attemptedSettings.use_starttls,
                  smtp_username: values.smtp_username || values.email,
                });
              } catch (error) {
                setSmtpSetupStage('manual');
                setSmtpSetupError(
                  error instanceof Error
                    ? error.message
                    : 'Не удалось проверить подключение. Проверьте настройки вручную.',
                );
                return false;
              }

              if (!verification.verified) {
                if (verification.analysis) setSmtpAnalysis(verification.analysis);
                setSmtpSetupStage('manual');
                setSmtpSetupError(
                  verification.error
                    || 'Сервер найден, но подключение не прошло проверку. Проверьте логин, пароль и настройки SMTP.',
                );
                return false;
              }

              await connectionsApi.create({
                transport: 'smtp',
                provider: verification.settings.provider,
                email: values.email,
                sender_name: values.sender_name,
                password: values.password,
                smtp_username: values.smtp_username || values.email,
                host: verification.settings.host,
                port: verification.settings.port,
                use_ssl: verification.settings.use_ssl,
                use_starttls: verification.settings.use_starttls,
                make_default: false,
              });
            } else {
              await connectionsApi.create({
                ...values,
                transport,
                make_default: false,
              });
            }
            message.success(
              (transport === 'smtp' ? 'Почтовый ящик' : PROVIDER_LABELS[transport]) + ' подключён',
            );
            refreshConnections();
            resetSmtpWizard();
            return true;
          }}
        >
          <ProFormSelect
            name="transport"
            label="Способ отправки"
            options={[
              { label: 'Почтовый ящик — автоматическая настройка', value: 'smtp' },
              { label: 'RuSender — по API-ключу', value: 'rusender' },
              { label: 'MailoPost — по API-токену', value: 'mailopost' },
            ]}
            fieldProps={{
              onChange: (value: ConnectionTransport) => {
                setTransport(value);
                resetSmtpWizard();
                if (value === 'rusender') {
                  form.setFieldsValue({ api_base_url: 'https://api.rusender.ru/api/v1' });
                } else if (value === 'mailopost') {
                  form.setFieldsValue({ api_base_url: 'https://api.mailopost.ru/v1' });
                }
              },
            }}
            rules={[{ required: true }]}
          />

          {transport === 'smtp' ? (
            <>
              <Steps
                size="small"
                current={smtpSetupStage === 'email' ? 0 : 1}
                items={[
                  { title: 'Почта' },
                  { title: 'Настройки и пароль' },
                  { title: 'Подключено' },
                ]}
                style={{ marginBottom: 24 }}
              />
              <ProFormText
                name="email"
                label="Email почтового ящика"
                rules={[{ required: true, type: 'email' }]}
                fieldProps={{
                  onChange: () => {
                    if (smtpSetupStage !== 'email') resetSmtpWizard();
                  },
                }}
              />
              <ProFormText name="sender_name" label="Имя отправителя" />

              {smtpSetupStage === 'email' ? (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Alert
                    type="info"
                    showIcon
                    message="Сначала найдём настройки почтового сервера"
                    description="Определим провайдера по домену и MX-записям, проверим autoconfig и доступные SMTP-порты."
                  />
                  <Button
                    type="primary"
                    loading={isAnalyzingSmtp}
                    onClick={() => void analyzeSmtpEmail()}
                  >
                    Определить настройки
                  </Button>
                </Space>
              ) : (
                <>
                  {smtpSetupError ? (
                    <Alert
                      type="error"
                      showIcon
                      message="Подключение не прошло проверку"
                      description={smtpSetupError}
                      style={{ marginBottom: 16 }}
                    />
                  ) : null}

                  {smtpSetupSettings?.host ? (
                    <Alert
                      type={smtpSetupStage === 'manual' ? 'warning' : 'success'}
                      showIcon
                      message={
                        smtpSetupStage === 'manual'
                          ? 'Проверьте настройки SMTP вручную'
                          : 'Найден провайдер: '
                            + (SMTP_PROVIDER_LABELS[smtpSetupSettings.provider] || 'Почтовый сервер')
                      }
                      description={
                        smtpSetupSettings.host + ':' + smtpSetupSettings.port + ' · '
                        + (
                          smtpSetupSettings.use_ssl
                            ? 'SSL/TLS'
                            : smtpSetupSettings.use_starttls
                              ? 'STARTTLS'
                              : 'без шифрования'
                        )
                      }
                      style={{ marginBottom: 16 }}
                    />
                  ) : null}

                  {smtpAnalysis?.action.message_ru ? (
                    <Alert
                      type="info"
                      showIcon
                      message={smtpAnalysis.action.message_ru}
                      description={smtpAnalysis.action.instructions.slice(0, 2).join(' ')}
                      style={{ marginBottom: 16 }}
                    />
                  ) : null}

                  <ProFormText.Password
                    name="password"
                    label={
                      smtpAnalysis?.action.action === 'show_app_password'
                        ? 'Пароль приложения'
                        : 'Пароль почтового ящика или приложения'
                    }
                    rules={[{ required: true }]}
                    fieldProps={{ autoComplete: 'new-password' }}
                  />

                  {smtpSetupStage === 'manual' ? (
                    <>
                      <ProFormText
                        name="smtp_username"
                        label="Логин SMTP"
                        tooltip="Обычно совпадает с полным адресом электронной почты."
                        rules={[{ required: true }]}
                      />
                      <ProFormText
                        name="host"
                        label="SMTP-сервер"
                        placeholder="Например, smtp.example.ru"
                        rules={[{ required: true }]}
                      />
                      <ProFormSelect
                        name="security"
                        label="Защита соединения"
                        options={[
                          { label: 'SSL/TLS — обычно порт 465', value: 'tls' },
                          { label: 'STARTTLS — обычно порт 587', value: 'starttls' },
                          { label: 'Без шифрования — не рекомендуется', value: 'none' },
                        ]}
                        fieldProps={{
                          onChange: (value: SmtpSecurity) => {
                            form.setFieldValue('port', SECURITY_PORTS[value]);
                          },
                        }}
                        rules={[{ required: true }]}
                      />
                      <ProFormDigit
                        name="port"
                        label="Порт SMTP"
                        fieldProps={{ min: 1, max: 65535, precision: 0 }}
                        rules={[{ required: true }]}
                      />
                      <Button
                        type="link"
                        onClick={() => void analyzeSmtpEmail()}
                        loading={isAnalyzingSmtp}
                      >
                        Попробовать автоматическую настройку снова
                      </Button>
                    </>
                  ) : (
                    <Button type="link" onClick={() => setSmtpSetupStage('manual')}>
                      Указать настройки вручную
                    </Button>
                  )}
                </>
              )}
            </>
          ) : (
            <>
              <ProFormText
                name="email"
                label="Подтверждённый email отправителя"
                rules={[{ required: true, type: 'email' }]}
              />
              <ProFormText name="sender_name" label="Имя отправителя" />
              <ProFormText.Password
                name="api_token"
                label={transport === 'rusender' ? 'API-ключ RuSender' : 'API-токен MailoPost'}
                rules={[{ required: true }]}
              />
              <ProFormText
                name="api_base_url"
                label="Адрес API"
                tooltip="Меняйте только при использовании отдельного или тестового API-сервера."
                rules={[{ required: true, type: 'url' }]}
              />
              <Alert
                type="info"
                showIcon
                message="Перед подключением подтвердите адрес отправителя у провайдера"
                description="Токен хранится в зашифрованном виде и не отображается после сохранения. Кнопка «Проверить» отправит тестовое письмо на этот адрес."
              />
            </>
          )}
        </ModalForm>,
      ]}
      dataSource={data || []}
      columns={[
        {
          title: 'Способ',
          dataIndex: 'transport',
          render: (_, row) => <Tag color="blue">{connectionLabel(row)}</Tag>,
        },
        { title: 'Отправитель', dataIndex: 'email' },
        { title: 'Имя', dataIndex: 'sender_name', render: (value) => value || '—' },
        {
          title: 'Параметры',
          render: (_, row) =>
            row.transport === 'smtp'
              ? `${row.host}:${row.port} · ${row.use_ssl ? 'SSL/TLS' : row.use_starttls ? 'STARTTLS' : 'без шифрования'}`
              : row.api_base_url,
        },
        {
          title: 'Статус',
          dataIndex: 'status',
          render: (_, row) => (
            <Space direction="vertical" size={0}>
              <Tag color={row.status === 'active' ? 'green' : 'red'}>
                {row.status === 'active' ? 'Подключено' : 'Ошибка'}
              </Tag>
              {row.last_error ? <Typography.Text type="danger">{row.last_error}</Typography.Text> : null}
            </Space>
          ),
        },
        {
          title: 'Действия',
          valueType: 'option',
          render: (_, row) => (
            <Space>
              <EditConnectionAction connection={row} onSaved={refreshConnections} />
              <a
                onClick={async () => {
                  try {
                    const result = await connectionsApi.test(row.id);
                    message.success(result.message || 'Подключение работает');
                    refreshConnections();
                  } catch (error) {
                    message.error(error instanceof Error ? error.message : 'Проверка не пройдена');
                    refreshConnections();
                  }
                }}
              >
                Проверить
              </a>
              <Popconfirm
                title="Удалить подключение?"
                description="Кампании с этим отправителем нельзя будет запустить."
                okText="Удалить"
                cancelText="Отмена"
                onConfirm={() => removeMutation.mutate(row.id)}
              >
                <a>Удалить</a>
              </Popconfirm>
            </Space>
          ),
        },
      ]}
    />
  );
}
