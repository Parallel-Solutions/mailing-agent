import {
  DeleteOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  RedoOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import { App, Button, Input, Space, Spin, Typography } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useLocation, useParams } from 'react-router-dom';
import { chainsApi, type ChainRecord } from '@/api/chains';
import { campaignsApi } from '@/api/campaigns';
import { templatesApi, templatesQueryKeys } from '@/api/templates';
import type { ChainLinkKind, EmailChain } from '@/api/types';
import { EditorSideAccordion } from '@/features/assistants';
import { useUrlNavigation } from '@/hooks/useUrlNavigation';
import { readBoolParam } from '@/utils/urlState';
import { ChainCanvas } from '@/features/campaigns/chain/ChainCanvas';
import { ChainNodeBlock } from '@/features/campaigns/chain/ChainNodeBlock';
import { ChainNodeSettingsPanel } from '@/features/campaigns/chain/ChainNodeSettingsPanel';
import { invalidateCampaignDerivedData } from '@/features/campaigns/campaignQueryUtils';
import { resolveCampaignReturnTarget } from '@/features/campaigns/campaignNavigation';
import { useCampaignDraftStore } from '@/stores/campaignDraftStore';
import {
  addChildEmailNode,
  addChildLinkNode,
  computeChainLayout,
  createEmptyChain,
  removeNodeSubtree,
} from '@/features/campaigns/chain/chainUtils';
import './EmailChainBuilderPage.css';

const MAX_HISTORY = 50;

function isCanvasPanTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    !target.closest('.chain-node-block') &&
    !target.closest('.email-chain-canvas-fullscreen')
  );
}

export function EmailChainBuilderPage({ legacyCampaign = false }: { legacyCampaign?: boolean }) {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const activeCampaignId = useCampaignDraftStore((state) => state.campaignId);
  const campaignContext = resolveCampaignReturnTarget(location.state, activeCampaignId);
  const campaignId = campaignContext?.campaignId;
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const { searchParams, pushParams } = useUrlNavigation();
  const [chain, setChain] = useState<EmailChain>(createEmptyChain());
  const [chainName, setChainName] = useState('');
  const [history, setHistory] = useState<EmailChain[]>([]);
  const [future, setFuture] = useState<EmailChain[]>([]);
  const [dirty, setDirty] = useState(false);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(() => readBoolParam(searchParams, 'fullscreen'));
  const debounceRef = useRef<number | null>(null);
  const chainLoadedRef = useRef(false);
  const dirtyRef = useRef(false);
  const savedNameRef = useRef('');
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const panStartRef = useRef({ x: 0, y: 0, panX: 0, panY: 0 });

  const nodeParam = searchParams.get('node');
  const selectedNodeId = useMemo(() => {
    if (nodeParam && chain.nodes.some((node) => node.id === nodeParam)) {
      return nodeParam;
    }
    return chain.root_node_id || null;
  }, [chain.nodes, chain.root_node_id, nodeParam]);

  const selectNode = useCallback(
    (nodeId: string | null) => {
      if (!nodeId || nodeId === chain.root_node_id) {
        pushParams({}, ['node']);
        return;
      }
      pushParams({ node: nodeId });
    },
    [chain.root_node_id, pushParams],
  );

  dirtyRef.current = dirty;

  const campaignQuery = useQuery({
    queryKey: ['campaign', id],
    queryFn: () => campaignsApi.get(id),
    enabled: Boolean(id) && legacyCampaign,
  });

  const chainQuery = useQuery({
    queryKey: legacyCampaign ? ['email-chain', id] : ['chain', id],
    queryFn: () =>
      legacyCampaign ? campaignsApi.getEmailChain(id) : chainsApi.get(id),
    enabled: Boolean(id),
  });

  const emailTemplatesQuery = useQuery({
    queryKey: templatesQueryKeys.list('email'),
    queryFn: () => templatesApi.list({ template_type: 'email' }),
  });

  const documentTemplatesQuery = useQuery({
    queryKey: templatesQueryKeys.list('document'),
    queryFn: () => templatesApi.list({ template_type: 'document' }),
  });

  useEffect(() => {
    if (!chainQuery.data?.chain) return;

    if (!legacyCampaign) {
      const loadedName = (chainQuery.data as ChainRecord).name ?? '';
      if (!chainLoadedRef.current || !dirtyRef.current) {
        setChainName(loadedName);
        savedNameRef.current = loadedName;
      }
    }

    if (!chainLoadedRef.current) {
      setChain(chainQuery.data.chain);
      setHistory([]);
      setFuture([]);
      setDirty(false);
      chainLoadedRef.current = true;
      return;
    }

    if (!dirtyRef.current) {
      setChain(chainQuery.data.chain);
    }
  }, [chainQuery.data, legacyCampaign]);

  useEffect(() => {
    chainLoadedRef.current = false;
    setChainName('');
    savedNameRef.current = '';
  }, [id]);

  useEffect(() => {
    if (!isPanning) return;

    const onMove = (event: MouseEvent) => {
      const start = panStartRef.current;
      setPan({
        x: start.panX + event.clientX - start.x,
        y: start.panY + event.clientY - start.y,
      });
    };

    const onUp = () => setIsPanning(false);

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [isPanning]);

  useEffect(() => {
    const onFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === canvasWrapRef.current);
    };
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, []);

  const layout = useMemo(() => computeChainLayout(chain), [chain]);

  const pushHistory = useCallback((prev: EmailChain) => {
    setHistory((items) => [...items.slice(-MAX_HISTORY + 1), prev]);
    setFuture([]);
  }, []);

  const applyChain = useCallback(
    (next: EmailChain, trackHistory = true) => {
      if (trackHistory) pushHistory(chain);
      setChain(next);
      setDirty(true);
    },
    [chain, pushHistory],
  );

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (legacyCampaign) {
        return campaignsApi.putEmailChain(id, chain);
      }
      const trimmed = chainName.trim();
      if (!trimmed) {
        throw new Error('Укажите название цепочки');
      }
      if (trimmed !== savedNameRef.current) {
        await chainsApi.update(id, { name: trimmed });
        savedNameRef.current = trimmed;
        setChainName(trimmed);
      }
      return chainsApi.save(id, chain);
    },
    onSuccess: () => {
      setDirty(false);
      if (legacyCampaign) {
        invalidateCampaignDerivedData(queryClient, id);
      } else {
        void queryClient.invalidateQueries({ queryKey: ['chain', id] });
        void queryClient.invalidateQueries({ queryKey: ['chains'] });
      }
      message.success('Цепочка сохранена');
    },
    onError: (error: Error) => message.error(error.message),
  });

  const publishMutation = useMutation({
    mutationFn: async () => {
      if (legacyCampaign) {
        return campaignsApi.publishEmailChain(id);
      }
      return chainsApi.publish(id);
    },
    onSuccess: () => {
      setDirty(false);
      void queryClient.invalidateQueries({
        queryKey: legacyCampaign ? ['email-chain', id] : ['chain', id],
      });
      if (legacyCampaign) {
        invalidateCampaignDerivedData(queryClient, id);
      } else {
        void queryClient.invalidateQueries({ queryKey: ['chains'] });
      }
      message.success('Цепочка опубликована');
    },
    onError: (error: Error) => message.error(error.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async () => {
      if (debounceRef.current) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      return chainsApi.remove(id);
    },
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ['chain', id] });
      void queryClient.invalidateQueries({ queryKey: ['chains'] });
      message.success('Цепочка удалена');
      navigate('/chains');
    },
    onError: (error: Error) => message.error(error.message),
  });

  useEffect(() => {
    if (!dirty || !id) return;
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      saveMutation.mutate();
    }, 1200);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chain, chainName, dirty, id]);

  const undo = () => {
    const prev = history[history.length - 1];
    if (!prev) return;
    setFuture((items) => [chain, ...items]);
    setHistory((items) => items.slice(0, -1));
    setChain(prev);
    setDirty(true);
  };

  const redo = () => {
    const next = future[0];
    if (!next) return;
    setHistory((items) => [...items, chain]);
    setFuture((items) => items.slice(1));
    setChain(next);
    setDirty(true);
  };

  const handleDeleteNode = () => {
    if (!selectedNodeId || selectedNodeId === chain.root_node_id) return;
    modal.confirm({
      title: 'Удалить блок и все дочерние?',
      onOk: () => {
        const next = removeNodeSubtree(chain, selectedNodeId);
        applyChain(next);
        selectNode(next.root_node_id);
      },
    });
  };

  const handleDeleteChain = () => {
    modal.confirm({
      title: `Удалить цепочку «${chainName.trim() || 'Без названия'}»?`,
      content: 'Цепочка исчезнет из списка. В связанных рассылках потребуется выбрать новую цепочку.',
      okText: 'Удалить',
      okType: 'danger',
      cancelText: 'Отмена',
      onOk: () => deleteMutation.mutateAsync(),
    });
  };

  const handleCanvasMouseDown = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !isCanvasPanTarget(event.target)) return;
    event.preventDefault();
    setIsPanning(true);
    panStartRef.current = {
      x: event.clientX,
      y: event.clientY,
      panX: pan.x,
      panY: pan.y,
    };
  };

  const toggleFullscreen = () => {
    const element = canvasWrapRef.current;
    if (!element) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen();
      pushParams({}, ['fullscreen']);
      return;
    }
    void element.requestFullscreen();
    pushParams({ fullscreen: '1' });
  };

  if (chainQuery.isLoading || (legacyCampaign && campaignQuery.isLoading)) {
    return <Spin style={{ margin: 48 }} />;
  }

  if (chainQuery.isError) {
    return (
      <Typography.Text type="danger" style={{ margin: 48, display: 'block' }}>
        {chainQuery.error instanceof Error
          ? chainQuery.error.message
          : legacyCampaign
            ? 'Рассылка не найдена'
            : 'Цепочка не найдена'}
      </Typography.Text>
    );
  }

  const titleName = legacyCampaign ? campaignQuery.data?.name : chainName;
  const campaignLink = legacyCampaign
    ? `/campaigns/new?id=${id}`
    : campaignId
      ? `/campaigns/new?id=${campaignId}&email_chain_id=${id}`
      : `/campaigns/new?email_chain_id=${id}`;

  return (
    <div className="email-chain-page">
      <div className="email-chain-header">
        <div className="email-chain-header__title">
          <Typography.Title level={3} style={{ margin: 0 }}>
            Конструктор цепочек писем
          </Typography.Title>
          {legacyCampaign ? (
            <Typography.Text type="secondary">{titleName}</Typography.Text>
          ) : (
            <label className="email-chain-name-field">
              <span className="email-chain-name-field__label">Название</span>
              <Input
                value={chainName}
                onChange={(event) => {
                  setChainName(event.target.value);
                  setDirty(true);
                }}
                placeholder="Название цепочки"
                maxLength={255}
              />
            </label>
          )}
        </div>
        <Space wrap>
          <Button icon={<UndoOutlined />} onClick={undo} disabled={!history.length} />
          <Button icon={<RedoOutlined />} onClick={redo} disabled={!future.length} />
          <Button onClick={() => navigate(campaignLink)}>К рассылке</Button>
          {!legacyCampaign && (
            <Button
              danger
              icon={<DeleteOutlined />}
              loading={deleteMutation.isPending}
              onClick={handleDeleteChain}
            >
              Удалить цепочку
            </Button>
          )}
          {selectedNodeId && selectedNodeId !== chain.root_node_id && (
            <Button danger onClick={handleDeleteNode}>
              Удалить блок
            </Button>
          )}
          <Button loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            Сохранить
          </Button>
          <Button type="primary" loading={publishMutation.isPending} onClick={() => publishMutation.mutate()}>
            Опубликовать
          </Button>
        </Space>
      </div>

      <div className="email-chain-workspace">
        <div
          ref={canvasWrapRef}
          className={`email-chain-canvas-wrap${isPanning ? ' email-chain-canvas-wrap--panning' : ''}`}
          onMouseDown={handleCanvasMouseDown}
        >
          <div
            className="email-chain-canvas-stage"
            style={{
              width: layout.width,
              height: layout.height,
              transform: `translate(${pan.x}px, ${pan.y}px)`,
            }}
          >
            <ChainCanvas layout={layout} />
            <ChainNodeBlock
              chain={chain}
              layout={layout}
              selectedNodeId={selectedNodeId}
              onSelectNode={selectNode}
              onAddChildEmail={(nodeId) => {
                const next = addChildEmailNode(chain, nodeId);
                applyChain(next);
                const newEdge = next.edges[next.edges.length - 1];
                selectNode(newEdge?.target_id ?? nodeId);
              }}
              onAddChildLink={(nodeId, linkKind: ChainLinkKind) => {
                const next = addChildLinkNode(chain, nodeId, linkKind);
                applyChain(next);
                const newEdge = next.edges[next.edges.length - 1];
                selectNode(newEdge?.target_id ?? nodeId);
              }}
            />
          </div>
          <Button
            type="default"
            className="email-chain-canvas-fullscreen"
            icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
            aria-label={isFullscreen ? 'Выйти из полноэкранного режима' : 'Развернуть на весь экран'}
            onMouseDown={(event) => event.stopPropagation()}
            onClick={toggleFullscreen}
          />
        </div>
        <EditorSideAccordion
          className="chain-settings-panel"
          editorKind="chain"
          resourceId={id}
          buildSnapshot={() => ({
            chain,
            selected_node_id: selectedNodeId,
          })}
          handlers={{
            setChain: (next, selectedId) => {
              applyChain(next);
              if (selectedId) selectNode(selectedId);
            },
            selectChainNode: (nodeId) => selectNode(nodeId),
          }}
          settings={
            <ChainNodeSettingsPanel
              chain={chain}
              nodeId={selectedNodeId}
              emailTemplates={emailTemplatesQuery.data ?? []}
              documentTemplates={(documentTemplatesQuery.data ?? []).filter((t) => t.version?.filename)}
              onChange={(next) => applyChain(next, false)}
            />
          }
        />
      </div>
    </div>
  );
}
