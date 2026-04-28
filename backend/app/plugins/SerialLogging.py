"""SerialLogging — 시리얼 포트 로그 캡처·저장·키워드 판정 모듈.

시나리오 스텝 내에서:
  - 시리얼 포트에 연결하여 실시간 로그 수신
  - 로그를 파일로 저장 (시작/중단)
  - 키워드 검색으로 PASS/FAIL 판정
  - 스텝 인덱스 구간 지정 검색

사용 예 (시나리오 스텝):
  SerialLogging.StartSave("C:/logs/serial.log")  # 연결 + 캡처 + 파일 저장 시작
  SerialLogging.MarkStep(1)                      # 스텝 1 경계 표시
  ... (다른 스텝들) ...
  SerialLogging.MarkStep(5)                      # 스텝 5 경계 표시
  SerialLogging.SearchAll("BootComplete")        # 전체 로그에서 키워드 판정
  SerialLogging.SearchRange("ERROR", 1, 5)       # 스텝 1~5 구간 키워드 판정
  SerialLogging.StopSave()                       # 파일 저장 중단 + 연결 해제
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

        # 스텝 마킹: {step_index: log_buffer_index}
        self._step_marks: dict[int, int] = {}

        # 키워드 실시간 카운터: {name: {"keyword", "count", "timestamps", "started_at"}}
        # capture_loop에서 라인마다 keyword in line 검사 → match 시 count+1, timestamps에 time.time() 추가
        self._counters: dict[str, dict] = {}
        self._counter_lock = threading.Lock()

        # 키워드 단언(assert): 모든 라인이 keyword를 포함해야 함. 미포함 라인이 들어오면
        # 시나리오 재생 중일 때 한해 playback_service.report_runtime_fail로 보고되어
        # 결과의 step_results에 fail row로 누적됨. {name: {"keyword","miss_count","miss_timestamps","started_at"}}
        self._asserts: dict[str, dict] = {}

        # fail_on_keyword: assert_keyword의 반대 — keyword가 라인에 **포함되면** fail로 보고.
        # 직관적: 'ERROR'/'Fail' 같은 비정상 단어 검출에 사용.
        self._fail_keywords: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # 연결 관리 (내부)
    # ------------------------------------------------------------------

    def _connect(self) -> str:
        """시리얼 포트 연결."""
        if not self._port:
            return "ERROR: port가 설정되지 않았습니다"
        if self._serial and self._serial.is_open:
            return ""  # 이미 연결됨 — 정상

        try:
            import serial as pyserial
            self._serial = pyserial.Serial(self._port, self._bps, timeout=1)
            self._logs.clear()
            self._log_capture_ts.clear()
            self._line_counter = 0
            self._step_marks.clear()
            with self._counter_lock:
                self._counters.clear()  # 새 세션마다 키워드 카운터 자동 리셋
                self._asserts.clear()    # assert 카운터도 함께 리셋
                self._fail_keywords.clear()
            self._start_capture()
            logger.info("[SerialLogging] Connected to %s @ %d", self._port, self._bps)
            return ""
        except Exception as e:
            self._serial = None
            logger.error("[SerialLogging] Connection failed: %s", e)
            return f"ERROR: 연결 실패 — {e}"

    def _disconnect(self):
        """시리얼 포트 연결 해제."""
        self._stop_capture()
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        logger.info("[SerialLogging] Disconnected")

    def IsConnected(self) -> bool:
        """연결 상태 확인. StartSave 전에도 모듈은 사용 가능 (지연 연결)."""
        return True

    def _session_id(self) -> str:
        return f"{self._port}@{self._bps}"

    # ------------------------------------------------------------------
    # 뷰어 연동: StartLogging / StopLogging (DLTLogging과 동일 시그니처)
    # ------------------------------------------------------------------

    def StartLogging(self) -> str:
        """뷰어 연동용: 시리얼 연결 + 로그 캡처 시작 (메모리만, 파일 저장 없음).

        SERIAL_HUB에 session_started 이벤트를 emit하여 뷰어가 자동 오픈된다.
        """
        err = self._connect()
        if err:
            return err
        SERIAL_HUB.emit_lifecycle({
            "type": "session_started",
            "session_id": self._session_id(),
            "port": self._port,
            "bps": self._bps,
            "save_path": "",
            "started_at": time.time(),
        })
        return f"Logging started: {self._port} @ {self._bps}"

    def StopLogging(self, save_path: str = "") -> str:
        """뷰어 연동용: 시리얼 연결 종료 + 메모리 버퍼를 파일로 일괄 저장.

        Args:
            save_path: 저장할 파일 경로. 빈 값이면 컨텍스트별 자동 저장:
                - 재생 중: {run_dir}/logs/serial_{timestamp}.log
                - 스텝 테스트: backend/results/Temp_logs/serial_{timestamp}.log
        """
        sid = self._session_id()
        with self._lock:
            logs_snapshot = list(self._logs)

        if not save_path:
            save_path = _auto_save_path("serial")
        elif not os.path.dirname(save_path):
            base_dir = Path(_auto_save_path("serial")).parent
            save_path = str(base_dir / save_path)

        saved_path = ""
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
            self._disconnect()
            SERIAL_HUB.emit_lifecycle({
                "type": "session_stopped",
                "session_id": sid,
                "save_path": "",
                "stopped_at": time.time(),
            })
            return f"ERROR: 저장 실패 — {e}"

        self._disconnect()
        SERIAL_HUB.emit_lifecycle({
            "type": "session_stopped",
            "session_id": sid,
            "save_path": saved_path,
            "stopped_at": time.time(),
        })
        return f"Logging stopped. Saved {len(logs_snapshot)} lines to: {saved_path}"

    # ------------------------------------------------------------------
    # 뷰어용 조회 (DLT와 동일 인터페이스)
    # ------------------------------------------------------------------

    def GetRecentLogs(self, limit: int = 1000) -> list[str]:
        with self._lock:
            return list(self._logs[-int(limit):]) if self._logs else []

    def GetStepMarks(self) -> dict[int, int]:
        return dict(self._step_marks)

    def SearchAllDetailed(self, keyword: str, max_results: int = 500) -> list[str]:
        keywords = keyword.split()
        with self._lock:
            logs = list(self._logs)
        out: list[str] = []
        for line in logs:
            if all(k in line for k in keywords):
                out.append(line)
                if len(out) >= int(max_results):
                    break
        return out

    def SearchSectionDetailed(self, keyword: str, from_step: int,
                               to_step: int, max_results: int = 500) -> list[str]:
        from_step = int(from_step)
        to_step = int(to_step)
        if from_step not in self._step_marks:
            return []
        start_idx = self._step_marks[from_step]
        if to_step in self._step_marks:
            end_idx = self._step_marks[to_step]
        else:
            with self._lock:
                end_idx = len(self._logs)
        with self._lock:
            logs_slice = self._logs[start_idx:end_idx]
        keywords = keyword.split()
        out: list[str] = []
        for line in logs_slice:
            if all(k in line for k in keywords):
                out.append(line)
                if len(out) >= int(max_results):
                    break
        return out

    # ------------------------------------------------------------------
    # 로그 저장 시작/중단 (연결 포함)
    # ------------------------------------------------------------------

    def StartSave(self, save_path: str = "") -> str:
        """시리얼 포트에 연결하고 로그 캡처 + 파일 저장을 시작합니다.

        Args:
            save_path: 저장 파일 경로. 빈 값이면 자동 생성 (backend/logs/serial_YYYYMMDD_HHMMSS.log)

        Returns:
            결과 메시지
        """
        if self._save_file:
            return f"ERROR: 이미 저장 중입니다 ({self._save_path}). StopSave() 먼저 호출하세요."

        # 연결이 안 되어 있으면 자동 연결
        err = self._connect()
        if err:
            return err

        if not save_path:
            # 재생 중: run_dir/logs, 스텝 테스트: backend/results/Temp_logs
            save_path = _auto_save_path("serial")
        else:
            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        try:
            self._save_file = open(save_path, "w", encoding="utf-8")
            self._save_path = save_path
            logger.info("[SerialLogging] Save started: %s", save_path)
            return f"Save started: {save_path}"
        except Exception as e:
            return f"ERROR: 파일 열기 실패 — {e}"

    def StopSave(self) -> str:
        """로그 파일 저장을 중단하고 시리얼 연결을 해제합니다.

        Returns:
            저장된 파일 경로
        """
        if not self._save_file:
            return "저장 중이 아닙니다."

        path = self._save_path
        self._close_save_file()
        self._disconnect()
        logger.info("[SerialLogging] Save stopped + disconnected: %s", path)
        return f"Save stopped: {path}"

    def _close_save_file(self):
        if self._save_file:
            try:
                self._save_file.close()
            except Exception:
                pass
            self._save_file = None
            self._save_path = None

    # ------------------------------------------------------------------
    # 스텝 마킹
    # ------------------------------------------------------------------

    def MarkStep(self, step_index: int) -> str:
        """현재 로그 버퍼 위치에 스텝 경계를 표시합니다.

        SearchRange에서 구간 검색할 때 사용됩니다.

        Args:
            step_index: 스텝 인덱스 번호

        Returns:
            결과 메시지
        """
        with self._lock:
            pos = len(self._logs)
        self._step_marks[int(step_index)] = pos
        logger.info("[SerialLogging] MarkStep %d at log index %d", step_index, pos)
        return f"Step {step_index} marked at log index {pos}"

    # ------------------------------------------------------------------
    # 실시간 키워드 카운터 — 호출 시점부터 활성, 매 라인 검사
    # ------------------------------------------------------------------

    def count_keyword(self, keyword: str, name: str = "") -> str:
        """캡처 로그에서 keyword가 등장할 때마다 카운트하고 발생 시간을 누적합니다.

        호출 동작:
          - 첫 호출(또는 같은 name으로 처음): 카운터 시작 (count=0, timestamps=[]). "Started counting ..." 반환.
          - 같은 name 재호출: 현재까지의 결과 반환. "COUNT 'kw': N occurrences | first: HH:MM:SS | last: HH:MM:SS".

        Args:
            keyword: 검색할 부분 문자열 (substring match, case-sensitive)
            name: 카운터 식별자. 빈 값이면 keyword 자체를 사용.
                  같은 keyword라도 name을 다르게 주면 별도 카운터로 동작.

        Returns:
            상태 메시지 (시나리오 expected/match_mode로 PASS/FAIL 판정 가능한 문자열)
        """
        key = name.strip() if name else keyword
        with self._counter_lock:
            existing = self._counters.get(key)
            if existing is None:
                self._counters[key] = {
                    "keyword": keyword,
                    "count": 0,
                    "timestamps": [],
                    "started_at": time.time(),
                }
                logger.info("[SerialLogging] count_keyword started: name='%s' keyword='%s'", key, keyword)
                return f"Started counting '{keyword}' (name='{key}')"
            # 기존 카운터 — 결과 스냅샷
            cnt = existing["count"]
            ts_list = list(existing["timestamps"])
            started_at = existing["started_at"]
            kw = existing["keyword"]

        def _fmt(t: float) -> str:
            return time.strftime("%H:%M:%S", time.localtime(t))

        if cnt == 0:
            return f"COUNT '{kw}' (name='{key}'): 0 occurrences (since {_fmt(started_at)})"
        first_s = _fmt(ts_list[0])
        last_s = _fmt(ts_list[-1])
        logger.info("[SerialLogging] count_keyword query: name='%s' count=%d", key, cnt)
        return f"COUNT '{kw}' (name='{key}'): {cnt} occurrences | first: {first_s} | last: {last_s}"

    def reset_count_keyword(self, name: str = "") -> str:
        """키워드 카운터를 리셋합니다.

        Args:
            name: 카운터 식별자. 빈 값이면 모든 카운터 제거.

        Returns:
            결과 메시지
        """
        with self._counter_lock:
            if not name:
                n = len(self._counters)
                self._counters.clear()
                return f"Reset all counters ({n})"
            existing = self._counters.get(name)
            if existing is None:
                return f"Counter '{name}' not found"
            existing["count"] = 0
            existing["timestamps"].clear()
            existing["started_at"] = time.time()
            return f"Reset counter '{name}'"

    def get_count_details(self, name: str = "") -> dict:
        """raw 카운터 dict 반환 (프로그램적 검증용).

        name 빈 값이면 모든 카운터 dict, 지정하면 해당 카운터 dict.
        timestamps는 epoch float 그대로 — 호출자가 포맷 결정.
        """
        with self._counter_lock:
            if not name:
                return {k: {**v, "timestamps": list(v["timestamps"])} for k, v in self._counters.items()}
            v = self._counters.get(name)
            if v is None:
                return {}
            return {**v, "timestamps": list(v["timestamps"])}

    # ------------------------------------------------------------------
    # 키워드 단언(assert) — 일치하지 않는 라인을 시나리오 fail로 누적 보고
    # ------------------------------------------------------------------

    def assert_keyword(self, keyword: str, name: str = "") -> str:
        """캡처되는 모든 라인이 keyword를 포함해야 함을 단언.

        - keyword를 **포함하지 않는** 라인이 들어오면, 시나리오 재생 중일 때 한해
          ScenarioResult.step_results에 fail step row가 자동 추가됩니다.
        - 결과 페이지에서 그 row를 클릭하면 매칭 시점의 영상으로 점프 가능.
        - 시나리오 재생이 아닌 단발 스텝 테스트에선 보고 안 됨 (테스트 환경 보호).

        호출 흐름:
          - 첫 호출(또는 같은 name으로 처음): 단언 시작
          - 같은 name 재호출: 현재까지의 miss count + first/last timestamp 반환
        """
        key = name.strip() if name else f"assert_{keyword}"
        with self._counter_lock:
            existing = self._asserts.get(key)
            if existing is None:
                self._asserts[key] = {
                    "keyword": keyword,
                    "miss_count": 0,
                    "miss_timestamps": [],
                    "started_at": time.time(),
                }
                logger.info("[SerialLogging] assert_keyword started: name='%s' keyword='%s'", key, keyword)
                return f"Asserting all lines contain '{keyword}' (name='{key}')"
            cnt = existing["miss_count"]
            ts_list = list(existing["miss_timestamps"])
            started_at = existing["started_at"]
            kw = existing["keyword"]

        def _fmt(t: float) -> str:
            return time.strftime("%H:%M:%S", time.localtime(t))

        if cnt == 0:
            return f"ASSERT '{kw}' (name='{key}'): 0 misses (since {_fmt(started_at)})"
        return f"ASSERT '{kw}' (name='{key}'): {cnt} miss lines | first: {_fmt(ts_list[0])} | last: {_fmt(ts_list[-1])}"

    def reset_assert_keyword(self, name: str = "") -> str:
        """assert 카운터 리셋. name 빈 값이면 모든 단언 제거."""
        with self._counter_lock:
            if not name:
                n = len(self._asserts)
                self._asserts.clear()
                return f"Reset all assertions ({n})"
            existing = self._asserts.get(name)
            if existing is None:
                return f"Assertion '{name}' not found"
            existing["miss_count"] = 0
            existing["miss_timestamps"].clear()
            existing["started_at"] = time.time()
            return f"Reset assertion '{name}'"

    # ------------------------------------------------------------------
    # 키워드 검출 모드 — 키워드가 들어오면 fail로 보고 (assert_keyword의 반대)
    # ------------------------------------------------------------------

    def fail_on_keyword(self, keyword: str, name: str = "") -> str:
        """캡처되는 라인에 keyword가 **포함되면** 시나리오 결과에 fail row 자동 누적.

        직관적 사용 — 'ERROR'/'Fail'/'crash' 등 비정상 단어 검출용.
        시나리오 재생 중일 때만 fail 보고됨. 결과 페이지에서 row 클릭 시 영상 점프 가능.

        **첫 호출 시 backfill**: 호출 이전에 이미 캡처된 로그 라인도 함께 스캔하여
        keyword 매칭 라인의 정확한 capture timestamp로 fail row 추가. 시나리오의
        StartLogging부터 fail_on_keyword 호출 사이에 발생한 매칭이 누락되지 않음.

        호출 흐름:
          - 첫 호출: 검출 시작 + 기존 로그 backfill 스캔
          - 같은 name 재호출: 현재까지 hit count + first/last timestamp 반환
        """
        key = name.strip() if name else f"fail_{keyword}"
        backfill_reports: list[tuple[float, str]] = []  # 첫 호출 backfill용
        is_new = False
        with self._counter_lock:
            existing = self._fail_keywords.get(key)
            if existing is None:
                is_new = True
                new_entry = {
                    "keyword": keyword,
                    "hit_count": 0,
                    "hit_timestamps": [],
                    "started_at": time.time(),
                }
                self._fail_keywords[key] = new_entry
                # backfill: 이미 캡처된 라인 중 keyword 매칭한 것 모두 보고
                with self._lock:
                    logs_snapshot = list(self._logs)
                    ts_snapshot = list(self._log_capture_ts)
                for i, ln in enumerate(logs_snapshot):
                    if keyword in ln:
                        ts_b = ts_snapshot[i] if i < len(ts_snapshot) else time.time()
                        new_entry["hit_count"] += 1
                        new_entry["hit_timestamps"].append(ts_b)
                        backfill_reports.append((ts_b, ln))
                logger.info("[SerialLogging] fail_on_keyword started: name='%s' keyword='%s' backfill=%d",
                            key, keyword, len(backfill_reports))
            else:
                cnt = existing["hit_count"]
                ts_list = list(existing["hit_timestamps"])
                started_at = existing["started_at"]
                kw = existing["keyword"]

        # backfill 항목을 playback_service에 보고 (lock 밖에서)
        if is_new and backfill_reports:
            try:
                from backend.app.services.playback_service import report_runtime_fail
                for ts_b, ln in backfill_reports:
                    report_runtime_fail("SerialLogging", keyword, ts_b, ln, reason="matched")
            except Exception:
                pass

        if is_new:
            return (f"Failing on keyword '{keyword}' (name='{key}')"
                    + (f" — backfill matched {len(backfill_reports)} lines" if backfill_reports else ""))

        def _fmt(t: float) -> str:
            return time.strftime("%H:%M:%S", time.localtime(t))

        if cnt == 0:
            return f"FAIL_ON '{kw}' (name='{key}'): 0 hits (since {_fmt(started_at)})"
        return f"FAIL_ON '{kw}' (name='{key}'): {cnt} hit lines | first: {_fmt(ts_list[0])} | last: {_fmt(ts_list[-1])}"

    def reset_fail_on_keyword(self, name: str = "") -> str:
        """fail_on_keyword 검출 리셋. name 빈 값이면 모두 제거."""
        with self._counter_lock:
            if not name:
                n = len(self._fail_keywords)
                self._fail_keywords.clear()
                return f"Reset all fail-on detectors ({n})"
            existing = self._fail_keywords.get(name)
            if existing is None:
                return f"Detector '{name}' not found"
            existing["hit_count"] = 0
            existing["hit_timestamps"].clear()
            existing["started_at"] = time.time()
            return f"Reset detector '{name}'"

    # ------------------------------------------------------------------
    # 키워드 검색 — PASS/FAIL 판정
    # ------------------------------------------------------------------

    def SearchAll(self, keyword: str, count: int = 5) -> str:
        """전체 로그에서 키워드를 검색하여 PASS/FAIL 판정합니다.

        처음부터 현재까지 캡처된 모든 로그를 대상으로 검색합니다.

        Args:
            keyword: 검색 키워드 (공백 구분 시 AND 조건)
            count: 최대 매칭 결과 수 (기본 5)

        Returns:
            "PASS: N건 발견 — (첫 매칭 로그)" 또는 "FAIL: keyword not found"
        """
        keywords = keyword.split()
        with self._lock:
            logs = list(self._logs)

        matches = []
        for line in logs:
            if all(k in line for k in keywords):
                matches.append(line.strip())
                if len(matches) >= int(count):
                    break

        if matches:
            summary = matches[0][:120]
            logger.info("[SerialLogging] SearchAll PASS: '%s' → %d건", keyword, len(matches))
            return f"PASS: {len(matches)}건 발견 — {summary}"
        else:
            logger.info("[SerialLogging] SearchAll FAIL: '%s'", keyword)
            return f"FAIL: keyword '{keyword}' not found"

    def SearchRange(self, keyword: str, from_step: int, to_step: int, count: int = 5) -> str:
        """스텝 구간 내 로그에서 키워드를 검색하여 PASS/FAIL 판정합니다.

        MarkStep으로 표시된 구간만 대상으로 검색합니다.

        Args:
            keyword: 검색 키워드 (공백 구분 시 AND 조건)
            from_step: 시작 스텝 인덱스 (이 스텝 이후 로그부터)
            to_step: 종료 스텝 인덱스 (이 스텝까지의 로그)
            count: 최대 매칭 결과 수 (기본 5)

        Returns:
            "PASS: N건 발견 — (첫 매칭 로그)" 또는 "FAIL: keyword not found in step range"
        """
        from_step = int(from_step)
        to_step = int(to_step)

        if from_step not in self._step_marks:
            return f"ERROR: step {from_step}이 마킹되지 않았습니다. MarkStep({from_step})을 먼저 호출하세요."
        if to_step not in self._step_marks:
            with self._lock:
                end_idx = len(self._logs)
        else:
            end_idx = self._step_marks[to_step]

        start_idx = self._step_marks[from_step]
        keywords = keyword.split()

        with self._lock:
            logs_slice = self._logs[start_idx:end_idx]

        matches = []
        for line in logs_slice:
            if all(k in line for k in keywords):
                matches.append(line.strip())
                if len(matches) >= int(count):
                    break

        if matches:
            summary = matches[0][:120]
            logger.info("[SerialLogging] SearchRange PASS: '%s' step %d~%d → %d건",
                        keyword, from_step, to_step, len(matches))
            return f"PASS: {len(matches)}건 발견 (step {from_step}~{to_step}) — {summary}"
        else:
            logger.info("[SerialLogging] SearchRange FAIL: '%s' step %d~%d", keyword, from_step, to_step)
            return f"FAIL: keyword '{keyword}' not found in step {from_step}~{to_step}"

    def WaitLog(self, keyword: str, timeout: int = 30) -> str:
        """키워드가 포함된 로그가 나타날 때까지 대기합니다 (블로킹).

        Args:
            keyword: 검색 키워드 (공백 구분 시 AND 조건)
            timeout: 최대 대기 시간 (초, 기본 30)

        Returns:
            "PASS: (매칭된 로그)" 또는 "FAIL: keyword not found within {timeout}s"
        """
        if not self._capturing:
            return "ERROR: 캡처가 실행 중이 아닙니다. StartSave() 먼저 호출하세요."

        keywords = keyword.split()
        timeout_sec = float(timeout)
        start = time.time()
        check_idx = 0

        while time.time() - start < timeout_sec:
            with self._lock:
                logs = list(self._logs)

            for i in range(check_idx, len(logs)):
                if all(k in logs[i] for k in keywords):
                    line = logs[i].strip()
                    logger.info("[SerialLogging] WaitLog PASS: %s", line)
                    return f"PASS: {line}"

            check_idx = len(logs)
            time.sleep(0.3)

        logger.info("[SerialLogging] WaitLog FAIL: '%s' not found in %ds", keyword, timeout_sec)
        return f"FAIL: keyword '{keyword}' not found within {int(timeout_sec)}s"

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
            return "ERROR: 시리얼 포트가 연결되어 있지 않습니다. StartSave() 먼저 호출하세요."
        data = command
        if append_newline and not data.endswith("\n"):
            data += "\n"
        self._serial.write(data.encode(encoding))
        logger.info("[SerialLogging] SendCommand: %s", command.strip())
        return "OK"

    def SendHex(self, hex_string: str) -> str:
        """시리얼 포트로 HEX 바이트를 전송합니다.

        Args:
            hex_string: 전송할 HEX 문자열 (예: 'FF 01 A0')

        Returns:
            결과 메시지
        """
        if not self._serial or not self._serial.is_open:
            return "ERROR: 시리얼 포트가 연결되어 있지 않습니다. StartSave() 먼저 호출하세요."
        raw = bytes.fromhex(hex_string.replace(" ", ""))
        self._serial.write(raw)
        logger.info("[SerialLogging] SendHex: %d bytes", len(raw))
        return f"Sent {len(raw)} bytes"

    def SendPacket(self, data: str) -> str:
        """공백으로 구분된 hex 토큰을 패킷 단위로 전송합니다.

        SendHex가 'FF01A0'/'FF 01 A0' 모두 받는 반면, SendPacket은 토큰 단위로
        ``int(x, 16)``으로 파싱하여 '0x'/'0X' 접두사가 섞여도 허용합니다.
        write 후 flush까지 수행하므로 짧은 컨트롤 패킷의 즉시 송출에 적합합니다.

        Args:
            data: 공백 구분 hex 문자열. 예) "01 0A FF", "0x01 0x0A 0xFF"

        Returns:
            "OK: sent N bytes — 0x1 0xa 0xff" 또는 ERROR 메시지
        """
        if not self._serial or not self._serial.is_open:
            return "ERROR: 시리얼 포트가 연결되어 있지 않습니다. StartSave() 먼저 호출하세요."

        tokens = (data or "").split()
        if not tokens:
            return "ERROR: 빈 패킷입니다"

        try:
            byte_list = [int(x, 16) for x in tokens]
        except ValueError as e:
            return f"ERROR: hex 파싱 실패 — {e}"

        for b in byte_list:
            if not 0 <= b <= 0xFF:
                return f"ERROR: 바이트 범위 초과 — {hex(b)}"

        packet = bytes(byte_list)
        try:
            self._serial.write(packet)
            self._serial.flush()
        except Exception as e:
            return f"ERROR: 전송 실패 — {e}"

        hex_repr = " ".join(hex(b) for b in byte_list)
        logger.info("[SerialLogging] SendPacket send : %s", hex_repr)
        return f"OK: sent {len(packet)} bytes — {hex_repr}"

    def SendAndWait(self, command: str, keyword: str, timeout: int = 10) -> str:
        """명령어를 전송하고 키워드가 포함된 응답을 대기합니다 (블로킹).

        Args:
            command: 전송할 명령어
            keyword: 응답에서 검색할 키워드 (공백 구분 시 AND 조건)
            timeout: 최대 대기 시간 (초, 기본 10)

        Returns:
            "PASS: (매칭된 응답)" 또는 "FAIL: keyword not found within {timeout}s"
        """
        send_result = self.SendCommand(command)
        if send_result != "OK":
            return send_result
        return self.WaitLog(keyword, timeout)

    # ------------------------------------------------------------------
    # 상태 조회
    # ------------------------------------------------------------------

    def GetStatus(self) -> str:
        """현재 모듈 상태를 조회합니다.

        Returns:
            상태 문자열
        """
        connected = self.IsConnected()
        with self._lock:
            log_count = len(self._logs)
        saving = self._save_path or "N/A"
        marks = ", ".join(f"{k}:{v}" for k, v in sorted(self._step_marks.items()))

        parts = [
            f"Port: {self._port} @ {self._bps}",
            f"Connected: {connected}",
            f"Capturing: {self._capturing}",
            f"Logs: {log_count} (total: {self._line_counter})",
            f"Saving: {saving}",
            f"StepMarks: {marks or 'none'}",
        ]
        return " | ".join(parts)

    def ClearLogs(self) -> str:
        """로그 버퍼와 스텝 마킹을 초기화합니다.

        Returns:
            결과 메시지
        """
        with self._lock:
            self._logs.clear()
        self._line_counter = 0
        self._step_marks.clear()
        return "Logs and step marks cleared"

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

                # 키워드 카운터/단언/검출 검사
                if self._counters or self._asserts or self._fail_keywords:
                    now_ts = time.time()
                    fail_reports: list[tuple[str, str, str]] = []  # (keyword, line, reason) — fail 보고
                    with self._counter_lock:
                        for c in self._counters.values():
                            if c["keyword"] in stamped:
                                c["count"] += 1
                                c["timestamps"].append(now_ts)
                        # assert_keyword: 미포함 라인 → fail
                        for a in self._asserts.values():
                            if a["keyword"] not in stamped:
                                a["miss_count"] += 1
                                a["miss_timestamps"].append(now_ts)
                                fail_reports.append((a["keyword"], stamped, "missing"))
                        # fail_on_keyword: 포함 라인 → fail
                        for f in self._fail_keywords.values():
                            if f["keyword"] in stamped:
                                f["hit_count"] += 1
                                f["hit_timestamps"].append(now_ts)
                                fail_reports.append((f["keyword"], stamped, "matched"))
                    # playback_service에 fail 보고 (재생 active일 때만 효과)
                    if fail_reports:
                        try:
                            from backend.app.services.playback_service import report_runtime_fail
                            for kw, ln, reason in fail_reports:
                                report_runtime_fail("SerialLogging", kw, now_ts, ln, reason=reason)
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
