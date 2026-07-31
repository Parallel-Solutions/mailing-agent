import { DeleteOutlined, UploadOutlined } from '@ant-design/icons';
import { ModalForm, ProFormText } from '@ant-design/pro-components';
import { App, Avatar, Button, Space, Typography, Upload } from 'antd';
import { useMemo, useState } from 'react';
import { companiesApi } from '@/api/companies';
import type { Company } from '@/api/types';

type CompanyFormValues = {
  name: string;
  phone?: string;
  contact_person_name?: string;
};

type CompanyFormModalProps = {
  mode: 'create' | 'edit';
  company?: Company;
  trigger?: React.ReactElement;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onSuccess?: () => void;
  onDelete?: () => Promise<unknown>;
  deleting?: boolean;
};

function resetLogoState(
  setLogoFile: (file: File | null) => void,
  setLogoPreview: (url: string | null) => void,
  setRemoveLogo: (value: boolean) => void,
  previewUrl?: string | null,
) {
  if (previewUrl) {
    URL.revokeObjectURL(previewUrl);
  }
  setLogoFile(null);
  setLogoPreview(null);
  setRemoveLogo(false);
}

export function CompanyFormModal({
  mode,
  company,
  trigger,
  open,
  onOpenChange,
  onSuccess,
  onDelete,
  deleting = false,
}: CompanyFormModalProps) {
  const { message, modal } = App.useApp();
  const [logoFile, setLogoFile] = useState<File | null>(null);
  const [logoPreview, setLogoPreview] = useState<string | null>(null);
  const [removeLogo, setRemoveLogo] = useState(false);
  const [saving, setSaving] = useState(false);

  const avatarSrc = useMemo(() => {
    if (removeLogo) return undefined;
    if (logoPreview) return logoPreview;
    if (mode === 'edit' && company?.logo_url) return company.logo_url;
    return undefined;
  }, [company?.logo_url, logoPreview, mode, removeLogo]);

  const avatarLabel = company?.name || 'K';

  const handleLogoSelect = (file: File) => {
    setLogoFile(file);
    setRemoveLogo(false);
    setLogoPreview(URL.createObjectURL(file));
    return false;
  };

  const applyLogoChanges = async (companyId: string) => {
    if (removeLogo) {
      await companiesApi.deleteLogo(companyId);
      return;
    }
    if (logoFile) {
      await companiesApi.uploadLogo(companyId, logoFile);
    }
  };

  return (
    <ModalForm<CompanyFormValues>
      title={mode === 'create' ? 'Новая компания' : 'Редактировать компанию'}
      trigger={trigger}
      open={open}
      modalProps={{
        okText: 'Сохранить',
        cancelText: 'Отмена',
        destroyOnHidden: true,
        confirmLoading: saving,
      }}
      submitter={{
        render: (_, dom) => {
          if (mode !== 'edit' || !company || !onDelete) return dom;
          return [
            <Button
              key="delete"
              danger
              htmlType="button"
              icon={<DeleteOutlined />}
              loading={deleting}
              disabled={saving}
              onClick={() => {
                modal.confirm({
                  title: `Удалить компанию «${company.name}»?`,
                  content: 'Компания и её настройки будут удалены. Аккаунты участников и их данные сохранятся.',
                  okText: 'Удалить',
                  okType: 'danger',
                  cancelText: 'Отмена',
                  onOk: onDelete,
                });
              }}
            >
              Удалить компанию
            </Button>,
            ...dom,
          ];
        },
      }}
      initialValues={
        mode === 'edit' && company
          ? {
              name: company.name,
              phone: company.phone,
              contact_person_name: company.contact_person_name,
            }
          : undefined
      }
      onOpenChange={(nextOpen) => {
        onOpenChange?.(nextOpen);
        if (!nextOpen) {
          resetLogoState(setLogoFile, setLogoPreview, setRemoveLogo, logoPreview);
        }
      }}
      onFinish={async (values) => {
        setSaving(true);
        try {
          if (mode === 'create') {
            const created = await companiesApi.create(values);
            await applyLogoChanges(created.id);
            message.success('Компания создана');
          } else if (company) {
            await companiesApi.update(company.id, values);
            await applyLogoChanges(company.id);
            message.success('Настройки компании сохранены');
          }
          onSuccess?.();
          return true;
        } catch (err) {
          message.error(err instanceof Error ? err.message : 'Не удалось сохранить компанию');
          return false;
        } finally {
          setSaving(false);
        }
      }}
    >
      <ProFormText name="name" label="Название" rules={[{ required: true }]} />
      <ProFormText name="phone" label="Телефон" />
      <ProFormText name="contact_person_name" label="Контактное лицо" />

      <Space direction="vertical" size="small" style={{ width: '100%' }}>
        <Typography.Text type="secondary">Логотип</Typography.Text>
        <Space align="center" wrap>
          <Avatar size={72} src={avatarSrc} style={{ backgroundColor: '#1677ff' }}>
            {avatarLabel.slice(0, 1).toUpperCase()}
          </Avatar>
          <Space direction="vertical" size="small">
            <Upload showUploadList={false} beforeUpload={handleLogoSelect} accept=".png,.jpg,.jpeg,.webp">
              <Button icon={<UploadOutlined />}>Загрузить логотип</Button>
            </Upload>
            {mode === 'edit' && company?.logo_url && !removeLogo && !logoFile && (
              <Button
                danger
                htmlType="button"
                icon={<DeleteOutlined />}
                onClick={() => {
                  setRemoveLogo(true);
                  if (logoPreview) {
                    URL.revokeObjectURL(logoPreview);
                  }
                  setLogoFile(null);
                  setLogoPreview(null);
                }}
              >
                Удалить логотип
              </Button>
            )}
            {removeLogo && (
              <Typography.Text type="secondary">Логотип будет удалён при сохранении</Typography.Text>
            )}
          </Space>
        </Space>
      </Space>
    </ModalForm>
  );
}
