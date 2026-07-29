import { LayoutOutlined, LoadingOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { App, Button, Card, Input, Modal, Select, Space, Typography, Upload } from 'antd';
import type { UploadFile } from 'antd/es/upload/interface';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';
import { OperationProgress } from '@/components/OperationProgress';
import { DEFAULT_VISUAL_EMAIL_HTML } from '@/features/templates/emailConstants';
import {
  advanceOnboarding,
  ONBOARDING_ENTER_EVENT,
  type OnboardingEnterDetail,
} from '@/features/onboarding/events';
import { showDocumentUploadError } from '@/features/templates/documentUploadError';
import { TemplatePreviewImage } from '@/features/templates/TemplatePreviewImage';
import './AddTemplateWizard.css';

type TemplateKind = 'email' | 'document';
type EmailFormat = 'simple' | 'visual' | 'upload';
export type WizardStep = 'format' | 'gallery' | 'custom';

type Props = {
  open: boolean;
  templateType: TemplateKind;
  step?: WizardStep;
  onStepChange?: (step: WizardStep) => void;
  onClose: () => void;
  onCreated: (template: Template) => void;
};

const EMAIL_IMPORT_ACCEPT = '.docx,.pdf,.html,.htm,.txt';
const DOCUMENT_UPLOAD_ACCEPT = '.docx,.pdf,.html,.htm';
const SIMPLE_EMAIL_UPLOAD_ACCEPT = '.docx,.pdf,.html,.htm,.txt';

function getAcceptString(templateType: TemplateKind, emailFormat: EmailFormat): string {
  if (templateType === 'document') return DOCUMENT_UPLOAD_ACCEPT;
  if (emailFormat === 'upload') return EMAIL_IMPORT_ACCEPT;
  return SIMPLE_EMAIL_UPLOAD_ACCEPT;
}

function getUploadHint(templateType: TemplateKind): string {
  if (templateType === 'document') return 'DOCX, PDF, HTML';
  return 'DOCX, PDF, HTML, TXT';
}

function getUploadOperation(
  file: File | null,
  templateType: TemplateKind,
  emailFormat: EmailFormat,
) {
  const extension = file?.name.split('.').pop()?.toLowerCase() || '';
  if (templateType === 'document') {
    if (extension === 'docx') {
      return {
        estimate: [20, 45] as [number, number],
        stages: [
          'Загружаем файл',
          'Проверяем формат и безопасность',
          'Извлекаем текст и определяем шрифты',
          'Готовим PDF-предпросмотр',
          'Сохраняем шаблон',
        ],
      };
    }
    return {
      estimate: [8, 20] as [number, number],
      stages: [
        'Загружаем файл',
        'Проверяем формат и безопасность',
        'Извлекаем текст и поля',
        'Готовим предпросмотр',
        'Сохраняем шаблон',
      ],
    };
  }
  if (emailFormat === 'upload') {
    return {
      estimate: [20, 60] as [number, number],
      stages: [
        'Загружаем файл',
        'Извлекаем содержимое',
        'Преобразуем документ в письмо',
        'Проверяем HTML-вёрстку',
        'Сохраняем черновик',
      ],
    };
  }
  return {
    estimate: [20, 60] as [number, number],
    stages: [
      'Загружаем файл',
      'Извлекаем содержимое',
      'Формируем структуру письма',
      'Проверяем результат',
      'Сохраняем шаблон',
    ],
  };
}

async function uploadTemplateFromFile(
  file: File,
  templateType: TemplateKind,
  emailFormat: EmailFormat,
): Promise<Template> {
  if (templateType === 'document') {
    return templatesApi.uploadFile(file, 'document');
  }
  if (emailFormat === 'upload') {
    return templatesApi.importFile(file);
  }
  return templatesApi.generate({
    template_type: 'email',
    prompt: '',
    files: [file],
  });
}

export function AddTemplateWizard({
  open,
  templateType,
  step: controlledStep,
  onStepChange,
  onClose,
  onCreated,
}: Props) {
  const { message, modal } = App.useApp();
  const defaultStep: WizardStep = templateType === 'email' ? 'format' : 'gallery';
  const [internalStep, setInternalStep] = useState<WizardStep>(defaultStep);
  const step = controlledStep ?? internalStep;
  const setStep = useCallback(
    (next: WizardStep) => {
      if (onStepChange) onStepChange(next);
      else setInternalStep(next);
    },
    [onStepChange],
  );
  const [emailFormat, setEmailFormat] = useState<EmailFormat>('simple');
  const [prompt, setPrompt] = useState('');
  const [model, setModel] = useState<string>();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [activeUploadFile, setActiveUploadFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragCounterRef = useRef(0);

  const acceptString = useMemo(
    () => getAcceptString(templateType, emailFormat),
    [emailFormat, templateType],
  );
  const uploadHint = useMemo(() => getUploadHint(templateType), [templateType]);
  const showDocumentGalleryUpload = templateType === 'document';

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
    if (!controlledStep) setInternalStep(defaultStep);
    setEmailFormat('simple');
    setPrompt('');
    setFileList([]);
    setActiveUploadFile(null);
    setModel(undefined);
    setIsDragging(false);
    dragCounterRef.current = 0;
  }, [controlledStep, defaultStep, open, templateType]);

  useEffect(() => {
    if (!open) return;
    const handleOnboardingEnter = (event: Event) => {
      const { stepId } = (event as CustomEvent<OnboardingEnterDetail>).detail || {};
      if (stepId === 'template-format') setStep('format');
      if (stepId === 'template-source') setStep('gallery');
      if (stepId === 'template-custom') setStep('custom');
    };
    window.addEventListener(ONBOARDING_ENTER_EVENT, handleOnboardingEnter);
    return () => window.removeEventListener(ONBOARDING_ENTER_EVENT, handleOnboardingEnter);
  }, [open, setStep]);

  useEffect(() => {
    if (!modelsQuery.data?.length || model) return;
    const preferred = modelsQuery.data.find((item) => item.default) || modelsQuery.data[0];
    setModel(preferred.id);
  }, [modelsQuery.data, model]);

  const filteredStarters = useMemo(() => {
    const items = startersQuery.data || [];
    if (templateType !== 'email') return items;
    if (emailFormat === 'upload') return [];
    return items.filter((starter) => (starter.email_format || 'simple') === emailFormat);
  }, [emailFormat, startersQuery.data, templateType]);

  const useStarterMutation = useMutation({
    mutationFn: (starterId: string) => templatesApi.useStarter(starterId),
    onSuccess: (template) => {
      message.success('Шаблон добавлен из примера');
      onCreated(template);
      advanceOnboarding('template-source', 'audience-open');
      onClose();
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось создать шаблон');
    },
  });

  const uploadFileMutation = useMutation({
    mutationFn: (file: File) => uploadTemplateFromFile(file, templateType, emailFormat),
    onMutate: (file) => {
      setActiveUploadFile(file);
    },
    onSuccess: (template) => {
      message.success(
        emailFormat === 'upload'
          ? 'Черновик импортирован — доработайте в редакторе'
          : 'Шаблон загружен',
      );
      onCreated(template);
      advanceOnboarding(emailFormat === 'upload' ? 'template-format' : 'template-source', 'audience-open');
      onClose();
    },
    onError: (error) => {
      if (templateType === 'document') {
        showDocumentUploadError(modal, error);
      } else {
        message.error(error instanceof Error ? error.message : 'Не удалось загрузить шаблон');
      }
    },
    onSettled: () => {
      setActiveUploadFile(null);
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
      advanceOnboarding('template-source', 'audience-open');
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
      advanceOnboarding('template-custom', 'audience-open');
      onClose();
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось создать шаблон');
    },
  });

  const isGalleryBusy =
    useStarterMutation.isPending || uploadFileMutation.isPending || createVisualBlankMutation.isPending;

  const handleUploadFile = useCallback(
    (file: File | undefined) => {
      if (!file || isGalleryBusy) return;
      uploadFileMutation.mutate(file);
    },
    [isGalleryBusy, uploadFileMutation],
  );
  const uploadOperation = useMemo(
    () => getUploadOperation(activeUploadFile, templateType, emailFormat),
    [activeUploadFile, emailFormat, templateType],
  );

  useEffect(() => {
    if (!open || step !== 'gallery' || !showDocumentGalleryUpload) {
      setIsDragging(false);
      dragCounterRef.current = 0;
      return;
    }

    const hasFiles = (event: DragEvent) =>
      Array.from(event.dataTransfer?.types || []).includes('Files');

    const onDragEnter = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      dragCounterRef.current += 1;
      setIsDragging(true);
    };

    const onDragLeave = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
      if (dragCounterRef.current === 0) {
        setIsDragging(false);
      }
    };

    const onDragOver = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
    };

    const onDrop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      dragCounterRef.current = 0;
      setIsDragging(false);
      handleUploadFile(event.dataTransfer?.files?.[0]);
    };

    document.addEventListener('dragenter', onDragEnter);
    document.addEventListener('dragleave', onDragLeave);
    document.addEventListener('dragover', onDragOver);
    document.addEventListener('drop', onDrop);

    return () => {
      document.removeEventListener('dragenter', onDragEnter);
      document.removeEventListener('dragleave', onDragLeave);
      document.removeEventListener('dragover', onDragOver);
      document.removeEventListener('drop', onDrop);
      dragCounterRef.current = 0;
      setIsDragging(false);
    };
  }, [handleUploadFile, open, showDocumentGalleryUpload, step]);

  const title = useMemo(() => {
    if (step === 'format') return 'Выберите тип письма';
    const kind = templateType === 'email' ? 'письма' : 'документа';
    return step === 'gallery' ? `Выберите пример ${kind}` : `Добавить свой шаблон ${kind}`;
  }, [step, templateType]);

  const footer = (() => {
    if (uploadFileMutation.isPending) {
      return (
        <Button disabled loading>
          Обработка файла
        </Button>
      );
    }
    if (step === 'format') {
      return (
        <Space>
          <Button onClick={onClose}>Отмена</Button>
          {emailFormat === 'upload' ? (
            <Button
              type="primary"
              icon={<UploadOutlined />}
              loading={uploadFileMutation.isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              Выбрать файл
            </Button>
          ) : (
            <Button
              type="primary"
              onClick={() => {
                setStep('gallery');
                advanceOnboarding('template-format');
              }}
            >
              Далее
            </Button>
          )}
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
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                setStep('custom');
                advanceOnboarding('template-source', 'template-custom');
              }}
            >
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

  const dropOverlay =
    open && step === 'gallery' && showDocumentGalleryUpload && isDragging
      ? createPortal(
          <div
            className={[
              'add-template-wizard__drop-overlay',
              'add-template-wizard__drop-overlay--active',
            ].join(' ')}
            aria-hidden
          >
            <div className="add-template-wizard__drop-overlay-text">
              Отпустите файл, чтобы загрузить шаблон
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      {dropOverlay}
      <input
        ref={fileInputRef}
        type="file"
        accept={acceptString}
        hidden
        onChange={(event) => {
          handleUploadFile(event.target.files?.[0]);
          event.target.value = '';
        }}
      />
      <Modal
        open={open}
        onCancel={() => {
          if (!uploadFileMutation.isPending) onClose();
        }}
        title={uploadFileMutation.isPending ? 'Обработка загруженного файла' : title}
        width={920}
        destroyOnClose
        closable={!uploadFileMutation.isPending}
        maskClosable={!uploadFileMutation.isPending}
        keyboard={!uploadFileMutation.isPending}
        footer={footer}
      >
        {uploadFileMutation.isPending ? (
          <div className="add-template-wizard__upload-progress">
            <div>
              <Typography.Text strong>{activeUploadFile?.name || 'Файл'}</Typography.Text>
              {activeUploadFile ? (
                <Typography.Text type="secondary">
                  {(activeUploadFile.size / 1024 / 1024).toFixed(1)} МБ
                </Typography.Text>
              ) : null}
            </div>
            <OperationProgress
              active
              title="Подготавливаем шаблон"
              stages={uploadOperation.stages}
              estimatedSeconds={uploadOperation.estimate}
            />
          </div>
        ) : step === 'format' ? (
          <div className="add-template-wizard__format-grid" data-onboarding-id="template-format">
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
            <Card
              hoverable
              onClick={() => setEmailFormat('upload')}
              style={{ borderColor: emailFormat === 'upload' ? '#236348' : undefined }}
            >
              <Typography.Title level={5}>
                <UploadOutlined /> Загрузить шаблон
              </Typography.Title>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                PDF/DOCX → редактируемый HTML-шаблон. Проверьте вёрстку в редакторе перед отправкой.
              </Typography.Paragraph>
            </Card>
          </div>
        ) : step === 'gallery' ? (
          <div className="add-template-wizard__gallery" data-onboarding-id="template-source">
            {showDocumentGalleryUpload ? (
              <button
                type="button"
                className="starter-tile starter-tile--upload"
                disabled={isGalleryBusy}
                onClick={() => fileInputRef.current?.click()}
              >
                <div className="starter-tile__upload-icon" aria-hidden>
                  {uploadFileMutation.isPending ? <LoadingOutlined /> : <UploadOutlined />}
                </div>
                <Typography.Text strong>Загрузить свой</Typography.Text>
                <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
                  {uploadHint}
                </Typography.Paragraph>
              </button>
            ) : null}
            {filteredStarters.map((starter) => (
              <button
                key={starter.id}
                type="button"
                className="starter-tile"
                onClick={() => useStarterMutation.mutate(starter.id)}
                disabled={isGalleryBusy}
              >
                <TemplatePreviewImage starterId={starter.id} alt={starter.name} />
                <Typography.Text strong>{starter.name}</Typography.Text>
                {starter.subject ? (
                  <Typography.Paragraph type="secondary" style={{ margin: 0 }}>
                    {starter.subject}
                  </Typography.Paragraph>
                ) : null}
              </button>
            ))}
            {filteredStarters.length === 0 && (
              <Typography.Paragraph type="secondary">
                {emailFormat === 'visual'
                  ? 'Примеры для HTML-письма пока не добавлены — создайте пустой шаблон.'
                  : 'Примеры для выбранного типа пока не добавлены — добавьте свой шаблон.'}
              </Typography.Paragraph>
            )}
          </div>
        ) : (
          <Space
            direction="vertical"
            size="middle"
            style={{ width: '100%' }}
            data-onboarding-id="template-custom"
          >
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
                accept={acceptString}
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
    </>
  );
}
