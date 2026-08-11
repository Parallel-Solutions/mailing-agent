import { App, Card, Select, Typography } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';

export function canConfigureDocumentPageLimit(template: Template): boolean {
  const filename = template.version?.filename?.toLowerCase() || '';
  return Boolean(
    template.is_template
      && filename.endsWith('.docx')
      && template.attachment_output_format === 'pdf',
  );
}

export function documentPageMode(template: Template): 'one_page' | 'multi_page' {
  return template.enforce_one_page === false ? 'multi_page' : 'one_page';
}

export function DocumentPageLimitField({ template }: { template: Template }) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const saveMutation = useMutation({
    mutationFn: (value: 'one_page' | 'multi_page') =>
      templatesApi.save(template.id, { enforce_one_page: value === 'one_page' }),
    onSuccess: () => {
      message.success('Режим страниц сохранён');
      void queryClient.invalidateQueries({ queryKey: ['template', template.id] });
      void queryClient.invalidateQueries({ queryKey: ['templates'] });
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось сохранить режим страниц');
    },
  });

  if (!canConfigureDocumentPageLimit(template)) return null;

  const value = documentPageMode(template);
  return (
    <Card className="template-side-panel" title="Страницы PDF">
      <Typography.Text>Ограничение коммерческого предложения</Typography.Text>
      <Select
        style={{ width: '100%', marginTop: 8 }}
        value={value}
        disabled={saveMutation.isPending}
        options={[
          { value: 'one_page', label: 'Одна страница' },
          { value: 'multi_page', label: 'Несколько страниц' },
        ]}
        onChange={(next: 'one_page' | 'multi_page') => saveMutation.mutate(next)}
      />
      <Typography.Paragraph
        type="secondary"
        style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}
      >
        {value === 'one_page'
          ? 'Система уменьшает основной шрифт до 8,5 pt и блокирует отправку, если КП всё равно не помещается на одну страницу.'
          : 'Исходная вёрстка и размер шрифта сохраняются; PDF может содержать несколько страниц.'}
      </Typography.Paragraph>
    </Card>
  );
}
