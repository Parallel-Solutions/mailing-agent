import { App, Card, Select, Typography } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';

export function AttachmentOutputFormatField({ template }: { template: Template }) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const isPptx = template.version?.filename?.toLowerCase().endsWith('.pptx') ?? false;
  const savedValue = isPptx ? 'original' : template.attachment_output_format || 'original';
  const saveMutation = useMutation({
    mutationFn: (value: 'original' | 'pdf') =>
      templatesApi.save(template.id, { attachment_output_format: value }),
    onSuccess: () => {
      message.success('Формат вложения сохранён');
      void queryClient.invalidateQueries({ queryKey: ['template', template.id] });
      void queryClient.invalidateQueries({ queryKey: ['templates'] });
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось сохранить формат');
    },
  });

  return (
    <Card className="template-side-panel" title="Формат вложения">
      <Typography.Text>Переводить документ в другой формат</Typography.Text>
      <Select
        style={{ width: '100%', marginTop: 8 }}
        value={savedValue}
        disabled={saveMutation.isPending || isPptx}
        options={[
          { value: 'original', label: 'Нет' },
          { value: 'pdf', label: 'PDF' },
        ]}
        onChange={(value: 'original' | 'pdf') => saveMutation.mutate(value)}
      />
      <Typography.Paragraph
        type="secondary"
        style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}
      >
        {isPptx
          ? 'PPTX отправляется оригиналом; текстовые замечания не блокируют отправку.'
          : 'При выборе PDF в цепочку и предпросмотр попадёт только сконвертированный файл.'}
      </Typography.Paragraph>
    </Card>
  );
}
