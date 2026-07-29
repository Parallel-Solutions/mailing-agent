import {
  CloudDownloadOutlined,
  DeleteOutlined,
  FontSizeOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Checkbox,
  Empty,
  List,
  Popconfirm,
  Space,
  Tag,
  Typography,
  Upload,
} from 'antd';
import { useEffect, useRef, useState } from 'react';
import { templatesApi } from '@/api/templates';
import type { TemplateFontRequirement } from '@/api/types';

const { Paragraph, Text } = Typography;

function statusTag(item: TemplateFontRequirement) {
  if (item.status === 'resolved') {
    return <Tag color="green">Подключён</Tag>;
  }
  if (item.status === 'system') {
    return <Tag color="blue">Есть на сервере</Tag>;
  }
  return <Tag color="error">Отсутствует</Tag>;
}

function sourceLabel(item: TemplateFontRequirement) {
  if (item.source === 'google_fonts') return 'Google Fonts';
  if (item.source === 'upload') return 'Загружен вручную';
  if (item.source === 'system') return 'Системный шрифт';
  return 'Указан в DOCX';
}

export function TemplateFontsPanel({ templateId }: { templateId: string }) {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [licenseConfirmed, setLicenseConfirmed] = useState(false);
  const autoAttemptedVersion = useRef('');
  const fontsQuery = useQuery({
    queryKey: ['template-fonts', templateId],
    queryFn: () => templatesApi.templateFonts(templateId),
    retry: false,
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['template-fonts', templateId] }),
      queryClient.invalidateQueries({ queryKey: ['fonts'] }),
      queryClient.invalidateQueries({ queryKey: ['template', templateId] }),
      queryClient.invalidateQueries({ queryKey: ['templates'] }),
    ]);
  };

  const resolveMutation = useMutation({
    mutationFn: (_options?: { silent?: boolean }) => templatesApi.resolveTemplateFonts(templateId),
    onSuccess: async (result, options) => {
      await refresh();
      if (options?.silent) return;
      if (result.missing_count === 0) {
        message.success('Шрифты найдены и подключены к PDF-конвертации');
      } else if (result.downloaded_fonts?.length) {
        message.warning('Часть шрифтов подключена, но некоторые всё ещё отсутствуют');
      } else {
        message.info('В открытом каталоге подходящие шрифты не найдены. Загрузите TTF или OTF вручную.');
      }
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось найти шрифты');
    },
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) =>
      templatesApi.uploadFont(file, { templateId, licenseConfirmed }),
    onSuccess: async (font) => {
      setLicenseConfirmed(false);
      await refresh();
      message.success(`Шрифт «${font.family}» подключён`);
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось загрузить шрифт');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (fontId: string) => templatesApi.deleteFont(fontId, templateId),
    onSuccess: async () => {
      await refresh();
      message.success('Шрифт удалён');
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : 'Не удалось удалить шрифт');
    },
  });

  const requirements = fontsQuery.data?.requirements || [];
  const missingCount = fontsQuery.data?.missing_count || 0;

  useEffect(() => {
    const versionId = fontsQuery.data?.version_id || '';
    if (
      versionId
      && missingCount > 0
      && autoAttemptedVersion.current !== versionId
      && !resolveMutation.isPending
    ) {
      autoAttemptedVersion.current = versionId;
      resolveMutation.mutate({ silent: true });
    }
  }, [fontsQuery.data?.version_id, missingCount, resolveMutation]);

  return (
    <Card
      className="template-fonts-panel"
      title={<Space><FontSizeOutlined />Шрифты для PDF</Space>}
      loading={fontsQuery.isLoading}
      extra={(
        <Button
          icon={<CloudDownloadOutlined />}
          loading={resolveMutation.isPending}
          disabled={!missingCount}
          onClick={() => resolveMutation.mutate({ silent: false })}
        >
          Найти автоматически
        </Button>
      )}
    >
      {fontsQuery.isError && (
        <Alert
          type="error"
          showIcon
          message="Не удалось проанализировать шрифты DOCX"
          action={<Button onClick={() => void fontsQuery.refetch()}>Повторить</Button>}
        />
      )}

      {!fontsQuery.isError && (
        <>
          <Alert
            type={missingCount ? 'warning' : 'success'}
            showIcon
            message={
              missingCount
                ? `Не найдено шрифтов: ${missingCount}. Подмена шрифта может изменить число страниц.`
                : 'Все используемые шрифты доступны PDF-конвертеру.'
            }
          />

          <List
            style={{ marginTop: 12 }}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="В DOCX не найдены явные ссылки на шрифты" /> }}
            dataSource={requirements}
            renderItem={(item) => (
              <List.Item
                actions={
                  item.font_asset
                    ? [
                        <Popconfirm
                          key="delete"
                          title="Удалить загруженный шрифт?"
                          description="PDF будет снова собираться с системной заменой, пока шрифт не подключён."
                          okText="Удалить"
                          cancelText="Отмена"
                          onConfirm={() => deleteMutation.mutate(item.font_asset!.id)}
                        >
                          <Button
                            type="text"
                            danger
                            icon={<DeleteOutlined />}
                            loading={deleteMutation.isPending}
                            aria-label={`Удалить ${item.family}`}
                          />
                        </Popconfirm>,
                      ]
                    : undefined
                }
              >
                <List.Item.Meta
                  title={(
                    <Space wrap>
                      <Text strong>{item.family}</Text>
                      <Text type="secondary">
                        {item.weight >= 600 ? 'полужирный' : 'обычный'}
                        {item.italic ? ', курсив' : ''}
                      </Text>
                      {statusTag(item)}
                    </Space>
                  )}
                  description={sourceLabel(item)}
                />
              </List.Item>
            )}
          />

          <div style={{ marginTop: 12 }}>
            <Checkbox
              checked={licenseConfirmed}
              onChange={(event) => setLicenseConfirmed(event.target.checked)}
            >
              Подтверждаю право использовать загружаемый шрифт при серверной конвертации
            </Checkbox>
            <Paragraph type="secondary" style={{ margin: '6px 0 10px', fontSize: 12 }}>
              Поддерживаются TTF и OTF до 20 МБ. Файл проверяется, а семейство определяется из метаданных шрифта.
            </Paragraph>
            <Upload
              accept=".ttf,.otf"
              maxCount={1}
              showUploadList={false}
              disabled={!licenseConfirmed || uploadMutation.isPending}
              customRequest={({ file, onSuccess, onError }) => {
                uploadMutation.mutate(file as File, {
                  onSuccess: () => onSuccess?.({}),
                  onError: (error) => onError?.(error),
                });
              }}
            >
              <Button
                icon={<UploadOutlined />}
                loading={uploadMutation.isPending}
                disabled={!licenseConfirmed}
              >
                Загрузить TTF/OTF
              </Button>
            </Upload>
          </div>
        </>
      )}
    </Card>
  );
}
