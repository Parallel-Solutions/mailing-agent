import { useQuery, type QueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { campaignsApi } from '@/api/campaigns';
import type { CampaignValidateResponse } from '@/api/types';
import { campaignValidateQueryKey } from '@/features/campaigns/campaignQueryUtils';

export type CampaignLaunchValidationInput = {
  campaignId?: string;
  step: number;
  validationSignature: string;
  flushPendingChanges: () => Promise<void>;
  queryClient: QueryClient;
};

export function useCampaignLaunchValidation(input: CampaignLaunchValidationInput) {
  const { campaignId, step, validationSignature, flushPendingChanges, queryClient } = input;
  const [isChecking, setIsChecking] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [hasChecked, setHasChecked] = useState(false);
  const runIdRef = useRef(0);
  const lastValidatedSignatureRef = useRef<string | null>(null);
  const flushPendingChangesRef = useRef(flushPendingChanges);
  flushPendingChangesRef.current = flushPendingChanges;

  const validateQuery = useQuery({
    queryKey: campaignId ? campaignValidateQueryKey(campaignId) : ['campaign-validate'],
    queryFn: () => campaignsApi.validate(campaignId!, { deep: true }),
    enabled: false,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (step !== 4 || !campaignId) {
      return;
    }

    let cancelled = false;
    const runId = ++runIdRef.current;

    void (async () => {
      setIsChecking(true);
      setError(undefined);

      const cached = queryClient.getQueryData<CampaignValidateResponse>(
        campaignValidateQueryKey(campaignId),
      );
      const signatureUnchanged = lastValidatedSignatureRef.current === validationSignature;

      try {
        await flushPendingChangesRef.current();
        if (cancelled || runIdRef.current !== runId) return;

        if (signatureUnchanged && cached) {
          setHasChecked(true);
          return;
        }

        setHasChecked(false);

        await queryClient.fetchQuery<CampaignValidateResponse>({
          queryKey: campaignValidateQueryKey(campaignId),
          queryFn: () => campaignsApi.validate(campaignId, { deep: true }),
          staleTime: Infinity,
        });

        if (cancelled || runIdRef.current !== runId) return;
        lastValidatedSignatureRef.current = validationSignature;
        setHasChecked(true);
      } catch (err) {
        if (cancelled || runIdRef.current !== runId) return;
        setError(err instanceof Error ? err.message : 'Не удалось выполнить проверку');
      } finally {
        if (!cancelled && runIdRef.current === runId) {
          setIsChecking(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [step, campaignId, validationSignature, queryClient]);

  return {
    data: validateQuery.data,
    isChecking,
    error,
    hasChecked: hasChecked && !isChecking,
  };
}
