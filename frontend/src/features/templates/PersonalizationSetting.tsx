import { App, Card, Checkbox, Typography } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { templatesApi } from '@/api/templates';
import type { Template } from '@/api/types';

export function PersonalizationSetting({ template }: { template: Template }) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [checked, setChecked] = useState(Boolean(template.is_template));
  useEffect(() => {
    setChecked(Boolean(template.is_template));
  }, [template.id, template.is_template]);
  const saveMutation = useMutation({
    mutationFn: (value: boolean) => templatesApi.save(template.id, { is_template: value }),
    onSuccess: () => {
      message.success('Настройка сохранена');
      void queryClient.invalidateQueries({ queryKey: ['template', template.id] });
    },
    onError: () => {
      setChecked(Boolean(template.is_template));
    },
  });
  return (
    <Card className="template-side-panel" title="Настройки документа">
      <Checkbox
        checked={checked}
        disabled={saveMutation.isPending}
        onChange={(event) => {
          const next = event.target.checked;
          setChecked(next);
          saveMutation.mutate(next);
        }}
      >
        Персонализировать для каждого получателя
      </Checkbox>
      <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
        Если включено, при отправке рассылки документ будет заполнен данными каждого получателя.
      </Typography.Paragraph>
    </Card>
  );
}
