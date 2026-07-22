import { Button, Tooltip } from 'antd';
import type { ButtonProps } from 'antd';
import { VALIDATION_AUTO_FIX_UI_ENABLED } from '@/features/campaigns/campaignQueryUtils';

type Props = Pick<ButtonProps, 'type' | 'ghost' | 'loading' | 'onClick'>;

export function ValidationAutoFixButton({ type, ghost, loading, onClick }: Props) {
  const button = (
    <Button
      type={type}
      ghost={ghost}
      loading={loading}
      disabled={!VALIDATION_AUTO_FIX_UI_ENABLED}
      onClick={VALIDATION_AUTO_FIX_UI_ENABLED ? onClick : undefined}
    >
      Исправить с помощью ИИ
    </Button>
  );

  if (!VALIDATION_AUTO_FIX_UI_ENABLED) {
    return (
      <Tooltip title="Временно недоступно">
        <span>{button}</span>
      </Tooltip>
    );
  }

  return button;
}
