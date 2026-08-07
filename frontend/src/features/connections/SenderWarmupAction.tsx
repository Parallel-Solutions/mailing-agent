import { Alert, App, Button, Checkbox, Input, Modal, Popconfirm, Select, Space, Tag, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { connectionsApi } from '@/api/connections';
import type { DeliveryConnection } from '@/api/types';

const STATUS_LABELS: Record<string, string> = {
  draft: 'Не запущен', running: 'Выполняется', paused: 'На паузе',
  completed: 'Завершён', blocked: 'Заблокирован', cancelled: 'Остановлен',
};
const CHECK_LABELS: Record<string, string> = {
  spf_record: 'SPF', dmarc_record: 'DMARC', spf_result: 'SPF в письме',
  dkim_record: 'DKIM в DNS', ptr: 'PTR/rDNS', template_variation: 'Варианты писем', content_links: 'Ссылки', short_links: 'Сокращатели', reputation: 'Репутация', dkim_result: 'DKIM в письме', alignment: 'Совпадение доменов', sample_headers: 'Заголовки письма',
  smtp_connection: 'SMTP-подключение',
};

export function SenderWarmupAction({ connection, connections }: { connection: DeliveryConnection; connections: DeliveryConnection[] }) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [recipientEmails, setRecipientEmails] = useState<string[]>([]);
  const [headers, setHeaders] = useState('');
  const [busy, setBusy] = useState(false);
  const [smtpConnectionId, setSmtpConnectionId] = useState('');
  const [dailyStartTime, setDailyStartTime] = useState('10:00');
  const [dailyEndTime, setDailyEndTime] = useState('18:00');
  const [growthPercent, setGrowthPercent] = useState('25');
  const [pauseCampaigns, setPauseCampaigns] = useState(true);
  const [subjectTemplatesText, setSubjectTemplatesText] = useState('');
  const [bodyTemplatesText, setBodyTemplatesText] = useState('');
  const queryKey = ['connection-sender-warmup', connection.id];
  const query = useQuery({
    queryKey,
    queryFn: () => connectionsApi.getWarmup(connection.id),
    enabled: open,
    refetchInterval: open ? 10_000 : false,
  });
  const warmup = query.data;
  const smtpConnections = connections.filter((item) => {
    if (item.status !== 'active') return false;
    if (connection.transport === 'rusender') {
      return item.transport === 'rusender'
        && item.sending_key_id != null
        && item.sending_key_id === connection.sending_key_id;
    }
    return item.transport === 'smtp'
      && item.email.trim().toLowerCase() === connection.email.trim().toLowerCase();
  });
  const selectedSmtpConnection = smtpConnections.find((item) => item.id === smtpConnectionId);
  const currentDayVolume = warmup
    ? warmup.daily_plan[Math.min(warmup.current_day - 1, warmup.daily_plan.length - 1)] || 0
    : 0;
  const distributionLabel = warmup && warmup.active_recipient_count > 0
    ? warmup.active_recipient_count === 1
      ? `${currentDayVolume} писем на один адрес`
      : `${Math.floor(currentDayVolume / warmup.active_recipient_count)}–${Math.ceil(currentDayVolume / warmup.active_recipient_count)} писем на адрес`
    : 'нет активных адресов';
  useEffect(() => {
    if (!warmup) return;
    setDailyStartTime(warmup.daily_start_time);
    setSmtpConnectionId(warmup.smtp_connection_id);
    setDailyEndTime(warmup.daily_end_time);
    setGrowthPercent(String(warmup.max_growth_percent));
    setPauseCampaigns(warmup.pause_campaigns_during_warmup);
    setSubjectTemplatesText(warmup.subject_templates.join('\n'));
    setBodyTemplatesText(warmup.body_templates.join('\n---\n'));
  }, [warmup?.id, warmup?.smtp_connection_id]);

  const run = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true);
    try {
      await action();
      await queryClient.invalidateQueries({ queryKey });
      message.success(success);
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Не удалось выполнить действие');
    } finally {
      setBusy(false);
    }
  };

  return <>
    <a onClick={() => setOpen(true)}>Прогрев</a>
    <Modal open={open} title={connection.transport === 'rusender' ? `Прогрев ключа RuSender ${connection.sending_key_id}` : `Прогрев отправителя ${connection.email}`} onCancel={() => setOpen(false)} footer={null} width={860} destroyOnClose>
      {query.isLoading ? <Typography.Text>Загрузка…</Typography.Text> : null}
      {warmup ? <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Alert type="info" showIcon message="Сначала техническая проверка, затем постепенный рост объёма" description={connection.transport === 'rusender' ? "Письма отправляются именно через выбранный ключ RuSender. Email отправителя можно выбрать среди подключений этого ключа." : "Дневной план — это общее количество писем. Они равномерно распределяются по активным адресам и отправляются через выбранный SMTP."} />

        <Space wrap>
          <Typography.Title level={5} style={{ margin: 0 }}>Состояние</Typography.Title>
          <Tag color={warmup.status === 'running' ? 'processing' : warmup.status === 'completed' ? 'success' : 'default'}>{STATUS_LABELS[warmup.status] || warmup.status}</Tag>
          <Typography.Text type="secondary">День {Math.min(warmup.current_day, warmup.daily_plan.length)} из {warmup.daily_plan.length}</Typography.Text>
        </Space>
        <Space wrap>
          <Typography.Text type="secondary">Принято: {warmup.delivery_counts.accepted || 0}</Typography.Text>
          <Typography.Text type="secondary">Доставлено: {warmup.delivery_counts.delivered || 0}</Typography.Text>
          <Typography.Text type="secondary">{'\u041f\u0440\u0435\u0434\u043f\u043e\u043b\u0430\u0433\u0430\u0435\u043c\u043e \u043e\u0442\u043a\u0440\u044b\u0442\u043e'}: {warmup.delivery_counts.opened || 0}</Typography.Text>
          <Typography.Text type="secondary">В очереди: {(warmup.delivery_counts.queued || 0) + (warmup.delivery_counts.sending || 0)}</Typography.Text>
          <Typography.Text type="secondary">Ошибки: {warmup.delivery_counts.error || 0}</Typography.Text>
          <Typography.Text type="secondary">Hard bounce: {warmup.delivery_counts.hard_bounced || 0}</Typography.Text>
          <Typography.Text type="secondary">Soft bounce: {warmup.delivery_counts.soft_bounced || 0}</Typography.Text>
          <Typography.Text type="secondary">Жалобы: {warmup.delivery_counts.complaint || 0}</Typography.Text>
        </Space>

        <div>
          <Typography.Title level={5}>1. Техническая проверка</Typography.Title>
          <Input.TextArea value={headers} onChange={(event) => setHeaders(event.target.value)} placeholder="Вставьте технические заголовки тестового письма. Можно сначала выполнить только DNS-проверку." autoSize={{ minRows: 3, maxRows: 8 }} />
          <Button style={{ marginTop: 8 }} loading={busy} onClick={() => run(() => connectionsApi.diagnoseWarmup(connection.id, headers), 'Техническая проверка завершена')}>Выполнить проверку</Button>
          <Space wrap style={{ marginTop: 12 }}>
            {(warmup.diagnostics.checks || []).map((check) => <Tag key={check.key} color={check.status === 'pass' ? 'success' : check.status === 'warning' ? 'warning' : 'error'}>{CHECK_LABELS[check.key] || check.key}: {check.detail}</Tag>)}
          </Space>
        </div>

        <div>
          <Typography.Title level={5}>2. Адреса получателей</Typography.Title>
          <Space.Compact block>
            <Select
              mode="tags"
              value={recipientEmails}
              onChange={setRecipientEmails}
              tokenSeparators={[',', ';', ' ']}
              options={[
                { label: 'ffff06@yandex.ru', value: 'ffff06@yandex.ru' },
                { label: 'fmagomedova654@gmail.ru', value: 'fmagomedova654@gmail.ru' },
              ]}
              placeholder="Выберите адреса или введите свои"
              style={{ flex: 1, minWidth: 420 }}
            />
            <Button type="primary" loading={busy} onClick={() => {
              const emails = recipientEmails.map((item) => item.trim()).filter(Boolean);
              void run(() => connectionsApi.addWarmupRecipients(connection.id, emails), `Добавлено адресов: ${emails.length}`).then(() => setRecipientEmails([]));
            }}>Добавить</Button>
          </Space.Compact>
          <Typography.Text type="secondary">Активно: {warmup.active_recipient_count}. План текущего дня: {distributionLabel}.</Typography.Text>
          <Space direction="vertical" size={6} style={{ width: '100%', marginTop: 12 }}>
            {warmup.recipients.map((recipient) => <Space key={recipient.id} wrap style={{ justifyContent: 'space-between', width: '100%' }}>
              <Space wrap>
                <Typography.Text>{recipient.email}</Typography.Text><Tag>{recipient.provider}</Tag>
                <Tag color={recipient.status === 'active' ? 'success' : 'default'}>{recipient.status === 'active' ? 'Активен' : 'Отключён'}</Tag>
                <Typography.Text type="secondary">отправлено {recipient.sent_count}, ошибок {recipient.error_count}</Typography.Text>
              </Space>
              <Space>
                <a onClick={() => void run(() => connectionsApi.setWarmupRecipientStatus(connection.id, recipient.id, recipient.status === 'active' ? 'disabled' : 'active'), recipient.status === 'active' ? 'Адрес отключён' : 'Адрес включён')}>{recipient.status === 'active' ? 'Отключить' : 'Включить'}</a>
                <Popconfirm title="Удалить адрес из прогрева?" onConfirm={() => run(() => connectionsApi.removeWarmupRecipient(connection.id, recipient.id), 'Адрес удалён')}><a>Удалить</a></Popconfirm>
              </Space>
            </Space>)}
            {!warmup.recipients.length ? <Typography.Text type="secondary">Адреса пока не добавлены.</Typography.Text> : null}
          </Space>
        </div>

        <div>
          <Typography.Title level={5}>3. Настройки отправки</Typography.Title>
          <label style={{ display: 'block', marginBottom: 12 }}>
            <Typography.Text type="secondary">{connection.transport === 'rusender' ? 'Отправитель ключа RuSender' : 'SMTP-подключение для отправки'}</Typography.Text>
            <Select
              value={smtpConnectionId || undefined}
              onChange={setSmtpConnectionId}
              placeholder={connection.transport === 'rusender' ? 'Выберите отправителя этого ключа' : 'Выберите SMTP-подключение'}
              style={{ display: 'block', width: '100%', maxWidth: 520 }}
              options={smtpConnections.map((item) => ({
                value: item.id,
                label: connection.transport === 'rusender' ? `${item.email} · ключ ${item.sending_key_id}` : `${item.email} · ${item.host}:${item.port}`,
              }))}
              disabled={warmup.status === 'running'}
            />
          </label>
          {selectedSmtpConnection
            ? <Alert type="success" showIcon message={connection.transport === 'rusender' ? `Прогрев идёт через ключ ${selectedSmtpConnection.sending_key_id}, отправитель: ${selectedSmtpConnection.email}` : `Прогрев отправляется через SMTP: ${selectedSmtpConnection.email}`} style={{ marginBottom: 12 }} />
            : <Alert type="error" showIcon message={connection.transport === 'rusender' ? "Нет активного подключения с этим ключом RuSender" : "Добавьте и проверьте SMTP-подключение с тем же email"} style={{ marginBottom: 12 }} />}
          <Space wrap align="start">
            <label>
              <Typography.Text type="secondary">Начало дня</Typography.Text>
              <Input value={dailyStartTime} onChange={(event) => setDailyStartTime(event.target.value)} placeholder="10:00" style={{ width: 110, display: 'block' }} />
            </label>
            <label>
              <Typography.Text type="secondary">Конец дня</Typography.Text>
              <Input value={dailyEndTime} onChange={(event) => setDailyEndTime(event.target.value)} placeholder="18:00" style={{ width: 110, display: 'block' }} />
            </label>
            <label>
              <Typography.Text type="secondary">Рост в день, %</Typography.Text>
              <Input type="number" min={20} max={30} value={growthPercent} onChange={(event) => setGrowthPercent(event.target.value)} style={{ width: 110, display: 'block' }} />
            </label>
          </Space>
          <Checkbox checked={pauseCampaigns} onChange={(event) => setPauseCampaigns(event.target.checked)} style={{ marginTop: 12 }}>
            При запуске поставить обычные кампании подключения на паузу (возобновление вручную)
          </Checkbox>
          <Typography.Text strong style={{ display: 'block', marginTop: 12 }}>Темы — по одной в строке</Typography.Text>
          <Input.TextArea value={subjectTemplatesText} onChange={(event) => setSubjectTemplatesText(event.target.value)} autoSize={{ minRows: 3, maxRows: 8 }} />
          <Typography.Text strong style={{ display: 'block', marginTop: 12 }}>Тексты — разделитель ---</Typography.Text>
          <Input.TextArea value={bodyTemplatesText} onChange={(event) => setBodyTemplatesText(event.target.value)} autoSize={{ minRows: 6, maxRows: 14 }} />
          <Button
            style={{ marginTop: 8 }}
            loading={busy}
            disabled={warmup.status === 'running'}
            onClick={() => void run(
              () => connectionsApi.updateWarmup(connection.id, {
                daily_start_time: dailyStartTime,
                daily_end_time: dailyEndTime,
                max_growth_percent: Number(growthPercent),
                smtp_connection_id: smtpConnectionId,
                pause_campaigns_during_warmup: pauseCampaigns,
                subject_templates: subjectTemplatesText.split('\n').map((item) => item.trim()).filter(Boolean),
                body_templates: bodyTemplatesText.split(/\n---\n/).map((item) => item.trim()).filter(Boolean),
              }),
              'Настройки прогрева сохранены',
            )}
          >
            Сохранить настройки
          </Button>
        </div>

        <div>
          <Typography.Title level={5}>4. План</Typography.Title>
          <Space wrap>{warmup.daily_plan.map((planned, index) => <Tag key={index} color={index + 1 === warmup.current_day ? 'processing' : 'default'}>День {index + 1}: {planned} писем</Tag>)}</Space>
          {warmup.active_recipient_count === 1 ? <Alert style={{ marginTop: 12 }} type="warning" showIcon message="Все письма дневного плана будут отправлены на один адрес. Для более естественного распределения добавьте несколько адресов." /> : null}
        </div>

        {warmup.pause_reason ? <Alert type="warning" showIcon message="Причина паузы" description={warmup.pause_reason} /> : null}
        <Checkbox
          checked={warmup.recipients_consent_confirmed}
          disabled={busy || warmup.status === 'running'}
          onChange={(event) => void run(
            () => connectionsApi.updateWarmup(connection.id, { recipients_consent_confirmed: event.target.checked }),
            event.target.checked ? 'Согласие подтверждено' : 'Подтверждение согласия снято',
          )}
        >
          Подтверждаю, что владельцы добавленных адресов согласны получать эти письма
        </Checkbox>
        <Space wrap>
          {['draft', 'completed', 'cancelled'].includes(warmup.status) ? <Button type="primary" disabled={!warmup.recipients_consent_confirmed || !smtpConnectionId} loading={busy} onClick={() => run(() => connectionsApi.changeWarmupStatus(connection.id, 'start'), 'Прогрев запущен')}>Запустить прогрев</Button> : null}
          {warmup.status === 'running' ? <Button loading={busy} onClick={() => run(() => connectionsApi.changeWarmupStatus(connection.id, 'pause'), 'Прогрев поставлен на паузу')}>Пауза</Button> : null}
          {warmup.status === 'paused' ? <Button type="primary" loading={busy} onClick={() => run(() => connectionsApi.changeWarmupStatus(connection.id, 'resume'), 'Прогрев продолжен')}>Продолжить</Button> : null}
          {['running', 'paused'].includes(warmup.status) ? <Popconfirm title="Полностью остановить прогрев?" onConfirm={() => run(() => connectionsApi.changeWarmupStatus(connection.id, 'stop'), 'Прогрев остановлен')}><Button danger loading={busy}>Остановить</Button></Popconfirm> : null}
        </Space>
      </Space> : null}
    </Modal>
  </>;
}