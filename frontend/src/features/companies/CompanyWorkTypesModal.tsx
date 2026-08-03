import { CloseOutlined } from '@ant-design/icons';
import { App, Button, Input, Modal, Space, Typography } from 'antd';
import { cloneElement, useCallback, useEffect, useRef, useState, type MouseEvent, type ReactElement } from 'react';
import { companiesApi } from '@/api/companies';
import type { Company } from '@/api/types';

type Row = {
  clientKey: string;
  id?: string;
  name: string;
};

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

type TriggerProps = {
  onClick?: (event: MouseEvent<HTMLElement>) => void;
};

type CompanyWorkTypesModalProps = {
  company: Company;
  trigger?: ReactElement<TriggerProps>;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onboardingPreview?: boolean;
};

function createEmptyRow(): Row {
  const clientKey = globalThis.crypto?.randomUUID?.()
    ?? `work-type-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return { clientKey, name: '' };
}

function ensureTrailingEmptyRow(rows: Row[]): Row[] {
  if (rows.length === 0 || rows[rows.length - 1].name.trim()) {
    return [...rows, createEmptyRow()];
  }
  return rows;
}

export function CompanyWorkTypesModal({
  company,
  trigger,
  open: controlledOpen,
  onOpenChange,
  onboardingPreview = false,
}: CompanyWorkTypesModalProps) {
  const { message } = App.useApp();
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = (nextOpen: boolean) => {
    if (controlledOpen === undefined) {
      setInternalOpen(nextOpen);
    }
    onOpenChange?.(nextOpen);
  };
  const [rows, setRows] = useState<Row[]>([createEmptyRow()]);
  const [loading, setLoading] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>('idle');
  const debounceRef = useRef<number | null>(null);
  const rowsRef = useRef(rows);

  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, []);

  const loadRows = useCallback(async () => {
    setLoading(true);
    try {
      const items = await companiesApi.workTypes.list(company.id);
      setRows(ensureTrailingEmptyRow(items.map((item) => ({ clientKey: item.id, id: item.id, name: item.name }))));
      setSaveState('idle');
    } catch (err) {
      message.error(err instanceof Error ? err.message : 'Не удалось загрузить виды работ');
    } finally {
      setLoading(false);
    }
  }, [company.id, message]);

  const persistRow = useCallback(
    async (clientKey: string) => {
      const row = rowsRef.current.find((item) => item.clientKey === clientKey);
      if (!row) return;

      const trimmed = row.name.trim();
      if (!trimmed) return;

      setSaveState('saving');
      try {
        if (row.id) {
          const updated = await companiesApi.workTypes.update(company.id, row.id, { name: trimmed });
          setRows((current) =>
            ensureTrailingEmptyRow(
              current.map((item) =>
                item.clientKey === clientKey ? { ...item, id: updated.id, name: updated.name } : item,
              ),
            ),
          );
        } else {
          const created = await companiesApi.workTypes.create(company.id, { name: trimmed });
          setRows((current) =>
            ensureTrailingEmptyRow(
              current.map((item) =>
                item.clientKey === clientKey
                  ? { clientKey: created.id, id: created.id, name: created.name }
                  : item,
              ),
            ),
          );
        }
        setSaveState('saved');
      } catch (err) {
        setSaveState('error');
        message.error(err instanceof Error ? err.message : 'Не удалось сохранить вид работ');
      }
    },
    [company.id, message],
  );

  const scheduleSave = useCallback(
    (clientKey: string) => {
      if (onboardingPreview) return;
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
      debounceRef.current = window.setTimeout(() => {
        void persistRow(clientKey);
      }, 700);
    },
    [onboardingPreview, persistRow],
  );

  const handleNameChange = (clientKey: string, value: string) => {
    setRows((current) => {
      const index = current.findIndex((item) => item.clientKey === clientKey);
      if (index < 0) return current;

      const next = current.map((item) => (item.clientKey === clientKey ? { ...item, name: value } : item));
      const isLast = index === current.length - 1;
      if (isLast && value.trim()) {
        return [...next, createEmptyRow()];
      }
      return next;
    });
    setSaveState('idle');

    const trimmed = value.trim();
    if (trimmed) {
      scheduleSave(clientKey);
    }
  };

  const handleDelete = async (clientKey: string) => {
    const row = rowsRef.current.find((item) => item.clientKey === clientKey);
    if (!row) return;

    if (debounceRef.current) window.clearTimeout(debounceRef.current);

    if (row.id) {
      setSaveState('saving');
      try {
        await companiesApi.workTypes.remove(company.id, row.id);
        setRows((current) => ensureTrailingEmptyRow(current.filter((item) => item.clientKey !== clientKey)));
        setSaveState('saved');
      } catch (err) {
        setSaveState('error');
        message.error(err instanceof Error ? err.message : 'Не удалось удалить вид работ');
      }
      return;
    }

    setRows((current) => ensureTrailingEmptyRow(current.filter((item) => item.clientKey !== clientKey)));
    setSaveState('idle');
  };

  const saveLabel =
    saveState === 'saving'
      ? 'Сохранение…'
      : saveState === 'saved'
        ? 'Сохранено'
        : saveState === 'error'
          ? 'Ошибка сохранения'
          : '';

  return (
    <>
      {trigger
        ? cloneElement(trigger, {
            onClick: (event: MouseEvent<HTMLElement>) => {
              trigger.props.onClick?.(event);
              setOpen(true);
            },
          })
        : null}
      <Modal
        title={(
          <span data-onboarding-id="company-work-types">
            Виды работ — {company.name}
          </span>
        )}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnHidden
        afterOpenChange={(nextOpen) => {
          if (nextOpen) {
            if (onboardingPreview) {
              setRows([
                { clientKey: 'preview-audit', name: 'Аудит бизнес-процесса' },
                { clientKey: 'preview-automation', name: 'Автоматизация отчётности' },
                { clientKey: 'preview-empty', name: '' },
              ]);
            } else {
              void loadRows();
            }
          } else {
            if (debounceRef.current) window.clearTimeout(debounceRef.current);
            setRows([createEmptyRow()]);
            setSaveState('idle');
          }
        }}
      >
        <Space
          direction="vertical"
          size="middle"
          style={{ width: '100%' }}
        >
          <Typography.Text type="secondary">
            Введите название вида работ. Новая строка появится автоматически, изменения сохраняются сами.
          </Typography.Text>

          {rows.map((row) => (
            <Space.Compact key={row.clientKey} style={{ width: '100%' }}>
              <Input
                value={row.name}
                placeholder="Название вида работ"
                disabled={loading}
                readOnly={onboardingPreview}
                onChange={(event) => handleNameChange(row.clientKey, event.target.value)}
              />
              <Button
                aria-label="Удалить вид работ"
                icon={<CloseOutlined />}
                disabled={loading || onboardingPreview}
                onClick={() => {
                  void handleDelete(row.clientKey);
                }}
              />
            </Space.Compact>
          ))}

          {saveLabel ? (
            <Typography.Text type={saveState === 'error' ? 'danger' : 'secondary'}>{saveLabel}</Typography.Text>
          ) : null}
        </Space>
      </Modal>
    </>
  );
}
