"""ICAS Agent Service — SSH 기반 VW ICAS HU 제어.

References/RemoteController.py, Control_Lib.py 를 바탕으로 HKMC6thService와
동일한 async API 계약을 제공한다.

지원 범위 (MVP):
  - Touch: tap / swipe / long_press / repeat_tap
  - Hardkey: VOLUME_UP, VOLUME_DOWN, MUTE, PTT, HOME, POWER (6개)
  - Screenshot: HU (LayerManagerControl dump + SCP pull)
  - Screen type: HU (향후 IID/HUD 확장 예정)

좌표 인코딩 (RemoteController.excutecmdTouch* 동일):
  x' = round(x / X_MULT), y' = round(y / Y_MULT)
  X_MULT = int(res_x / 1023) + 1, Y_MULT = int(res_y / 1023) + 1
  param1 = 0xFF & ((x' >> 6) + 0x10)
  param2 = ((x' >> 2 & 0xF) << 4) + ((x' << 2) & 0xC) + int(y' / 255)
  param3 = 0xFF & (y' % 255)
  end byte: 0xFD(press) / 0xFE(drag) / 0xFF(release)
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# ── 하드키 서브 커맨드 (HKMC6thService API 호환용 — 내부적으로 press/release 구분) ──
SHORT_KEY = 0x43
LONG_KEY = 0x44
PRESS_KEY = 0x41
RELEASE_KEY = 0x42


# ── ICAS 하드키 테이블 ──
# class: "short" (13B 프레임) / "long" (15B 프레임)
# key: KEY_CODE 바이트 (ksend 프레임의 키 위치)
ICAS_KEYS: dict[str, dict] = {
    "VOLUME_UP":   {"class": "short", "key": 0x10},
    "VOLUME_DOWN": {"class": "short", "key": 0x11},
    "MUTE":        {"class": "short", "key": 0x20},
    "HOME":        {"class": "long",  "key": 0x66},
    "POWER":       {"class": "long",  "key": 0x38},
}


def _encode_touch_xy(x: int, y: int, x_mult: int, y_mult: int) -> tuple[int, int, int]:
    """Touch 좌표를 ksend param1/param2/param3 바이트로 인코딩."""
    x2 = int(round(float(x) / max(1, x_mult)))
    y2 = int(round(float(y) / max(1, y_mult)))
    y_layer = int(y2 / 255)
    param1 = 0xFF & ((x2 >> 6) + 0x10)
    param2 = ((x2 >> 2 & 0xF) << 4) + ((x2 << 2) & 0xC) + y_layer
    param3 = 0xFF & (y2 % 255)
    return param1, param2, param3


def _encode_image(pil_image, fmt: str) -> bytes:
    """PIL Image → PNG/JPEG 바이트."""
    buf = io.BytesIO()
    if (fmt or "png").lower() == "jpeg":
        pil_image.convert("RGB").save(buf, format="JPEG", quality=85)
    else:
        pil_image.save(buf, format="PNG")
    return buf.getvalue()


def _validate_png_file(path: str) -> bool:
    """PNG 파일이 시그니처 + IEND chunk를 모두 갖춘 완전한 파일인지 빠르게 검증.

    PIL.Image.open의 lazy load는 IEND 부재 등 일부 손상에 무관심하지만, .convert('RGBA')에서
    실제 디코딩이 일어나며 chunk 경계 깨짐을 만나면 실패. SCP 결과를 사용 전 미리 거르기 위함.
    """
    try:
        size = os.path.getsize(path)
        if size < 16:
            return False
        with open(path, "rb") as f:
            sig = f.read(8)
            if sig != b"\x89PNG\r\n\x1a\n":
                return False
            # 마지막 12 bytes는 IEND chunk: 4byte length(0) + 'IEND' + 4byte CRC
            f.seek(-12, 2)
            tail = f.read(12)
            if len(tail) < 12 or tail[4:8] != b"IEND":
                return False
        return True
    except Exception:
        return False


def _rm_tree(path: str) -> None:
    try:
        import shutil
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


class ICASAgentService:
    """SSH 기반 ICAS HU 제어 서비스.

    HKMC6thService와 동일한 async API를 제공하여 playback_service가
    동일한 step 타입(hkmc_touch/hkmc_swipe/hkmc_key)을 그대로 디스패치할 수 있게 한다.
    """

    default_screen = "HU"

    def __init__(self, host: str, port: int = 22, device_id: str = "",
                 username: str = "root", password: str = "",
                 resolution: str = "1560x700",
                 private_server_ip: str = "",
                 private_server_password: str = "",
                 iid_display: str = "10",
                 hud_display: str = "11",
                 market: str = "EU",
                 key_overrides: Optional[dict[str, dict]] = None,
                 on_resolution_changed: Optional[Callable[[str], None]] = None,
                 screen_indices: Optional[list[int]] = None):
        self.host = host
        self.port = int(port)
        self.device_id = device_id or f"ICAS_{host}"
        self.username = username
        self.password = password or ""
        self._resolution = resolution.upper()
        self._parse_resolution()
        # market 분기 (RemoteController.py 라인 63-75 참조)
        # EU/NAR/CN: legacy 주소 + IPv6 private server
        # GP(KR): 숫자 주소 + IPv4 private server
        self.market = (market or "EU").upper()
        self._apply_market_defaults(self.market, private_server_ip)
        self.private_server_password = private_server_password
        self.iid_display = str(iid_display or "10")
        self.hud_display = str(hud_display or "11")

        self._connected = False
        self.agent_version = "ICAS Agent"
        # 캡처 전용 SSH 세션 — LayerManagerControl dump + SCP pull 용. 캡처 한 사이클은
        # 1초 가까이 걸리므로, 같은 락에 묶이면 그 사이 입력(ksend)이 블록됨.
        # 따라서 터치/하드키는 별도 _input_ssh_*에서 보내 캡처와 병렬화.
        self._ssh_client = None
        self._ssh_shell = None  # (legacy, 더 이상 사용하지 않음 — 입력은 _input_ssh_shell이 담당)
        self._ssh_lock = threading.RLock()
        self._ssh_keepalive_interval = 30  # seconds; transport.set_keepalive로 TCP idle 방지
        # 입력(터치/하드키) 전용 SSH 세션 — 캡처 락과 독립이라 SCP가 바빠도 ksend는 즉시 송신.
        # invoke_shell도 이 connection 위에서 유지하여 fire-and-forget 패턴 그대로 사용.
        self._input_ssh_client = None
        self._input_ssh_shell = None
        self._input_ssh_lock = threading.RLock()
        # IID/HUD 캡처 — private_server로의 direct-tcpip 터널 + SSH 클라이언트도 장수명 캐시.
        # 매 프레임마다 paramiko.connect() 인증(~300-500ms)을 반복하지 않도록.
        self._ps_ssh = None
        self._ps_tunnel_chan = None
        self._ps_lock = threading.RLock()
        self._key_overrides: dict[str, dict] = dict(key_overrides or {})
        # 캡처에서 PNG 실제 크기와 _res_x/_res_y가 다를 때 자동 정정 + 영구 저장 콜백.
        # 시그니처: callback("WxH"). DeviceManager가 dev.info 갱신과 파일 저장을 담당.
        self._on_resolution_changed = on_resolution_changed
        # 콜백 폭주 방지 — 동일 해상도면 호출 안 함, 락으로 직렬화.
        self._res_callback_lock = threading.Lock()
        # LayerManagerControl로 dump할 screen 인덱스 — 디바이스마다 가용한 layer가 다름.
        # 기본값 [0, 2]은 일반적인 IVI 환경 추정치. 일부 단일 디스플레이 ICAS는 [0]만 존재.
        # 캡처 실패가 누적되면 해당 인덱스를 자동 비활성화하고, 첫 연결 시 진단 명령으로 가용 레이어를 학습.
        if screen_indices is None or not screen_indices:
            self._screen_indices: list[int] = [0, 2]
        else:
            self._screen_indices = [int(i) for i in screen_indices]
        # 인덱스별 연속 실패 카운트. 이 임계치 이상이면 비활성화.
        self._screen_fail_count: dict[int, int] = {i: 0 for i in self._screen_indices}
        self._screen_disabled: set[int] = set()
        self._screen_fail_threshold = 3  # 3회 연속 실패하면 해당 인덱스 dump 시도 중단

    # ------------------------------------------------------------------
    # Basic accessors
    # ------------------------------------------------------------------
    def _parse_resolution(self) -> None:
        try:
            rx, ry = self._resolution.upper().split("X")
            self._res_x = int(rx)
            self._res_y = int(ry)
        except Exception:
            self._res_x, self._res_y = 1560, 700
        self._x_mult = int(self._res_x / 1023) + 1
        self._y_mult = int(self._res_y / 1023) + 1

    @property
    def resolution(self) -> str:
        return self._resolution

    @resolution.setter
    def resolution(self, value: str) -> None:
        self._resolution = value.upper()
        self._parse_resolution()

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _maybe_autoupdate_resolution(self, width: int, height: int) -> bool:
        """캡처된 PNG 크기와 현재 _res_x/_res_y 비교 후 다르면 자동 갱신.

        반환값: 실제로 갱신되었는지 여부. 콜백은 DeviceManager가 주입하며
        dev.info dict + 파일 저장을 담당. 동일 해상도면 no-op.
        """
        if width <= 0 or height <= 0:
            return False
        if width == self._res_x and height == self._res_y:
            return False
        with self._res_callback_lock:
            new_res = f"{width}x{height}"
            self._resolution = new_res.upper()
            self._parse_resolution()
            cb = self._on_resolution_changed
        if cb is not None:
            try:
                cb(new_res)
            except Exception as e:
                logger.warning("ICAS on_resolution_changed callback failed: %s", e)
        else:
            logger.info("ICAS resolution auto-detected (no persistence callback): %s", new_res)
        return True

    def detect_resolution(self) -> tuple[int, int]:
        """1회 캡처를 트리거해 디바이스 실제 해상도를 반환 + 자동 갱신.

        호출자: 자동 감지 버튼 / 등록 직후 1회 보정.
        반환: (width, height). 캡처 실패 시 RuntimeError 전파.
        """
        # _screencap_hu 내부에서 _maybe_autoupdate_resolution이 호출되므로
        # 캡처 후 self._res_x/_res_y가 곧 디바이스 실제 해상도가 됨.
        self._screencap_hu(fmt="png")
        return self._res_x, self._res_y

    def set_addr(self, src: str, dst: str) -> None:
        """src/dst ksend 주소 변경 (EU/NAR/CN/GP 분기)."""
        self.src_addr = src
        self.dst_addr = dst

    def _apply_market_defaults(self, market: str, private_server_ip_override: str = "") -> None:
        """market 값에 따라 ksend src/dst 주소 + private_server_ip 기본값 설정.

        RemoteController.py 라인 63-75 참조:
          EU/NAR/CN: legacy — src=0x200000000000000, dst=0x80000000000, private=IPv6
          GP (그 외): src=57, dst=43, private=IPv4 192.168.0.2
        private_server_ip_override가 비어있지 않으면 그 값을 그대로 사용.
        """
        m = (market or "EU").upper()
        if m in ("EU", "NAR", "CN"):
            self.src_addr = "0x200000000000000"
            self.dst_addr = "0x80000000000"
            default_private = "fd53:7cb8:383:3::73"
        else:
            self.src_addr = "57"
            self.dst_addr = "43"
            default_private = "192.168.0.2"
        self.private_server_ip = private_server_ip_override or default_private

    def set_market(self, market: str, private_server_ip_override: str = "") -> None:
        """런타임 market 전환 (addr + private_server_ip 동시 갱신)."""
        self.market = (market or "EU").upper()
        self._apply_market_defaults(self.market, private_server_ip_override)

    # ------------------------------------------------------------------
    # Connection (SSH check)
    # ------------------------------------------------------------------
    def _new_ssh(self):
        """새 paramiko SSHClient 생성 및 연결 (IID/HUD hop 등 일회성 용도).

        공유 세션이 필요한 경우는 `_get_shared_ssh()`를 사용할 것.
        """
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(self.host, username=self.username, port=self.port,
                    password=self.password, timeout=10)
        return ssh

    def _is_ssh_alive(self, ssh) -> bool:
        """paramiko SSHClient의 transport 활성 여부 체크."""
        if ssh is None:
            return False
        try:
            t = ssh.get_transport()
            return bool(t and t.is_active() and t.is_authenticated())
        except Exception:
            return False

    def _get_shared_ssh(self):
        """공유 SSH 세션 반환 — 끊어졌으면 재연결.

        락 안에서 호출해야 함. 최초 호출 시 새로 연결하고,
        transport가 dead면 닫고 재생성. keep-alive를 설정해 일정 주기마다 NO-OP 프레임을 보내
        방화벽/NAT TCP idle timeout으로 끊어지는 것을 방지.
        """
        if self._is_ssh_alive(self._ssh_client):
            return self._ssh_client
        # 죽은 세션 정리
        if self._ssh_client is not None:
            try:
                self._ssh_client.close()
            except Exception:
                pass
            self._ssh_client = None
        # 공유 shell도 dead SSH와 함께 폐기
        if self._ssh_shell is not None:
            try:
                self._ssh_shell.close()
            except Exception:
                pass
            self._ssh_shell = None
        # 새 연결
        ssh = self._new_ssh()
        try:
            t = ssh.get_transport()
            if t is not None:
                t.set_keepalive(self._ssh_keepalive_interval)
        except Exception:
            pass
        self._ssh_client = ssh
        return ssh

    def _get_shared_shell(self):
        """공유 interactive shell 채널 반환 — 죽었으면 새로 오픈하고 초기 배너를 드레인.

        ksend 등 fire-and-forget 명령은 exec_command(채널 당 open_session=sshd MaxSessions 소모)
        대신 단일 shell 채널에 `shell.send(cmd + "\\n")` 으로 보낸다.
        레퍼런스 구현과 동일한 패턴이며, sshd 세션 한도를 소모하지 않아 장기간 안정.
        """
        ssh = self._get_shared_ssh()
        if self._ssh_shell is not None:
            try:
                if not self._ssh_shell.closed:
                    return self._ssh_shell
            except Exception:
                pass
            # 죽은 shell 정리
            try:
                self._ssh_shell.close()
            except Exception:
                pass
            self._ssh_shell = None
        # 새 shell 오픈 + 초기 배너/프롬프트 드레인
        shell = ssh.invoke_shell()
        shell.settimeout(0.5)
        # 초기 프롬프트가 나올 때까지 최대 1s 드레인
        deadline = time.time() + 1.0
        while time.time() < deadline:
            try:
                if shell.recv_ready():
                    shell.recv(65536)
                else:
                    time.sleep(0.05)
            except Exception:
                break
        self._ssh_shell = shell
        return shell

    def _get_input_ssh(self):
        """입력 전용 SSH 클라이언트 반환 — 끊어졌으면 재연결.

        _input_ssh_lock 안에서 호출해야 함. 캡처 SSH(_ssh_client)와는 완전히 독립된
        TCP 세션이라 한쪽이 바빠도 다른 쪽은 영향 없음.
        """
        if self._is_ssh_alive(self._input_ssh_client):
            return self._input_ssh_client
        if self._input_ssh_client is not None:
            try:
                self._input_ssh_client.close()
            except Exception:
                pass
            self._input_ssh_client = None
        if self._input_ssh_shell is not None:
            try:
                self._input_ssh_shell.close()
            except Exception:
                pass
            self._input_ssh_shell = None
        ssh = self._new_ssh()
        try:
            t = ssh.get_transport()
            if t is not None:
                t.set_keepalive(self._ssh_keepalive_interval)
        except Exception:
            pass
        self._input_ssh_client = ssh
        return ssh

    def _get_input_shell(self):
        """입력 전용 invoke_shell 채널 반환 — ksend 등 fire-and-forget 명령용."""
        ssh = self._get_input_ssh()
        if self._input_ssh_shell is not None:
            try:
                if not self._input_ssh_shell.closed:
                    return self._input_ssh_shell
            except Exception:
                pass
            try:
                self._input_ssh_shell.close()
            except Exception:
                pass
            self._input_ssh_shell = None
        shell = ssh.invoke_shell()
        shell.settimeout(0.5)
        # 초기 프롬프트 드레인 — 최대 1초
        deadline = time.time() + 1.0
        while time.time() < deadline:
            try:
                if shell.recv_ready():
                    shell.recv(65536)
                else:
                    time.sleep(0.05)
            except Exception:
                break
        self._input_ssh_shell = shell
        return shell

    def _drain_shell(self, shell, max_bytes: int = 65536) -> bytes:
        """공유 shell의 수신 버퍼를 non-blocking으로 비움 (pipe 백프레셔 방지)."""
        buf = b""
        try:
            while shell.recv_ready() and len(buf) < max_bytes:
                chunk = shell.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except Exception:
            pass
        return buf

    def _shell_run(self, commands: list[str], post_sleep_s: float = 0.02) -> None:
        """입력 전용 shell 채널로 명령 송신 + drain. transport/shell dead면 1회 리셋 재시도.

        캡처 SSH 락(_ssh_lock)과 독립된 _input_ssh_lock에서 실행되므로,
        스크린샷 SCP가 진행 중이어도 터치/하드키는 즉시 송신됨.
        """
        def _do(shell) -> None:
            for c in commands:
                shell.send(c + "\n")
                if post_sleep_s > 0:
                    time.sleep(post_sleep_s)
                self._drain_shell(shell)

        with self._input_ssh_lock:
            try:
                shell = self._get_input_shell()
                _do(shell)
                return
            except Exception as e:
                logger.warning("ICAS input shell exec failed, retrying: %s", e)
                # shell 리셋 → 다시 시도 (transport가 살아있으면 재사용, 죽었으면 재연결)
                if self._input_ssh_shell is not None:
                    try:
                        self._input_ssh_shell.close()
                    except Exception:
                        pass
                    self._input_ssh_shell = None
            shell = self._get_input_shell()
            _do(shell)

    def connect(self, timeout: float = 10.0) -> bool:
        """캡처/입력 SSH 세션을 모두 확보. 두 세션은 독립이라 한쪽이 바빠도 다른쪽 영향 없음."""
        try:
            with self._ssh_lock:
                self._get_shared_ssh()  # 캡처용 SSH 사전 확보
            with self._input_ssh_lock:
                self._get_input_ssh()   # 입력용 SSH 사전 확보 (첫 ksend 지연 제거)
            self._connected = True
            logger.info("ICAS connected to %s:%d (capture+input sessions)", self.host, self.port)
            # 캡처 layer 진단: 가용 screen/layer 인덱스를 알면 사용자에게 가이드 제공.
            # 실패해도 연결 자체에는 영향 없음 (best-effort, 5초 타임아웃).
            try:
                self._probe_layer_info()
            except Exception as e:
                logger.debug("ICAS layer probe skipped: %s", e)
            # ksend 입력 경로 진단: 바이너리 존재 + src/dst addr로 더미 프레임 송신 결과 확인.
            try:
                self._probe_ksend()
            except Exception as e:
                logger.debug("ICAS ksend probe skipped: %s", e)
            return True
        except Exception as e:
            logger.error("ICAS connect failed %s:%d: %s", self.host, self.port, e)
            self._connected = False
            return False

    def _probe_ksend(self) -> None:
        """ksend 입력 경로의 가용성을 진단. 입력 전용 SSH 세션에서 실행.

        진단 내용:
          1) ksend 바이너리 존재/권한 확인
          2) help/usage 출력 확인 — 인자 형식이 다른 ksend 변종인지 식별
          3) 다른 입력 메커니즘 탐색 (uinput/evtouch/input* 노드)
          4) KIPC listener 진단 — 실행 중인 touch/input handler 후보 프로세스 식별
          5) 더미 ksend 송신 1회 — verbose(-v) 옵션으로 실제 송신 결과 확인
        """
        with self._input_ssh_lock:
            ssh = self._get_input_ssh()
            # 더미 송신: 현재 src/dst로 짧은 binary 메시지 1회. -v로 verbose 출력 활성.
            # 실제 touch frame 형식과 동일한 16바이트 + end byte 0xFF(release) 형태이지만
            # 좌표를 0,0으로 설정해 실 영향 최소화.
            dummy_data = (
                "0x83 0x50 0x20 0x0b 0x00 0x00 0x00 0x00 0x00 0xa0 0x01 0x11 "
                "0x10 0x00 0x00 0xff"
            )
            cmd = (
                "echo '--ksend bin--' ; "
                "ls -la /lge/app_ro/bin/ksend 2>&1 ; "
                "echo '--ksend help--' ; "
                "/lge/app_ro/bin/ksend 2>&1 | head -n 20 ; "
                "echo '--alt input nodes--' ; "
                "ls -la /dev/input/ 2>&1 | head -n 30 ; "
                "echo '--uinput--' ; "
                "ls -la /dev/uinput 2>&1 ; "
                "echo '--KIPC procs--' ; "
                "(ps -ef 2>/dev/null || ps 2>/dev/null) | "
                "grep -iE '(touch|input|hmi|kipc|hardkey|remote)' | "
                "grep -v grep | head -n 20 ; "
                "echo '--KIPC proc table--' ; "
                "ls /proc/lge_kipc/ 2>&1 | head -n 20 ; "
                "cat /proc/lge_kipc/list 2>/dev/null | head -n 30 ; "
                "echo '--addr defaults--' ; "
                f"echo 'src={self.src_addr} dst={self.dst_addr} market={self.market}' ; "
                "echo '--ksend -v dummy send--' ; "
                f"/lge/app_ro/bin/ksend -v -s {self.src_addr} -d {self.dst_addr} "
                f'-b "{dummy_data}" 2>&1 | head -n 20 ; '
                'echo "exit=$?"'
            )
            try:
                stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
                try:
                    stdin.close()
                except Exception:
                    pass
                out = stdout.read().decode("utf-8", errors="replace")
                err = stderr.read().decode("utf-8", errors="replace")
                snippet = (out + ("\n[stderr] " + err if err.strip() else "")).strip().replace("\r", " ").replace("\n", " | ")[:2500]
                logger.info("ICAS ksend probe → %s", snippet or "(empty)")
            except Exception as e:
                logger.debug("ICAS ksend probe exec failed: %s", e)

    def _wait_remote_files_stable(self, ssh, items: list[tuple[int, str]],
                                  max_wait_s: float = 1.0,
                                  poll_interval_s: float = 0.05,
                                  stable_iters: int = 2) -> None:
        """디바이스 쪽 파일 크기가 stable_iters회 연속 동일할 때까지 폴링.

        LayerManagerControl이 비동기로 PNG를 쓰는 환경에서 SCP가 partial 파일을 가져가는
        race를 막기 위함. items: [(idx, remote_path), ...]. 실패해도 silent — 안정성은
        PNG 무결성 검증(_validate_png_file)에서 한 번 더 거름.
        """
        if not items:
            return
        deadline = time.monotonic() + max_wait_s
        # 단일 SSH 명령으로 모든 파일 크기를 한 번에 조회 (왕복 비용 절감).
        size_cmd = " ; ".join([f"wc -c < {rp} 2>/dev/null || echo 0" for _, rp in items])
        prev_sizes: Optional[list[int]] = None
        stable_streak = 0
        while time.monotonic() < deadline:
            try:
                stdin, stdout, stderr = ssh.exec_command(size_cmd, timeout=2)
                try:
                    stdin.close()
                except Exception:
                    pass
                out = stdout.read().decode("utf-8", errors="replace")
                lines = [l.strip() for l in out.splitlines() if l.strip()]
                sizes: list[int] = []
                for l in lines:
                    try:
                        sizes.append(int(l.split()[0]))
                    except Exception:
                        sizes.append(0)
                # 모든 파일이 양수 + 직전과 동일하면 stable streak 증가
                if sizes and all(s > 0 for s in sizes) and sizes == prev_sizes:
                    stable_streak += 1
                    if stable_streak >= stable_iters:
                        return
                else:
                    stable_streak = 0
                prev_sizes = sizes
            except Exception:
                pass
            time.sleep(poll_interval_s)

    def _maybe_disable_screen(self, idx: int) -> None:
        """연속 실패가 임계치를 넘으면 해당 screen 인덱스를 비활성화 (이후 dump 시도 안 함)."""
        if idx in self._screen_disabled:
            return
        if self._screen_fail_count.get(idx, 0) >= self._screen_fail_threshold:
            self._screen_disabled.add(idx)
            logger.warning(
                "ICAS HU screen %d auto-disabled after %d consecutive failures — "
                "this layer is likely not available on this device. Active indices now: %s",
                idx, self._screen_fail_threshold,
                [i for i in self._screen_indices if i not in self._screen_disabled] or "[fallback: 0]",
            )

    def _probe_layer_info(self) -> None:
        """LayerManagerControl get screens/layers를 실행해 진단 정보를 로깅.

        실제 디바이스마다 가용 layer 인덱스가 다르므로, 사용자가 올바른 값을 설정하도록
        로그로 안내. 명령 실패는 무시 (LayerManagerControl 자체가 없을 수도 있음).
        """
        with self._ssh_lock:
            ssh = self._get_shared_ssh()
            for label, cmd in (
                ("screens", "export XDG_RUNTIME_DIR=/run/platform/weston ; LayerManagerControl get screens"),
                ("layers",  "export XDG_RUNTIME_DIR=/run/platform/weston ; LayerManagerControl get layers"),
            ):
                try:
                    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=5)
                    try:
                        stdin.close()
                    except Exception:
                        pass
                    out = stdout.read().decode("utf-8", errors="replace")
                    err = stderr.read().decode("utf-8", errors="replace")
                    snippet = (out or err).strip().replace("\r", " ").replace("\n", " | ")[:500]
                    logger.info("ICAS LayerManagerControl get %s → %s", label, snippet or "(empty)")
                except Exception as e:
                    logger.debug("ICAS LayerManagerControl get %s failed: %s", label, e)

    def disconnect(self) -> None:
        self._connected = False
        with self._ssh_lock:
            if self._ssh_shell is not None:
                try:
                    self._ssh_shell.close()
                except Exception:
                    pass
                self._ssh_shell = None
            if self._ssh_client is not None:
                try:
                    self._ssh_client.close()
                except Exception:
                    pass
                self._ssh_client = None
        # 입력 전용 세션도 정리
        with self._input_ssh_lock:
            if self._input_ssh_shell is not None:
                try:
                    self._input_ssh_shell.close()
                except Exception:
                    pass
                self._input_ssh_shell = None
            if self._input_ssh_client is not None:
                try:
                    self._input_ssh_client.close()
                except Exception:
                    pass
                self._input_ssh_client = None
        self._close_private_server_ssh()

    def _close_private_server_ssh(self) -> None:
        """private_server 공유 SSH와 터널 채널을 닫는다."""
        with self._ps_lock:
            if self._ps_ssh is not None:
                try:
                    self._ps_ssh.close()
                except Exception:
                    pass
                self._ps_ssh = None
            if self._ps_tunnel_chan is not None:
                try:
                    self._ps_tunnel_chan.close()
                except Exception:
                    pass
                self._ps_tunnel_chan = None

    def _get_private_server_ssh(self):
        """IID/HUD용 private_server 공유 SSH 반환 — 죽어있으면 새로 열고 인증.

        매 프레임 새로 paramiko.connect()를 하면 인증만 300-500ms가 들어 FPS가 떨어짐.
        direct-tcpip 터널 + SSH 클라이언트를 프로세스 수명 동안 재사용.
        호출자는 `_ps_lock` 잡고 사용 (SFTP/exec_command가 동시에 돌지 않도록).
        """
        # 살아있으면 그대로 반환
        if self._ps_ssh is not None:
            try:
                t = self._ps_ssh.get_transport()
                if t is not None and t.is_active() and t.is_authenticated():
                    return self._ps_ssh
            except Exception:
                pass
            # 죽었으면 정리
            self._close_private_server_ssh()
        # 새로 연결
        import paramiko
        shared = self._get_shared_ssh()  # HU shared SSH (락 보호됨 — _ssh_lock)
        hu_transport = shared.get_transport()
        if hu_transport is None or not hu_transport.is_active():
            raise RuntimeError("ICAS shared HU transport not active")
        chan = hu_transport.open_channel(
            "direct-tcpip",
            (self.private_server_ip, 22),
            ("127.0.0.1", 0),
            timeout=10,
        )
        ps_ssh = paramiko.SSHClient()
        ps_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ps_ssh.connect(
            self.private_server_ip, port=22,
            username="root", password=(self.private_server_password or ""),
            sock=chan, timeout=15,
            allow_agent=False, look_for_keys=False,
        )
        try:
            pt = ps_ssh.get_transport()
            if pt is not None:
                pt.set_keepalive(self._ssh_keepalive_interval)
        except Exception:
            pass
        self._ps_tunnel_chan = chan
        self._ps_ssh = ps_ssh
        return ps_ssh

    async def async_connect(self, timeout: float = 10.0) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.connect, timeout)

    async def async_disconnect(self) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.disconnect)

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------
    def _exec_on_shared(self, commands: list[str], interval_s: float = 0.0,
                        per_cmd_timeout: float = 5.0) -> None:
        """공유 SSH 세션에서 exec_command들을 순차 실행.

        각 명령은 exit_status를 기다려 채널을 즉시 해제함 (sshd MaxSessions=10 한도 보호).
        ksend는 즉시 반환되므로 wait 비용이 무시할 수준. transport 에러 시 세션 리셋 후 1회 재시도.
        """
        def _run_one(ssh, c: str) -> None:
            stdin, stdout, stderr = ssh.exec_command(c, timeout=per_cmd_timeout)
            try:
                stdin.close()
            except Exception:
                pass
            # exit_status 대기 → 채널 즉시 클로즈 (sshd 세션 누수 방지)
            try:
                stdout.channel.settimeout(per_cmd_timeout)
                stdout.channel.recv_exit_status()
            except Exception:
                pass
            finally:
                for f in (stdout, stderr):
                    try:
                        f.close()
                    except Exception:
                        pass

        def _run_all(ssh, cmd_list: list[str]) -> None:
            for i, c in enumerate(cmd_list):
                _run_one(ssh, c)
                if interval_s > 0 and i < len(cmd_list) - 1:
                    time.sleep(interval_s)

        with self._ssh_lock:
            try:
                ssh = self._get_shared_ssh()
                _run_all(ssh, commands)
                return
            except Exception as e:
                # transport 끊김/EOF/채널 한도 초과 등 → 세션 리셋 후 1회 재시도
                logger.warning("ICAS shared SSH exec failed, retrying: %s", e)
                if self._ssh_client is not None:
                    try:
                        self._ssh_client.close()
                    except Exception:
                        pass
                    self._ssh_client = None
            ssh = self._get_shared_ssh()
            _run_all(ssh, commands)

    def _ksend(self, data_bytes: str) -> None:
        """ksend 명령 1회 송신.

        기본 모드(invoke_shell): 빠르지만 stderr/exit를 알 수 없어 silent fail 가능.
        ICAS_KSEND_VERBOSE=1 환경변수: exec_command 모드 + ksend -v 옵션 + 결과 로깅.
        """
        verbose = os.environ.get("ICAS_KSEND_VERBOSE", "").strip() in ("1", "true", "yes")
        v_flag = " -v " if verbose else " "
        cmd = f'/lge/app_ro/bin/ksend{v_flag}-s {self.src_addr} -d {self.dst_addr} -b "{data_bytes}"'
        if verbose:
            self._ksend_exec_verbose(cmd)
        else:
            self._shell_run([cmd])

    def _ksend_many(self, data_list: list[str], interval_s: float = 0.1) -> None:
        """ksend 명령 여러 개를 공유 shell 채널에서 순차 송신."""
        verbose = os.environ.get("ICAS_KSEND_VERBOSE", "").strip() in ("1", "true", "yes")
        v_flag = " -v " if verbose else " "
        cmds = [
            f'/lge/app_ro/bin/ksend{v_flag}-s {self.src_addr} -d {self.dst_addr} -b "{data}"'
            for data in data_list
        ]
        if verbose:
            for c in cmds:
                self._ksend_exec_verbose(c)
                if interval_s > 0:
                    time.sleep(interval_s)
            return
        # 각 cmd 사이 간격은 shell_run의 post_sleep_s로 들어감 — interval_s 우선
        self._shell_run(cmds, post_sleep_s=max(0.02, interval_s))

    def _ksend_exec_verbose(self, cmd: str) -> None:
        """진단 모드: exec_command로 ksend 실행하고 stderr/exit 결과를 로깅.

        성능 영향 있음 (매 명령당 SSH channel 1회). 디버깅 후 환경변수 해제 권장.
        입력 전용 SSH 세션 사용 — 캡처와 독립.
        """
        with self._input_ssh_lock:
            ssh = self._get_input_ssh()
            try:
                stdin, stdout, stderr = ssh.exec_command(cmd, timeout=5)
                try:
                    stdin.close()
                except Exception:
                    pass
                out = stdout.read().decode("utf-8", errors="replace")
                err = stderr.read().decode("utf-8", errors="replace")
                ec = stdout.channel.recv_exit_status()
                if ec != 0 or err.strip():
                    logger.warning(
                        "ksend exit=%d stderr=%r stdout=%r cmd=%r",
                        ec, err.strip()[:200], out.strip()[:200], cmd,
                    )
                else:
                    logger.info("ksend ok: %r", cmd[:160])
            except Exception as e:
                logger.warning("ksend exec failed: type=%s repr=%r cmd=%r",
                               type(e).__name__, e, cmd)

    # ------------------------------------------------------------------
    # Touch (press/drag/release) — ref RemoteController.excutecmdTouch*
    # ------------------------------------------------------------------
    def _touch_frame(self, x: int, y: int, end_byte: int) -> str:
        p1, p2, p3 = _encode_touch_xy(int(x), int(y), self._x_mult, self._y_mult)
        return (
            f"0x83 0x50 0x20 0x0b 0x00 0x00 0x00 0x00 0x00 0xa0 0x01 0x11 "
            f"0x{p1:02x} 0x{p2:02x} 0x{p3:02x} 0x{end_byte:02x}"
        )

    def _touch_press(self, x: int, y: int) -> None:
        self._ksend(self._touch_frame(x, y, 0xFD))

    def _touch_drag(self, x: int, y: int) -> None:
        self._ksend(self._touch_frame(x, y, 0xFE))

    def _touch_release(self, x: int, y: int) -> None:
        self._ksend(self._touch_frame(x, y, 0xFF))

    def tap(self, x: int, y: int, screen_type: str = "HU",
            dp: float = 0.2, dr: float = 0.0) -> None:
        """단일 탭. press → (dp초 대기) → release."""
        self._touch_press(x, y)
        if dp > 0:
            time.sleep(dp)
        self._touch_release(x, y)
        if dr > 0:
            time.sleep(dr)

    def long_press(self, x: int, y: int, duration_ms: int = 3000,
                   screen_type: str = "HU") -> None:
        self._touch_press(x, y)
        time.sleep(duration_ms / 1000.0)
        self._touch_release(x, y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              screen_type: str = "HU", duration_ms: int = 300) -> None:
        """press(x1,y1) → drag(보간) → release(x2,y2)."""
        # 보간 스텝 수: duration 기반 (각 스텝 ~20ms 목표, 최소 3 최대 20)
        target_interval_ms = 20
        steps = max(3, min(20, max(1, duration_ms // target_interval_ms)))
        dx = (x2 - x1) / steps
        dy = (y2 - y1) / steps

        # 동일 SSH 세션으로 일괄 송신 — 오버헤드 최소화
        frames: list[str] = []
        frames.append(self._touch_frame(x1, y1, 0xFD))  # press
        for i in range(1, steps):
            ix = int(round(x1 + dx * i))
            iy = int(round(y1 + dy * i))
            frames.append(self._touch_frame(ix, iy, 0xFE))  # drag
        frames.append(self._touch_frame(x2, y2, 0xFF))  # release

        # 간격은 duration_ms에 맞춰 분배
        interval_s = max(0.01, (duration_ms / 1000.0) / max(1, len(frames) - 1))
        self._ksend_many(frames, interval_s=interval_s)

    def repeat_tap(self, x: int, y: int, count: int = 5,
                   interval_ms: int = 100, screen_type: str = "HU") -> None:
        for i in range(count):
            self.tap(x, y, screen_type, dp=0.05, dr=0.0)
            if i < count - 1 and interval_ms > 0:
                time.sleep(interval_ms / 1000.0)

    # ------------------------------------------------------------------
    # Hardkey
    # ------------------------------------------------------------------
    def _hkey_short_frame(self, key_code: int, state: int) -> str:
        """Short 클래스(Volume/Mute/PTT) — 13 bytes."""
        return (
            f"0x83 0x50 0x10 0x0A 0x00 0x00 0x05 0xBF 0x00 "
            f"0x{key_code:02X} 0x{state:02X} 0x00 0x00"
        )

    def _hkey_long_frame(self, key_code: int, state: int) -> str:
        """Long 클래스(Home/Power) — 15 bytes.
        state=0x01 / 0x00 에 따라 tail(0x10 / 0xD9) 변경 (ref 코드 관찰값)."""
        tail = 0x10 if state else 0xD9
        return (
            f"0x83 0x50 0x20 0x0B 0x17 0xF8 0xF1 0x73 0x00 0x30 "
            f"0x{key_code:02X} 0x{state:02X} 0x{tail:02X} 0x00 0x00"
        )

    def resolve_key(self, key_name: str) -> Optional[dict]:
        """키 스펙 반환 (override 병합)."""
        base = ICAS_KEYS.get(key_name)
        if not base:
            return None
        merged = dict(base)
        ov = self._key_overrides.get(key_name) or {}
        for k in ("class", "key"):
            if k in ov:
                merged[k] = ov[k]
        return merged

    def set_key_overrides(self, overrides: Optional[dict[str, dict]]) -> None:
        self._key_overrides = dict(overrides or {})

    def get_key_overrides(self) -> dict[str, dict]:
        return dict(self._key_overrides)

    def send_key_by_name(self, key_name: str, sub_cmd: int = SHORT_KEY,
                         screen_type: Optional[str] = None,
                         direction: Optional[int] = None) -> None:
        """이름 기반 하드키 송신. sub_cmd는 HKMC6th API 호환용(SHORT/LONG).

        ICAS는 press→release 시퀀스가 기본. LONG은 press→대기→release 패턴으로 처리.
        """
        info = self.resolve_key(key_name)
        if not info:
            raise ValueError(f"Unknown ICAS key: {key_name}")
        key_code = int(info["key"])
        klass = info.get("class", "short")

        # press / release — Short class는 release 시 key=0x00, state=0x00 (ref RemoteController:525)
        # Long class는 key_code 유지, state만 0x00 + tail 변경 (ref line 562)
        press = (self._hkey_short_frame(key_code, 0x01) if klass == "short"
                 else self._hkey_long_frame(key_code, 0x01))
        release = (self._hkey_short_frame(0x00, 0x00) if klass == "short"
                   else self._hkey_long_frame(key_code, 0x00))

        hold_s = 1.0 if sub_cmd == LONG_KEY else 0.1
        self._ksend_many([press], interval_s=0)
        time.sleep(hold_s)
        self._ksend_many([release], interval_s=0)

    def send_key(self, cmd: int, sub_cmd: int, key_data: int,
                 monitor: int = 0x00, direction: Optional[int] = None) -> None:
        """HKMC 호환용 raw send_key. key_data를 KEY_CODE로 해석해 single press/release 수행.

        ICAS는 cmd 분류가 하나라, 별도 분기 없이 short 프레임을 기본으로 사용.
        long class가 필요하면 key_data 범위로 자동 판별 (POWER=0x38, HOME=0x66).
        """
        klass = "long" if key_data in (0x38, 0x66) else "short"
        press = (self._hkey_short_frame(key_data, 0x01) if klass == "short"
                 else self._hkey_long_frame(key_data, 0x01))
        # Short release는 key=0, state=0 (send_key_by_name과 동일 규칙)
        release = (self._hkey_short_frame(0x00, 0x00) if klass == "short"
                   else self._hkey_long_frame(key_data, 0x00))
        hold_s = 1.0 if sub_cmd == LONG_KEY else 0.1
        self._ksend_many([press], interval_s=0)
        time.sleep(hold_s)
        self._ksend_many([release], interval_s=0)

    # ------------------------------------------------------------------
    # Screenshot (HU only in MVP)
    # ------------------------------------------------------------------
    def screencap_bytes(self, screen_type: str = "HU",
                        fmt: str = "png", timeout: float = 15.0) -> bytes:
        """스크린샷 캡처. 현재는 HU만 지원.

        IID/HUD 경로는 private_server의 `screenshot` 바이너리가 'no displays'를
        반환하는 환경 제약으로 비활성. 향후 지원 시 `_screencap_iid_hud` 재활성.
        """
        # screen_type은 UI 호환을 위해 받되, 실제 경로는 항상 HU.
        return self._screencap_hu(fmt=fmt)

    # ------------------------------------------------------------------
    # HU screenshot — LayerManagerControl dump + SCP pull + composite
    # ------------------------------------------------------------------
    def _screencap_hu(self, fmt: str = "png") -> bytes:
        import tempfile
        import os
        from PIL import Image, ImageFile
        ImageFile.LOAD_TRUNCATED_IMAGES = True

        # HU sshd는 SFTP 서브시스템 미지원 → SCP(paramiko-scp)로 pull.
        try:
            from scp import SCPClient
        except ImportError as e:
            raise RuntimeError("scp module required: pip install scp") from e

        tmp_dir = tempfile.mkdtemp(prefix="icas_cap_")
        try:
            # 공유 SSH 세션에서 dump + SCP pull 을 일괄 수행 (매 프레임마다 재인증 방지).
            # - 활성화된 screen 인덱스만 dump 시도 (디바이스마다 가용 layer 다름)
            # - 각 dump를 ;로 분리: 한쪽 실패가 다른쪽을 막지 않음
            # - SCPClient 하나로 모든 파일 연속 get (subsystem 1회)
            def _do_capture(ssh) -> list[str]:
                active_indices = [i for i in self._screen_indices if i not in self._screen_disabled]
                if not active_indices:
                    # 모든 인덱스가 비활성화됨 — 안전 fallback: screen 0만 다시 시도
                    active_indices = [0]
                # 인덱스 → 로컬 파일명 매핑 (사람 가독 위해 1-base)
                file_map = [(idx, f"screen_idx{idx}.png") for idx in active_indices]
                # 매 프레임 시작 시 stale 파일을 제거해야 SCP가 이전 프레임의 partial 파일을 가져오지 않음.
                # LayerManagerControl dump는 IVI 그래픽 파이프라인을 통해 비동기로 PNG를 쓰는 구현체가
                # 있어 exec_command가 끝나도 파일이 완성 전일 수 있음 → rm + dump + sync 순으로 처리.
                rm_parts = [f"rm -f /tmp/{fname}" for _, fname in file_map]
                dump_parts = [
                    f"LayerManagerControl dump screen {idx} to /tmp/{fname} 2>/tmp/lmc_idx{idx}.err"
                    for idx, fname in file_map
                ]
                dump_cmd = (
                    "export XDG_RUNTIME_DIR=/run/platform/weston ; "
                    + " ; ".join(rm_parts)
                    + " ; "
                    + " ; ".join(dump_parts)
                    + " ; sync"
                )
                stdin, stdout, stderr = ssh.exec_command(dump_cmd, timeout=20)
                try:
                    stdin.close()
                except Exception:
                    pass
                exit_status = -1
                err_text = ""
                try:
                    stdout.channel.settimeout(20)
                    while not stdout.channel.exit_status_ready():
                        if stdout.channel.recv_stderr_ready():
                            try:
                                err_text += stdout.channel.recv_stderr(4096).decode("utf-8", errors="replace")
                            except Exception:
                                pass
                        else:
                            time.sleep(0.05)
                    while stdout.channel.recv_stderr_ready():
                        try:
                            err_text += stdout.channel.recv_stderr(4096).decode("utf-8", errors="replace")
                        except Exception:
                            break
                    exit_status = stdout.channel.recv_exit_status()
                except Exception:
                    pass
                finally:
                    for f in (stdout, stderr):
                        try:
                            f.close()
                        except Exception:
                            pass

                if exit_status != 0:
                    snippet = err_text.strip().replace("\r", " ").replace("\n", " | ")[:200]
                    logger.warning("ICAS HU dump exit=%d stderr=%r", exit_status, snippet)

                # LayerManagerControl이 비동기 처리하는 경우 dump_cmd 종료 후에도 파일 쓰기가 진행 중일 수 있음.
                # SCP 전 짧은 wait + 디바이스 측 파일 크기 폴링으로 안정화 확인 (최대 1초).
                self._wait_remote_files_stable(ssh, [(idx, f"/tmp/{fname}") for idx, fname in file_map])

                files: list[str] = []
                try:
                    with SCPClient(ssh.get_transport()) as scp:
                        for idx, fname in file_map:
                            remote = f"/tmp/{fname}"
                            local = os.path.join(tmp_dir, fname)
                            try:
                                scp.get(remote, local)
                                ok = False
                                if os.path.exists(local) and os.path.getsize(local) > 0:
                                    # PNG 무결성 1차 검증 — 시그니처 + IEND chunk 존재 여부
                                    if _validate_png_file(local):
                                        files.append(local)
                                        self._screen_fail_count[idx] = 0
                                        ok = True
                                    else:
                                        logger.warning(
                                            "ICAS HU scp %s: PNG truncated/corrupt (size=%d)",
                                            remote, os.path.getsize(local),
                                        )
                                if not ok:
                                    self._screen_fail_count[idx] = self._screen_fail_count.get(idx, 0) + 1
                                    self._maybe_disable_screen(idx)
                            except Exception as ee:
                                self._screen_fail_count[idx] = self._screen_fail_count.get(idx, 0) + 1
                                # 임계치 도달 직전까지만 warn — 그 후엔 auto-disable되어 시도 안 함
                                if self._screen_fail_count[idx] <= self._screen_fail_threshold:
                                    logger.warning(
                                        "ICAS HU scp %s failed (%d/%d): type=%s repr=%r",
                                        remote, self._screen_fail_count[idx],
                                        self._screen_fail_threshold,
                                        type(ee).__name__, ee,
                                    )
                                self._maybe_disable_screen(idx)
                except Exception as ee:
                    logger.warning(
                        "ICAS HU SCPClient failed: type=%s repr=%r",
                        type(ee).__name__, ee, exc_info=True,
                    )
                return files

            local_files: list[str] = []
            with self._ssh_lock:
                try:
                    ssh = self._get_shared_ssh()
                    local_files = _do_capture(ssh)
                except Exception as e:
                    logger.warning(
                        "ICAS HU capture failed on shared SSH, retrying: type=%s repr=%r",
                        type(e).__name__, e,
                    )
                    if self._ssh_client is not None:
                        try:
                            self._ssh_client.close()
                        except Exception:
                            pass
                        self._ssh_client = None
                    ssh = self._get_shared_ssh()
                    local_files = _do_capture(ssh)

            if not local_files:
                raise RuntimeError(
                    f"No HU screenshot captured (LayerManagerControl dump may have failed; "
                    f"check 'ICAS HU dump' / 'ICAS HU scp' / 'ICAS HU SCPClient' warnings above)"
                )

            # _validate_png_file이 1차로 거르지만, IDAT 내부 손상은 .convert에서야 드러남.
            # 손상된 파일은 무시하고 정상 파일만 사용. 모두 실패 시 RuntimeError로 외부 재시도.
            images: list[Image.Image] = []
            corrupt_paths: list[tuple[str, int, str]] = []
            for p in local_files:
                try:
                    img = Image.open(p)
                    img = img.convert("RGBA")
                    images.append(img)
                except Exception as ie:
                    sz = os.path.getsize(p) if os.path.exists(p) else -1
                    corrupt_paths.append((p, sz, f"{type(ie).__name__}: {ie!r}"))
            if not images:
                raise RuntimeError(
                    f"PIL Image.open failed for all captures (likely truncated PNG): {corrupt_paths}"
                )
            if corrupt_paths:
                logger.debug("ICAS HU partial composite — corrupt skipped: %s", corrupt_paths)
            base = images[0]
            for over in images[1:]:
                if over.size != base.size:
                    over = over.resize(base.size)
                base = Image.alpha_composite(base, over)
            # PNG 실제 크기 == 디바이스 실제 화면 해상도. 사용자가 잘못 입력한 경우 자동 보정.
            # _x_mult/_y_mult가 어긋나면 터치 좌표 인코딩이 깨지므로 캡처가 들어올 때마다 점검.
            try:
                self._maybe_autoupdate_resolution(int(base.size[0]), int(base.size[1]))
            except Exception as e:
                logger.debug("ICAS resolution auto-correct skipped: %s", e)
            return _encode_image(base, fmt)
        finally:
            _rm_tree(tmp_dir)

    # ------------------------------------------------------------------
    # IID/HUD screenshot — HU로 SSH → private server로 ssh hop → screenshot
    # ------------------------------------------------------------------
    def _screencap_iid_hud(self, display_number: str, fmt: str = "png") -> bytes:
        """ref RemoteController.IID_get_capture_path 이식.

        1) HU에 SSH로 2개 세션 연결 (하나는 private_server로 hop, 하나는 SCP 전용)
        2) hop 세션에서 `screenshot -display=N` 실행 → private server의 /tmp/screenshot.bmp 생성
        3) hop 세션에서 scp로 HU의 /tmp/screenshot.bmp로 가져옴
        4) SCP 세션으로 로컬에 pull
        5) BMP → PNG/JPEG 변환
        """
        import tempfile
        import os
        from PIL import Image, ImageFile
        ImageFile.LOAD_TRUNCATED_IMAGES = True

        if not self.private_server_ip:
            raise RuntimeError("ICAS IID/HUD capture: private_server_ip not configured")

        tmp_dir = tempfile.mkdtemp(prefix="icas_iid_")
        local_bmp = os.path.join(tmp_dir, "screenshot.bmp")
        try:
            # 방식: HU의 공유 SSH transport 위에 direct-tcpip 채널을 열어
            #        private_server:22로 터널링한 뒤, paramiko로 native SSH 로그인.
            #        이후 exec_command(+recv_exit_status)로 screenshot 실행,
            #        SFTP로 private_server:/tmp/screenshot.bmp → 로컬로 직접 pull.
            #
            # interactive shell-over-shell + scp password expect 방식은
            # 프롬프트 타이밍에 따라 자주 실패 → direct-tcpip으로 기초부터 제거.
            # paramiko SSH 클라이언트/터널은 _get_private_server_ssh에서 캐시하여 재사용.

            def _do_capture() -> None:
                # 공유 ps_ssh (direct-tcpip 터널 + SSH 인증 캐시됨) 재사용.
                # 죽어있으면 _get_private_server_ssh가 알아서 재연결.
                # _ps_lock으로 동시 호출 직렬화 — SFTP/exec_command 간섭 방지.
                with self._ps_lock:
                    with self._ssh_lock:
                        ps_ssh = self._get_private_server_ssh()
                    # private_server는 busybox 계열이라 bash가 없을 수 있음 → 기본 쉘 사용.
                    # PATH를 명시적으로 prepend + stale bmp 제거 + screenshot 실행.
                    cmd = (
                        "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:"
                        "/sbin:/bin:$PATH && "
                        "cd /tmp && rm -f /tmp/screenshot.bmp && "
                        f"screenshot -display={display_number}"
                    )
                    stdin, stdout, stderr = ps_ssh.exec_command(
                        cmd, timeout=30, get_pty=True,
                    )
                    try:
                        stdin.close()
                    except Exception:
                        pass
                    out_text = ""
                    err_text = ""
                    exit_status = -1
                    try:
                        stdout.channel.settimeout(30)
                        deadline = time.time() + 30.0
                        while time.time() < deadline:
                            if stdout.channel.exit_status_ready():
                                break
                            if stdout.channel.recv_ready():
                                try:
                                    out_text += stdout.channel.recv(4096).decode("utf-8", errors="replace")
                                except Exception:
                                    pass
                            elif stdout.channel.recv_stderr_ready():
                                try:
                                    err_text += stdout.channel.recv_stderr(4096).decode("utf-8", errors="replace")
                                except Exception:
                                    pass
                            else:
                                time.sleep(0.05)
                        while stdout.channel.recv_ready():
                            try:
                                out_text += stdout.channel.recv(4096).decode("utf-8", errors="replace")
                            except Exception:
                                break
                        while stdout.channel.recv_stderr_ready():
                            try:
                                err_text += stdout.channel.recv_stderr(4096).decode("utf-8", errors="replace")
                            except Exception:
                                break
                        exit_status = stdout.channel.recv_exit_status()
                    except Exception:
                        pass
                    finally:
                        for f in (stdout, stderr):
                            try:
                                f.close()
                            except Exception:
                                pass

                    # SFTP — ps_ssh 공유 transport 위에 subsystem 1개 열어 stat + get + remove
                    sftp = ps_ssh.open_sftp()
                    try:
                        st = None
                        file_deadline = time.time() + 5.0
                        while time.time() < file_deadline:
                            try:
                                candidate = sftp.stat("/tmp/screenshot.bmp")
                                if candidate.st_size > 0:
                                    st = candidate
                                    break
                            except IOError:
                                pass
                            time.sleep(0.2)
                        if st is None:
                            snippet = (out_text + err_text).strip().replace("\r", " ").replace("\n", " | ")
                            if len(snippet) > 240:
                                snippet = snippet[:240] + "..."
                            raise RuntimeError(
                                f"screenshot.bmp not produced on private_server "
                                f"(display={display_number}, exit_status={exit_status}, "
                                f"output={snippet!r})"
                            )
                        sftp.get("/tmp/screenshot.bmp", local_bmp)
                        try:
                            sftp.remove("/tmp/screenshot.bmp")
                        except Exception:
                            pass
                    finally:
                        try:
                            sftp.close()
                        except Exception:
                            pass

            # 1회 재시도 — transport 죽어있으면 공유 ps_ssh/HU 모두 리셋 후 재시도
            try:
                _do_capture()
            except Exception as e:
                logger.warning("ICAS IID/HUD capture via direct-tcpip failed, retrying: %s", e)
                # private_server 세션 먼저 버리고, HU 세션도 같이 리셋 (터널이 HU 위에 있음)
                self._close_private_server_ssh()
                with self._ssh_lock:
                    if self._ssh_client is not None:
                        try:
                            self._ssh_client.close()
                        except Exception:
                            pass
                        self._ssh_client = None
                    if self._ssh_shell is not None:
                        try:
                            self._ssh_shell.close()
                        except Exception:
                            pass
                        self._ssh_shell = None
                _do_capture()

            if not os.path.exists(local_bmp) or os.path.getsize(local_bmp) == 0:
                raise RuntimeError("IID/HUD screenshot transfer failed")

            img = Image.open(local_bmp).convert("RGBA")
            return _encode_image(img, fmt)
        finally:
            _rm_tree(tmp_dir)

    @staticmethod
    def _drain_until(shel, want: Optional[tuple[str, ...]] = None,
                     max_wait_s: float = 5.0, poll_s: float = 0.1) -> str:
        """shell의 수신 버퍼를 누적하면서 want 문자열 중 하나가 나올 때까지 대기.

        want가 None이면 수신이 조용해질 때(quiet period 0.3s)까지만 읽고 리턴.
        리턴값: 누적된 문자열 (마지막 4KB 정도). 타임아웃이어도 누적된 버퍼 반환.
        """
        deadline = time.time() + max_wait_s
        last_data = time.time()
        buf = ""
        while time.time() < deadline:
            got_chunk = False
            try:
                if shel.recv_ready():
                    chunk = shel.recv(65536)
                    if chunk:
                        buf += chunk.decode("utf-8", errors="replace")
                        got_chunk = True
                        last_data = time.time()
            except Exception:
                break
            # want 매칭 체크 — 최근 2KB 만 보면 충분
            if want:
                tail = buf[-2048:]
                for w in want:
                    if w in tail:
                        return buf
            else:
                # quiet period 기반 종료
                if not got_chunk and (time.time() - last_data) > 0.3:
                    return buf
            if not got_chunk:
                time.sleep(poll_s)
        return buf

    @classmethod
    def _wait_for_remote_file(cls, shel, path: str, max_wait_s: float = 8.0) -> bool:
        """원격 shell에서 `ls -la path`를 폴링해서 파일 존재 + size>0 을 확인."""
        deadline = time.time() + max_wait_s
        marker = "__ICAS_FILE_OK__"
        while time.time() < deadline:
            shel.send(f'if [ -s "{path}" ]; then echo {marker}; fi\n')
            buf = cls._drain_until(shel, want=(marker, "$", "#"), max_wait_s=1.5)
            if marker in buf:
                return True
            time.sleep(0.3)
        return False

    @staticmethod
    def _shell_send_recv(shel, data: str, delay: float = 0.3) -> Optional[str]:
        """paramiko invoke_shell에 문자열 1회 송신 후 수신 버퍼를 반환 (ref ssh_send/iid_send)."""
        try:
            shel.send(data + "\r\n")
        except Exception as e:
            logger.debug("ICAS shell send failed: %s", e)
            return None
        time.sleep(delay)
        if shel.recv_ready():
            try:
                return shel.recv(65536).decode("utf-8", errors="replace")
            except Exception:
                return None
        return None

    async def async_screencap_bytes(self, screen_type: str = "HU",
                                    fmt: str = "png", timeout: float = 15.0) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.screencap_bytes, screen_type, fmt, timeout
        )

    # ------------------------------------------------------------------
    # Async wrappers (HKMC6th API 호환)
    # ------------------------------------------------------------------
    async def async_tap(self, x: int, y: int, screen_type: str = "HU") -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.tap, x, y, screen_type)

    async def async_long_press(self, x: int, y: int, duration_ms: int = 3000,
                               screen_type: str = "HU") -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.long_press, x, y, duration_ms, screen_type)

    async def async_swipe(self, x1: int, y1: int, x2: int, y2: int,
                          screen_type: str = "HU", duration_ms: int = 300) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self.swipe, x1, y1, x2, y2, screen_type, duration_ms
        )

    async def async_repeat_tap(self, x: int, y: int, count: int = 5,
                               interval_ms: int = 100, screen_type: str = "HU") -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self.repeat_tap, x, y, count, interval_ms, screen_type
        )

    async def async_send_key_by_name(self, key_name: str, sub_cmd: int = SHORT_KEY,
                                     screen_type: Optional[str] = None,
                                     direction: Optional[int] = None) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self.send_key_by_name, key_name, sub_cmd, screen_type, direction
        )

    async def async_send_key(self, cmd: int, sub_cmd: int, key_data: int,
                             monitor: int = 0x00,
                             direction: Optional[int] = None) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self.send_key, cmd, sub_cmd, key_data, monitor, direction
        )

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------
    def get_info(self) -> dict:
        """HKMC6th.get_info()와 동형. IID/HUD 해상도는 캡처 시 실제 BMP 크기로 확정되므로
        초기값은 HU 해상도 기반으로 추정 (최초 캡처 전 프레임 렌더링용 기본치).
        """
        return {
            "host": self.host,
            "port": self.port,
            "connected": self._connected,
            "agent_version": self.agent_version,
            "screens": {
                "HU":  {"width": self._res_x, "height": self._res_y},
                "IID": {"width": self._res_x, "height": self._res_y},
                "HUD": {"width": self._res_x, "height": self._res_y},
            },
        }
