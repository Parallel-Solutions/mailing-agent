import { ArrowLeftOutlined, ArrowRightOutlined } from '@ant-design/icons';
import { Button, Tooltip } from 'antd';
import { useHistoryNavigation } from '@/hooks/useHistoryNavigation';
import './HistoryNavButtons.css';

export function HistoryNavButtons() {
  const { goBack, goForward, canGoBack, canGoForward } = useHistoryNavigation();

  return (
    <div className="history-nav-buttons">
      <Tooltip title="Назад">
        <Button
          type="text"
          size="small"
          icon={<ArrowLeftOutlined />}
          disabled={!canGoBack}
          onClick={goBack}
          aria-label="Назад"
        />
      </Tooltip>
      <Tooltip title="Вперёд">
        <Button
          type="text"
          size="small"
          icon={<ArrowRightOutlined />}
          disabled={!canGoForward}
          onClick={goForward}
          aria-label="Вперёд"
        />
      </Tooltip>
    </div>
  );
}
