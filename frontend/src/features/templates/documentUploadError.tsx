import type { ReactNode } from 'react';
import { ApiError } from '@/api/client';

type ErrorModal = {
  error: (config: {
    title: ReactNode;
    content: ReactNode;
    okText: string;
    centered: boolean;
  }) => unknown;
};

export type DocumentUploadErrorPresentation = {
  title: string;
  message: string;
  hint?: string;
};

const DEFAULT_ERROR: DocumentUploadErrorPresentation = {
  title: 'Не удалось загрузить документ',
  message: 'Документ не удалось загрузить или преобразовать в PDF.',
  hint: 'Проверьте файл и подключение к сети, затем повторите загрузку.',
};

export function getDocumentUploadErrorPresentation(
  error: unknown,
): DocumentUploadErrorPresentation {
  if (!(error instanceof ApiError)) return DEFAULT_ERROR;
  return {
    title: error.payload.title || DEFAULT_ERROR.title,
    message: error.payload.message || DEFAULT_ERROR.message,
    hint: error.payload.hint,
  };
}

export function showDocumentUploadError(modal: ErrorModal, error: unknown): void {
  const presentation = getDocumentUploadErrorPresentation(error);
  modal.error({
    title: presentation.title,
    content: (
      <div>
        <p>{presentation.message}</p>
        {presentation.hint ? <p>{presentation.hint}</p> : null}
      </div>
    ),
    okText: 'Понятно',
    centered: true,
  });
}
