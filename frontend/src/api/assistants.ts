import { api } from './client';
import type { AssistantAction, AssistantChatRequest, AssistantChatResponse, EditorKind } from '@/features/assistants/types';

export type { AssistantAction, AssistantChatRequest, AssistantChatResponse, EditorKind };

export const assistantsApi = {
  chat: (body: AssistantChatRequest) =>
    api.post<AssistantChatResponse>('/api/v1/assistants/chat', body),
};
