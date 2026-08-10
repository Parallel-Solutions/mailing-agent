import {
  ProForm,
  ProFormDateTimePicker,
  ProFormDigit,
  ProFormSelect,
} from '@ant-design/pro-components';
import { Form, Space, Typography, type FormInstance } from 'antd';
import {
  SCHEDULE_DATE_TIME_FORMAT,
  type ScheduleFormValues,
} from '@/utils/scheduleForm';

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
      <div data-onboarding-id="campaign-batch-size">
        <ProFormDigit
          name="batch_size"
          label="Размер пакета"
          min={1}
          fieldProps={{ min: 1, precision: 0, step: 1 }}
          rules={[
            { required: true, message: 'Укажите размер пакета' },
            { type: 'number', min: 1, message: 'Размер пакета должен быть больше нуля' },
          ]}
        />
      </div>
      <div data-onboarding-id="campaign-start-at">
        <ProFormDateTimePicker
          name="start_at"
          label="Дата и время старта"
          rules={[{ required: true, message: 'Укажите дату и время старта' }]}
          fieldProps={{ style: { width: '100%' }, format: SCHEDULE_DATE_TIME_FORMAT }}
        />
      </div>
      <div data-onboarding-id="campaign-interval">
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
      </div>
      <div data-onboarding-id="campaign-schedule-preview">
        <Typography.Text>
          Прогноз: {batchCountPreview} пакетов
          {estimatedDurationHours && estimatedDurationHours > 0
            ? `, длительность ≈ ${estimatedDurationHours} ч`
            : ''}
        </Typography.Text>
      </div>
    </ProForm>
  );
}
