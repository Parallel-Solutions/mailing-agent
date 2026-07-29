import { App, Card, Input, Typography } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';

type Props = {
  template: Template;
};

function isDocumentTemplateType(templateType: string): boolean {
  return templateType === 'document' || templateType === 'kp' || templateType === 'contract';
}

export function DeliveryFilenameField({ template }: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const savedValue = template.version?.rendered_pdf_filename || '';
  const [value, setValue] = useState(savedValue);
  const hasDeliveryPdf = Boolean(template.version?.rendered_pdf_filename);

  useEffect(() => {
    setValue(savedValue);
  }, [template.id, template.version?.id, savedValue]);

  const saveMutation = useMutation({
    mutationFn: (nextValue: string) =>
      templatesApi.save(template.id, { rendered_pdf_filename: nextValue }),
    onSuccess: () => {
      message.success('Имя вложения сохранено');
      void queryClient.invalidateQueries({ queryKey: ['template', template.id] });
      void queryClient.invalidateQueries({ queryKey: ['templates'] });
    },
    onError: (error) => {
      setValue(savedValue);
      message.error(error instanceof Error ? error.message : 'Не удалось сохранить имя вложения');
    },
  });

  if (!isDocumentTemplateType(template.template_type)) {
    return null;
  }

  const commit = () => {
    const trimmed = value.trim();
    if (!trimmed) {
      setValue(savedValue);
      message.warning('Имя PDF-вложения не может быть пустым');
      return;
    }
    if (trimmed === savedValue) {
      return;
    }
    saveMutation.mutate(trimmed);
  };

  return (
    <Card className="template-side-panel" title="Имя в письме">
      <Input
        value={value}
        disabled={saveMutation.isPending}
        placeholder={hasDeliveryPdf ? 'КП_СТП_районы.pdf' : 'Появится после загрузки PDF'}
        onChange={(event) => setValue(event.target.value)}
        onBlur={commit}
        onPressEnter={commit}
      />
      <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
        Имя PDF-файла, который увидит получатель во вложении.
      </Typography.Paragraph>
    </Card>
  );
}
