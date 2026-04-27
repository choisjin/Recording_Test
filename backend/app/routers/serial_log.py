"""Serial 뷰어 REST + WebSocket 라우터 (DLTLogging과 동일한 인터페이스).

엔드포인트:
  GET  /api/serial-log/sessions                  — 활성 로깅 세션 목록
  GET  /api/serial-log/{session_id}/logs         — 백필용 최근 로그 조회
  GET  /api/serial-log/{session_id}/step-marks   — 스텝 마킹 위치
  POST /api/serial-log/{session_id}/search-all   — 전체 로그 검색
  POST /api/serial-log/{session_id}/search-section — 스텝 구간 검색
  WS   /ws/serial-log/{session_id}               — 실시간 로그 스트리밍
  WS   /ws/serial-lifecycle                      — 세션 시작/종료 이벤트
"""

from __future__ import annotations

import asyncio
import functools
import logging
import queue
import urllib.parse

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..plugins.SerialLogging import SERIAL_HUB, get_active_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/serial-log", tags=["serial-log"])


def _decode_session(session_id: str) -> str:
    return urllib.parse.unquote(session_id)


@router.get("/sessions")
async def list_sessions():
    return {"sessions": SERIAL_HUB.list_sessions()}


@router.get("/{session_id}/logs")
async def get_recent_logs(session_id: str, limit: int = 1000):
    sid = _decode_session(session_id)
    inst = get_active_session(sid)
    if not inst:
        raise HTTPException(404, f"Serial session '{sid}' not active")
    return {
        "session_id": sid,
        "logs": inst.GetRecentLogs(limit),
        "total": inst._line_counter,
    }


@router.get("/{session_id}/step-marks")
async def get_step_marks(session_id: str):
    sid = _decode_session(session_id)
    inst = get_active_session(sid)
    if not inst:
        raise HTTPException(404, f"Serial session '{sid}' not active")
    marks = inst.GetStepMarks()
    return {"session_id": sid, "marks": [{"step": k, "index": v} for k, v in sorted(marks.items())]}


class SearchAllRequest(BaseModel):
    keyword: str
    max_results: int = 500


@router.post("/{session_id}/search-all")
async def search_all(session_id: str, req: SearchAllRequest):
    sid = _decode_session(session_id)
    inst = get_active_session(sid)
    if not inst:
        raise HTTPException(404, f"Serial session '{sid}' not active")
    matches = inst.SearchAllDetailed(req.keyword, req.max_results)
    return {
        "session_id": sid,
        "keyword": req.keyword,
        "count": len(matches),
        "matches": matches,
    }


class SearchSectionRequest(BaseModel):
    keyword: str
    from_step: int
    to_step: int
    max_results: int = 500


@router.post("/{session_id}/search-section")
async def search_section(session_id: str, req: SearchSectionRequest):
    sid = _decode_session(session_id)
    inst = get_active_session(sid)
    if not inst:
        raise HTTPException(404, f"Serial session '{sid}' not active")
    if req.from_step not in inst._step_marks:
        return {
            "session_id": sid,
            "keyword": req.keyword,
            "error": f"step {req.from_step} not marked",
            "matches": [],
            "count": 0,
        }
    matches = inst.SearchSectionDetailed(req.keyword, req.from_step, req.to_step, req.max_results)
    return {
        "session_id": sid,
        "keyword": req.keyword,
        "from_step": req.from_step,
        "to_step": req.to_step,
        "count": len(matches),
        "matches": matches,
    }


# ── WebSocket: 실시간 로그 스트림 ─────────────────────────────────────

async def ws_serial_stream(websocket: WebSocket, session_id: str):
    """실시간 Serial 로그 스트리밍. 접속 시 backfill 후 새 로그 push."""
    await websocket.accept()
    sid = _decode_session(session_id)
    q: queue.Queue = SERIAL_HUB.register_log(sid)

    inst = get_active_session(sid)
    if inst:
        try:
            await websocket.send_json({
                "type": "backfill",
                "session_id": sid,
                "logs": inst.GetRecentLogs(2000),
            })
        except Exception:
            pass

    loop = asyncio.get_event_loop()
    try:
        while True:
            try:
                line = await loop.run_in_executor(None, functools.partial(q.get, True, 1.0))
            except queue.Empty:
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
                continue
            try:
                await websocket.send_json({"type": "log", "line": line})
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("Serial stream WS error (sid=%s): %s", sid, e)
    finally:
        SERIAL_HUB.unregister_log(sid, q)
        try:
            await websocket.close()
        except Exception:
            pass


async def ws_serial_lifecycle(websocket: WebSocket):
    """세션 시작/종료 이벤트 스트림. recv 루프 동시 실행으로 close 즉시 감지."""
    await websocket.accept()
    q: queue.Queue = SERIAL_HUB.register_lifecycle()
    client = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "?"
    logger.info("[Serial WS] lifecycle subscriber connected: %s", client)
    loop = asyncio.get_event_loop()

    async def _recv_drain():
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return
        except Exception:
            return

    recv_task = asyncio.create_task(_recv_drain())
    disconnect_reason = "normal"
    try:
        while True:
            if recv_task.done():
                disconnect_reason = "client_close_detected"
                break
            try:
                event = await loop.run_in_executor(None, functools.partial(q.get, True, 2.0))
            except queue.Empty:
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception as e:
                    disconnect_reason = f"ping_send_fail: {e}"
                    break
                continue
            try:
                await websocket.send_json(event)
            except Exception as e:
                disconnect_reason = f"event_send_fail: {e}"
                break
    except WebSocketDisconnect:
        disconnect_reason = "WebSocketDisconnect"
    except Exception as e:
        logger.warning("[Serial WS] lifecycle error: %s", e)
        disconnect_reason = f"exception: {e}"
    finally:
        recv_task.cancel()
        try:
            await recv_task
        except (asyncio.CancelledError, Exception):
            pass
        SERIAL_HUB.unregister_lifecycle(q)
        logger.info("[Serial WS] lifecycle subscriber disconnected: %s (reason=%s)", client, disconnect_reason)
        try:
            await websocket.close()
        except Exception:
            pass
