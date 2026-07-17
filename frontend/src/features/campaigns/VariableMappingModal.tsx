import { CheckCircleOutlined } from '@ant-design/icons';
import { App, Button, Modal, Select, Spin, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { campaignsApi, type VariableMappingSuggestResult } from '@/api/campaigns';

type ModalPhase = 'loading' | 'success' | 'review';

type Props = {
  open: boolean;
  campaignId: string;
  onClose: () => void;
  onConfirmed: () => void;
};

export function VariableMappingModal({ open, campaignId, onClose, onConfirmed }: Props) {
  const { message } = App.useApp();
  const [phase, setPhase] = useState<ModalPhase>('loading');
  const [result, setResult] = useState<VariableMappingSuggestResult | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const columnOptions = useMemo(
    () =>
      (result?.recipient_columns || []).map((column) => ({
        label: column,
        value: column,
      })),
    [result?.recipient_columns],
  );

  const saveMapping = async (nextMapping: Record<string, string>) => {
    setSaving(true);
    try {
      await campaignsApi.saveVariableMapping(campaignId, nextMapping);
      setPhase('success');
      window.setTimeout(() => {
        onConfirmed();
        onClose();
      }, 1500);
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Не удалось сохранить сопоставление');
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    if (!open) {
      setPhase('loading');
      setResult(null);
      setMapping({});
      return;
    }

    let cancelled = false;
    setPhase('loading');

    void (async () => {
      try {
        const suggest = await campaignsApi.suggestVariableMapping(campaignId);
        if (cancelled) return;
        setResult(suggest);
        setMapping(suggest.suggested_mapping || {});

        if (suggest.status === 'complete') {
          await saveMapping(suggest.suggested_mapping || {});
          if (cancelled) return;
          return;
        }
        setPhase('review');
      } catch (error) {
        if (cancelled) return;
        message.error(error instanceof Error ? error.message : 'Не удалось подобрать переменные');
        onClose();
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, campaignId]);

  const reviewIncomplete = (result?.template_variables || []).some(
    (item) => !mapping[item.name],
  );

  return (
    <Modal
      title="Сопоставление переменных"
      open={open}
      onCancel={onClose}
      footer={
        phase === 'review' ? (
          <Button
            type="primary"
            disabled={reviewIncomplete || saving}
            loading={saving}
            onClick={() => void saveMapping(mapping)}
          >
            ОК
          </Button>
        ) : null
      }
      closable={phase !== 'loading'}
      maskClosable={phase !== 'loading'}
      destroyOnHidden
      width={720}
    >
      {phase === 'loading' && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '48px 0' }}>
          <Spin size="large" tip="Подбираем соответствия…" />
        </div>
      )}

      {phase === 'success' && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '32px 0', gap: 12 }}>
          <CheckCircleOutlined style={{ fontSize: 48, color: '#52c41a' }} />
          <Typography.Text>Сопоставление сохранено</Typography.Text>
        </div>
      )}

      {phase === 'review' && result && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            Проверьте соответствие переменных шаблона колонкам списка получателей.
          </Typography.Paragraph>
          {(result.template_variables || []).map((item) => (
            <div
              key={item.name}
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 1fr',
                gap: 12,
                alignItems: 'center',
              }}
            >
              <Typography.Text strong>{item.label || item.name}</Typography.Text>
              <Select
                allowClear
                placeholder="Выберите колонку"
                options={columnOptions}
                value={mapping[item.name] || undefined}
                onChange={(value) =>
                  setMapping((current) => {
                    const next = { ...current };
                    if (value) next[item.name] = value;
                    else delete next[item.name];
                    return next;
                  })
                }
              />
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
