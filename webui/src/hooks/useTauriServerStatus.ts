import { invoke } from '@tauri-apps/api/core';
import { useEffect, useState } from 'react';
import { isTauriEnvironment } from '@/utils/tauri';

interface TauriServerStatus {
  running: boolean;
  port: number;
  port_available: boolean;
  manages_local_server: boolean;
}

let cachedServerStatus: TauriServerStatus | null = null;
let inflightServerStatus: Promise<TauriServerStatus> | null = null;

async function fetchTauriServerStatus(): Promise<TauriServerStatus> {
  if (cachedServerStatus) {
    return cachedServerStatus;
  }

  if (!inflightServerStatus) {
    inflightServerStatus = invoke<TauriServerStatus>('get_server_status')
      .then((status) => {
        cachedServerStatus = status;
        return status;
      })
      .finally(() => {
        inflightServerStatus = null;
      });
  }

  return inflightServerStatus;
}

export function useTauriServerStatus() {
  const isTauri = isTauriEnvironment();
  const [serverStatus, setServerStatus] = useState<TauriServerStatus | null>(
    isTauri ? cachedServerStatus : null
  );
  const [isLoading, setIsLoading] = useState(isTauri && cachedServerStatus === null);

  useEffect(() => {
    if (!isTauri) {
      setServerStatus(null);
      setIsLoading(false);
      return;
    }

    if (cachedServerStatus) {
      setServerStatus(cachedServerStatus);
      setIsLoading(false);
      return;
    }

    let cancelled = false;
    setIsLoading(true);

    void fetchTauriServerStatus()
      .then((status) => {
        if (cancelled) return;
        setServerStatus(status);
      })
      .catch((error) => {
        if (cancelled) return;
        console.error('Failed to query Tauri server status:', error);
        setServerStatus({
          running: false,
          port: 5700,
          port_available: false,
          manages_local_server: false,
        });
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [isTauri]);

  return {
    isLoading,
    managesLocalServer: isTauri ? (serverStatus?.manages_local_server ?? null) : false,
    serverStatus,
  };
}
