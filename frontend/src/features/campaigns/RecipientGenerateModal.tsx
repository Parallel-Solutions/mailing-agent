import { App, Button, Checkbox, Form, Input, Modal, Segmented, Space, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { campaignsApi } from '@/api/campaigns';
import { parserApi } from '@/api/parser';

type Props = {
  open: boolean;
  campaignId: string;
  jobId: string;
  onClose: () => void;
  onImported: () => void;
  mode?: 'generate' | 'topup';
};

type SearchValues = {
  what: string;
  where: string;
  volume?: string;
  fields?: string;
};

const DEFAULT_FIELDS = 'email, телефон, адрес, ИНН, ФИО руководителя';

// Ждём не «всего N минут», а «N минут тишины»: пока сервер шлёт события
// прогресса, таймер сбрасывается. Один лимит одинаково подходит и для 13 строк,
// и для 400 — а жёсткий общий таймаут обрывал длинные сборы на полпути.
const SILENCE_LIMIT_MS = 10 * 60 * 1000;

function buildPrompt(values: SearchValues): string {
  return [
    `Найди ${values.what} в регионе: ${values.where}.`,
    values.fields ? `Нужны данные: ${values.fields}.` : '',
    values.volume ? `Объём: ${values.volume}.` : '',
    'После сбора подготовь таблицу для скачивания.',
  ]
    .filter(Boolean)
    .join(' ');
}

export function RecipientGenerateModal({ open, campaignId, jobId, mode = 'generate', onClose, onImported }: Props) {
  const { message } = App.useApp();
  const [form] = Form.useForm<SearchValues>();
  const [phase, setPhase] = useState<'form' | 'running' | 'done'>('form');
  const [logs, setLogs] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [hasFile, setHasFile] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [topupMode, setTopupMode] = useState<'fill' | 'find'>('fill');
  const [verifyEmails, setVerifyEmails] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const streamRef = useRef<EventSource | null>(null);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    if (!open) return;
    setPhase('form');
    setTopupMode('fill');
    setVerifyEmails(false);
    setLogs([]);
    setRunning(false);
    setHasFile(false);
    form.setFieldsValue({
      what: '',
      where: '',
      volume: '',
      fields: DEFAULT_FIELDS,
    });
  }, [open, form]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      streamRef.current?.close();
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    };
  }, []);

  const appendLog = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setLogs((prev) => [...prev, trimmed]);
  };

  const cleanupRun = () => {
    abortRef.current = null;
    streamRef.current?.close();
    streamRef.current = null;
    setRunning(false);
  };

  const handleCancel = () => {
    if (running) {
      abortRef.current?.abort();
      cleanupRun();
    }
    onClose();
  };

  // Тот же файл, что модалка импортирует в таблицу, — отдаём пользователю.
  const handleDownload = async () => {
    setDownloading(true);
    try {
      const file = await parserApi.downloadResult(jobId);
      const url = URL.createObjectURL(file);
      const link = document.createElement('a');
      link.href = url;
      link.download = file.name || `recipients_${jobId}.xlsx`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Не удалось скачать файл';
      message.error(detail);
    } finally {
      setDownloading(false);
    }
  };

  const handleSubmit = async () => {
    const isFill = mode === 'topup' && topupMode === 'fill';

    let values: SearchValues = { what: '', where: '' };
    if (!isFill) {
      try {
        values = await form.validateFields();
      } catch {
        return;
      }
      const what = values.what.trim();
      const where = values.where.trim();
      if (!what || !where) {
        message.warning('Заполните, что и где нужно найти');
        return;
      }
    }

    const prompt = isFill
      ? ''
      : buildPrompt({
          what: values.what.trim(),
          where: values.where.trim(),
          volume: values.volume?.trim(),
          fields: values.fields?.trim(),
        });

    setPhase('running');
    setRunning(true);
    setHasFile(false);
    setLogs(['Запрос отправлен агенту…']);

    const controller = new AbortController();
    abortRef.current = controller;

    const armTimeout = () => {
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = window.setTimeout(() => controller.abort(), SILENCE_LIMIT_MS);
    };
    armTimeout();

    streamRef.current = parserApi.openProgressStream(jobId, (event) => {
      // Любое событие (в т.ч. keep-alive «ping» без текста) означает, что сервер
      // жив, — отодвигаем обрыв. В лог пишем только содержательные, текстовые.
      armTimeout();
      if (event.text) appendLog(event.text);
    });

    try {
      const result =
        mode === 'topup'
          ? topupMode === 'fill'
            ? await parserApi.fillGaps(jobId, verifyEmails, controller.signal)
            : await parserApi.topup(prompt, jobId, controller.signal)
          : await parserApi.chat(prompt, jobId, controller.signal);
      if (result.reply) appendLog(result.reply);

      if (result.result_file) {
        setHasFile(true);
        try {
          const file = await parserApi.downloadResult(jobId);
          const imported = await campaignsApi.importRecipients(campaignId, file);
          appendLog(`Импортировано получателей: ${imported.import?.total ?? 0}`);
          message.success('Список получателей сформирован и загружен');
          onImported();
        } catch (error) {
          const detail = error instanceof Error ? error.message : 'Не удалось импортировать результат';
          appendLog(detail);
          message.error(detail);
        }
        setPhase('done');
      } else {
        message.info(result.reply || 'Агент ответил без готового файла. Уточните запрос или повторите.');
        setPhase('done');
      }
    } catch (error) {
      const aborted = error instanceof DOMException && error.name === 'AbortError';
      appendLog(
        aborted
          ? 'Ожидание прервано. Если сбор ещё идёт на сервере, попробуйте скачать результат позже.'
          : error instanceof Error
            ? error.message
            : 'Не удалось связаться с агентом таблицы',
      );
      message.error(aborted ? 'Генерация прервана' : 'Ошибка генерации списка');
      setPhase('done');
    } finally {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      cleanupRun();
    }
  };

  return (
    <Modal
      title={
        phase === 'form'
          ? mode === 'topup' ? 'Что дозаполнить?' : 'Что нужно найти?'
          : mode === 'topup' ? 'Дозаполнение файла' : 'Сбор списка получателей'
      }
      open={open}
      onCancel={handleCancel}
      destroyOnHidden
      width={560}
      footer={
        phase === 'form' ? (
          <Space>
            <Button onClick={handleCancel}>Отмена</Button>
            <Button type="primary" onClick={() => void handleSubmit()}>
              {mode === 'topup' && topupMode === 'fill' ? 'Заполнить' : 'Сформировать и отправить'}
            </Button>
          </Space>
        ) : (
          <Space>
            {running ? (
              <Button danger onClick={() => abortRef.current?.abort()}>
                Остановить ожидание
              </Button>
            ) : null}
            {!running && hasFile ? (
              <Button loading={downloading} onClick={() => void handleDownload()}>
                Скачать таблицу
              </Button>
            ) : null}
            <Button type="primary" disabled={running} onClick={onClose}>
              Закрыть
            </Button>
          </Space>
        )
      }
    >
      {phase === 'form' ? (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {mode === 'topup' ? (
            <Segmented
              block
              value={topupMode}
              onChange={(v) => setTopupMode(v as 'fill' | 'find')}
              options={[
                { label: 'Заполнить пробелы', value: 'fill' },
                { label: 'Донайти новые', value: 'find' },
              ]}
            />
          ) : null}

          {mode === 'topup' && topupMode === 'fill' ? (
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <Typography.Text type="secondary">
                Достроит недостающие данные у строк, которые уже есть в файле.
                Искать новые организации не нужно — просто нажмите «Заполнить».
              </Typography.Text>
              <Checkbox checked={verifyEmails} onChange={(e) => setVerifyEmails(e.target.checked)}>
                Проверить почты по официальным сайтам
              </Checkbox>
              {verifyEmails ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  Пройдёт по сайтам и обновит почты. Если в столбце «Сайт» (S) уже
                  указан адрес — возьмёт почту прямо оттуда. Прежняя почта не теряется.
                </Typography.Text>
              ) : null}
            </Space>
          ) : (
            <Form form={form} layout="vertical" initialValues={{ fields: DEFAULT_FIELDS }}>
              <Form.Item
                name="what"
                label="Что ищем"
                rules={[{ required: true, message: 'Укажите, что искать' }]}
              >
                <Input placeholder="Например: сельские поселения, администрации, организации" />
              </Form.Item>
              <Form.Item
                name="where"
                label="Где ищем"
                rules={[{ required: true, message: 'Укажите регион или территорию' }]}
              >
                <Input placeholder="Например: Забайкальский край" />
              </Form.Item>
              <Form.Item label="Объём">
                <Space.Compact style={{ width: '100%' }}>
                  <Form.Item name="volume" noStyle>
                    <Input placeholder="Например: 3000 записей, все районы, первые 500" />
                  </Form.Item>
                  <Button
                    onClick={() => form.setFieldValue('volume', 'всё, что есть')}
                  >
                    Искать всё
                  </Button>
                </Space.Compact>
              </Form.Item>
              <Form.Item name="fields" label="Какие данные нужны">
                <Input />
              </Form.Item>
            </Form>
          )}
        </Space>
      ) : (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text type="secondary">
            {running
              ? 'Агент собирает данные через внешние сервисы. Это может занять несколько минут.'
              : 'Сбор завершён.'}
          </Typography.Text>
          <div
            style={{
              maxHeight: 280,
              overflow: 'auto',
              background: '#fafafa',
              border: '1px solid #f0f0f0',
              borderRadius: 8,
              padding: 12,
              whiteSpace: 'pre-wrap',
              fontSize: 13,
            }}
          >
            {logs.length ? logs.join('\n\n') : 'Ожидание ответа…'}
          </div>
        </Space>
      )}
    </Modal>
  );
}