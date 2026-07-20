import { Collapse } from 'antd';
import type { ReactNode } from 'react';
import { useState } from 'react';
import { AssistantChatPanel } from './AssistantChatPanel';
import { useAssistantChat } from './useAssistantChat';
import type { AssistantApplyHandlers, AssistantSnapshotBuilder, EditorKind } from './types';
import './assistant.css';

type Props = {
  editorKind: EditorKind;
  resourceId: string;
  settings: ReactNode;
  buildSnapshot: AssistantSnapshotBuilder;
  handlers: AssistantApplyHandlers;
  defaultActiveKey?: 'settings' | 'assistant';
  className?: string;
};

export function EditorSideAccordion({
  editorKind,
  resourceId,
  settings,
  buildSnapshot,
  handlers,
  defaultActiveKey = 'settings',
  className,
}: Props) {
  const [activeKey, setActiveKey] = useState<string | string[]>(defaultActiveKey);
  const chat = useAssistantChat({
    editorKind,
    resourceId,
    buildSnapshot,
    handlers,
  });

  return (
    <aside className={`template-editor-aside editor-side-accordion ${className || ''}`.trim()}>
      <Collapse
        accordion
        activeKey={activeKey}
        onChange={(key) => setActiveKey(key)}
        className="editor-side-collapse"
        items={[
          {
            key: 'settings',
            label: 'Настройки',
            children: <div className="editor-side-settings">{settings}</div>,
          },
          {
            key: 'assistant',
            label: 'Помощник',
            children: (
              <AssistantChatPanel
                messages={chat.messages}
                pending={chat.pending}
                error={chat.error}
                onSend={(message) => {
                  setActiveKey('assistant');
                  void chat.send(message);
                }}
              />
            ),
          },
        ]}
      />
    </aside>
  );
}
