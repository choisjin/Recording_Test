"""DLTViewer — DLT (Diagnostic Log and Trace) 로그 수신 및 분석 플러그인.

DltViewerSDK를 활용하여:
  - DLT 데몬에 TCP 연결로 실시간 로그 수신 (AUTOSAR DLT 프로토콜)
  - 키워드 기반 로그 모니터링/검증 (WaitLog / StartMonitor)
  - DLT Viewer GUI 실행 관리 (LaunchViewer / CloseViewer)

사용 예:
  module_command → DLTViewer.Connect()           # DLT 데몬 연결
  module_command → DLTViewer.WaitLog("keyword")  # 로그 키워드 대기 (블로킹)
  module_command → DLTViewer.StartMonitor("kw")  # 백그라운드 모니터링 시작
  module_command → DLTViewer.GetMonitorResult()   # 모니터링 결과 확인
  module_command → DLTViewer.LaunchViewer()       # GUI 실행
"""

import logging
import socket
import struct
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# DLT Viewer SDK 경로 (프로젝트 루트 기준)
_SDK_DIR = Path(__file__).resolve().parent.parent.parent.parent / "DltViewerSDK_21.1.3_ver"

# DLT 프로토콜 상수
_MSG_TYPE = {0: "LOG", 1: "TRACE", 2: "NW", 3: "CTRL"}
_LOG_LEVEL = {0: "", 1: "FATAL", 2: "ERROR", 3: "WARN", 4: "INFO", 5: "DEBUG", 6: "VERBOSE"}
# TYLE → 바이트 수 매핑
_TYLE_BYTES = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16}


class DLTViewer:
    """DLT 로그 수신 및 분석 모듈.

    생성자:
        host: DLT 데몬 IP 주소
        port: DLT 데몬 TCP 포트 (기본 3490)
    """

    def __init__(self, host: str = "", port: int = 3490):
        self._host = host
        self._port = int(port)
        self._socket: Optional[socket.socket] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._capturing = False
        self._logs: deque[str] = deque(maxlen=10000)
        self._lock = threading.Lock()
        self._viewer_proc: Optional[subprocess.Popen] = None
        self._sdk_dir = _SDK_DIR
        self._recv_buffer = bytearray()
        self._msg_counter = 0
        # 백그라운드 모니터링
        self._monitors: dict[str, dict] = {}  # id → {keyword, start, timeout, result, thread}
        self._monitor_counter = 0

    # ------------------------------------------------------------------
    # 연결 관리
    # ------------------------------------------------------------------

    def Connect(self) -> str:
        """DLT 데몬에 TCP 연결 후 자동 캡처 시작.

        Returns:
            연결 결과 메시지
        """
        if not self._host:
            return "ERROR: host가 설정되지 않았습니다"
        if self._socket:
            return f"이미 연결됨: {self._host}:{self._port}"

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((self._host, self._port))
            sock.settimeout(1)  # recv timeout — 캡처 루프에서 사용
            self._socket = sock
            self._recv_buffer.clear()
            self._start_capture()
            logger.info("[DLT] Connected to %s:%d", self._host, self._port)
            return f"Connected to {self._host}:{self._port}"
        except Exception as e:
            self._socket = None
            logger.error("[DLT] Connection failed: %s", e)
            return f"ERROR: {e}"

    def Disconnect(self) -> str:
        """DLT 데몬 연결 해제 및 캡처 중지.

        Returns:
            결과 메시지
        """
        self._stop_capture()
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        logger.info("[DLT] Disconnected")
        return "Disconnected"

    def IsConnected(self) -> bool:
        """연결 상태 확인."""
        return self._socket is not None

    # ------------------------------------------------------------------
    # 로그 캡처
    # ------------------------------------------------------------------

    def _start_capture(self):
        if self._capturing:
            return
        self._capturing = True
        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="DLT-Capture", daemon=True
        )
        self._capture_thread.start()

    def _stop_capture(self):
        self._capturing = False
        if self._capture_thread:
            self._capture_thread.join(timeout=3)
            self._capture_thread = None

    def _capture_loop(self):
        """백그라운드 스레드: DLT 메시지를 수신하고 파싱."""
        while self._capturing and self._socket:
            try:
                data = self._socket.recv(65536)
                if not data:
                    logger.warning("[DLT] Connection closed by remote")
                    break
                self._recv_buffer.extend(data)
                self._process_buffer()
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception as e:
                logger.error("[DLT] Capture error: %s", e)
                break

        self._capturing = False
        logger.info("[DLT] Capture loop ended (logs=%d)", len(self._logs))

    def _process_buffer(self):
        """수신 버퍼에서 완전한 DLT 메시지를 파싱."""
        while len(self._recv_buffer) >= 4:
            # DLT Standard Header: HTYP(1) MCNT(1) LEN(2 big-endian)
            htyp = self._recv_buffer[0]
            version = (htyp >> 5) & 0x07

            # 버전 검증 (DLT v1)
            if version != 1:
                # 유효하지 않은 헤더 — 1바이트 건너뛰고 동기화 시도
                del self._recv_buffer[0]
                continue

            msg_len = struct.unpack(">H", self._recv_buffer[2:4])[0]
            if msg_len < 4 or msg_len > 65535:
                del self._recv_buffer[0]
                continue

            if len(self._recv_buffer) < msg_len:
                break  # 불완전한 메시지 — 추가 데이터 대기

            msg_data = bytes(self._recv_buffer[:msg_len])
            del self._recv_buffer[:msg_len]

            line = self._parse_message(msg_data)
            if line:
                with self._lock:
                    self._logs.append(line)
                    self._msg_counter += 1

    # ------------------------------------------------------------------
    # DLT 메시지 파싱
    # ------------------------------------------------------------------

    def _parse_message(self, data: bytes) -> Optional[str]:
        """DLT 메시지 1개를 파싱하여 텍스트 한 줄로 변환."""
        if len(data) < 4:
            return None

        htyp = data[0]
        msg_len = struct.unpack(">H", data[2:4])[0]
        pos = 4

        # 옵션 필드 읽기
        ecu_id = ""
        timestamp = 0

        if htyp & 0x04:  # WEID — ECU ID 포함
            if pos + 4 > msg_len:
                return None
            ecu_id = data[pos : pos + 4].decode("ascii", errors="replace").rstrip("\x00")
            pos += 4

        if htyp & 0x08:  # WSID — Session ID
            pos += 4

        if htyp & 0x10:  # WTMS — Timestamp
            if pos + 4 <= msg_len:
                timestamp = struct.unpack(">I", data[pos : pos + 4])[0]
            pos += 4

        # Extended Header
        apid = ""
        ctid = ""
        msg_type_str = ""
        verbose = False
        noar = 0

        if htyp & 0x01:  # UEH — Extended Header 존재
            if pos + 10 > msg_len:
                return None
            msin = data[pos]
            noar = data[pos + 1]
            apid = data[pos + 2 : pos + 6].decode("ascii", errors="replace").rstrip("\x00")
            ctid = data[pos + 6 : pos + 10].decode("ascii", errors="replace").rstrip("\x00")
            pos += 10

            verbose = bool(msin & 0x01)
            mtype = (msin >> 1) & 0x07
            msub = (msin >> 4) & 0x0F

            mtype_name = _MSG_TYPE.get(mtype, str(mtype))
            if mtype == 0:  # LOG
                msub_name = _LOG_LEVEL.get(msub, str(msub))
            else:
                msub_name = str(msub)
            msg_type_str = f"{mtype_name} {msub_name}".strip()

        # Payload 추출
        payload_data = data[pos:msg_len]
        payload_text = ""

        if verbose and noar > 0 and len(payload_data) > 0:
            payload_text = self._parse_verbose_payload(payload_data, noar)
        elif len(payload_data) > 0:
            # Non-verbose: 읽을 수 있는 텍스트 추출
            payload_text = self._extract_printable(payload_data)

        if not payload_text.strip():
            return None

        # 타임스탬프 포맷 (0.1ms 단위 → 초)
        ts_sec = timestamp / 10000.0
        ts_str = f"{ts_sec:>12.4f}"

        return f"{ts_str} {ecu_id:<4s} {apid:<4s} {ctid:<4s} {msg_type_str:<12s} {payload_text}"

    def _parse_verbose_payload(self, data: bytes, noar: int) -> str:
        """Verbose 모드 DLT payload 인자들을 파싱."""
        parts = []
        pos = 0

        for _ in range(noar):
            if pos + 4 > len(data):
                break

            type_info = struct.unpack("<I", data[pos : pos + 4])[0]
            pos += 4

            tyle = type_info & 0x0F
            is_bool = bool(type_info & 0x10)
            is_sint = bool(type_info & 0x20)
            is_uint = bool(type_info & 0x40)
            is_float = bool(type_info & 0x80)
            is_string = bool(type_info & 0x200)
            is_raw = bool(type_info & 0x400)
            has_vari = bool(type_info & 0x800)

            # VARI — 변수 이름이 있으면 건너뛰기
            if has_vari:
                if pos + 2 > len(data):
                    break
                name_len = struct.unpack("<H", data[pos : pos + 2])[0]
                pos += 2
                if pos + name_len > len(data):
                    break
                pos += name_len  # 이름 건너뛰기 (표시 불필요)

            if is_string:
                if pos + 2 > len(data):
                    break
                str_len = struct.unpack("<H", data[pos : pos + 2])[0]
                pos += 2
                if pos + str_len > len(data):
                    break
                s = data[pos : pos + str_len].decode("utf-8", errors="replace").rstrip("\x00")
                parts.append(s)
                pos += str_len

            elif is_raw:
                if pos + 2 > len(data):
                    break
                raw_len = struct.unpack("<H", data[pos : pos + 2])[0]
                pos += 2
                if pos + raw_len > len(data):
                    break
                parts.append(data[pos : pos + raw_len].hex())
                pos += raw_len

            elif is_bool:
                byte_len = _TYLE_BYTES.get(tyle, 1)
                if pos + byte_len > len(data):
                    break
                parts.append(str(bool(data[pos])))
                pos += byte_len

            elif is_uint:
                byte_len = _TYLE_BYTES.get(tyle, 4)
                if pos + byte_len > len(data):
                    break
                val = int.from_bytes(data[pos : pos + byte_len], "little", signed=False)
                parts.append(str(val))
                pos += byte_len

            elif is_sint:
                byte_len = _TYLE_BYTES.get(tyle, 4)
                if pos + byte_len > len(data):
                    break
                val = int.from_bytes(data[pos : pos + byte_len], "little", signed=True)
                parts.append(str(val))
                pos += byte_len

            elif is_float:
                byte_len = 4 if tyle <= 3 else 8
                if pos + byte_len > len(data):
                    break
                if byte_len == 4:
                    val = struct.unpack("<f", data[pos : pos + byte_len])[0]
                else:
                    val = struct.unpack("<d", data[pos : pos + byte_len])[0]
                parts.append(f"{val:.6f}")
                pos += byte_len

            else:
                # 알 수 없는 타입 — 나머지 payload를 텍스트로 추출
                parts.append(self._extract_printable(data[pos:]))
                break

        return " ".join(parts)

    @staticmethod
    def _extract_printable(data: bytes) -> str:
        """바이트에서 출력 가능한 텍스트 추출."""
        text = data.decode("utf-8", errors="replace")
        return "".join(c if c.isprintable() or c in "\n\t " else "" for c in text).strip()

    # ------------------------------------------------------------------
    # 로그 조회/검색
    # ------------------------------------------------------------------

    def WaitLog(self, keyword: str, timeout: int = 30) -> str:
        """캡처된 로그에서 키워드가 나타날 때까지 대기 (블로킹).

        스텝 실행을 멈추고 키워드가 나올 때까지 기다립니다.
        비블로킹으로 사용하려면 StartMonitor를 사용하세요.

        Args:
            keyword: 검색할 키워드 (공백으로 구분 시 AND 조건)
            timeout: 최대 대기 시간 (초, 기본 30)

        Returns:
            "PASS: <매칭된 로그 줄>" 또는 "FAIL: keyword not found within {timeout}s"
        """
        if not self._capturing:
            return "ERROR: 캡처가 실행 중이 아닙니다. Connect() 먼저 호출하세요."

        keywords = keyword.split()
        timeout_sec = float(timeout)
        start = time.time()
        check_idx = 0

        while time.time() - start < timeout_sec:
            with self._lock:
                logs = list(self._logs)

            for i in range(check_idx, len(logs)):
                line = logs[i]
                if all(k in line for k in keywords):
                    logger.info("[DLT] WaitLog PASS: %s", line.strip())
                    return f"PASS: {line.strip()}"

            check_idx = len(logs)
            time.sleep(0.3)

        logger.info("[DLT] WaitLog FAIL: '%s' not found in %ds", keyword, timeout_sec)
        return f"FAIL: keyword '{keyword}' not found within {int(timeout_sec)}s"

    # ------------------------------------------------------------------
    # 백그라운드 모니터링
    # ------------------------------------------------------------------

    def StartMonitor(self, keyword: str, timeout: int = 60) -> str:
        """백그라운드 키워드 모니터링 시작 (비블로킹).

        다음 스텝으로 즉시 진행되며, 별도 스레드에서 키워드를 감시합니다.
        결과는 GetMonitorResult로 확인합니다.

        Args:
            keyword: 검색할 키워드 (공백으로 구분 시 AND 조건)
            timeout: 최대 감시 시간 (초, 기본 60)

        Returns:
            모니터 ID (예: "mon_1")
        """
        if not self._capturing:
            return "ERROR: 캡처가 실행 중이 아닙니다. Connect() 먼저 호출하세요."

        self._monitor_counter += 1
        mon_id = f"mon_{self._monitor_counter}"

        monitor = {
            "keyword": keyword,
            "start": time.time(),
            "timeout": float(timeout),
            "result": None,  # None=진행중, str=완료
            "matched_line": "",
        }
        self._monitors[mon_id] = monitor

        def _watch():
            keywords = keyword.split()
            timeout_sec = float(timeout)
            start = time.time()
            check_idx = 0

            while time.time() - start < timeout_sec:
                if mon_id not in self._monitors:
                    return  # StopMonitor로 취소됨

                with self._lock:
                    logs = list(self._logs)

                for i in range(check_idx, len(logs)):
                    line = logs[i]
                    if all(k in line for k in keywords):
                        logger.info("[DLT] Monitor %s PASS: %s", mon_id, line.strip())
                        monitor["result"] = "PASS"
                        monitor["matched_line"] = line.strip()
                        return

                check_idx = len(logs)
                time.sleep(0.3)

            logger.info("[DLT] Monitor %s FAIL: '%s' not found in %ds", mon_id, keyword, timeout_sec)
            monitor["result"] = "FAIL"

        t = threading.Thread(target=_watch, name=f"DLT-Monitor-{mon_id}", daemon=True)
        t.start()
        monitor["thread"] = t

        logger.info("[DLT] StartMonitor %s: keyword='%s' timeout=%ds", mon_id, keyword, timeout)
        return mon_id

    def StopMonitor(self, monitor_id: str = "") -> str:
        """백그라운드 모니터링 중지.

        Args:
            monitor_id: 중지할 모니터 ID (빈 값이면 전체 중지)

        Returns:
            결과 메시지
        """
        if not monitor_id:
            ids = list(self._monitors.keys())
            self._monitors.clear()
            return f"All monitors stopped ({len(ids)})"

        mon = self._monitors.pop(monitor_id, None)
        if not mon:
            return f"Monitor '{monitor_id}' not found"
        return f"Monitor '{monitor_id}' stopped"

    def GetMonitorResult(self, monitor_id: str = "") -> str:
        """백그라운드 모니터링 결과 확인.

        Args:
            monitor_id: 확인할 모니터 ID (빈 값이면 전체)

        Returns:
            결과 문자열:
            - 단일: "PASS: <로그>" / "FAIL: ..." / "RUNNING: ..."
            - 전체: 모니터별 결과 목록
        """
        if monitor_id:
            mon = self._monitors.get(monitor_id)
            if not mon:
                return f"Monitor '{monitor_id}' not found"
            if mon["result"] is None:
                elapsed = int(time.time() - mon["start"])
                return f"RUNNING: '{mon['keyword']}' ({elapsed}s / {int(mon['timeout'])}s)"
            elif mon["result"] == "PASS":
                return f"PASS: {mon['matched_line']}"
            else:
                return f"FAIL: keyword '{mon['keyword']}' not found within {int(mon['timeout'])}s"

        # 전체 결과
        if not self._monitors:
            return "(no monitors)"

        lines = []
        for mid, mon in self._monitors.items():
            if mon["result"] is None:
                elapsed = int(time.time() - mon["start"])
                lines.append(f"{mid}: RUNNING '{mon['keyword']}' ({elapsed}s)")
            elif mon["result"] == "PASS":
                lines.append(f"{mid}: PASS {mon['matched_line'][:60]}")
            else:
                lines.append(f"{mid}: FAIL '{mon['keyword']}'")
        return "\n".join(lines)

    def SearchLog(self, keyword: str, count: int = 10) -> str:
        """현재 버퍼에서 키워드를 포함하는 로그 검색.

        Args:
            keyword: 검색할 키워드
            count: 최대 결과 수 (기본 10)

        Returns:
            매칭된 로그 줄들 (줄바꿈 구분)
        """
        keywords = keyword.split()
        with self._lock:
            logs = list(self._logs)

        results = []
        for line in reversed(logs):
            if all(k in line for k in keywords):
                results.append(line.strip())
                if len(results) >= int(count):
                    break

        results.reverse()
        return "\n".join(results) if results else f"(no match for '{keyword}')"

    def GetRecentLogs(self, count: int = 100) -> str:
        """최근 로그 조회.

        Args:
            count: 조회할 줄 수 (기본 100)

        Returns:
            최근 로그 줄들 (줄바꿈 구분)
        """
        with self._lock:
            logs = list(self._logs)
        recent = logs[-int(count) :]
        return "\n".join(recent) if recent else "(empty)"

    def GetLogCount(self) -> str:
        """수신된 총 로그 수 반환."""
        return str(self._msg_counter)

    def ClearLogs(self) -> str:
        """로그 버퍼 초기화.

        Returns:
            결과 메시지
        """
        with self._lock:
            self._logs.clear()
        self._msg_counter = 0
        return "Logs cleared"

    # ------------------------------------------------------------------
    # DLT Viewer GUI 관리
    # ------------------------------------------------------------------

    def LaunchViewer(self, project_file: str = "", log_file: str = "") -> str:
        """DLT Viewer GUI를 실행.

        Args:
            project_file: .dlp 프로젝트 파일 경로 (선택)
            log_file: .dlt 로그 파일 경로 (선택)

        Returns:
            실행 결과 메시지
        """
        exe = self._sdk_dir / "dlt-viewer.exe"
        if not exe.exists():
            return f"ERROR: dlt-viewer.exe를 찾을 수 없습니다: {exe}"

        # 이미 실행 중이면 종료 후 재실행
        if self._viewer_proc and self._viewer_proc.poll() is None:
            self._viewer_proc.kill()
            self._viewer_proc.wait(timeout=5)

        cmd = [str(exe)]
        if project_file:
            cmd.extend(["-p", project_file])
        if log_file:
            cmd.extend(["-l", log_file])

        try:
            self._viewer_proc = subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            logger.info("[DLT] Launched Viewer PID=%d cmd=%s", self._viewer_proc.pid, cmd)
            return f"DLT Viewer launched (PID: {self._viewer_proc.pid})"
        except Exception as e:
            return f"ERROR: {e}"

    def CloseViewer(self) -> str:
        """DLT Viewer GUI를 종료.

        Returns:
            결과 메시지
        """
        if self._viewer_proc and self._viewer_proc.poll() is None:
            try:
                self._viewer_proc.terminate()
                self._viewer_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._viewer_proc.kill()
            self._viewer_proc = None
            return "DLT Viewer closed"
        self._viewer_proc = None
        return "DLT Viewer not running"

    # ------------------------------------------------------------------
    # DLT 프로젝트 파일 (.dlp) 관리
    # ------------------------------------------------------------------

    def CreateProject(self, file_path: str, host: str = "", port: int = 3490) -> str:
        """DLT Viewer 프로젝트 파일 (.dlp) 생성.

        Args:
            file_path: 저장할 .dlp 파일 경로
            host: ECU IP 주소 (기본: 연결된 호스트)
            port: ECU DLT 포트 (기본: 3490)

        Returns:
            결과 메시지
        """
        ecu_host = host or self._host or "192.168.105.100"
        ecu_port = int(port) if port else self._port

        root = ET.Element("dltproject")

        # Settings
        settings = ET.SubElement(root, "settings")
        table = ET.SubElement(settings, "table")
        for tag, val in [
            ("fontSize", "8"), ("automaticTimeSettings", "1"),
            ("utcOffset", "32400"), ("dst", "0"),
            ("showIndex", "1"), ("showTime", "1"),
            ("showTimestamp", "1"), ("showCount", "1"),
            ("showEcuId", "1"), ("showApId", "1"),
            ("showCtId", "1"), ("showType", "1"),
            ("showSubtype", "1"), ("showMode", "1"),
            ("showNoar", "1"), ("showPayload", "1"),
        ]:
            ET.SubElement(table, tag).text = val

        other = ET.SubElement(settings, "other")
        for tag, val in [
            ("autoConnect", "1"), ("autoScroll", "1"),
            ("autoMarkFatalError", "0"), ("autoMarkWarn", "0"),
            ("writeControl", "1"), ("updateContextLoadingFile", "1"),
        ]:
            ET.SubElement(other, tag).text = val

        # ECU
        ecu = ET.SubElement(root, "ecu")
        for tag, val in [
            ("id", "ECU"), ("description", "DLT ECU"),
            ("interface", "0"), ("hostname", ecu_host),
            ("ipport", str(ecu_port)), ("port", ""),
            ("baudrate", "115200"),
            ("loglevel", "4"), ("verbosemode", "1"),
            ("autoReconnect", "1"), ("autoReconnectTimeout", "5"),
        ]:
            ET.SubElement(ecu, tag).text = val

        tree = ET.ElementTree(root)
        out_path = Path(file_path)
        ET.indent(tree, space="    ")
        tree.write(str(out_path), encoding="UTF-8", xml_declaration=True)
        logger.info("[DLT] Created project file: %s", out_path)
        return f"Project file created: {out_path}"

    # ------------------------------------------------------------------
    # 상태 조회
    # ------------------------------------------------------------------

    def GetStatus(self) -> str:
        """현재 DLT 모듈 상태 조회.

        Returns:
            상태 문자열
        """
        connected = self._socket is not None
        capturing = self._capturing
        with self._lock:
            log_count = len(self._logs)
        viewer_running = self._viewer_proc is not None and self._viewer_proc.poll() is None
        sdk_found = self._sdk_dir.is_dir()

        parts = [
            f"Host: {self._host}:{self._port}",
            f"Connected: {connected}",
            f"Capturing: {capturing}",
            f"Logs: {log_count} (total received: {self._msg_counter})",
            f"Viewer: {'running' if viewer_running else 'stopped'}",
            f"SDK: {'found' if sdk_found else 'not found'} ({self._sdk_dir})",
        ]
        return " | ".join(parts)
