import { ProForm, ProFormSelect, ProFormText } from '@ant-design/pro-components';
import { Alert, App, Form, Select, Typography } from 'antd';
import type { FormInstance } from 'antd';
import { useEffect, useRef } from 'react';
import type { ChainLinkKind, EmailChain, EmailChainNode, Template } from '@/api/types';
import { hasChainButtonPlaceholder } from '@/features/templates/emailTemplateUtils';
import { isEmailNode, isLinkNode } from './chainUtils';

type Props = {
  chain: EmailChain;
  nodeId: string | null;
  emailTemplates: Template[];
  documentTemplates: Template[];
  onChange: (next: EmailChain) => void;
};

type NodeFormValues = {
  kind: 'email' | 'link';
  name: string;
  email_template_id?: string;
  document_template_ids: string[];
  link_kind: ChainLinkKind;
  link_url: string;
};

const LINK_KIND_OPTIONS = [
  { label: 'Пользовательская ссылка', value: 'custom' },
  { label: 'Отписаться (системная)', value: 'unsubscribe' },
  { label: 'Подписаться (системная)', value: 'subscribe' },
];

function nodeToFormValues(node: EmailChainNode): NodeFormValues {
  return {
    kind: node.kind ?? 'email',
    name: node.name,
    email_template_id: node.email_template_id ?? undefined,
    document_template_ids: node.document_template_ids ?? [],
    link_kind: node.link_kind ?? 'custom',
    link_url: node.link_url ?? '',
  };
}

function formValuesMatchNode(values: Record<string, unknown>, node: EmailChainNode): boolean {
  const expected = nodeToFormValues(node);
  const kind = (values.kind as 'email' | 'link') ?? 'email';
  if (kind !== expected.kind) return false;
  if (String(values.name ?? '') !== expected.name) return false;

  if (kind === 'email') {
    if ((values.email_template_id as string | undefined) !== expected.email_template_id) return false;
    const docs = (values.document_template_ids as string[]) ?? [];
    if (docs.length !== expected.document_template_ids.length) return false;
    return docs.every((id, index) => id === expected.document_template_ids[index]);
  }

  const linkKind = (values.link_kind as ChainLinkKind) ?? 'custom';
  if (linkKind !== expected.link_kind) return false;
  if (linkKind === 'custom' && String(values.link_url ?? '') !== expected.link_url) return false;
  return true;
}

function syncFormFromNode(form: FormInstance, node: EmailChainNode | null, nodeId: string | null) {
  if (!nodeId || !node) {
    form.resetFields();
    return;
  }
  const current = form.getFieldsValue();
  if (!formValuesMatchNode(current, node)) {
    form.setFieldsValue(nodeToFormValues(node));
  }
}

export function ChainNodeSettingsPanel({
  chain,
  nodeId,
  emailTemplates,
  documentTemplates,
  onChange,
}: Props) {
  const { modal } = App.useApp();
  const [form] = Form.useForm();
  const node = chain.nodes.find((n) => n.id === nodeId) ?? null;
  const isRoot = node?.id === chain.root_node_id;
  const prevNodeIdRef = useRef<string | null>(null);
  const linkKind =
    (Form.useWatch('link_kind', form) as ChainLinkKind | undefined) ??
    node?.link_kind ??
    'custom';

  useEffect(() => {
    if (!nodeId || !node) {
      form.resetFields();
      prevNodeIdRef.current = nodeId;
      return;
    }

    const nodeSelectionChanged = prevNodeIdRef.current !== nodeId;
    prevNodeIdRef.current = nodeId;

    if (nodeSelectionChanged) {
      form.setFieldsValue(nodeToFormValues(node));
      return;
    }

    syncFormFromNode(form, node, nodeId);
  }, [nodeId, chain, form, node]);

  if (!node) {
    return (
      <div className="chain-settings-panel">
        <Typography.Text type="secondary">Выберите блок на схеме</Typography.Text>
      </div>
    );
  }

  const applyPatch = (values: Record<string, unknown>) => {
    const nextName = String(values.name ?? node.name);
    const nextKind = (values.kind as 'email' | 'link') ?? node.kind ?? 'email';
    const nextLinkKind = (values.link_kind as ChainLinkKind) ?? node.link_kind ?? 'custom';

    let nextNode: typeof node = {
      ...node,
      name: nextName,
      kind: nextKind,
    };

    if (nextKind === 'email') {
      nextNode = {
        ...nextNode,
        email_template_id: (values.email_template_id as string | undefined) ?? null,
        document_template_ids: (values.document_template_ids as string[]) ?? [],
        link_kind: undefined,
        link_url: undefined,
      };
    } else {
      nextNode = {
        ...nextNode,
        link_kind: nextLinkKind,
        link_url: nextLinkKind === 'custom' ? String(values.link_url ?? '') : null,
        email_template_id: undefined,
        document_template_ids: undefined,
      };
    }

    let next: EmailChain = {
      ...chain,
      nodes: chain.nodes.map((n) => (n.id === node.id ? nextNode : n)),
    };
    if (!isRoot && values.name !== undefined) {
      next = {
        ...next,
        edges: next.edges.map((e) =>
          e.target_id === node.id ? { ...e, button_label: nextName || 'Перейти' } : e,
        ),
      };
    }
    onChange(next);
  };

  const handleKindChange = (nextKind: 'email' | 'link') => {
    if (nextKind === (node.kind ?? 'email')) return;
    modal.confirm({
      title: 'Сменить тип блока?',
      content: 'Настройки текущего типа будут сброшены.',
      okText: 'Сменить',
      cancelText: 'Отмена',
      onOk: () => {
        if (nextKind === 'email') {
          form.setFieldsValue({
            kind: 'email',
            email_template_id: undefined,
            document_template_ids: [],
          });
          applyPatch({
            kind: 'email',
            name: form.getFieldValue('name'),
            email_template_id: null,
            document_template_ids: [],
          });
        } else {
          form.setFieldsValue({
            kind: 'link',
            link_kind: 'custom',
            link_url: '',
          });
          applyPatch({
            kind: 'link',
            name: form.getFieldValue('name'),
            link_kind: 'custom',
            link_url: '',
          });
        }
      },
      onCancel: () => {
        form.setFieldsValue({ kind: node.kind ?? 'email' });
      },
    });
  };

  const kind = node?.kind ?? 'email';
  const selectedEmailTemplate = emailTemplates.find((template) => template.id === node.email_template_id);
  const missingChainButtonsPlaceholder =
    isEmailNode({ ...node, kind })
    && Boolean(node.email_template_id)
    && selectedEmailTemplate
    && !hasChainButtonPlaceholder(selectedEmailTemplate.version?.body_html || '');

  return (
    <div className="chain-settings-panel">
      <Typography.Title level={5} style={{ marginTop: 0 }}>
        Настройки узла
      </Typography.Title>
      <ProForm
        key={nodeId}
        form={form}
        submitter={false}
        layout="vertical"
        onValuesChange={(changed, values) => {
          if ('kind' in changed) {
            handleKindChange(changed.kind as 'email' | 'link');
            return;
          }
          applyPatch(values);
        }}
      >
        {!isRoot && (
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
            Это название будет отображаться на кнопке в предыдущем письме
          </Typography.Text>
        )}
        <ProFormSelect
          name="kind"
          label="Тип блока"
          disabled={isRoot}
          options={[
            { label: 'Письмо', value: 'email' },
            { label: 'Ссылка', value: 'link' },
          ]}
        />
        <ProFormText name="name" label="Название" />

        {isEmailNode({ ...node, kind }) && (
          <>
            <ProFormSelect
              name="email_template_id"
              label="Шаблон письма"
              showSearch
              options={emailTemplates.map((t) => ({ label: t.name, value: t.id }))}
              fieldProps={{ placeholder: 'Поиск шаблона...', optionFilterProp: 'label' }}
            />
            {missingChainButtonsPlaceholder && (
              <Alert
                type="info"
                showIcon
                message='В шаблоне нет блока «Кнопки цепочки»'
                description="Кнопки веток будут добавлены в конец письма. Добавьте блок в HTML-конструкторе шаблона, чтобы разместить их в нужном месте."
                style={{ marginBottom: 12 }}
              />
            )}
            <Form.Item name="document_template_ids" label="Документы">
              <Select
                mode="multiple"
                allowClear
                showSearch
                optionFilterProp="label"
                placeholder="Выберите документы"
                options={documentTemplates.map((t) => ({
                  label: t.version?.filename ? `${t.name} — ${t.version.filename}` : t.name,
                  value: t.id,
                }))}
              />
            </Form.Item>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              Можно выбрать несколько документов
            </Typography.Text>
          </>
        )}

        {isLinkNode({ ...node, kind }) && (
          <>
            <ProFormSelect
              name="link_kind"
              label="Тип ссылки"
              options={LINK_KIND_OPTIONS}
            />
            {linkKind === 'custom' && (
              <ProFormText
                name="link_url"
                label="URL"
                fieldProps={{ placeholder: 'https://example.com' }}
              />
            )}
            {linkKind === 'unsubscribe' && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Системная ссылка: при клике email попадает в глобальный список отписок.
              </Typography.Text>
            )}
            {linkKind === 'subscribe' && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                Системная ссылка: фиксирует согласие на новости и рекламные рассылки на 1 год.
              </Typography.Text>
            )}
          </>
        )}
      </ProForm>
    </div>
  );
}
