#!/usr/bin/env python3
"""HKMC MJPEG Streaming Server — HKMC IVI 디바이스에서 실행.

디바이스의 HKMC 에이전트(localhost:6655)에서 BMP 스크린샷을 캡처하고,
JPEG으로 변환하여 TCP 스트림으로 전송한다.

사용법 (디바이스 SSH에서):
    python3 /tmp/hkmc_streamer.py [--port 9998] [--agent-port 6655] [--quality 70]

PC에서 수신:
    nc 192.168.105.100 9998 > test_stream.bin

프레임 프로토콜:
    [4바이트 JPEG 크기 big-endian] [JPEG 데이터] [4바이트 크기] [JPEG 데이터] ...
"""

import argparse
import io
import logging
import os
import socket
import struct
import sys
import threading
import time

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("hkmc_streamer")

# ---------------------------------------------------------------------------
# HKMC 프로토콜 상수
# ---------------------------------------------------------------------------
START_BIT = 0x61
END_BIT = 0x6F
CMD_GETIMG = 0x6A
CMD_ATSA_GETVERSION = 0xA0
CMD_ATSA_GETSCREENWIDTHHEIGHT = 0xA3
NOTI_CONNECTED = 0x5E

SCREEN_CAPTURE_MAP = {
    "front_center": 8,
    "cluster": 1,
    "rear_left": 32,
    "rear_right": 128,
}

DEFAULT_SCREEN_SIZE = (1920, 720)

HANDSHAKE_VALUES = {
    "6161000000035e002185fd6f6f",
    "6161000000035e0000df856f6f",
}


def calc_crc16(data: list) -> int:
    crc = 0xFFFF
    key = 0xC659
    for b in data:
        tmp = (b & 0xFF) ^ (crc & 0x00FF)
        for _ in range(8):
            if tmp & 1:
                tmp = (tmp >> 1) ^ key
            else:
                tmp >>= 1
        crc = (crc >> 8) ^ tmp
    return crc


def build_packet(cmd: int, sub_cmd: int, resp: int, data: list) -> bytes:
    agent_cmd = [cmd, sub_cmd, resp] + data
    crc = calc_crc16(agent_cmd)
    plen = len(agent_cmd)
    packet = [START_BIT, START_BIT]
    packet.append((plen >> 24) & 0xFF)
    packet.append((plen >> 16) & 0xFF)
    packet.append((plen >> 8) & 0xFF)
    packet.append(plen & 0xFF)
    packet.extend(agent_cmd)
    packet.append((crc >> 8) & 0xFF)
    packet.append(crc & 0xFF)
    packet.append(END_BIT)
    packet.append(END_BIT)
    return bytes(packet)


# ---------------------------------------------------------------------------
# HKMC Agent 클라이언트
# ---------------------------------------------------------------------------
class HKMCCapture:
    """HKMC 에이전트에서 BMP 스크린샷을 캡처한다."""

    def __init__(self, host: str = "127.0.0.1", port: int = 6655):
        self.host = host
        self.port = port
        self._sock: socket.socket = None
        self._connected = False
        self._recv_thread: threading.Thread = None
        self._exit = False

        # 수신 상태
        self._recv_complete = True
        self._recv_packet_len = 0
        self._recv_data = b""

        # 이미지 수신
        self._img_event = threading.Event()
        self._img_data: bytes = b""

        # 화면 크기
        self._screen_event = threading.Event()
        self.screen_w, self.screen_h = DEFAULT_SCREEN_SIZE

    def connect(self, timeout: float = 10.0) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock.connect((self.host, self.port))
        except Exception as e:
            logger.error("연결 실패: %s", e)
            self._sock = None
            return False

        # 핸드셰이크 대기
        deadline = time.time() + timeout
        while not self._connected and time.time() < deadline:
            try:
                raw = self._sock.recv(13)
                if raw and raw.hex() in HANDSHAKE_VALUES:
                    self._connected = True
                    logger.info("HKMC 에이전트 연결 성공: %s:%d", self.host, self.port)
                else:
                    logger.warning("잘못된 핸드셰이크: %s", raw.hex() if raw else "empty")
                    break
            except socket.error:
                break

        if not self._connected:
            self._sock = None
            return False

        # 수신 스레드 시작
        self._exit = False
        self._recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._recv_thread.start()

        # 버전 및 화면 크기 요청
        self._send(build_packet(CMD_ATSA_GETVERSION, 0, 0, []))
        self._screen_event.clear()
        self._send(build_packet(CMD_ATSA_GETSCREENWIDTHHEIGHT, 0, 0, []))
        self._screen_event.wait(timeout=5)

        logger.info("화면 크기: %dx%d", self.screen_w, self.screen_h)
        return True

    def disconnect(self):
        self._exit = True
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        self._connected = False

    def capture_bmp(self, screen_type: str = "front_center", timeout: float = 10.0) -> bytes:
        """BMP 스크린샷 캡처. 반환: BMP 바이트."""
        w, h = self.screen_w, self.screen_h
        if w == 0 or h == 0:
            w, h = DEFAULT_SCREEN_SIZE

        screen_bits = SCREEN_CAPTURE_MAP.get(screen_type)

        data = []
        for v in (0, 0, w, h):  # left, top, right, bottom
            data.append((v >> 8) & 0xFF)
            data.append(v & 0xFF)
        if screen_bits is not None:
            data.append((screen_bits >> 8) & 0xFF)
            data.append(screen_bits & 0xFF)

        self._img_data = b""
        self._img_event.clear()
        self._send(build_packet(CMD_GETIMG, 0, 0, data))

        if not self._img_event.wait(timeout=timeout):
            logger.warning("스크린샷 타임아웃")
            return b""
        return self._img_data

    def _send(self, data: bytes):
        if self._sock:
            try:
                self._sock.send(data)
            except socket.error as e:
                logger.error("전송 오류: %s", e)

    def _receive_loop(self):
        buf = b""
        while not self._exit:
            try:
                chunk = self._sock.recv(65536)
                if not chunk:
                    break
                buf += chunk

                # 패킷 파싱
                while len(buf) >= 6:
                    if buf[0] != START_BIT or buf[1] != START_BIT:
                        # 동기 복구: START_BIT 쌍 찾기
                        idx = buf.find(bytes([START_BIT, START_BIT]), 1)
                        if idx == -1:
                            buf = b""
                            break
                        buf = buf[idx:]
                        continue

                    plen = struct.unpack(">I", buf[2:6])[0]
                    total = 6 + plen + 4  # header + payload + crc(2) + end(2)
                    if len(buf) < total:
                        break  # 더 많은 데이터 필요

                    packet = buf[:total]
                    buf = buf[total:]
                    self._process_packet(packet, plen)

            except (socket.error, OSError):
                if not self._exit:
                    logger.error("수신 스레드 소켓 에러")
                break

        logger.info("수신 스레드 종료")

    def _process_packet(self, packet: bytes, plen: int):
        cmd = packet[6]

        if cmd == NOTI_CONNECTED:
            self._connected = True

        elif cmd == CMD_ATSA_GETVERSION:
            ver = packet[9:9 + plen - 3].decode("iso-8859-1", errors="replace")
            logger.info("에이전트 버전: %s", ver)

        elif cmd == CMD_ATSA_GETSCREENWIDTHHEIGHT:
            data = packet[9:9 + plen - 3]
            if len(data) >= 8:
                self.screen_w = struct.unpack(">I", data[0:4])[0]
                self.screen_h = struct.unpack(">I", data[4:8])[0]
            self._screen_event.set()

        elif cmd == CMD_GETIMG:
            self._img_data = packet[9:9 + plen - 3]
            self._img_event.set()


# ---------------------------------------------------------------------------
# JPEG 변환
# ---------------------------------------------------------------------------
def _detect_format(data: bytes) -> str:
    """이미지 포맷 감지."""
    if data[:2] == b'\xff\xd8':
        return "jpeg"
    if data[:2] == b'BM':
        return "bmp"
    if data[:4] == b'\x89PNG':
        return "png"
    return "unknown"


def _bmp_to_raw_bgr(bmp_data: bytes) -> tuple:
    """순수 Python으로 BMP 파싱 → (raw_bgr_bytes, width, height)."""
    if len(bmp_data) < 54 or bmp_data[:2] != b'BM':
        return b"", 0, 0

    data_offset = struct.unpack_from('<I', bmp_data, 10)[0]
    width = struct.unpack_from('<i', bmp_data, 18)[0]
    height = struct.unpack_from('<i', bmp_data, 22)[0]
    bpp = struct.unpack_from('<H', bmp_data, 28)[0]

    if bpp not in (24, 32):
        logger.warning("지원하지 않는 BPP: %d", bpp)
        return b"", 0, 0

    bytes_per_pixel = bpp // 8
    abs_height = abs(height)
    row_stride = (width * bytes_per_pixel + 3) & ~3  # 4바이트 정렬

    pixel_data = bmp_data[data_offset:]
    rows = []
    for y in range(abs_height):
        row_start = y * row_stride
        rows.append(pixel_data[row_start:row_start + width * bytes_per_pixel])

    # BMP는 bottom-up (height > 0)이므로 뒤집기
    if height > 0:
        rows.reverse()

    raw = b''.join(rows)
    return raw, width, abs_height


def bmp_to_jpeg(bmp_data: bytes, quality: int = 70) -> bytes:
    """BMP 바이트를 JPEG로 변환. PIL → cv2 → GStreamer CLI 순으로 시도."""
    # 이미 JPEG면 그대로 반환
    fmt = _detect_format(bmp_data)
    if fmt == "jpeg":
        return bmp_data

    # PIL 시도
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(bmp_data))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue()
    except (ImportError, Exception):
        pass

    # cv2 시도
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(bmp_data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
            return buf.tobytes()
    except (ImportError, Exception):
        pass

    # GStreamer CLI: BMP → raw BGR 파싱 후 gst-launch-1.0으로 JPEG 인코딩
    if fmt == "bmp":
        return _bmp_to_jpeg_gstreamer(bmp_data, quality)

    logger.error("JPEG 변환 실패: 포맷=%s, 크기=%d", fmt, len(bmp_data))
    return b""


def _bmp_to_jpeg_gstreamer(bmp_data: bytes, quality: int = 70) -> bytes:
    """순수 Python BMP 파싱 + gst-launch-1.0 subprocess로 JPEG 인코딩."""
    import subprocess

    raw, w, h = _bmp_to_raw_bgr(bmp_data)
    if not raw or w == 0 or h == 0:
        return b""

    bpp = len(raw) // (w * h)
    fmt = "BGR" if bpp == 3 else "BGRx"

    try:
        proc = subprocess.run(
            ['gst-launch-1.0', '-q',
             'fdsrc', 'blocksize=' + str(len(raw)), '!',
             'video/x-raw,format={},width={},height={},framerate=0/1'.format(fmt, w, h), '!',
             'videoconvert', '!',
             'jpegenc', 'quality={}'.format(quality), '!',
             'fdsink'],
            input=raw,
            capture_output=True,
            timeout=5,
        )
        if proc.stdout and len(proc.stdout) > 100:
            return proc.stdout
        if proc.stderr:
            logger.warning("GStreamer stderr: %s", proc.stderr.decode(errors='replace')[:200])
    except Exception as e:
        logger.error("GStreamer 인코딩 실패: %s", e)

    return b""


# ---------------------------------------------------------------------------
# TCP 스트리밍 서버
# ---------------------------------------------------------------------------
class MJPEGServer:
    """JPEG 프레임을 연결된 클라이언트들에게 TCP 스트리밍."""

    def __init__(self, port: int = 9998):
        self.port = port
        self._server: socket.socket = None
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("0.0.0.0", self.port))
        self._server.listen(5)
        logger.info("MJPEG 서버 시작: 포트 %d", self.port)

        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        accept_thread.start()

    def _accept_loop(self):
        while True:
            try:
                client, addr = self._server.accept()
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock:
                    self._clients.append(client)
                logger.info("클라이언트 연결: %s", addr)
            except Exception:
                break

    def broadcast_frame(self, jpeg_data: bytes):
        """JPEG 프레임을 모든 클라이언트에게 전송."""
        header = struct.pack(">I", len(jpeg_data))
        frame = header + jpeg_data

        with self._lock:
            dead = []
            for c in self._clients:
                try:
                    c.sendall(frame)
                except Exception:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)
                try:
                    c.close()
                except Exception:
                    pass

    def stop(self):
        with self._lock:
            for c in self._clients:
                try:
                    c.close()
                except Exception:
                    pass
            self._clients.clear()
        if self._server:
            self._server.close()


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="HKMC MJPEG 스트리밍 서버")
    parser.add_argument("--agent-host", default="127.0.0.1", help="HKMC 에이전트 호스트")
    parser.add_argument("--agent-port", type=int, default=6655, help="HKMC 에이전트 포트")
    parser.add_argument("--port", type=int, default=9998, help="MJPEG 스트리밍 포트")
    parser.add_argument("--quality", type=int, default=70, help="JPEG 품질 (1-100)")
    parser.add_argument("--screen", default="front_center", help="화면 타입")
    args = parser.parse_args()

    # HKMC 에이전트 연결
    capture = HKMCCapture(args.agent_host, args.agent_port)
    if not capture.connect():
        logger.error("HKMC 에이전트 연결 실패")
        sys.exit(1)

    # MJPEG 서버 시작
    server = MJPEGServer(args.port)
    server.start()

    # 캡처 루프
    frame_count = 0
    fps_start = time.time()
    try:
        while True:
            bmp = capture.capture_bmp(screen_type=args.screen)
            if not bmp:
                time.sleep(0.1)
                continue

            jpeg = bmp_to_jpeg(bmp, quality=args.quality)
            if not jpeg:
                time.sleep(0.1)
                continue

            server.broadcast_frame(jpeg)
            frame_count += 1

            # FPS 로그 (10초마다)
            elapsed = time.time() - fps_start
            if elapsed >= 10:
                logger.info("FPS: %.1f (프레임 크기: %.1f KB)",
                            frame_count / elapsed, len(jpeg) / 1024)
                frame_count = 0
                fps_start = time.time()

    except KeyboardInterrupt:
        logger.info("중지")
    finally:
        server.stop()
        capture.disconnect()


if __name__ == "__main__":
    main()
