import { useEffect, useRef, useState } from 'react';
import { SerialSessionInfo } from '../components/SerialViewer';

/**
 * /ws/serial-lifecycle 를 구독하여 현재 활성 Serial 세션 목록을 유지하는 훅.
 * useDLTSessions와 동일 패턴.
 */
export function useSerialSessions() {
  const [sessions, setSessions] = useState<SerialSessionInfo[]>([]);
  const [lastEvent, setLastEvent] = useState<any>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);

  useEffect(() => {
    let closed = false;

    const connect = () => {
      if (closed) return;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${protocol}//${window.location.host}/ws/serial-lifecycle`;
      // eslint-disable-next-line no-console
      console.log('[Serial WS] connecting:', url);
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        // eslint-disable-next-line no-console
        console.log('[Serial WS] OPEN');
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'ping') return;
          // eslint-disable-next-line no-console
          console.log('[Serial WS] msg:', msg.type, msg.session_id ?? '');
          setLastEvent(msg);
          if (msg.type === 'session_started' && msg.session_id) {
            setSessions((prev) => {
              const exists = prev.find((s) => s.session_id === msg.session_id);
              if (exists) return prev;
              return [...prev, {
                session_id: msg.session_id,
                port: msg.port,
                bps: msg.bps,
                save_path: msg.save_path,
                started_at: msg.started_at,
              }];
            });
          } else if (msg.type === 'session_stopped' && msg.session_id) {
            setSessions((prev) => prev.filter((s) => s.session_id !== msg.session_id));
          }
        } catch { /* ignore */ }
      };

      ws.onclose = (e) => {
        // eslint-disable-next-line no-console
        console.warn('[Serial WS] CLOSE code=', e.code);
        if (closed) return;
        reconnectTimerRef.current = window.setTimeout(connect, 2000);
      };
      ws.onerror = (e) => {
        // eslint-disable-next-line no-console
        console.warn('[Serial WS] ERROR', e);
      };
    };

    connect();
    return () => {
      closed = true;
      if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
      try { wsRef.current?.close(); } catch { /* ignore */ }
      wsRef.current = null;
    };
  }, []);

  return { sessions, lastEvent };
}
