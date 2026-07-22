import {
  ProForm,
  ProFormDateTimePicker,
  ProFormDigit,
  ProFormSelect,
} from '@ant-design/pro-components';
import { Form, Space, Typography, type FormInstance } from 'antd';
import type { ScheduleFormValues } from '@/utils/scheduleForm';

type Props = {
  form: FormInstance;
  initialValues: ScheduleFormValues;
  batchCountPreview: number;
  estimatedDurationHours?: number;
  onValuesChange: (values: ScheduleFormValues) => void | Promise<void>;
};

export function CampaignWizardScheduleStep({
  form,
  initialValues,
  batchCountPreview,
  estimatedDurationHours,
  onValuesChange,
}: Props) {
  return (
    <ProForm
      form={form}
      submitter={false}
      initialValues={initialValues}
      onValuesChange={async (_, values) => {
        await onValuesChange(values as ScheduleFormValues);
      }}
    >
      <ProFormDigit name="batch_size" label="Размер пакета" min={1} fieldProps={{ precision: 0 }} />
      <ProFormDateTimePicker
        name="start_at"
        label="Дата и время старта"
        rules={[{ required: true, message: 'Укажите дату и время старта' }]}
        fieldProps={{ style: { width: '100%' }, format: 'DD.MM.YYYY HH:mm' }}
      />
      <Form.Item label="Интервал между пакетами" required>
        <Space align="start">
          <ProFormDigit
            name="interval_value"
            min={1}
            width="sm"
            fieldProps={{ precision: 0 }}
            rules={[{ required: true, message: 'Укажите интервал' }]}
            formItemProps={{ style: { marginBottom: 0 } }}
          />
          <ProFormSelect
            name="interval_unit"
            width="sm"
            options={[
              { label: 'часы', value: 'hours' },
              { label: 'дни', value: 'days' },
            ]}
            rules={[{ required: true }]}
            formItemProps={{ style: { marginBottom: 0 } }}
          />
        </Space>
      </Form.Item>
      <Typography.Text>
        Прогноз: {batchCountPreview} пакетов
        {estimatedDurationHours && estimatedDurationHours > 0
          ? `, длительность ≈ ${estimatedDurationHours} ч`
          : ''}
      </Typography.Text>
    </ProForm>
  );
}
