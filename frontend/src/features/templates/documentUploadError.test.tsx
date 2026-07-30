import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import {
  getDocumentUploadErrorPresentation,
  showDocumentUploadError,
} from './documentUploadError';

describe('document upload error modal', () => {
  it('shows the structured backend message and hint', () => {
    const modal = { error: vi.fn() };
    const error = new ApiError(422, {
      code: 'document_conversion_failed',
      title: 'Не удалось преобразовать документ',
      message: 'Документ не удалось преобразовать в PDF.',
      hint: 'Проверьте файл и повторите загрузку.',
    });

    showDocumentUploadError(modal, error);

    expect(modal.error).toHaveBeenCalledOnce();
    const config = modal.error.mock.calls[0][0];
    expect(config).toMatchObject({
      title: 'Не удалось преобразовать документ',
      okText: 'Понятно',
      centered: true,
    });
    render(config.content);
    expect(screen.getByText('Документ не удалось преобразовать в PDF.')).toBeInTheDocument();
    expect(screen.getByText('Проверьте файл и повторите загрузку.')).toBeInTheDocument();
  });

  it('does not expose an unknown technical exception', () => {
    expect(
      getDocumentUploadErrorPresentation(new Error('Gotenberg connection refused')),
    ).toEqual({
      title: 'Не удалось загрузить документ',
      message: 'Документ не удалось загрузить или преобразовать в PDF.',
      hint: 'Проверьте файл и подключение к сети, затем повторите загрузку.',
    });
  });
});
