"""SerialLogging — 시리얼 포트 로그 캡처·저장·키워드 합부 판정 모듈.

시나리오 스텝 내에서:
  - StartLogging / StopLogging 으로 시리얼 캡처 시작/종료
  - SendCommand 로 명령 전송
  - SendCommand_fail_on_keyword / SendCommand_pass_on_keyword 로
    명령 전송 + 응답 캡처를 한 호출로 묶어 합부 판정

사용 예 (시나리오 스텝):
  SerialLogging.StartLogging()                                          # 연결 + 캡처 시작
  SerialLogging.SendCommand_pass_on_keyword("ping", "OK", time=3)       # 응답 OK 검사
  SerialLogging.SendCommand_fail_on_keyword("self_test", "ERROR", 10)   # ERROR 검출 모니터링
  SerialLogging.StopLogging()                                           # 캡처 종료 + 파일 저장
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

    def IsConnected(self) -> bool:
        """포트가 실제로 열려 있는지 보고.

        module_service._is_connected가 이 메서드를 우선 호출하여 디바이스 status를
        결정한다. 따라서 정확한 포트 상태를 반환해야 보조 디바이스 '연결' 직후
        Send_Packet 등이 즉시 사용 가능한지 UI에 올바로 반영된다.
        """
        return bool(self._serial and getattr(self._serial, "is_open", False))

    def Connect(self) -> str:
        """모듈 표준 연결 인터페이스 — 보조 디바이스 '연결' 클릭 시 자동 호출됨.

        module_service._get_instance가 인자 없는 Connect()를 발견하면 인스턴스
        생성 직후 자동으로 호출한다. 포트 open + capture 스레드 시작 + SERIAL_HUB
        lifecycle emit까지 수행하므로 이후 Send_Packet/SendCommand가 즉시 동작하고
        뷰어도 자동 오픈된다.

        StartLogging과 동일한 효과지만 메시지/파라미터가 다르며, 둘 중 어느 쪽을
        호출해도 _connect()가 idempotent하므로 안전하다.
        """
        err = self._connect()
        if err:
            return err
        try:
            SERIAL_HUB.emit_lifecycle({
                "type": "session_started",
                "session_id": self._session_id(),
                "port": self._port,
                "bps": self._bps,
                "save_path": "",
                "started_at": time.time(),
                "scenario_playback": _is_scenario_playback(),
            })
        except Exception:
            pass
        return f"Connected: {self._port} @ {self._bps}"

    def Disconnect(self) -> str:
        """모듈 표준 연결 해제 인터페이스 — 보조 디바이스 '연결 해제' / cleanup 경로에서 자동 호출됨.

        capture 스레드 중단 + 시리얼 포트 close + SERIAL_HUB session_stopped emit.
        파일 저장은 하지 않으므로(StopLogging과의 차이) raw 송수신용으로만 사용한
        세션을 깔끔히 닫을 때 적합하다.
        """
        if not self._serial or not self._serial.is_open:
            return "Already disconnected"
        sid = self._session_id()
        self._disconnect()
        try:
            SERIAL_HUB.emit_lifecycle({
                "type": "session_stopped",
                "session_id": sid,
                "save_path": "",
                "stopped_at": time.time(),
            })
        except Exception:
            pass
        return f"Disconnected: {self._port}"

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

    def Send_Packet(self, data: str) -> str:
        """raw hex 바이트 패킷을 시리얼 포트로 전송합니다.

        공백으로 구분된 hex 토큰 문자열을 받아 각 토큰을 바이트로 변환 후 송신.
        토큰별 파싱이라 `"00 77 42"`, `"0x79 0x6D"`, `"7 6D F2"` 같이 자릿수가
        다양해도 처리됩니다. write 후 `flush()` 호출로 OS 출력 버퍼까지 비워
        실제 회선 도달을 보장합니다.

        Args:
            data: 공백 구분 hex 문자열 (예: "00 77 42 37 02 F2 00 FE 00 FE 00")

        Returns:
            "OK: Sent N bytes (HH HH HH ...)" 또는 "ERROR: ..."

        예:
            SerialLogging.Send_Packet("79 6D F2 0F")
            SerialLogging.Send_Packet("00 77 42 37 02 F2 00 FE 00 FE 00")
        """
        if not self._serial or not self._serial.is_open:
            return "ERROR: 시리얼 포트가 연결되어 있지 않습니다. StartLogging() 먼저 호출하세요."
        if not data or not data.strip():
            return "ERROR: data가 비어 있습니다"
        try:
            # 공백 분리 → 각 토큰 hex 정수 변환 (1자리/2자리/0x prefix 모두 허용)
            tokens = data.split()
            byte_list: list[int] = []
            for tok in tokens:
                val = int(tok, 16)
                if val < 0 or val > 0xFF:
                    return f"ERROR: hex 값이 1바이트 범위(0~0xFF)를 벗어남 — '{tok}' → {val}"
                byte_list.append(val)
            raw = bytes(byte_list)
        except ValueError as e:
            return f"ERROR: hex 파싱 실패 — {e}"

        try:
            self._serial.write(raw)
            self._serial.flush()  # OS 출력 버퍼 비워서 wire 도달 보장
        except Exception as e:
            return f"ERROR: 송신 실패 — {e}"

        hex_str = " ".join(f"{b:02X}" for b in raw)
        logger.info("[SerialLogging] Send_Packet (%d bytes): %s", len(raw), hex_str)
        return f"OK: Sent {len(raw)} bytes ({hex_str})"

    # ------------------------------------------------------------------
    # 명령어 전송 + 키워드 합부 판정 (응답 라인을 즉시 캐치)
    # ------------------------------------------------------------------

    def SendCommand_fail_on_keyword(self, command: str, keyword: str, time: float = 5,
                                      encoding: str = "utf-8",
                                      append_newline: bool = True) -> str:
        """명령어 전송 후 응답에 keyword가 **포함되면 FAIL 판정**.

        'ERROR'/'Fail'/'crash' 등 비정상 키워드 검출용. 명령 전송 직후 캡처되는
        라인을 'time' 초간 모니터링하여, keyword 매칭 라인이 발견되면 모두
        fail row로 누적되며 결과 표에 인라인(Fail_Count_N) 표시됨.

        동작:
          1) SendCommand로 명령 전송 (실패 시 즉시 ERROR 반환)
          2) 전송 직전의 로그 인덱스를 잡아 그 이후 라인만 검사 — 과거 로그 무시
          3) time 초간 새로 들어오는 라인을 폴링하며 keyword 검사
          4) 매칭된 모든 라인을 fail row로 누적 후 PASS/FAIL 메시지 반환

        Args:
            command: 전송할 명령어
            keyword: FAIL을 일으킬 검출 키워드 (substring match)
            time: 응답 모니터링 시간(초). 기본 5초
            encoding: 인코딩 (기본 utf-8)
            append_newline: 개행 문자 자동 추가 (기본 True)
        """
        import time as _time_mod
        if not self._serial or not self._serial.is_open:
            return "ERROR: 시리얼 포트가 연결되어 있지 않습니다. StartLogging() 먼저 호출하세요."
        if not keyword:
            return "ERROR: keyword가 비어 있습니다"

        # 응답 매칭 시작점 — 전송 직전의 로그 인덱스. capture_loop와의 race를 lock으로 차단
        with self._lock:
            start_idx = len(self._logs)

        data = command if (not append_newline or command.endswith("\n")) else command + "\n"
        try:
            self._serial.write(data.encode(encoding))
        except Exception as e:
            return f"ERROR: 명령 전송 실패 — {e}"
        logger.info("[SerialLogging] SendCommand_fail_on_keyword: cmd='%s' kw='%s' time=%.1fs",
                    command.strip(), keyword, float(time))

        # parent step 컨텍스트 (인라인 결과 표시용)
        parent_step_id: Optional[int] = None
        parent_repeat_index = 1
        try:
            from backend.app.services.playback_service import get_current_step_context
            parent_step_id, parent_repeat_index = get_current_step_context()
        except Exception:
            pass

        deadline = _time_mod.time() + float(time)
        hits: list[tuple[float, str]] = []
        check_idx = start_idx
        while _time_mod.time() < deadline:
            with self._lock:
                snapshot_logs = self._logs[check_idx:]
                snapshot_ts = self._log_capture_ts[check_idx:check_idx + len(snapshot_logs)]
            check_idx += len(snapshot_logs)
            for ln, ts in zip(snapshot_logs, snapshot_ts):
                if keyword in ln:
                    hits.append((ts, ln))
            _time_mod.sleep(0.1)

        # 마지막 한 번 더 확인 — deadline 직전 도착한 라인 누락 방지
        with self._lock:
            tail_logs = self._logs[check_idx:]
            tail_ts = self._log_capture_ts[check_idx:check_idx + len(tail_logs)]
        for ln, ts in zip(tail_logs, tail_ts):
            if keyword in ln:
                hits.append((ts, ln))

        if hits:
            try:
                from backend.app.services.playback_service import report_runtime_fail
                for ts_b, ln in hits:
                    report_runtime_fail(
                        "SerialLogging", keyword, ts_b, ln, reason="matched",
                        repeat_index=parent_repeat_index,
                        parent_step_id=parent_step_id,
                    )
            except Exception:
                pass
            first = hits[0][1].strip()[:120]
            return (f"FAIL: keyword '{keyword}' detected {len(hits)} time(s) "
                    f"after command — {first}")
        return f"PASS: keyword '{keyword}' not detected within {float(time):g}s after command"

    def SendCommand_pass_on_keyword(self, command: str, keyword: str, time: float = 5,
                                      encoding: str = "utf-8",
                                      append_newline: bool = True) -> str:
        """명령어 전송 후 응답에 keyword가 **포함되면 PASS 판정**.

        'OK'/'Pass'/'BootComplete' 등 정상 응답 키워드 검출용. 명령 전송 직후
        캡처되는 라인을 모니터링하여 keyword를 발견하면 즉시 PASS 반환.
        time 초 안에 발견되지 않으면 fail row 누적 후 FAIL 반환.

        동작:
          1) SendCommand로 명령 전송 (실패 시 즉시 ERROR 반환)
          2) 전송 직전의 로그 인덱스를 잡아 그 이후 라인만 검사
          3) 새 라인 폴링하며 keyword 검사 — 발견 즉시 PASS 반환 (조기 종료)
          4) 타임아웃이면 fail row 1건 누적 후 FAIL 반환

        Args:
            command: 전송할 명령어
            keyword: PASS를 만족할 키워드 (substring match)
            time: 응답 대기 시간(초). 기본 5초
            encoding: 인코딩 (기본 utf-8)
            append_newline: 개행 문자 자동 추가 (기본 True)
        """
        import time as _time_mod
        if not self._serial or not self._serial.is_open:
            return "ERROR: 시리얼 포트가 연결되어 있지 않습니다. StartLogging() 먼저 호출하세요."
        if not keyword:
            return "ERROR: keyword가 비어 있습니다"

        with self._lock:
            start_idx = len(self._logs)

        data = command if (not append_newline or command.endswith("\n")) else command + "\n"
        try:
            self._serial.write(data.encode(encoding))
        except Exception as e:
            return f"ERROR: 명령 전송 실패 — {e}"
        logger.info("[SerialLogging] SendCommand_pass_on_keyword: cmd='%s' kw='%s' time=%.1fs",
                    command.strip(), keyword, float(time))

        parent_step_id: Optional[int] = None
        parent_repeat_index = 1
        try:
            from backend.app.services.playback_service import get_current_step_context
            parent_step_id, parent_repeat_index = get_current_step_context()
        except Exception:
            pass

        deadline = _time_mod.time() + float(time)
        check_idx = start_idx
        while _time_mod.time() < deadline:
            with self._lock:
                snapshot_logs = self._logs[check_idx:]
                snapshot_ts = self._log_capture_ts[check_idx:check_idx + len(snapshot_logs)]
            check_idx += len(snapshot_logs)
            for ln, ts in zip(snapshot_logs, snapshot_ts):
                if keyword in ln:
                    summary = ln.strip()[:120]
                    return f"PASS: keyword '{keyword}' detected — {summary}"
            _time_mod.sleep(0.1)

        # 최종 확인
        with self._lock:
            tail_logs = self._logs[check_idx:]
            tail_ts = self._log_capture_ts[check_idx:check_idx + len(tail_logs)]
        for ln, ts in zip(tail_logs, tail_ts):
            if keyword in ln:
                summary = ln.strip()[:120]
                return f"PASS: keyword '{keyword}' detected — {summary}"

        # 타임아웃 — fail row 1건 보고
        fail_ts = _time_mod.time()
        fail_line = f"(timeout: '{keyword}' not found after command '{command.strip()}')"
        try:
            from backend.app.services.playback_service import report_runtime_fail
            report_runtime_fail(
                "SerialLogging", keyword, fail_ts, fail_line, reason="missing",
                repeat_index=parent_repeat_index,
                parent_step_id=parent_step_id,
            )
        except Exception:
            pass
        return f"FAIL: keyword '{keyword}' not detected within {float(time):g}s after command"

    # ------------------------------------------------------------------
    # 상태 조회 (내부)
    # ------------------------------------------------------------------

    def _GetStatus(self) -> str:
        """현재 모듈 상태를 조회합니다.

        Returns:
            상태 문자열
        """
        connected = self.IsConnected()
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
