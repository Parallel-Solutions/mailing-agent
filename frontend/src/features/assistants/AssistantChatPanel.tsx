import { RobotOutlined, SendOutlined } from '@ant-design/icons';
import { Alert, Button, Input, Space, Tag, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import type { AssistantChatMessage } from './types';
import './assistant.css';

type Props = {
  messages: AssistantChatMessage[];
  pending: boolean;
  error: string | null;
  onSend: (message: string) => void;
  placeholder?: string;
};

export function AssistantChatPanel({
  messages,
  pending,
  error,
  onSend,
  placeholder = 'Опишите, что сделать в редакторе…',
}: Props) {
  const [draft, setDraft] = useState('');
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = listRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [messages, pending]);

  const submit = () => {
    const value = draft.trim();
    if (!value || pending) return;
    setDraft('');
    onSend(value);
  };

  return (
    <div className="assistant-chat-panel">
      <div className="assistant-chat-intro">
        <RobotOutlined />
        <Typography.Text type="secondary">
          Агент сам меняет инструмент через действия — как оператор, без кликов по UI.
        </Typography.Text>
      </div>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 8 }} />}
      <div className="assistant-chat-messages" ref={listRef}>
        {messages.length === 0 && (
          <Typography.Paragraph type="secondary" className="assistant-chat-empty">
            Например: «добавь приветствие с {"{{contact_name}}"}» или «создай ветку Отписаться».
          </Typography.Paragraph>
        )}
        {messages.map((item) => (
          <div key={item.id} className={`assistant-chat-bubble assistant-chat-bubble--${item.role}`}>
            <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>
              {item.content}
            </Typography.Paragraph>
            {(item.tools_used?.length || item.actions_count) ? (
              <Space size={[4, 4]} wrap style={{ marginTop: 6 }}>
                {item.tools_used?.map((tool) => (
                  <Tag key={`${item.id}-${tool}`} color="blue">
                    {tool}
                  </Tag>
                ))}
                {typeof item.actions_count === 'number' && item.actions_count > 0 && (
                  <Tag color="green">действий: {item.actions_count}</Tag>
                )}
              </Space>
            ) : null}
          </div>
        ))}
        {pending && (
          <div className="assistant-chat-bubble assistant-chat-bubble--assistant assistant-chat-bubble--pending">
            Думаю и применяю изменения…
          </div>
        )}
      </div>
      <div className="assistant-chat-composer">
        <Input.TextArea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={placeholder}
          autoSize={{ minRows: 2, maxRows: 5 }}
          disabled={pending}
          onPressEnter={(event) => {
            if (!event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          loading={pending}
          onClick={submit}
          disabled={!draft.trim()}
          block
        >
          Отправить
        </Button>
      </div>
    </div>
  );
}
