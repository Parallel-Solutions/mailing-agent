import { Button, Result } from 'antd';
import { Link } from 'react-router-dom';

export function NotFoundPage() {
  return (
    <Result
      status="404"
      title="Страница не найдена"
      subTitle="Проверьте адрес или вернитесь на дашборд"
      extra={
        <Link to="/">
          <Button type="primary">На дашборд</Button>
        </Link>
      }
    />
  );
}
