import { ProForm, ProFormSelect } from '@ant-design/pro-components';
import { Button, type FormInstance } from 'antd';
import type { Campaign, DeliveryConnection } from '@/api/types';

type Props = {
  form: FormInstance;
  draft: Partial<Campaign>;
  mailboxes: DeliveryConnection[];
  onAutosave: (patch: Record<string, unknown>) => void;
  onNavigateConnections: () => void;
};

export function CampaignWizardSenderStep({
  form,
  draft,
  mailboxes,
  onAutosave,
  onNavigateConnections,
}: Props) {
  return (
    <ProForm form={form} submitter={false} initialValues={draft} onValuesChange={(_, values) => onAutosave(values)}>
      <div data-onboarding-id="campaign-sender-connection">
        <ProFormSelect
          name="smtp_mailbox_id"
          label="Подключение отправителя"
          placeholder="Выберите SMTP, RuSender или MailoPost"
          options={mailboxes.map((m) => ({
            label: `${m.transport === 'smtp' ? 'SMTP' : m.transport === 'rusender' ? 'RuSender' : 'MailoPost'} · ${m.email}${m.is_default ? ' (по умолчанию)' : ''}`,
            value: m.id,
          }))}
          fieldProps={{
            onChange: (value: string) => {
              const connection = mailboxes.find((item) => item.id === value);
              if (!connection) return;
              form.setFieldValue('transport', connection.transport);
              onAutosave({ smtp_mailbox_id: value, transport: connection.transport });
            },
          }}
          rules={[{ required: true, message: 'Выберите подключение отправителя' }]}
        />
      </div>
      <ProFormSelect
        name="transport"
        label="Способ отправки"
        disabled
        options={[
          { label: 'SMTP', value: 'smtp' },
          { label: 'RuSender', value: 'rusender' },
          { label: 'MailoPost', value: 'mailopost' },
        ]}
      />
      <Button onClick={onNavigateConnections}>Управлять подключениями</Button>
    </ProForm>
  );
}
