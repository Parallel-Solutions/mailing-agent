import type { QueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { invalidateCampaignDerivedData } from '@/features/campaigns/campaignQueryUtils';
import { runMappingAutoSuggest } from '@/features/campaigns/mappingAutoSuggestUtils';

export type MappingAutoSuggestInput = {
  campaignId?: string;
  step: number;
  recipientCount: number;
  mappingConfirmed: boolean;
  mappingInputsSignature: string;
  queryClient: QueryClient;
  onDraftRefresh?: () => void | Promise<void>;
  onAutoSaved?: () => void;
  onNeedsReview?: () => void;
};

export function useCampaignMappingAutoSuggest(input: MappingAutoSuggestInput): { isRunning: boolean } {
  const {
    campaignId,
    step,
    recipientCount,
    mappingConfirmed,
    mappingInputsSignature,
    queryClient,
    onDraftRefresh,
    onAutoSaved,
    onNeedsReview,
  } = input;

  const [isRunning, setIsRunning] = useState(false);
  const lastSignatureRef = useRef<string | null>(null);
  const reviewPromptedRef = useRef<string | null>(null);
  const runIdRef = useRef(0);

  useEffect(() => {
    const inMappingWindow = step >= 2 && step <= 4;
    if (!inMappingWindow || !campaignId || mappingConfirmed || recipientCount <= 0) {
      setIsRunning(false);
      if (mappingConfirmed) {
        lastSignatureRef.current = null;
        reviewPromptedRef.current = null;
      }
      return;
    }

    if (lastSignatureRef.current === mappingInputsSignature) {
      return;
    }

    let cancelled = false;
    const runId = ++runIdRef.current;
    setIsRunning(true);

    void (async () => {
      try {
        const saved = await runMappingAutoSuggest(campaignId, queryClient, mappingInputsSignature);
        if (cancelled || runIdRef.current !== runId) return;

        if (saved) {
          invalidateCampaignDerivedData(queryClient, campaignId);
          await onDraftRefresh?.();
          onAutoSaved?.();
          reviewPromptedRef.current = null;
        } else if (reviewPromptedRef.current !== mappingInputsSignature) {
          reviewPromptedRef.current = mappingInputsSignature;
          onNeedsReview?.();
        }
        lastSignatureRef.current = mappingInputsSignature;
      } catch {
        if (cancelled || runIdRef.current !== runId) return;
      } finally {
        if (!cancelled && runIdRef.current === runId) {
          setIsRunning(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [
    campaignId,
    mappingConfirmed,
    mappingInputsSignature,
    onAutoSaved,
    onDraftRefresh,
    onNeedsReview,
    queryClient,
    recipientCount,
    step,
  ]);

  return { isRunning };
}
