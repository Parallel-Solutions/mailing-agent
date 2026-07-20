import type { EmailChain, PdfEditorField } from '@/api/types';

export type EditorKind =
  | 'visual_email'
  | 'simple_email'
  | 'kp'
  | 'pdf'
  | 'docx'
  | 'chain';

export type AssistantAction = {
  type: string;
  [key: string]: unknown;
};

export type AssistantChatRequest = {
  editor_kind: EditorKind;
  resource_id: string;
  message: string;
  session_id?: string | null;
  model?: string | null;
  snapshot?: Record<string, unknown> | null;
};

export type AssistantChatResponse = {
  reply: string;
  session_id: string;
  tools_used: string[];
  actions: AssistantAction[];
  editor_kind: EditorKind;
  resource_id: string;
};

export type AssistantChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  tools_used?: string[];
  actions_count?: number;
};

export type AssistantApplyHandlers = {
  setSubject?: (subject: string) => void;
  setHtml?: (html: string) => void;
  insertHtml?: (html: string) => void;
  insertComponents?: (html: string) => void;
  loadGrapesProject?: (project: Record<string, unknown>) => void;
  setPersonalization?: (enabled: boolean) => void;
  updatePdfFields?: (fields: Array<{ id: string; value?: string; font_size?: number }>) => void;
  setChain?: (chain: EmailChain, selectedNodeId?: string | null) => void;
  selectChainNode?: (nodeId: string) => void;
  reloadTemplate?: () => void | Promise<void>;
  markDirty?: () => void;
};

export type AssistantSnapshotBuilder = () => Record<string, unknown>;

export type PdfFieldSnapshot = Pick<
  PdfEditorField,
  'id' | 'page' | 'variable' | 'label' | 'value' | 'font_size' | 'source_text'
>;
