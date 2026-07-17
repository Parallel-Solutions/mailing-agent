import { FullscreenExitOutlined, FullscreenOutlined, RedoOutlined, UndoOutlined } from '@ant-design/icons';
import { App, Button, Space, Spin, Typography } from 'antd';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { chainsApi, type ChainRecord } from '@/api/chains';
import { campaignsApi } from '@/api/campaigns';
import { templatesApi } from '@/api/templates';
import type { ChainLinkKind, EmailChain } from '@/api/types';
import { ChainCanvas } from '@/features/campaigns/chain/ChainCanvas';
import { ChainNodeBlock } from '@/features/campaigns/chain/ChainNodeBlock';
import { ChainNodeSettingsPanel } from '@/features/campaigns/chain/ChainNodeSettingsPanel';
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
  const { message, modal } = App.useApp();
  const queryClient = useQueryClient();
  const [chain, setChain] = useState<EmailChain>(createEmptyChain());
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [history, setHistory] = useState<EmailChain[]>([]);
  const [future, setFuture] = useState<EmailChain[]>([]);
  const [dirty, setDirty] = useState(false);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const debounceRef = useRef<number | null>(null);
  const chainLoadedRef = useRef(false);
  const dirtyRef = useRef(false);
  const canvasWrapRef = useRef<HTMLDivElement>(null);
  const panStartRef = useRef({ x: 0, y: 0, panX: 0, panY: 0 });

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
    queryKey: ['templates-email'],
    queryFn: () => templatesApi.list({ template_type: 'email' }),
  });

  const documentTemplatesQuery = useQuery({
    queryKey: ['templates-document'],
    queryFn: () => templatesApi.list({ template_type: 'document' }),
  });

  useEffect(() => {
    if (!chainQuery.data?.chain) return;

    if (!chainLoadedRef.current) {
      setChain(chainQuery.data.chain);
      setSelectedNodeId(chainQuery.data.chain.root_node_id);
      setHistory([]);
      setFuture([]);
      setDirty(false);
      chainLoadedRef.current = true;
      return;
    }

    if (!dirtyRef.current) {
      setChain(chainQuery.data.chain);
    }
  }, [chainQuery.data?.chain]);

  useEffect(() => {
    chainLoadedRef.current = false;
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
    mutationFn: () =>
      legacyCampaign ? campaignsApi.putEmailChain(id, chain) : chainsApi.save(id, chain),
    onSuccess: () => {
      setDirty(false);
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
        void queryClient.invalidateQueries({ queryKey: ['campaign', id] });
      } else {
        void queryClient.invalidateQueries({ queryKey: ['chains'] });
      }
      message.success('Цепочка опубликована');
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
  }, [chain, dirty, id]);

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
        setSelectedNodeId(next.root_node_id);
      },
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
      return;
    }
    void element.requestFullscreen();
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

  const titleName = legacyCampaign
    ? campaignQuery.data?.name
    : (chainQuery.data as ChainRecord | undefined)?.name;
  const campaignLink = legacyCampaign
    ? `/campaigns/new?id=${id}`
    : `/campaigns/new?email_chain_id=${id}`;

  return (
    <div className="email-chain-page">
      <div className="email-chain-header">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Конструктор цепочек писем
          </Typography.Title>
          <Typography.Text type="secondary">{titleName}</Typography.Text>
        </div>
        <Space wrap>
          <Button icon={<UndoOutlined />} onClick={undo} disabled={!history.length} />
          <Button icon={<RedoOutlined />} onClick={redo} disabled={!future.length} />
          <Button onClick={() => navigate(campaignLink)}>К рассылке</Button>
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
              onSelectNode={setSelectedNodeId}
              onAddChildEmail={(nodeId) => {
                const next = addChildEmailNode(chain, nodeId);
                applyChain(next);
                const newEdge = next.edges[next.edges.length - 1];
                setSelectedNodeId(newEdge?.target_id ?? nodeId);
              }}
              onAddChildLink={(nodeId, linkKind: ChainLinkKind) => {
                const next = addChildLinkNode(chain, nodeId, linkKind);
                applyChain(next);
                const newEdge = next.edges[next.edges.length - 1];
                setSelectedNodeId(newEdge?.target_id ?? nodeId);
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
        <ChainNodeSettingsPanel
          chain={chain}
          nodeId={selectedNodeId}
          emailTemplates={emailTemplatesQuery.data ?? []}
          documentTemplates={(documentTemplatesQuery.data ?? []).filter((t) => t.version?.filename)}
          onChange={(next) => applyChain(next, false)}
        />
      </div>
    </div>
  );
}
