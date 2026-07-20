import { useCallback, useRef, useState } from 'react';
import { assistantsApi } from '@/api/assistants';
import { applyAssistantActions } from './applyAssistantActions';
import type {
  AssistantApplyHandlers,
  AssistantChatMessage,
  AssistantSnapshotBuilder,
  EditorKind,
} from './types';

function messageId(): string {
  return `msg-${crypto.randomUUID().slice(0, 8)}`;
}

type Options = {
  editorKind: EditorKind;
  resourceId: string;
  buildSnapshot: AssistantSnapshotBuilder;
  handlers: AssistantApplyHandlers;
};

export function useAssistantChat({ editorKind, resourceId, buildSnapshot, handlers }: Options) {
  const [messages, setMessages] = useState<AssistantChatMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const handlersRef = useRef(handlers);
  const snapshotRef = useRef(buildSnapshot);
  handlersRef.current = handlers;
  snapshotRef.current = buildSnapshot;

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || pending) return;
      setError(null);
      setPending(true);
      const userMessage: AssistantChatMessage = {
        id: messageId(),
        role: 'user',
        content: trimmed,
      };
      setMessages((current) => [...current, userMessage]);
      try {
        const result = await assistantsApi.chat({
          editor_kind: editorKind,
          resource_id: resourceId,
          message: trimmed,
          session_id: sessionIdRef.current,
          snapshot: snapshotRef.current(),
        });
        sessionIdRef.current = result.session_id;
        const assistantId = messageId();
        // Show the reply before reload actions so a document remount cannot drop it.
        setMessages((current) => [
          ...current,
          {
            id: assistantId,
            role: 'assistant',
            content: result.reply,
            tools_used: result.tools_used,
          },
        ]);
        const applied = await applyAssistantActions(result.actions || [], handlersRef.current);
        setMessages((current) =>
          current.map((item) =>
            item.id === assistantId ? { ...item, actions_count: applied } : item,
          ),
        );
      } catch (err) {
        const detail = err instanceof Error ? err.message : 'Не удалось связаться с помощником';
        setError(detail);
        setMessages((current) => [
          ...current,
          {
            id: messageId(),
            role: 'assistant',
            content: `Ошибка: ${detail}`,
          },
        ]);
      } finally {
        setPending(false);
      }
    },
    [editorKind, pending, resourceId],
  );

  const reset = useCallback(() => {
    sessionIdRef.current = null;
    setMessages([]);
    setError(null);
  }, []);

  return { messages, pending, error, send, reset, sessionId: sessionIdRef.current };
}
