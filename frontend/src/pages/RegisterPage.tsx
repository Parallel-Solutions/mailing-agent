import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { LoginForm, ProFormText } from '@ant-design/pro-components';
import { App, Typography } from 'antd';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '@/api/client';
import { useAuthStore } from '@/stores/authStore';
import { tokens } from '@/theme/tokens';

export function RegisterPage() {
  const navigate = useNavigate();
  const register = useAuthStore((s) => s.register);
  const { message } = App.useApp();

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        background: tokens.background,
      }}
    >
      <LoginForm
        title="Регистрация"
        subTitle="Создание аккаунта CampaignFlow"
        submitter={{ searchConfig: { submitText: 'Зарегистрироваться' } }}
        onFinish={async (values) => {
          try {
            await register(values.username, values.password, values.password_confirm);
            message.success('Аккаунт создан');
            navigate('/');
          } catch (error) {
            message.error(error instanceof ApiError ? error.detail : 'Ошибка регистрации');
          }
        }}
      >
        <ProFormText
          name="username"
          fieldProps={{ size: 'large', prefix: <UserOutlined /> }}
          placeholder="Логин"
          rules={[{ required: true, message: 'Введите логин' }]}
        />
        <ProFormText.Password
          name="password"
          fieldProps={{ size: 'large', prefix: <LockOutlined /> }}
          placeholder="Пароль"
          rules={[{ required: true, message: 'Введите пароль' }]}
        />
        <ProFormText.Password
          name="password_confirm"
          fieldProps={{ size: 'large', prefix: <LockOutlined /> }}
          placeholder="Подтверждение пароля"
          rules={[{ required: true, message: 'Повторите пароль' }]}
        />
        <Typography.Paragraph style={{ textAlign: 'center' }}>
          Уже есть аккаунт? <Link to="/login">Войти</Link>
        </Typography.Paragraph>
      </LoginForm>
    </div>
  );
}
