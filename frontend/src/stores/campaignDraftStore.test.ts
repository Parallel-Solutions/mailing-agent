import { beforeEach, describe, expect, it } from 'vitest';
import { useCampaignDraftStore } from './campaignDraftStore';

describe('campaign draft store', () => {
  beforeEach(() => {
    sessionStorage.clear();
    useCampaignDraftStore.getState().reset();
  });

  it('keeps a newer local value when an older save is acknowledged', () => {
    const store = useCampaignDraftStore.getState();
    store.queueDraftPatch({ name: 'First', smtp_mailbox_id: 'sender-1' });
    useCampaignDraftStore.getState().queueDraftPatch({ name: 'Second' });

    useCampaignDraftStore.getState().acknowledgeDraftPatch({
      name: 'First',
      smtp_mailbox_id: 'sender-1',
    });

    expect(useCampaignDraftStore.getState().pendingPatch).toEqual({ name: 'Second' });
    expect(useCampaignDraftStore.getState().draft.name).toBe('Second');
  });

  it('merges unsaved values over a server refresh', () => {
    useCampaignDraftStore.getState().queueDraftPatch({ name: 'Local name' });
    useCampaignDraftStore.getState().replaceDraft({ id: 'draft-1', name: 'Server name', status: 'draft' });

    expect(useCampaignDraftStore.getState().draft).toMatchObject({
      id: 'draft-1',
      name: 'Local name',
      status: 'draft',
    });
  });

  it('clears the active pointer and pending values on reset', () => {
    useCampaignDraftStore.getState().setCampaignId('draft-1');
    useCampaignDraftStore.getState().queueDraftPatch({ name: 'Pending' });

    useCampaignDraftStore.getState().reset();

    expect(useCampaignDraftStore.getState()).toMatchObject({
      campaignId: null,
      draft: {},
      pendingPatch: {},
      saveState: 'idle',
    });
  });
});
