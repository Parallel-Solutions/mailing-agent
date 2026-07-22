import { Avatar, Typography } from 'antd';
import { useAuthStore } from '@/stores/authStore';
import './CompanyBranding.css';

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return 'К';
  if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
  return `${parts[0].slice(0, 1)}${parts[1].slice(0, 1)}`.toUpperCase();
}

export function CompanyBranding() {
  const user = useAuthStore((s) => s.user);
  const companyName = user?.company?.name || 'Моя компания';
  const logoUrl = user?.company?.logo_url || undefined;

  return (
    <div className="company-branding">
      <Avatar src={logoUrl} size={24} className="company-branding__avatar">
        {!logoUrl ? initials(companyName) : null}
      </Avatar>
      <Typography.Text className="company-branding__name" ellipsis>
        {companyName}
      </Typography.Text>
    </div>
  );
}
