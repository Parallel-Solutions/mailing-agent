import { LayoutOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { App, Button, Card, Input, Modal, Select, Space, Typography, Upload } from 'antd';
import type { UploadFile } from 'antd/es/upload/interface';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';
import { DEFAULT_VISUAL_EMAIL_HTML } from '@/features/templates/emailConstants';

type TemplateKind = 'email' | 'document';
type EmailFormat = 'simple' | 'visual';
type WizardStep = 'format' | 'gallery' | 'custom';

type Props = {
  open: boolean;
  templateType: TemplateKind;
  onClose: () => void;
  onCreated: (template: Template) => void;
};

export function AddTemplateWizard({ open, templateType, onClose, onCreated }: Props) {
  const { message } = App.useApp();
  const [step, setStep] = useState<WizardStep>(templateType === 'email' ? 'format' : 'gallery');
  const [emailFormat, setEmailFormat] = useState<EmailFormat>('simple');
  const [prompt, setPrompt] = useState('');
  const [model, setModel] = useState<string>();
  const [fileList, setFileList] = useState<UploadFile[]>([]);

  const startersQuery = useQuery({
    queryKey: ['template-starters', templateType],
    queryFn: () => templatesApi.starters(templateType),
    enabled: open,
  });
  const modelsQuery = useQuery({
    queryKey: ['template-models'],
    queryFn: () => templatesApi.models(),
    enabled: open && step === 'custom' && emailFormat === 'simple',
  });

  useEffect(() => {
    if (!open) return;
    setStep(templateType === 'email' ? 'format' : 'gallery');
    setEmailFormat('simple');
    setPrompt('');
    setFileList([]);
    setModel(undefined);
  }, [open, templateType]);

  useEffect(() => {
    if (!modelsQuery.data?.length || model) return;
    const preferred = modelsQuery.data.find((item) => item.default) || modelsQuery.data[0];
    setModel(preferred.id);
  }, [modelsQuery.data, model]);

  const filteredStarters = useMemo(() => {
    const items = startersQuery.data || [];
    if (templateType !== 'email') return items;
    return items.filter((starter) => (starter.email_format || 'simple') === emailFormat);
  }, [emailFormat, startersQuery.data, templateType]);

  const useStarterMutation = useMutation({
    mutationFn: (starterId: string) => templatesApi.useStarter(starterId),
    onSuccess: (template) => {
      message.success('Шаблон добавлен из примера');
      onCreated(template);
      onClose();
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось создать шаблон');
    },
  });

  const createVisualBlankMutation = useMutation({
    mutationFn: () =>
      templatesApi.create({
        name: 'Новое HTML-письмо',
        template_type: 'email',
        subject: '',
        body_html: DEFAULT_VISUAL_EMAIL_HTML,
        body_text: '',
        editor_state: { email_format: 'visual' },
      }),
    onSuccess: (template) => {
      message.success('Создан пустой HTML-шаблон');
      onCreated(template);
      onClose();
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось создать шаблон');
    },
  });

  const generateMutation = useMutation({
    mutationFn: async () => {
      const files = fileList
        .map((item) => item.originFileObj)
        .filter((item): item is NonNullable<typeof item> => Boolean(item));
      if (!prompt.trim() && files.length === 0) {
        throw new Error('Опишите шаблон или приложите файлы');
      }
      return templatesApi.generate({
        template_type: templateType,
        prompt: prompt.trim(),
        model,
        files,
      });
    },
    onSuccess: (template) => {
      message.success(prompt.trim() ? 'Шаблон сгенерирован' : 'Шаблон создан из файлов');
      onCreated(template);
      onClose();
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось создать шаблон');
    },
  });

  const title = useMemo(() => {
    if (step === 'format') return 'Выберите тип письма';
    const kind = templateType === 'email' ? 'письма' : 'документа';
    return step === 'gallery' ? `Выберите пример ${kind}` : `Добавить свой шаблон ${kind}`;
  }, [step, templateType]);

  const footer = (() => {
    if (step === 'format') {
      return (
        <Space>
          <Button onClick={onClose}>Отмена</Button>
          <Button type="primary" onClick={() => setStep('gallery')}>
            Далее
          </Button>
        </Space>
      );
    }
    if (step === 'gallery') {
      return (
        <Space>
          <Button onClick={() => (templateType === 'email' ? setStep('format') : onClose())}>
            {templateType === 'email' ? 'Назад' : 'Отмена'}
          </Button>
          {emailFormat === 'visual' ? (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              loading={createVisualBlankMutation.isPending}
              onClick={() => createVisualBlankMutation.mutate()}
            >
              Пустой HTML-шаблон
            </Button>
          ) : (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setStep('custom')}>
              Добавить
            </Button>
          )}
        </Space>
      );
    }
    return (
      <Space>
        <Button onClick={() => setStep('gallery')}>Назад</Button>
        <Button type="primary" loading={generateMutation.isPending} onClick={() => generateMutation.mutate()}>
          Создать
        </Button>
      </Space>
    );
  })();

  return (
    <Modal open={open} onCancel={onClose} title={title} width={820} destroyOnClose footer={footer}>
      {step === 'format' ? (
        <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
          <Card
            hoverable
            onClick={() => setEmailFormat('simple')}
            style={{ borderColor: emailFormat === 'simple' ? '#236348' : undefined }}
          >
            <Typography.Title level={5}>Простое письмо</Typography.Title>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
              Текстовый редактор для быстрых деловых писем без сложной вёрстки.
            </Typography.Paragraph>
          </Card>
          <Card
            hoverable
            onClick={() => setEmailFormat('visual')}
            style={{ borderColor: emailFormat === 'visual' ? '#236348' : undefined }}
          >
            <Typography.Title level={5}>
              <LayoutOutlined /> HTML-письмо с дизайном
            </Typography.Title>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
              Визуальный конструктор: колонки, кнопки, изображения и фирменный стиль.
            </Typography.Paragraph>
          </Card>
        </div>
      ) : step === 'gallery' ? (
        <div style={{ display: 'grid', gap: 12, gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
          {filteredStarters.map((starter) => (
            <button
              key={starter.id}
              type="button"
              onClick={() => useStarterMutation.mutate(starter.id)}
              disabled={useStarterMutation.isPending}
              style={{
                textAlign: 'left',
                border: '1px solid #DEE2E6',
                borderRadius: 8,
                padding: 12,
                background: '#fff',
                cursor: 'pointer',
                minHeight: 160,
              }}
            >
              <Typography.Text strong>{starter.name}</Typography.Text>
              {starter.subject ? (
                <Typography.Paragraph type="secondary" style={{ marginTop: 4, marginBottom: 8 }}>
                  {starter.subject}
                </Typography.Paragraph>
              ) : (
                <div style={{ height: 8 }} />
              )}
              <div
                style={{ fontSize: 13, color: '#495057', lineHeight: 1.45 }}
                dangerouslySetInnerHTML={{ __html: starter.preview_html }}
              />
            </button>
          ))}
          {filteredStarters.length === 0 && (
            <Typography.Paragraph type="secondary">
              Примеры для выбранного типа пока не добавлены — создайте пустой шаблон.
            </Typography.Paragraph>
          )}
        </div>
      ) : (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <div>
            <Typography.Text>Нейронка</Typography.Text>
            <Select
              style={{ width: '100%', marginTop: 6 }}
              loading={modelsQuery.isLoading}
              value={model}
              onChange={setModel}
              options={(modelsQuery.data || []).map((item) => ({
                label: item.label,
                value: item.id,
              }))}
              placeholder="Выберите модель"
            />
          </div>
          <div>
            <Typography.Text>Описание для нейронки</Typography.Text>
            <Input.TextArea
              style={{ marginTop: 6 }}
              rows={5}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Опишите, какой шаблон нужен… Можно оставить пустым, если только прикладываете файлы."
            />
          </div>
          <div>
            <Typography.Text>Файлы (необязательно)</Typography.Text>
            <Upload
              multiple
              accept={
                templateType === 'email'
                  ? '.docx,.pdf,.html,.htm,.txt'
                  : '.docx,.pdf,.html,.htm'
              }
              fileList={fileList}
              beforeUpload={() => false}
              onChange={({ fileList: next }) => setFileList(next)}
              style={{ marginTop: 6, display: 'block' }}
            >
              <Button icon={<UploadOutlined />} style={{ marginTop: 6 }}>
                Приложить файлы
              </Button>
            </Upload>
            <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>
              Можно описать запрос, приложить файлы или сделать и то и другое.
            </Typography.Paragraph>
          </div>
        </Space>
      )}
    </Modal>
  );
}
