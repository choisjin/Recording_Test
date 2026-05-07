"""SerialLogging — 시리얼 포트 로그 캡처·저장·키워드 판정 모듈.

시나리오 스텝 내에서:
  - StartLogging / StopLogging 으로 시리얼 캡처 시작/종료
  - SendCommand 로 명령 전송
  - fail_on_keyword 로 비정상 키워드 검출 시 fail row 자동 누적

사용 예 (시나리오 스텝):
  SerialLogging.StartLogging()                    # 연결 + 캡처 시작
  SerialLogging.SendCommand("reboot")             # 명령 전송
  SerialLogging.fail_on_keyword("ERROR", time=10) # 10초간 ERROR 모니터링
  SerialLogging.StopLogging()                     # 캡처 종료 + 파일 저장
"""

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ==========================================================================
# Serial 뷰어용 Pub/Sub 허브 — DLT_HUB와 동일 패턴.
# ==========================================================================

class _SerialHub:
    """Serial 로깅 세션 + 로그 스트림 구독자 관리 (thread-safe)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}
        self._lifecycle_subs: list[queue.Queue] = []
        self._log_subs: dict[str, list[queue.Queue]] = {}

    def list_sessions(self) -> list[dict]:
        with self._lock:
            return [{"session_id": sid, **info} for sid, info in self._sessions.items()]

    def emit_lifecycle(self, event: dict) -> None:
        sid = event.get("session_id", "")
        etype = event.get("type", "")
        with self._lock:
            if etype == "session_started" and sid:
                self._sessions[sid] = {k: v for k, v in event.items() if k not in ("type",)}
            elif etype == "session_stopped" and sid:
                self._sessions.pop(sid, None)
            subs = list(self._lifecycle_subs)
        logger.info("[SERIAL_HUB] emit_lifecycle type=%s sid=%s subscribers=%d",
                    etype, sid, len(subs))
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass

    def register_lifecycle(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._lifecycle_subs.append(q)
            for sid, info in self._sessions.items():
                try:
                    q.put_nowait({"type": "session_started", "session_id": sid, **info})
                except queue.Full:
                    break
        return q

    def unregister_lifecycle(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._lifecycle_subs:
                self._lifecycle_subs.remove(q)

    def register_log(self, session_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=10000)
        with self._lock:
            self._log_subs.setdefault(session_id, []).append(q)
        return q

    def unregister_log(self, session_id: str, q: queue.Queue) -> None:
        with self._lock:
            lst = self._log_subs.get(session_id, [])
            if q in lst:
                lst.remove(q)

    def emit_log(self, session_id: str, line: str) -> None:
        with self._lock:
            subs = list(self._log_subs.get(session_id, []))
        for q in subs:
            try:
                q.put_nowait(line)
            except queue.Full:
                pass


SERIAL_HUB = _SerialHub()


def get_active_session(session_id: str) -> Optional["SerialLogging"]:
    """session_id(port@bps)에 대응하는 현재 활성 SerialLogging 인스턴스 반환."""
    try:
        from backend.app.services.module_service import _instances
    except Exception:
        return None
    inst = _instances.get("SerialLogging")
    if not inst:
        return None
    if f"{getattr(inst, '_port', '')}@{getattr(inst, '_bps', 0)}" == session_id:
        return inst
    return None


def _get_run_output_dir() -> Optional[Path]:
    """현재 재생 런의 출력 디렉토리. 재생 중이 아니면 None."""
    try:
        from backend.app.services.playback_service import get_run_output_dir
        return get_run_output_dir()
    except Exception:
        return None


def _is_scenario_playback() -> bool:
    """시나리오 재생 active 여부. lifecycle 이벤트에 컨텍스트 플래그로 부착되어
    프론트엔드(RecordPage) 모달 자동 오픈을 막는다 — ScenarioPage가 이미 좌측 카드로 표시함."""
    try:
        from backend.app.services.playback_service import is_playback_active
        return is_playback_active()
    except Exception:
        return False


def _auto_save_path(prefix: str = "serial") -> str:
    """컨텍스트별 자동 저장 경로.

    - 재생 중: {run_dir}/logs/{prefix}_{ts}.log
    - 스텝 테스트: backend/results/Temp_logs/{prefix}_{ts}.log
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    run_dir = _get_run_output_dir()
    if run_dir:
        log_dir = run_dir / "logs"
    else:
        try:
            from backend.app.services.playback_service import RESULTS_DIR
            log_dir = Path(RESULTS_DIR) / "Temp_logs"
        except Exception:
            log_dir = Path(__file__).resolve().parent.parent.parent / "results" / "Temp_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / f"{prefix}_{ts}.log")


class SerialLogging:
    """시리얼 로그 캡처·저장·키워드 판정 모듈.

    생성자:
        port: 시리얼 포트 (예: COM3)
        bps: 보드레이트 (기본 115200)
    """

    def __init__(self, port: str = "", bps: int = 115200):
        self._port = port
        self._bps = int(bps)
        self._serial = None  # serial.Serial (lazy import)
        self._capture_thread: Optional[threading.Thread] = None
        self._capturing = False
        self._lock = threading.Lock()

        # 로그 버퍼 + 라인별 capture timestamp (epoch float)
        # _log_capture_ts와 _logs는 같은 길이 유지 — backfill 스캔 시 정확한 발생 시각 사용
        self._logs: list[str] = []
        self._log_capture_ts: list[float] = []
        self._line_counter = 0

        # 파일 저장
        self._save_file = None
        self._save_path: Optional[str] = None

        # fail_on_keyword: keyword가 라인에 **포함되면** fail로 보고.
        # 'ERROR'/'Fail' 같은 비정상 단어 검출에 사용.
        self._fail_keywords: dict[str, dict] = {}
        self._fail_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 연결 관리 (내부)
    # ------------------------------------------------------------------

    def _connect(self, settle_ms: int = 500) -> str:
        """시리얼 포트 연결.

        Args:
            settle_ms: open 직후 드라이버/디바이스 안정화 대기(ms).
                       USB-Serial 어댑터(FTDI/CP210x/CH340 등)는 open 시 DTR/RTS 펄스가
                       발생하여 디바이스가 짧게 리셋되는 경우가 있고, OS도 buffer 설정 적용에
                       수십~수백 ms를 쓴다. 이 시간 안에 SendCommand가 들어오면 씹힘 — settle 후에
                       reset_input/output_buffer로 가비지를 비우고 capture loop를 시작한다.
        """
        if not self._port:
            return "ERROR: port가 설정되지 않았습니다"
        if self._serial and self._serial.is_open:
            return ""  # 이미 연결됨 — 정상

        try:
            import serial as pyserial
            self._serial = pyserial.Serial(self._port, self._bps, timeout=1)
            # 1) 드라이버/디바이스 안정화 — capture loop 시작 전에 처리 (가비지 라인 캡처 방지)
            if settle_ms and settle_ms > 0:
                time.sleep(settle_ms / 1000.0)
            # 2) open 동안 들어온 가비지 / 송신 잔여 비우기
            try:
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
            except Exception as _be:
                logger.debug("[SerialLogging] buffer reset skipped: %s", _be)
            self._logs.clear()
            self._log_capture_ts.clear()
            self._line_counter = 0
            with self._fail_lock:
                self._fail_keywords.clear()
            # 3) capture loop 시작 후, 스레드가 실제 readline에 진입할 시간을 짧게 보장
            self._start_capture()
            time.sleep(0.05)  # capture thread가 첫 read 루프에 진입할 충분한 시간
            logger.info("[SerialLogging] Connected to %s @ %d (settle=%dms)",
                        self._port, self._bps, settle_ms)
            return ""
        except Exception as e:
            self._serial = None
            logger.error("[SerialLogging] Connection failed: %s", e)
            return f"ERROR: 연결 실패 — {e}"

    def _disconnect(self):
        """시리얼 포트 연결 해제. cleanup 경로에서 호출되므로 어떤 단계도 raise 하지 않는다."""
        try:
            self._stop_capture()
        except Exception as e:
            logger.warning("[SerialLogging] stop_capture raised: %s", e)
        if self._serial is not None:
            try:
                if getattr(self._serial, "is_open", False):
                    self._serial.close()
            except Exception as e:
                logger.warning("[SerialLogging] serial.close raised: %s", e)
        self._serial = None
        logger.info("[SerialLogging] Disconnected")

    def _IsConnected(self) -> bool:
        """연결 상태 확인. StartLogging 전에도 모듈은 사용 가능 (지연 연결)."""
        return True

    def _session_id(self) -> str:
        return f"{self._port}@{self._bps}"

    # ------------------------------------------------------------------
    # 뷰어 연동: StartLogging / StopLogging (DLTLogging과 동일 시그니처)
    # ------------------------------------------------------------------

    def StartLogging(self, settle_ms: int = 500) -> str:
        """뷰어 연동용: 시리얼 연결 + 로그 캡처 시작 (메모리만, 파일 저장 없음).

        Args:
            settle_ms: 포트 open 후 안정화 대기 시간(ms). 기본 500ms — USB-Serial
                       드라이버 reset/buffer settle 동안 다음 스텝의 SendCommand가 씹히지
                       않도록 보장. 디바이스가 빠르게 준비되면 100~200으로 줄여도 되고,
                       Arduino처럼 DTR-reset되는 보드는 1500~2000으로 늘릴 수 있다.

        리턴 시점에는 포트가 열리고, 입력/출력 버퍼가 비워졌으며, capture 스레드가 첫
        readline 루프에 진입한 상태이므로 다음 스텝에서 즉시 SendCommand해도 안전하다.
        SERIAL_HUB에 session_started 이벤트를 emit하여 뷰어가 자동 오픈된다.
        """
        err = self._connect(settle_ms=settle_ms)
        if err:
            return err
        SERIAL_HUB.emit_lifecycle({
            "type": "session_started",
            "session_id": self._session_id(),
            "port": self._port,
            "bps": self._bps,
            "save_path": "",
            "started_at": time.time(),
            "scenario_playback": _is_scenario_playback(),
        })
        return f"Logging started: {self._port} @ {self._bps} (settle={settle_ms}ms)"

    def StopLogging(self, save_path: str = "") -> str:
        """뷰어 연동용: 시리얼 연결 종료 + 메모리 버퍼를 파일로 일괄 저장.

        Args:
            save_path: 저장할 파일 경로. 빈 값이면 컨텍스트별 자동 저장:
                - 재생 중: {run_dir}/logs/serial_{timestamp}.log
                - 스텝 테스트: backend/results/Temp_logs/serial_{timestamp}.log

        파일 저장 단계의 어떤 예외(경로 해석/mkdir/open)가 발생해도 finally에서
        _close_save_file + _disconnect를 무조건 실행하여 COM 포트 leak을 방지한다.
        cleanup_active_instances가 재생 중단 시 자동 호출하는 진입점이기도 하다.
        """
        sid = self._session_id()
        with self._lock:
            logs_snapshot = list(self._logs)

        saved_path = ""
        save_error = ""
        try:
            if not save_path:
                save_path = _auto_save_path("serial")
            elif not os.path.dirname(save_path):
                base_dir = Path(_auto_save_path("serial")).parent
                save_path = str(base_dir / save_path)
            try:
                os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(logs_snapshot))
                    if logs_snapshot:
                        f.write("\n")
                saved_path = save_path
                logger.info("[SerialLogging] Saved %d lines to %s", len(logs_snapshot), save_path)
            except Exception as e:
                logger.error("[SerialLogging] Save failed: %s", e)
                save_error = str(e)
        except Exception as e:
            # _auto_save_path 등 경로 해석 자체가 실패해도 finally의 _disconnect를 보장
            logger.error("[SerialLogging] StopLogging path resolution failed: %s", e)
            save_error = save_error or str(e)
        finally:
            # 저장 파일 누수 방지 + 캡처/시리얼 무조건 정리
            self._close_save_file()
            self._disconnect()
            try:
                SERIAL_HUB.emit_lifecycle({
                    "type": "session_stopped",
                    "session_id": sid,
                    "save_path": saved_path,
                    "stopped_at": time.time(),
                })
            except Exception:
                pass

        if save_error:
            return f"ERROR: 저장 실패 — {save_error}"
        return f"Logging stopped. Saved {len(logs_snapshot)} lines to: {saved_path}"

    # ------------------------------------------------------------------
    # 뷰어용 조회 (DLT와 동일 인터페이스)
    # ------------------------------------------------------------------

    def _GetRecentLogs(self, limit: int = 1000) -> list[str]:
        with self._lock:
            return list(self._logs[-int(limit):]) if self._logs else []

    def _close_save_file(self):
        if self._save_file:
            try:
                self._save_file.close()
            except Exception:
                pass
            self._save_file = None
            self._save_path = None

    # ------------------------------------------------------------------
    # 키워드 검출 모드 — 키워드가 들어오면 fail로 보고
    # ------------------------------------------------------------------

    def fail_on_keyword(self, keyword: str, time: float = 0, name: str = "") -> str:
        """캡처되는 라인에 keyword가 **포함되면** 시나리오 결과에 fail row 자동 누적.

        직관적 사용 — 'ERROR'/'Fail'/'crash' 등 비정상 단어 검출용.
        시나리오 재생 중일 때만 fail 보고됨. 결과 페이지에서 row 클릭 시 영상 점프 가능.

        **첫 호출 시 backfill**: 호출 이전에 이미 캡처된 로그 라인도 함께 스캔하여
        keyword 매칭 라인의 정확한 capture timestamp로 fail row 추가. 시나리오의
        StartLogging부터 fail_on_keyword 호출 사이에 발생한 매칭이 누락되지 않음.

        모드:
          - **time > 0 (sync, 권장)**: 등록 + backfill → 해당 시간 동안 모니터링 → 자동 unregister.
            검출된 fail은 이 스텝의 인라인 결과로 분류되어 결과 표에 바로 아래 Fail_Count_N으로 표시.
            blocking 호출이라 다음 스텝은 time 종료 후 실행됨.
          - **time == 0 (legacy)**: 등록 후 즉시 리턴, 백그라운드로 시나리오 종료까지 누적.
            같은 name 재호출 시 현재까지 hit count + first/last timestamp 반환.
        """
        # NOTE: time 매개변수가 stdlib `time` 모듈 이름과 겹친다.
        # 함수 시작에서 _time_mod로 alias, 이후 time은 매개변수 의미로 사용.
        import time as _time_mod
        sync_duration = float(time) if time else 0.0
        # sync 모드면 현재 실행 중인 스텝을 parent로 박음
        parent_step_id: Optional[int] = None
        parent_repeat_index = 1
        if sync_duration > 0:
            try:
                from backend.app.services.playback_service import get_current_step_context
                parent_step_id, parent_repeat_index = get_current_step_context()
            except Exception:
                pass

        key = name.strip() if name else f"fail_{keyword}"
        backfill_reports: list[tuple[float, str]] = []  # 첫 호출 backfill용
        is_new = False
        with self._fail_lock:
            existing = self._fail_keywords.get(key)
            if existing is None:
                is_new = True
                new_entry = {
                    "keyword": keyword,
                    "hit_count": 0,
                    "hit_timestamps": [],
                    "started_at": _time_mod.time(),
                    "parent_step_id": parent_step_id,
                    "parent_repeat_index": parent_repeat_index,
                }
                self._fail_keywords[key] = new_entry
                # backfill: 이미 캡처된 라인 중 keyword 매칭한 것 모두 보고
                with self._lock:
                    logs_snapshot = list(self._logs)
                    ts_snapshot = list(self._log_capture_ts)
                for i, ln in enumerate(logs_snapshot):
                    if keyword in ln:
                        ts_b = ts_snapshot[i] if i < len(ts_snapshot) else _time_mod.time()
                        new_entry["hit_count"] += 1
                        new_entry["hit_timestamps"].append(ts_b)
                        backfill_reports.append((ts_b, ln))
                logger.info("[SerialLogging] fail_on_keyword started: name='%s' keyword='%s' backfill=%d sync=%.1fs parent=%s",
                            key, keyword, len(backfill_reports), sync_duration, parent_step_id)
            else:
                cnt = existing["hit_count"]
                ts_list = list(existing["hit_timestamps"])
                started_at = existing["started_at"]
                kw = existing["keyword"]
                # 동일 name 재호출이면서 sync 요청이면, 등록의 parent도 갱신 (현재 스텝 결과로 흡수)
                if sync_duration > 0:
                    existing["parent_step_id"] = parent_step_id
                    existing["parent_repeat_index"] = parent_repeat_index

        # backfill 항목을 playback_service에 보고 (lock 밖에서)
        if is_new and backfill_reports:
            try:
                from backend.app.services.playback_service import report_runtime_fail
                for ts_b, ln in backfill_reports:
                    report_runtime_fail(
                        "SerialLogging", keyword, ts_b, ln, reason="matched",
                        repeat_index=parent_repeat_index,
                        parent_step_id=parent_step_id,
                    )
            except Exception:
                pass

        # sync 모드: 지정된 시간 동안 capture loop가 보고하도록 대기 후 자동 해제
        if sync_duration > 0:
            _time_mod.sleep(sync_duration)
            with self._fail_lock:
                final_entry = self._fail_keywords.pop(key, None)
            final_cnt = final_entry["hit_count"] if final_entry else 0
            final_ts_list = list(final_entry["hit_timestamps"]) if final_entry else []
            backfill_n = len(backfill_reports) if is_new else 0
            window_n = max(0, final_cnt - backfill_n)
            return (
                f"FAIL_ON '{keyword}' (name='{key}', time={sync_duration:g}s): "
                f"{final_cnt} hits (backfill={backfill_n}, window={window_n})"
            )

        if is_new:
            return (f"Failing on keyword '{keyword}' (name='{key}')"
                    + (f" — backfill matched {len(backfill_reports)} lines" if backfill_reports else ""))

        def _fmt(t: float) -> str:
            return _time_mod.strftime("%H:%M:%S", _time_mod.localtime(t))

        if cnt == 0:
            return f"FAIL_ON '{kw}' (name='{key}'): 0 hits (since {_fmt(started_at)})"
        return f"FAIL_ON '{kw}' (name='{key}'): {cnt} hit lines | first: {_fmt(ts_list[0])} | last: {_fmt(ts_list[-1])}"

    # ------------------------------------------------------------------
    # 명령어 전송
    # ------------------------------------------------------------------

    def SendCommand(self, command: str, encoding: str = "utf-8", append_newline: bool = True) -> str:
        """시리얼 포트로 문자열 명령어를 전송합니다.

        Args:
            command: 전송할 명령어
            encoding: 인코딩 (기본 utf-8)
            append_newline: 개행 문자 자동 추가 (기본 True)

        Returns:
            결과 메시지
        """
        if not self._serial or not self._serial.is_open:
            return "ERROR: 시리얼 포트가 연결되어 있지 않습니다. StartLogging() 먼저 호출하세요."
        data = command
        if append_newline and not data.endswith("\n"):
            data += "\n"
        self._serial.write(data.encode(encoding))
        logger.info("[SerialLogging] SendCommand: %s", command.strip())
        return "OK"

    # ------------------------------------------------------------------
    # 상태 조회 (내부)
    # ------------------------------------------------------------------

    def _GetStatus(self) -> str:
        """현재 모듈 상태를 조회합니다.

        Returns:
            상태 문자열
        """
        connected = self._IsConnected()
        with self._lock:
            log_count = len(self._logs)
        saving = self._save_path or "N/A"

        parts = [
            f"Port: {self._port} @ {self._bps}",
            f"Connected: {connected}",
            f"Capturing: {self._capturing}",
            f"Logs: {log_count} (total: {self._line_counter})",
            f"Saving: {saving}",
        ]
        return " | ".join(parts)

    def _ClearLogs(self) -> str:
        """로그 버퍼를 초기화합니다.

        Returns:
            결과 메시지
        """
        with self._lock:
            self._logs.clear()
            self._log_capture_ts.clear()
        self._line_counter = 0
        return "Logs cleared"

    # ------------------------------------------------------------------
    # 로그 캡처 (백그라운드 스레드)
    # ------------------------------------------------------------------

    def _start_capture(self):
        if self._capturing:
            return
        self._capturing = True
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="SerialLogging-Capture", daemon=True
        )
        self._capture_thread.start()

    def _stop_capture(self):
        self._capturing = False
        if self._capture_thread:
            self._capture_thread.join(timeout=3)
            self._capture_thread = None

    def _capture_loop(self):
        """백그라운드 스레드: 시리얼 데이터를 줄 단위로 수신."""
        while self._capturing and self._serial and self._serial.is_open:
            try:
                raw = self._serial.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                cap_ts = time.time()
                ts = time.strftime("%H:%M:%S", time.localtime(cap_ts))
                stamped = f"[{ts}] {line}"

                with self._lock:
                    self._logs.append(stamped)
                    self._log_capture_ts.append(cap_ts)
                    self._line_counter += 1

                # 파일 저장 중이면 기록
                if self._save_file:
                    try:
                        self._save_file.write(stamped + "\n")
                        self._save_file.flush()
                    except Exception:
                        pass

                # fail_on_keyword 검사
                if self._fail_keywords:
                    now_ts = time.time()
                    # (keyword, line, parent_step_id, parent_repeat_index)
                    fail_reports: list[tuple[str, str, Optional[int], int]] = []
                    with self._fail_lock:
                        for f in self._fail_keywords.values():
                            if f["keyword"] in stamped:
                                f["hit_count"] += 1
                                f["hit_timestamps"].append(now_ts)
                                fail_reports.append((
                                    f["keyword"], stamped,
                                    f.get("parent_step_id"),
                                    f.get("parent_repeat_index", 1),
                                ))
                    # playback_service에 fail 보고 (재생 active일 때만 효과)
                    if fail_reports:
                        try:
                            from backend.app.services.playback_service import report_runtime_fail
                            for kw, ln, p_sid, p_rep in fail_reports:
                                report_runtime_fail(
                                    "SerialLogging", kw, now_ts, ln, reason="matched",
                                    repeat_index=p_rep,
                                    parent_step_id=p_sid,
                                )
                        except Exception:
                            pass

                # 뷰어용 실시간 스트림으로 emit
                try:
                    SERIAL_HUB.emit_log(self._session_id(), stamped)
                except Exception:
                    pass

            except Exception as e:
                if self._capturing:
                    logger.error("[SerialLogging] Capture error: %s", e)
                break

        self._capturing = False
        logger.info("[SerialLogging] Capture loop ended (logs=%d)", len(self._logs))
