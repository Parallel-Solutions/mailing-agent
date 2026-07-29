import { CheckCircleOutlined } from '@ant-design/icons';
import { App, AutoComplete, Button, Modal, Spin, Tag, Typography } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { campaignsApi, type VariableMappingSuggestResult } from '@/api/campaigns';
import { campaignVariableMappingQueryKey } from '@/features/campaigns/campaignQueryUtils';
import { fetchVariableMappingSuggest } from '@/features/campaigns/mappingAutoSuggestUtils';
import {
  mappingToDisplayValues,
  mappingToStorageValues,
} from '@/features/campaigns/variableMappingUtils';
import './VariableMappingModal.css';

type ModalPhase = 'loading' | 'success' | 'review';

type Props = {
  open: boolean;
  campaignId: string;
  mappingInputsSignature: string;
  skipSuggestIfConfirmed?: boolean;
  onClose: () => void;
  onConfirmed: () => void;
};

function stateToSuggestResult(state: Awaited<ReturnType<typeof campaignsApi.getVariableMapping>>): VariableMappingSuggestResult {
  return {
    status: state.mapping_confirmed ? 'complete' : 'needs_review',
    template_variables: state.template_variables,
    recipient_columns: state.recipient_columns,
    suggested_mapping: state.variable_mapping,
    system_variables: state.system_variables,
    unmapped: [],
  };
}

export function VariableMappingModal({
  open,
  campaignId,
  mappingInputsSignature,
  skipSuggestIfConfirmed = true,
  onClose,
  onConfirmed,
}: Props) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<ModalPhase>('loading');
  const [result, setResult] = useState<VariableMappingSuggestResult | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const recipientColumns = useMemo(() => result?.recipient_columns || [], [result?.recipient_columns]);

  const columnOptions = useMemo(
    () =>
      recipientColumns.map((column) => ({
        label: column,
        value: column,
      })),
    [recipientColumns],
  );

  const recipientVariables = useMemo(() => {
    const systemKeys = new Set(Object.keys(result?.system_variables || {}));
    return (result?.template_variables || []).filter((item) => !systemKeys.has(item.name));
  }, [result]);

  const systemVariables = useMemo(
    () => Object.entries(result?.system_variables || {}),
    [result?.system_variables],
  );

  const saveMapping = useCallback(
    async (nextMapping: Record<string, string>, options?: { auto?: boolean }) => {
      setSaving(true);
      try {
        const stored = mappingToStorageValues(nextMapping, recipientColumns);
        await campaignsApi.saveVariableMapping(campaignId, stored);
        void queryClient.removeQueries({ queryKey: campaignVariableMappingQueryKey(campaignId) });
        if (options?.auto) {
          onConfirmed();
          onClose();
          return;
        }
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
    },
    [campaignId, message, onClose, onConfirmed, queryClient, recipientColumns],
  );

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
        const state = await queryClient.fetchQuery({
          queryKey: campaignVariableMappingQueryKey(campaignId),
          queryFn: () => campaignsApi.getVariableMapping(campaignId),
          staleTime: Infinity,
        });
        if (cancelled) return;

        if (skipSuggestIfConfirmed && state.mapping_confirmed) {
          const saved = stateToSuggestResult(state);
          setResult(saved);
          setMapping(mappingToDisplayValues(state.variable_mapping || {}));
          setPhase('review');
          return;
        }

        const suggest = await fetchVariableMappingSuggest(
          queryClient,
          campaignId,
          mappingInputsSignature,
        );
        if (cancelled) return;
        setResult(suggest);
        setMapping(mappingToDisplayValues(suggest.suggested_mapping || {}));

        if (suggest.status === 'complete') {
          await saveMapping(mappingToDisplayValues(suggest.suggested_mapping || {}), { auto: true });
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
  }, [
    open,
    campaignId,
    mappingInputsSignature,
    message,
    onClose,
    queryClient,
    saveMapping,
    skipSuggestIfConfirmed,
  ]);

  const reviewIncomplete = recipientVariables.some((item) => !mapping[item.name]?.trim());

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
      width={760}
      className="variable-mapping-modal"
    >
      {phase === 'loading' && (
        <div className="variable-mapping-modal__loading">
          <Spin size="large" tip="Подбираем соответствия…" />
        </div>
      )}

      {phase === 'success' && (
        <div className="variable-mapping-modal__success">
          <CheckCircleOutlined className="variable-mapping-modal__success-icon" />
          <Typography.Text>Сопоставление сохранено</Typography.Text>
        </div>
      )}

      {phase === 'review' && result && (
        <div className="variable-mapping-modal__body">
          <Typography.Paragraph type="secondary" className="variable-mapping-modal__hint">
            Слева — переменные из шаблонов. Справа выберите колонку списка получателей или введите
            своё значение для всей рассылки.
          </Typography.Paragraph>

          <div className="variable-mapping-modal__header-row">
            <Typography.Text strong>Переменная шаблона</Typography.Text>
            <Typography.Text strong>Колонка или своё значение</Typography.Text>
          </div>

          {systemVariables.length > 0 && (
            <div className="variable-mapping-modal__section">
              <Typography.Text strong className="variable-mapping-modal__section-title">
                Системные (авто)
              </Typography.Text>
              {systemVariables.map(([name, canonical]) => (
                <div key={name} className="variable-mapping-modal__row">
                  <Typography.Text>{name}</Typography.Text>
                  <Tag color="green">{canonical}</Tag>
                </div>
              ))}
            </div>
          )}

          {recipientVariables.map((item) => (
            <div key={item.name} className="variable-mapping-modal__row">
              <div className="variable-mapping-modal__variable">
                <Typography.Text strong>{item.label || item.name}</Typography.Text>
                {item.label && item.label !== item.name ? (
                  <Typography.Text type="secondary" className="variable-mapping-modal__variable-code">
                    {item.name}
                  </Typography.Text>
                ) : null}
              </div>
              <AutoComplete
                className="variable-mapping-modal__input"
                allowClear
                options={columnOptions}
                placeholder="Колонка или своё значение"
                value={mapping[item.name] || ''}
                onChange={(value) =>
                  setMapping((current) => {
                    const next = { ...current };
                    const trimmed = String(value || '').trim();
                    if (trimmed) next[item.name] = trimmed;
                    else delete next[item.name];
                    return next;
                  })
                }
                filterOption={(input, option) =>
                  String(option?.value || '')
                    .toLowerCase()
                    .includes(input.toLowerCase())
                }
              />
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}
