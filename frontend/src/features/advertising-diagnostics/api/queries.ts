import { queryOptions } from '@tanstack/react-query';
import { getAdvertisingHistory, getAdvertisingShops } from './service';

export const advertisingDiagnosticKeys = {
  all: ['advertising-diagnostics'] as const,
  shops: () => [...advertisingDiagnosticKeys.all, 'shops'] as const,
  history: () => [...advertisingDiagnosticKeys.all, 'history'] as const
};

export function advertisingShopsQueryOptions() {
  return queryOptions({
    queryKey: advertisingDiagnosticKeys.shops(),
    queryFn: getAdvertisingShops,
    staleTime: 5 * 60 * 1000,
    retry: 1
  });
}

export function advertisingHistoryQueryOptions() {
  return queryOptions({
    queryKey: advertisingDiagnosticKeys.history(),
    queryFn: getAdvertisingHistory,
    staleTime: 30_000,
    retry: 1
  });
}
