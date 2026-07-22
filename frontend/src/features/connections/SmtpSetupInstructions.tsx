import { Alert, Tag, Typography } from 'antd';
import type { CSSProperties } from 'react';
import type { SmtpSetupAction } from '@/api/connections';

const URL_PATTERN = /(https?:\/\/[^\s]+)/g;

function renderTextWithLinks(text: string) {
  const parts = text.split(URL_PATTERN);
  return parts.map((part, index) => {
    if (/^https?:\/\//.test(part)) {
      return (
        <Typography.Link key={index} href={part} target="_blank" rel="noopener noreferrer">
          {part}
        </Typography.Link>
      );
    }
    return part;
  });
}

type SmtpSetupInstructionsProps = {
  action: SmtpSetupAction;
  style?: CSSProperties;
};

export function SmtpSetupInstructions({ action, style }: SmtpSetupInstructionsProps) {
  if (!action.message_ru && action.instructions.length === 0) {
    return null;
  }

  const description = action.instructions.length > 0 ? (
    <ol style={{ margin: '8px 0 0', paddingLeft: 20 }}>
      {action.instructions.map((instruction, index) => (
        <li key={index} style={{ marginBottom: 4 }}>
          {renderTextWithLinks(instruction)}
        </li>
      ))}
    </ol>
  ) : undefined;

  return (
    <Alert
      type="info"
      showIcon
      message={(
        <span>
          {action.message_ru}
          {action.ai_used ? (
            <Tag color="blue" style={{ marginLeft: 8 }}>
              Подсказка AI
            </Tag>
          ) : null}
        </span>
      )}
      description={description}
      style={style}
    />
  );
}
