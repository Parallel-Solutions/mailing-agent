import { HistoryNavButtons } from '@/components/HistoryNavButtons';
import { CompanyBranding } from '@/components/CompanyBranding';
import './AppTopBar.css';

export const APP_TOP_BAR_HEIGHT = 40;

export function AppTopBar() {
  return (
    <header className="app-top-bar" style={{ height: APP_TOP_BAR_HEIGHT }}>
      <HistoryNavButtons />
      <CompanyBranding />
    </header>
  );
}
