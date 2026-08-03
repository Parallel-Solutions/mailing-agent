import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { CompanyFormModal } from './CompanyFormModal';
beforeAll(() => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
});

afterAll(() => {
  vi.unstubAllGlobals();
});

describe('CompanyFormModal deletion', () => {
  it('shows deletion only for an existing company and confirms it', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);

    render(
      <AntdApp>
        <CompanyFormModal
          mode="edit"
          company={{ id: 'company-1', name: 'Компания 1' }}
          open
          onDelete={onDelete}
        />
      </AntdApp>,
    );

    fireEvent.click(screen.getByRole('button', { name: /Удалить компанию/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Удалить' }));

    await waitFor(() => expect(onDelete).toHaveBeenCalledOnce());
  });

  it('does not show deletion before a company exists', () => {
    render(
      <AntdApp>
        <CompanyFormModal mode="create" open />
      </AntdApp>,
    );

    expect(screen.queryByRole('button', { name: /Удалить компанию/ })).not.toBeInTheDocument();
  });
});