import { Select, Space, Typography } from 'antd';
import type { SelectProps } from 'antd';
import type { Template } from '@/api/types';
import { TemplatePreviewImage, TemplatePreviewThumb } from './TemplatePreviewImage';
import './TemplatePickerField.css';

type Props = {
  templates: Template[];
  placeholder?: string;
  disabled?: boolean;
  mode?: 'single' | 'multiple';
  value?: string | string[];
  onChange?: (value: string | string[] | undefined) => void;
};

function templateLabel(template: Template): string {
  if (template.version?.filename) {
    return `${template.name} — ${template.version.filename}`;
  }
  return template.name;
}

function canPreviewTemplate(template: Template): boolean {
  if (template.template_type === 'email') {
    return Boolean(template.version?.body_html?.trim());
  }
  return Boolean(template.version?.filename);
}

function normalizeSelectedIds(value: string | string[] | undefined, mode: 'single' | 'multiple'): string[] {
  if (mode === 'multiple') {
    return Array.isArray(value) ? value : [];
  }
  return typeof value === 'string' && value ? [value] : [];
}

export function TemplatePickerField({
  templates,
  placeholder,
  disabled,
  mode = 'single',
  value,
  onChange,
}: Props) {
  const options = templates.map((template) => ({
    label: templateLabel(template),
    value: template.id,
  }));

  const optionRender: SelectProps['optionRender'] = (option) => {
    const template = templates.find((item) => item.id === option.value);
    if (!template || !canPreviewTemplate(template)) {
      return <span>{option.label}</span>;
    }
    return (
      <Space size={8} align="center">
        <TemplatePreviewThumb templateId={template.id} alt={template.name} />
        <span>{option.label}</span>
      </Space>
    );
  };

  const selectedIds = normalizeSelectedIds(value, mode);
  const selectedTemplates = templates.filter((template) => selectedIds.includes(template.id));

  const selectProps: SelectProps = {
    allowClear: true,
    showSearch: true,
    optionFilterProp: 'label',
    placeholder,
    disabled,
    options,
    optionRender,
    className: 'template-picker-field__select',
  };

  return (
    <div className="template-picker-field">
      {mode === 'multiple' ? (
        <Select
          {...selectProps}
          mode="multiple"
          value={selectedIds}
          onChange={(next) => onChange?.(next)}
        />
      ) : (
        <Select
          {...selectProps}
          value={selectedIds[0]}
          onChange={(next) => onChange?.(next)}
        />
      )}

      {selectedTemplates.length > 0 && (
        <div className={mode === 'multiple' ? 'template-picker-field__selected-list' : 'template-picker-field__selected-single'}>
          {selectedTemplates.map((template) => (
            <div key={template.id} className="template-picker-field__selected-item">
              {canPreviewTemplate(template) ? (
                <TemplatePreviewImage
                  templateId={template.id}
                  alt={template.name}
                  className="template-preview-image--selected"
                />
              ) : (
                <Typography.Text type="secondary">Превью недоступно</Typography.Text>
              )}
              <Typography.Text className="template-picker-field__selected-label">{template.name}</Typography.Text>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
