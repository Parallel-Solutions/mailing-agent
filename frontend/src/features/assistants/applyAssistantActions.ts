import type { EmailChain } from '@/api/types';
import type { AssistantAction, AssistantApplyHandlers } from './types';

export async function applyAssistantActions(
  actions: AssistantAction[],
  handlers: AssistantApplyHandlers,
): Promise<number> {
  let applied = 0;
  for (const action of actions) {
    const type = String(action.type || '');
    switch (type) {
      case 'set_subject':
        handlers.setSubject?.(String(action.subject ?? ''));
        handlers.markDirty?.();
        applied += 1;
        break;
      case 'set_html':
        handlers.setHtml?.(String(action.html ?? ''));
        handlers.markDirty?.();
        applied += 1;
        break;
      case 'insert_html':
        handlers.insertHtml?.(String(action.html ?? ''));
        handlers.markDirty?.();
        applied += 1;
        break;
      case 'insert_components':
        handlers.insertComponents?.(String(action.html ?? ''));
        handlers.markDirty?.();
        applied += 1;
        break;
      case 'load_grapes_project':
        if (action.project && typeof action.project === 'object') {
          handlers.loadGrapesProject?.(action.project as Record<string, unknown>);
          handlers.markDirty?.();
          applied += 1;
        }
        break;
      case 'set_personalization':
        handlers.setPersonalization?.(Boolean(action.enabled));
        applied += 1;
        break;
      case 'update_pdf_fields': {
        const fields = Array.isArray(action.fields) ? action.fields : [];
        handlers.updatePdfFields?.(
          fields.filter((item): item is { id: string; value?: string; font_size?: number } =>
            Boolean(item && typeof item === 'object' && 'id' in item),
          ) as Array<{ id: string; value?: string; font_size?: number }>,
        );
        handlers.markDirty?.();
        applied += 1;
        break;
      }
      case 'chain_set':
        if (action.chain && typeof action.chain === 'object') {
          handlers.setChain?.(
            action.chain as EmailChain,
            typeof action.selected_node_id === 'string' ? action.selected_node_id : null,
          );
          applied += 1;
        }
        break;
      case 'chain_select_node':
        if (typeof action.node_id === 'string') {
          handlers.selectChainNode?.(action.node_id);
          applied += 1;
        }
        break;
      case 'reload_template':
        await handlers.reloadTemplate?.();
        applied += 1;
        break;
      default:
        break;
    }
  }
  return applied;
}
