import { create } from 'zustand';
import type { Campaign } from '@/api/types';

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

type DraftState = {
  campaignId: string | null;
  draft: Partial<Campaign>;
  saveState: SaveState;
  setCampaignId: (id: string | null) => void;
  setDraft: (patch: Partial<Campaign>) => void;
  replaceDraft: (draft: Partial<Campaign>) => void;
  setSaveState: (state: SaveState) => void;
  reset: () => void;
};

export const useCampaignDraftStore = create<DraftState>((set) => ({
  campaignId: null,
  draft: {},
  saveState: 'idle',
  setCampaignId: (id) => set({ campaignId: id }),
  setDraft: (patch) => set((state) => ({ draft: { ...state.draft, ...patch } })),
  replaceDraft: (draft) => set({ draft }),
  setSaveState: (saveState) => set({ saveState }),
  reset: () => set({ campaignId: null, draft: {}, saveState: 'idle' }),
}));
