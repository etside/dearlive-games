# Realtime events — WS reference + Socket.IO mapping

Reference transport: stdlib WS server `games/teen_patti_pro/ws.py` (`GAME_WS_PORT`,
default 5003). The DearLive developer may front it with Socket.IO; event names
and payloads are identical — only framing changes.

## Wire protocol (reference WS)

- Client → server: `{type:"subscribe", session:<session_id>, room:<room_id>}`,
  `{type:"ping"}`, bets travel over REST only.
- Server → client: `{type:"snapshot", snapshot}`, `{type:"event", event}`,
  `{type:"tick", ...}`, `{type:"error", ...}`, `{type:"pong"}`.
- Tick 1/s: `{type:"tick", kind:"round.tick", room_id, round_id, status,
  serverTime, betting_end_at}`.
- Event kinds: `round.started, bet.accepted, bet.rejected, betting.closed,
  result.published, settlement.completed, session.completed, round.cancelled`.
- Pre-RESULT events redact hands/seed; `result.published` carries
  `{round_id, winners, hands(resolved), raw_hands, deck_commit, seed}`.

## Socket.IO mapping (for the DearLive developer)

| WS reference | Socket.IO equivalent |
|---|---|
| endpoint `ws://host:5003` | namespace `/games` on developer's Socket.IO gateway |
| `subscribe{session,room}` | `socket.emit("join", {session, room})` + server `socket.join(room)` |
| server `snapshot` | `socket.emit("snapshot", snapshot)` (ack required) |
| server `event` | `io.to(room).emit("event", event)` |
| server `tick` | `io.to(room).emit("tick", tick)` |
| client `ping`/`pong` | Socket.IO heartbeats (native) |
| reconnect replay | `socket.emit("resync", {session_id, last_seen_seq})` → `POST .../reconnect` response |

Sticky-by-room routing when scaling horizontally. Clients must use
`serverTime` for countdowns (client clocks untrusted).

## Example (Socket.IO client sketch)

```js
import { io } from "socket.io-client";
const s = io(GAMES_BASE_URL + "/games", { auth: { session: SESSION } });
s.emit("join", { session: SESSION, room: ROOM });
s.on("snapshot", s => render(s));
s.on("event", e => apply(e));
s.on("tick", t => countdown(t.serverTime, t.betting_end_at));
```
