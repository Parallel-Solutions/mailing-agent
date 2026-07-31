import { fireEvent, render, screen } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { ConsentDrilldownCards, ProblemDrilldownCards } from './StatisticsModals';

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});
describe('ConsentDrilldownCards', () => {
  it('shows long consent details in separate readable cards', () => {
    const { container } = render(
      <ConsentDrilldownCards
        loading={false}
        page={1}
        onOpen={vi.fn()}
        onPageChange={vi.fn()}
        rows={[
          {
            row_key: 'company-1',
            organization: 'Администрация Бабынинского муниципального округа',
            contact: 'Фатима Магомедова',
            email: 'fmagomedova654@example-municipality.ru',
            consent_status_label: 'Запрос согласия отправлен',
            materials_label: 'Материалы ещё не отправлялись',
            last_action_label: 'Запрос согласия отправлен',
            last_action_at: '2026-07-08T14:11:00+03:00',
            interest: { label: 'Низкий' },
            next_action: { label: 'Ожидать статус' },
          },
          {
            row_key: 'company-2',
            organization: 'ООО «Очень длинное название организации для проверки переноса»',
            email: 'long.department.address@example.ru',
            consent_status_label: 'Согласие подтверждено',
            materials_label: 'Материалы отправлены',
            interest: { label: 'Высокий' },
            next_action: { label: 'Связаться с компанией' },
          },
        ]}
      />,
    );

    expect(container.querySelectorAll('.ant-card')).toHaveLength(2);
    expect(
      screen.getByText('Администрация Бабынинского муниципального округа'),
    ).toBeInTheDocument();
    expect(screen.getByText('fmagomedova654@example-municipality.ru')).toBeInTheDocument();
    expect(screen.getByText('Материалы ещё не отправлялись')).toBeInTheDocument();
    expect(screen.getByText('Связаться с компанией')).toBeInTheDocument();
    expect(container.querySelector('.ant-table')).not.toBeInTheDocument();
  });
});
describe('ProblemDrilldownCards', () => {
  it('offers a manual resend for final delivery errors', () => {
    const onResend = vi.fn();
    const onOpen = vi.fn();
    render(
      <ProblemDrilldownCards
        loading={false}
        page={1}
        onOpen={onOpen}
        onPageChange={vi.fn()}
        onResend={onResend}
        resendingKey=""
        queuedResends={new Set()}
        rows={[
          {
            row_key: 'job:42:error@example.com',
            organization: 'Администрация тестового округа',
            email: 'error@example.com',
            emails: [{ email: 'error@example.com' }],
            manager_status: { key: 'delivery_error', label: 'Ошибка доставки' },
            bounce_reason_label: 'Провайдер отклонил письмо',
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Направить повторно' }));

    expect(onResend).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('does not allow a manual resend while a soft bounce is retried automatically', () => {
    render(
      <ProblemDrilldownCards
        loading={false}
        page={1}
        onOpen={vi.fn()}
        onPageChange={vi.fn()}
        onResend={vi.fn()}
        resendingKey=""
        queuedResends={new Set()}
        rows={[
          {
            row_key: 'job:42:wait@example.com',
            organization: 'Администрация тестового района',
            email: 'wait@example.com',
            manager_status: { key: 'soft_bounce', label: 'Временная ошибка' },
          },
        ]}
      />,
    );

    expect(screen.getByRole('button', { name: 'Автоповтор' })).toBeDisabled();
    expect(screen.getByText('Повтор выполняется автоматически')).toBeInTheDocument();
  });
});