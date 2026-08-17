import { ProForm, ProFormSelect } from '@ant-design/pro-components';
import { Button, type FormInstance } from 'antd';
import { useMemo } from 'react';
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
  // Memoized so `showSearch`'s rc-select internals see a stable `options`
  // identity across re-renders that don't actually change `mailboxes` (e.g.
  // while the user is typing into the search box) — an inline `.map()` here
  // handed rc-select a brand-new array on every parent re-render, which
  // combined with rapid typing was observed to overflow React's nested-update
  // guard ("Maximum update depth exceeded", minified as error #185) inside
  // rc-select's own open/search state handling.
  const mailboxOptions = useMemo(
    () =>
      mailboxes.map((m) => ({
        label: `${m.transport === 'smtp' ? 'SMTP' : m.transport === 'rusender' ? 'RuSender' : 'MailoPost'} · ${m.email}${m.is_default ? ' (по умолчанию)' : ''}`,
        value: m.id,
      })),
    [mailboxes],
  );

  return (
    <ProForm form={form} submitter={false} initialValues={draft} onValuesChange={(_, values) => onAutosave(values)}>
      <div data-onboarding-id="campaign-sender-connection">
        <ProFormSelect
          name="smtp_mailbox_id"
          label="Подключение отправителя"
          placeholder="Выберите SMTP, RuSender или MailoPost"
          options={mailboxOptions}
          fieldProps={{
            showSearch: true,
            optionFilterProp: 'label',
            // The connections list is small (never virtualization-scale), and
            // rc-virtual-list's height recalculation on every filtered
            // keystroke was observed (via a sourcemap-decoded prod stack —
            // `@rc-component/trigger`'s `useAlign`/`onAlign` → `setOffsetInfo`)
            // to retrigger the dropdown's popup-position realignment fast
            // enough, during rapid typing, to overflow React's nested-update
            // guard ("Maximum update depth exceeded", minified as error
            // #185). Disabling virtualization for this bounded list sidesteps
            // that resize/realign feedback loop entirely.
            virtual: false,
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
