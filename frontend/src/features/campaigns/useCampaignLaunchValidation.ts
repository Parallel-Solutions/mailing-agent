import { useQuery, type QueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
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
  const [error, setError] = useState<unknown>();
  const [hasChecked, setHasChecked] = useState(false);
  const runIdRef = useRef(0);
  const isMountedRef = useRef(true);
  const lastValidatedSignatureRef = useRef<string | null>(null);
  const flushPendingChangesRef = useRef(flushPendingChanges);
  flushPendingChangesRef.current = flushPendingChanges;

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const validateQuery = useQuery({
    queryKey: campaignId
      ? campaignValidateQueryKey(campaignId, validationSignature)
      : ['campaign-validate'],
    queryFn: ({ signal }) => campaignsApi.validate(campaignId!, { deep: true, signal }),
    enabled: false,
    staleTime: Infinity,
  });

  const runValidation = useCallback(
    (options?: { force?: boolean }) => {
      if (step !== 4 || !campaignId) {
        return;
      }

      const force = options?.force ?? false;
      const runId = ++runIdRef.current;

      void (async () => {
        setIsChecking(true);
        setError(undefined);

        const cached = queryClient.getQueryData<CampaignValidateResponse>(
          campaignValidateQueryKey(campaignId, validationSignature),
        );
        const signatureUnchanged = lastValidatedSignatureRef.current === validationSignature;

        try {
          await flushPendingChangesRef.current();
          if (!isMountedRef.current || runIdRef.current !== runId) return;

          if (!force && signatureUnchanged && cached) {
            setHasChecked(true);
            return;
          }

          setHasChecked(false);

          await queryClient.fetchQuery<CampaignValidateResponse>({
            queryKey: campaignValidateQueryKey(campaignId, validationSignature),
            queryFn: ({ signal }) => campaignsApi.validate(campaignId, { deep: true, signal }),
            staleTime: force ? 0 : Infinity,
          });

          if (!isMountedRef.current || runIdRef.current !== runId) return;
          lastValidatedSignatureRef.current = validationSignature;
          setHasChecked(true);
        } catch (err) {
          if (!isMountedRef.current || runIdRef.current !== runId) return;
          setError(err);
        } finally {
          if (isMountedRef.current && runIdRef.current === runId) {
            setIsChecking(false);
          }
        }
      })();
    },
    [step, campaignId, validationSignature, queryClient],
  );

  useEffect(() => {
    runValidation();
  }, [runValidation]);

  const retry = useCallback(() => runValidation({ force: true }), [runValidation]);

  return {
    data: validateQuery.data,
    isChecking,
    error,
    hasChecked: hasChecked && !isChecking,
    retry,
  };
}
