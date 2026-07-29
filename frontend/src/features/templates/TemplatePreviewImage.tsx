import { FileOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { templatesApi } from '@/api/templates';
import './TemplatePreviewImage.css';

type Props = {
  templateId?: string;
  starterId?: string;
  alt: string;
  className?: string;
  compact?: boolean;
  cacheKey?: string | number;
};

export function TemplatePreviewImage({
  templateId,
  starterId,
  alt,
  className,
  compact = false,
  cacheKey,
}: Props) {
  const [failed, setFailed] = useState(false);
  const baseSrc = templateId
    ? templatesApi.previewImageUrl(templateId)
    : starterId
      ? templatesApi.starterPreviewImageUrl(starterId)
      : null;
  const src = baseSrc && cacheKey !== undefined ? `${baseSrc}?v=${cacheKey}` : baseSrc;

  if (!src || failed) {
    return (
      <div
        className={[
          'template-preview-image',
          'template-preview-image--placeholder',
          compact ? 'template-preview-image--compact' : '',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
        aria-hidden={!alt}
      >
        <FileOutlined />
      </div>
    );
  }

  return (
    <div
      className={[
        'template-preview-image',
        compact ? 'template-preview-image--compact' : '',
        className,
      ]
        .filter(Boolean)
        .join(' ')}
    >
      <img
        src={src}
        alt={alt}
        loading="lazy"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

export function TemplatePreviewThumb({
  templateId,
  starterId,
  alt,
}: Pick<Props, 'templateId' | 'starterId' | 'alt'>) {
  return (
    <TemplatePreviewImage
      templateId={templateId}
      starterId={starterId}
      alt={alt}
      compact
      className="template-preview-image--option"
    />
  );
}
