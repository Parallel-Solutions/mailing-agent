import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { LoginForm, ProFormText } from '@ant-design/pro-components';
import { App, Typography } from 'antd';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '@/api/client';
import { useAuthStore } from '@/stores/authStore';
import { tokens } from '@/theme/tokens';

export function LoginPage() {
  const navigate = useNavigate();
  const login = useAuthStore((s) => s.login);
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
        title="ai-offer"
        subTitle="Вход в систему рассылок"
        onFinish={async (values) => {
          try {
            await login(values.username, values.password);
            message.success('Вход выполнен');
            navigate('/');
          } catch (error) {
            message.error(error instanceof ApiError ? error.detail : 'Ошибка входа');
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
        <Typography.Paragraph style={{ textAlign: 'center' }}>
          Нет аккаунта? <Link to="/register">Регистрация</Link>
        </Typography.Paragraph>
      </LoginForm>
    </div>
  );
}
