import { PlusOutlined } from '@ant-design/icons';
import { ModalForm, ProFormDigit, ProFormSelect, ProFormText, ProTable } from '@ant-design/pro-components';
import { Alert, App, Button, Form, Input, Popconfirm, Radio, Space, Steps, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';
import { connectionsApi } from '@/api/connections';
import type { SmtpSetupAnalysis, SmtpSetupSettings } from '@/api/connections';
import type { DeliveryConnection } from '@/api/types';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { readBoolParam, readEnumParam } from '@/utils/urlState';
import {
  MAILBOX_AUTH_KIND_OPTIONS,
  authKindFromSetupAction,
  isOAuthKindAvailable,
  resolveOAuthProvider,
  type AuthKind,
} from '@/utils/connectionAuthKind';
import { selectSmtpSetupSettings, smtpSetupSecurity } from '@/utils/smtpSetup';
import { SmtpSetupInstructions } from '@/features/connections/SmtpSetupInstructions';

type ConnectionTransport = 'smtp' | 'rusender' | 'mailopost';
type ApiTransport = 'rusender' | 'mailopost';
type MethodKind = 'mailbox' | 'api_key';
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

const API_BASE_URLS: Record<ApiTransport, string> = {
  rusender: 'https://api.rusender.ru/api/v1',
  mailopost: 'https://api.mailopost.ru/v1',
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

function formatRateLimit(value?: number | null) {
  return value && value > 0 ? String(value) : '∞';
}

function ConnectionRateLimitFields() {
  return (
    <>
      <Alert
        type="info"
        showIcon
        message="Лимиты отправки"
        description="Ограничение для этого подключения действует во всех кампаниях. 0 или пусто — без лимита."
        style={{ marginBottom: 16 }}
      />
      <ProFormDigit
        name="max_per_hour"
        label="Макс. писем в час"
        fieldProps={{ min: 0, precision: 0 }}
        extra="0 = без лимита"
      />
      <ProFormDigit
        name="max_per_day"
        label="Макс. писем в день"
        fieldProps={{ min: 0, precision: 0 }}
        extra="0 = без лимита"
      />
    </>
  );
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
  const isOAuth = connection.transport === 'smtp' && connection.auth_method === 'oauth';

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
        host: connection.host,
        port: connection.port,
        security: smtpSecurity(connection),
        api_base_url: connection.api_base_url,
        password: '',
        api_token: '',
        max_per_hour: connection.max_per_hour ?? 0,
        max_per_day: connection.max_per_day ?? 0,
      }}
      onFinish={async (values) => {
        const rateLimits = {
          max_per_hour: Number(values.max_per_hour) || 0,
          max_per_day: Number(values.max_per_day) || 0,
        };
        if (isMail) {
          await connectionsApi.update(connection.id, {
            transport: 'smtp',
            email: values.email,
            ...(isSecretEditing ? { password: values.password } : {}),
            ...rateLimits,
          });
        } else {
          const security = values.security as SmtpSecurity | undefined;
          const {
            security: _security,
            password: _password,
            api_token: _apiToken,
            max_per_hour: _maxPerHour,
            max_per_day: _maxPerDay,
            ...connectionValues
          } = values;
          await connectionsApi.update(connection.id, {
            ...connectionValues,
            transport: connection.transport,
            use_ssl: connection.transport === 'smtp' ? security === 'tls' : undefined,
            use_starttls: connection.transport === 'smtp' ? security === 'starttls' : undefined,
            ...(isSecretEditing && connection.transport === 'smtp' && !isOAuth
              ? { password: values.password }
              : {}),
            ...(isSecretEditing && connection.transport !== 'smtp'
              ? { api_token: values.api_token }
              : {}),
            ...rateLimits,
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

      {connection.transport === 'smtp' ? (
        isOAuth ? (
          <Alert
            type="info"
            showIcon
            message="Подключение через OAuth 2.0"
            description={
              connection.oauth_provider === 'microsoft'
                ? 'Авторизация Microsoft. Чтобы обновить доступ, удалите подключение и войдите заново.'
                : 'Авторизация Google. Чтобы обновить доступ, удалите подключение и войдите заново.'
            }
          />
        ) : isMail ? (
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
      <ConnectionRateLimitFields />
    </ModalForm>
  );
}

const SMTP_SETUP_STAGES = ['email', 'credentials', 'manual'] as const;

export function ConnectionsPage() {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const { searchParams, pushParams } = useUrlNavigation();
  const addModalOpen = readBoolParam(searchParams, 'add');
  const smtpSetupStage = readEnumParam(searchParams, 'smtp_stage', SMTP_SETUP_STAGES, 'email');
  const [form] = Form.useForm();
  const [methodKind, setMethodKind] = useState<MethodKind | null>(null);
  const [authKind, setAuthKind] = useState<AuthKind | null>(null);
  const [recommendedAuthKind, setRecommendedAuthKind] = useState<AuthKind | null>(null);
  const [apiTransport, setApiTransport] = useState<ApiTransport | null>(null);
  const [smtpAnalysis, setSmtpAnalysis] = useState<SmtpSetupAnalysis | null>(null);
  const [smtpSetupSettings, setSmtpSetupSettings] = useState<SmtpSetupSettings | null>(null);
  const [smtpSetupError, setSmtpSetupError] = useState('');
  const [isAnalyzingSmtp, setIsAnalyzingSmtp] = useState(false);
  const [isOAuthConnecting, setIsOAuthConnecting] = useState(false);
  const [isVerifyingSmtp, setIsVerifyingSmtp] = useState(false);
  const [showAuthKindPicker, setShowAuthKindPicker] = useState(false);
  const [limitsStepConnectionId, setLimitsStepConnectionId] = useState<string | null>(null);

  const setSmtpSetupStage = useCallback(
    (stage: SmtpSetupStage) => {
      pushParams({ add: '1', smtp_stage: stage === 'email' ? null : stage });
    },
    [pushParams],
  );

  const setAddModalOpen = useCallback(
    (open: boolean) => {
      if (open) {
        pushParams({ add: '1', smtp_stage: 'email' });
        return;
      }
      pushParams({}, ['add', 'smtp_stage']);
    },
    [pushParams],
  );

  const refreshConnections = () => {
    void queryClient.invalidateQueries({ queryKey: ['connections'] });
  };

  const resetSmtpWizard = () => {
    setSmtpSetupStage('email');
    setSmtpAnalysis(null);
    setSmtpSetupSettings(null);
    setSmtpSetupError('');
    setIsAnalyzingSmtp(false);
    setAuthKind(null);
    setRecommendedAuthKind(null);
    setIsOAuthConnecting(false);
    setIsVerifyingSmtp(false);
    setShowAuthKindPicker(false);
  };

  const resetAddModal = () => {
    setMethodKind(null);
    setApiTransport(null);
    setLimitsStepConnectionId(null);
    resetSmtpWizard();
    form.resetFields();
  };

  const enterLimitsStep = (connectionId: string) => {
    setLimitsStepConnectionId(connectionId);
    form.setFieldsValue({ max_per_hour: 0, max_per_day: 0 });
    refreshConnections();
  };

  const applySmtpSettings = (settings: SmtpSetupSettings) => {
    setSmtpSetupSettings(settings);
    form.setFieldsValue({
      host: settings.host,
      port: settings.port,
      security: smtpSetupSecurity(settings),
    });
  };

  const oauthAvailable = isOAuthKindAvailable({
    oauthAvailable: smtpAnalysis?.oauth_available,
    oauthProvider: smtpAnalysis?.action.oauth_provider,
    email: form.getFieldValue('email') || smtpAnalysis?.email,
    smtpProvider: smtpSetupSettings?.provider,
  });

  const oauthProvider = resolveOAuthProvider({
    oauthProvider: smtpAnalysis?.action.oauth_provider,
    email: form.getFieldValue('email') || smtpAnalysis?.email,
    smtpProvider: smtpSetupSettings?.provider,
  });

  const analyzeSmtpEmail = async () => {
    const values = await form.validateFields(['email']);
    const email = String(values.email || '').trim().toLowerCase();
    setIsAnalyzingSmtp(true);
    setSmtpSetupError('');
    setShowAuthKindPicker(false);
    try {
      const analysis = await connectionsApi.analyzeSmtp(email);
      setSmtpAnalysis(analysis);
      form.setFieldValue('smtp_username', email);
      const recommendedKind = authKindFromSetupAction(analysis.action.action);
      const oauthOk = isOAuthKindAvailable({
        oauthAvailable: analysis.oauth_available,
        oauthProvider: analysis.action.oauth_provider,
        email,
        smtpProvider: selectSmtpSetupSettings(analysis)?.provider,
      });
      const nextKind = recommendedKind === 'oauth' && !oauthOk ? 'app_password' : recommendedKind;
      setRecommendedAuthKind(nextKind);
      setAuthKind(nextKind);

      const recommended = selectSmtpSetupSettings(analysis);
      if (recommended?.host) {
        applySmtpSettings(recommended);
        setSmtpSetupStage(analysis.action.action === 'show_manual' ? 'manual' : 'credentials');
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
        setAuthKind('password');
        setRecommendedAuthKind('password');
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
      setAuthKind('password');
      setRecommendedAuthKind('password');
    } finally {
      setIsAnalyzingSmtp(false);
    }
  };

  const connectViaOAuth = async () => {
    const email = String(form.getFieldValue('email') || '').trim().toLowerCase();
    if (!email || !oauthProvider) {
      message.error('OAuth недоступен для этого адреса.');
      return;
    }
    setIsOAuthConnecting(true);
    setSmtpSetupError('');
    try {
      const oauthResult = await connectionsApi.runOAuthPopup({
        provider: oauthProvider,
        email,
        setup_session_id: smtpAnalysis?.setup_session_id || ('oauth-' + Date.now()),
      });
      const settings = smtpSetupSettings || {
        provider: oauthProvider === 'google' ? 'gmail' : 'outlook',
        host: oauthProvider === 'google' ? 'smtp.gmail.com' : 'smtp.office365.com',
        port: 587,
        use_ssl: false,
        use_starttls: true,
      };
      const created = await connectionsApi.create({
        transport: 'smtp',
        provider: settings.provider || (oauthProvider === 'google' ? 'gmail' : 'outlook'),
        email: oauthResult.email || email,
        auth_method: 'oauth',
        oauth_provider: oauthResult.provider || oauthProvider,
        oauth_tokens: oauthResult.tokens,
        smtp_username: oauthResult.email || email,
        host: settings.host,
        port: settings.port,
        use_ssl: settings.use_ssl,
        use_starttls: settings.use_starttls,
        make_default: false,
      });
      message.success('Почтовый ящик подключён через OAuth');
      enterLimitsStep(created.id);
    } catch (error) {
      setSmtpSetupError(
        error instanceof Error ? error.message : 'Не удалось завершить OAuth.',
      );
    } finally {
      setIsOAuthConnecting(false);
    }
  };

  const { data, isLoading } = useQuery({
    queryKey: ['connections'],
    queryFn: () => connectionsApi.list(),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => connectionsApi.remove(id),
    onSuccess: () => {
      message.success('Подключение удалено');
      refreshConnections();
    },
    onError: (error: Error) => message.error(error.message),
  });

  const onMailboxEmailStep =
    methodKind === 'mailbox' && (smtpSetupStage === 'email' || authKind === null);
  const onLimitsStep = Boolean(limitsStepConnectionId);
  const submitDisabled =
    !onLimitsStep
    && (methodKind === null
      || (methodKind === 'mailbox'
        && (onMailboxEmailStep || authKind === 'oauth' || isOAuthConnecting))
      || (methodKind === 'api_key' && apiTransport === null));

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
          open={addModalOpen}
          modalProps={{
            okText: onLimitsStep
              ? 'Сохранить'
              : methodKind === 'api_key'
                ? 'Подключить'
                : 'Проверить и подключить',
            cancelText: 'Отмена',
            destroyOnHidden: true,
          }}
          submitter={{
            searchConfig: {
              submitText: onLimitsStep
                ? 'Сохранить'
                : methodKind === 'api_key'
                  ? 'Подключить'
                  : 'Проверить и подключить',
            },
            submitButtonProps: {
              disabled: submitDisabled,
              loading: isAnalyzingSmtp || isOAuthConnecting || isVerifyingSmtp,
              style:
                !onLimitsStep && methodKind === 'mailbox' && authKind === 'oauth'
                  ? { display: 'none' }
                  : undefined,
            },
            render: (_, dom) => {
              if (!onLimitsStep) return dom;
              return [
                <Button
                  key="skip"
                  onClick={() => {
                    message.success('Подключение сохранено без лимитов');
                    setAddModalOpen(false);
                    resetAddModal();
                  }}
                >
                  Пропустить
                </Button>,
                ...dom,
              ];
            },
          }}
          trigger={
            <Button type="primary" icon={<PlusOutlined />}>
              Добавить
            </Button>
          }
          initialValues={{
            method_kind: undefined,
            email: '',
            smtp_username: '',
            security: 'starttls',
            port: 587,
            max_per_hour: 0,
            max_per_day: 0,
          }}
          onOpenChange={(open) => {
            setAddModalOpen(open);
            if (!open) resetAddModal();
          }}
          onFinish={async (values) => {
            if (limitsStepConnectionId) {
              await connectionsApi.update(limitsStepConnectionId, {
                max_per_hour: Number(values.max_per_hour) || 0,
                max_per_day: Number(values.max_per_day) || 0,
              });
              message.success('Лимиты отправки сохранены');
              refreshConnections();
              resetAddModal();
              return true;
            }

            if (methodKind === 'api_key') {
              if (!apiTransport) return false;
              const created = await connectionsApi.create({
                transport: apiTransport,
                email: values.email,
                api_token: values.api_token,
                api_base_url: API_BASE_URLS[apiTransport],
                make_default: false,
              });
              message.success(PROVIDER_LABELS[apiTransport] + ' подключён');
              enterLimitsStep(created.id);
              return false;
            }

            if (methodKind !== 'mailbox' || !authKind || authKind === 'oauth') return false;

            setIsVerifyingSmtp(true);
            try {
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
              setSmtpSetupError(
                error instanceof Error
                  ? error.message
                  : 'Не удалось проверить подключение. Проверьте настройки вручную.',
              );
              return false;
            }

            if (!verification.verified) {
              if (verification.analysis) {
                setSmtpAnalysis(verification.analysis);
                const analysisAction = verification.analysis.action?.action;
                if (analysisAction) {
                  const recommendedKind = authKindFromSetupAction(analysisAction);
                  const oauthOk = isOAuthKindAvailable({
                    oauthAvailable: verification.analysis.oauth_available,
                    oauthProvider: verification.analysis.action.oauth_provider,
                    email: values.email,
                    smtpProvider: selectSmtpSetupSettings(verification.analysis)?.provider,
                  });
                  const nextKind = recommendedKind === 'oauth' && !oauthOk ? 'app_password' : recommendedKind;
                  setRecommendedAuthKind(nextKind);
                  setAuthKind(nextKind);
                  if (analysisAction === 'show_manual') {
                    setSmtpSetupStage('manual');
                  }
                }
              }
              setSmtpSetupError(
                verification.error
                  || 'Сервер найден, но подключение не прошло проверку. Проверьте логин, пароль и настройки SMTP.',
              );
              return false;
            }

            const created = await connectionsApi.create({
              transport: 'smtp',
              provider: verification.settings.provider,
              email: values.email,
              password: values.password,
              smtp_username: values.smtp_username || values.email,
              host: verification.settings.host,
              port: verification.settings.port,
              use_ssl: verification.settings.use_ssl,
              use_starttls: verification.settings.use_starttls,
              make_default: false,
            });
            message.success('Почтовый ящик подключён');
            enterLimitsStep(created.id);
            return false;
            } finally {
              setIsVerifyingSmtp(false);
            }
          }}
        >
          {onLimitsStep ? (
            <>
              {methodKind === 'mailbox' ? (
                <Steps
                  size="small"
                  current={2}
                  items={[
                    { title: 'Почта' },
                    { title: 'Тип и доступ' },
                    { title: 'Лимиты' },
                  ]}
                  style={{ marginBottom: 24 }}
                />
              ) : null}
              <Alert
                type="success"
                showIcon
                message="Подключение создано"
                description="Укажите лимиты отправки для этого ящика или пропустите шаг."
                style={{ marginBottom: 16 }}
              />
              <ConnectionRateLimitFields />
            </>
          ) : (
            <>
          <ProFormSelect
            name="method_kind"
            label="Способ отправки"
            options={[
              { label: 'Почтовый ящик', value: 'mailbox' },
              { label: 'API-ключ', value: 'api_key' },
            ]}
            fieldProps={{
              allowClear: true,
              placeholder: 'Выберите способ отправки',
              onChange: (value: MethodKind | null) => {
                setMethodKind(value || null);
                setApiTransport(null);
                resetSmtpWizard();
                form.setFieldsValue({
                  transport: undefined,
                  email: '',
                  password: undefined,
                  api_token: undefined,
                  api_base_url: undefined,
                });
              },
            }}
            rules={[{ required: true, message: 'Выберите способ отправки' }]}
          />

          {methodKind === 'mailbox' ? (
            <>
              <Steps
                size="small"
                current={onMailboxEmailStep ? 0 : 1}
                items={[
                  { title: 'Почта' },
                  { title: 'Тип и доступ' },
                  { title: 'Лимиты' },
                ]}
                style={{ marginBottom: 24 }}
              />
              <ProFormText
                name="email"
                label="Email почтового ящика"
                rules={[{ required: true, type: 'email' }]}
                fieldProps={{
                  onChange: () => {
                    if (!onMailboxEmailStep) resetSmtpWizard();
                  },
                }}
              />

              {onMailboxEmailStep ? (
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Alert
                    type="info"
                    showIcon
                    message="Определим наиболее вероятный тип входа"
                    description="По домену и MX-записям подскажем: обычный пароль, пароль приложения или OAuth. Тип можно будет сменить вручную."
                  />
                  <Button
                    type="primary"
                    loading={isAnalyzingSmtp}
                    onClick={() => void analyzeSmtpEmail()}
                  >
                    Определить и продолжить
                  </Button>
                </Space>
              ) : (
                <>
                  {smtpSetupError ? (
                    <Alert
                      type="error"
                      showIcon
                      message="Нужна дополнительная настройка"
                      description={smtpSetupError}
                      style={{ marginBottom: 16 }}
                    />
                  ) : null}

                  {showAuthKindPicker ? (
                    <Form.Item label="Тип входа" required style={{ marginBottom: 16 }}>
                      <Radio.Group
                        value={authKind || undefined}
                        onChange={(event) => {
                          setAuthKind(event.target.value as AuthKind);
                          setSmtpSetupError('');
                        }}
                        style={{ width: '100%' }}
                      >
                        <Space direction="vertical" size={8} style={{ width: '100%' }}>
                          {MAILBOX_AUTH_KIND_OPTIONS.map((option) => {
                            const oauthDisabled = option.value === 'oauth' && !oauthAvailable;
                            return (
                              <Radio
                                key={option.value}
                                value={option.value}
                                disabled={oauthDisabled}
                                style={{
                                  width: '100%',
                                  margin: 0,
                                  padding: '12px 14px',
                                  border: '1px solid var(--ant-color-border)',
                                  borderRadius: 8,
                                  background:
                                    authKind === option.value
                                      ? 'var(--ant-color-primary-bg)'
                                      : undefined,
                                }}
                              >
                                <Space direction="vertical" size={0}>
                                  <Space size={8}>
                                    <Typography.Text strong>{option.label}</Typography.Text>
                                    {recommendedAuthKind === option.value ? (
                                      <Tag color="blue">Рекомендуем</Tag>
                                    ) : null}
                                    {oauthDisabled ? (
                                      <Tag>Недоступно</Tag>
                                    ) : null}
                                  </Space>
                                  <Typography.Text type="secondary">{option.description}</Typography.Text>
                                  {oauthDisabled ? (
                                    <Typography.Text type="secondary">
                                      OAuth для этого адреса не настроен на сервере или не поддерживается.
                                    </Typography.Text>
                                  ) : null}
                                </Space>
                              </Radio>
                            );
                          })}
                        </Space>
                      </Radio.Group>
                    </Form.Item>
                  ) : smtpSetupStage !== 'manual' ? (
                    <Button
                      type="link"
                      style={{ padding: 0, marginBottom: 16 }}
                      onClick={() => setShowAuthKindPicker(true)}
                    >
                      Изменить тип входа
                    </Button>
                  ) : null}

                  {authKind === 'oauth' ? (
                    <Space direction="vertical" size={12} style={{ width: '100%' }}>
                      {smtpAnalysis?.action ? (
                        <SmtpSetupInstructions action={smtpAnalysis.action} />
                      ) : null}
                      <Button
                        type="primary"
                        loading={isOAuthConnecting}
                        disabled={!oauthAvailable}
                        onClick={() => void connectViaOAuth()}
                      >
                        {oauthProvider === 'microsoft'
                          ? 'Войти через Microsoft'
                          : 'Войти через Google'}
                      </Button>
                    </Space>
                  ) : null}

                  {authKind === 'password' || authKind === 'app_password' ? (
                    <>
                      {smtpSetupStage !== 'manual' && smtpAnalysis?.action ? (
                        <SmtpSetupInstructions
                          action={smtpAnalysis.action}
                          style={{ marginBottom: 16 }}
                        />
                      ) : null}

                      <ProFormText.Password
                        name="password"
                        label={
                          authKind === 'app_password'
                            ? 'Пароль приложения'
                            : 'Пароль почтового ящика'
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
                  ) : null}
                </>
              )}
            </>
          ) : null}

          {methodKind === 'api_key' ? (
            <>
              <ProFormSelect
                name="transport"
                label="Провайдер"
                options={[
                  { label: 'RuSender', value: 'rusender' },
                  { label: 'MailoPost', value: 'mailopost' },
                ]}
                fieldProps={{
                  placeholder: 'Выберите провайдера',
                  onChange: (value: ApiTransport) => {
                    setApiTransport(value || null);
                    if (value) {
                      form.setFieldsValue({ api_base_url: API_BASE_URLS[value] });
                    }
                  },
                }}
                rules={[{ required: true, message: 'Выберите провайдера' }]}
              />
              {apiTransport ? (
                <>
                  <ProFormText.Password
                    name="api_token"
                    label={apiTransport === 'rusender' ? 'API-ключ RuSender' : 'API-токен MailoPost'}
                    rules={[{ required: true }]}
                  />
                  <ProFormText
                    name="email"
                    label="Подтверждённый email отправителя"
                    rules={[{ required: true, type: 'email' }]}
                  />
                  <Alert
                    type="info"
                    showIcon
                    message="Перед подключением подтвердите адрес отправителя у провайдера"
                    description="Токен хранится в зашифрованном виде и не отображается после сохранения. Кнопка «Проверить» отправит тестовое письмо на этот адрес. Имя отправителя берётся из названия компании в рассылке."
                  />
                </>
              ) : null}
            </>
          ) : null}
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
        {
          title: 'Параметры',
          render: (_, row) =>
            row.transport === 'smtp'
              ? `${row.host}:${row.port} · ${
                row.auth_method === 'oauth'
                  ? 'OAuth'
                  : row.use_ssl
                    ? 'SSL/TLS'
                    : row.use_starttls
                      ? 'STARTTLS'
                      : 'без шифрования'
              }`
              : row.api_base_url,
        },
        {
          title: 'Лимит час/день',
          render: (_, row) => `${formatRateLimit(row.max_per_hour)}/${formatRateLimit(row.max_per_day)}`,
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
