/** WebSocket client with auto-reconnect and event normalization. */

import type { ServerEvent } from "./types";

type Handler = (event: ServerEvent) => void;

function uuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export class GameSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers = new Set<Handler>();
  private sessionToken = "";
  private roomId = "teen-patti-low";
  private reconnectAttempts = 0;
  private reconnectTimer: number | undefined = undefined;
  private maxBackoff = 8000;
  private closed = false;

  constructor(url: string) {
    this.url = url;
  }

  connect(sessionToken: string, roomId: string) {
    this.sessionToken = sessionToken;
    this.roomId = roomId;
    this.closed = false;
    this.open();
  }

  private open() {
    if (this.closed) return;
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.emit({
        eventId: "local.connected",
        type: "connected",
        roundId: "",
        stateVersion: 0,
        serverTime: new Date().toISOString(),
        payload: {},
        requestId: "",
      });
      this.ws?.send(JSON.stringify({
        type: "subscribe",
        session: this.sessionToken,
        room: this.roomId,
      }));
    };

    this.ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data as string);
        // Normalize: engine may send snake_case or camelCase.
        const normalized: ServerEvent = {
          eventId: msg.event_id ?? msg.eventId ?? uuid(),
          type: msg.type ?? "unknown",
          roundId: msg.round_id ?? msg.roundId ?? "",
          stateVersion: msg.state_version ?? msg.stateVersion ?? 0,
          serverTime: msg.server_time ?? msg.serverTime ?? new Date().toISOString(),
          payload: msg.data ?? msg.payload ?? msg,
          requestId: msg.request_id ?? msg.requestId ?? "",
        };
        this.emit(normalized);
      } catch {
        /* A malformed frame is dropped, never rendered. */
      }
    };

    this.ws.onclose = () => {
      if (this.closed) return;
      this.emit({
        eventId: `local.disconnected.${Date.now()}`,
        type: "disconnected",
        roundId: "",
        stateVersion: 0,
        serverTime: new Date().toISOString(),
        payload: {},
        requestId: "",
      });
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      /* onclose follows; no separate handling needed. */
    };
  }

  private scheduleReconnect() {
    if (this.closed) return;
    this.reconnectAttempts++;
    const delay = Math.min(this.maxBackoff, 500 * Math.pow(2, this.reconnectAttempts));
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = window.setTimeout(() => this.open(), delay);
  }

  on(handler: Handler): () => void {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  private emit(event: ServerEvent) {
    this.handlers.forEach((h) => h(event));
  }

  send(data: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  close() {
    this.closed = true;
    window.clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }
}
