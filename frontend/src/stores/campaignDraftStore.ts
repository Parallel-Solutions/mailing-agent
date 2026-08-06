import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import type { Campaign } from '@/api/types';

type SaveState = 'idle' | 'saving' | 'saved' | 'error';
type DraftPatch = Record<string, unknown>;

type DraftState = {
  campaignId: string | null;
  draft: Partial<Campaign>;
  pendingPatch: DraftPatch;
  saveState: SaveState;
  setCampaignId: (id: string | null) => void;
  queueDraftPatch: (patch: DraftPatch) => void;
  acknowledgeDraftPatch: (patch: DraftPatch) => void;
  clearPendingPatch: () => void;
  replaceDraft: (draft: Partial<Campaign>) => void;
  setSaveState: (state: SaveState) => void;
  reset: () => void;
};

type PersistedDraftState = Pick<DraftState, 'campaignId' | 'pendingPatch'>;

export const useCampaignDraftStore = create<DraftState>()(
  persist<DraftState, [], [], PersistedDraftState>(
    (set) => ({
      campaignId: null,
      draft: {},
      pendingPatch: {},
      saveState: 'idle',
      setCampaignId: (id) => set({ campaignId: id }),
      queueDraftPatch: (patch) =>
        set((state) => ({
          draft: { ...state.draft, ...patch } as Partial<Campaign>,
          pendingPatch: { ...state.pendingPatch, ...patch },
          saveState: 'idle',
        })),
      acknowledgeDraftPatch: (patch) =>
        set((state) => {
          const pendingPatch = { ...state.pendingPatch };
          for (const [key, value] of Object.entries(patch)) {
            if (Object.is(pendingPatch[key], value)) delete pendingPatch[key];
          }
          return { pendingPatch };
        }),
      clearPendingPatch: () => set({ pendingPatch: {} }),
      replaceDraft: (draft) =>
        set((state) => ({
          draft: { ...draft, ...state.pendingPatch } as Partial<Campaign>,
        })),
      setSaveState: (saveState) => set({ saveState }),
      reset: () => set({
        campaignId: null,
        draft: {},
        pendingPatch: {},
        saveState: 'idle',
      }),
    }),
    {
      name: 'campaignflow:active-campaign-draft',
      storage: createJSONStorage<PersistedDraftState>(() => sessionStorage),
      partialize: (state) => ({
        campaignId: state.campaignId,
        pendingPatch: state.pendingPatch,
      }),
    },
  ),
);
