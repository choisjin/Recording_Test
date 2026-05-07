"""다중 캡처 소스(웹캠 N개 + 윈도우 N개)를 단일 캔버스에 합성하여 ffmpeg로 H.264 mp4 녹화.

WebcamService(싱글 카메라)는 그대로 두고, 사용자가 "합성 녹화" 모드를 켰을 때만
이 서비스가 활성화된다. 재생 중 _run_play_job이 compositor 모드 활성을 감지하면
WebcamService 대신 이 서비스의 cycle 녹화를 사용한다.

설계:
  - SourceCapture: 단일 소스 추상화 (get_latest_frame()로 BGR ndarray 반환)
    - WebcamCapture: cv2.VideoCapture 기반 백그라운드 스레드
    - WindowCapture: WinControlService.capture_hwnd_bgr를 polling
  - CompositorService:
    - 각 소스를 시작/정지
    - compose 스레드가 캔버스를 만들어 각 소스 프레임을 (crop → resize → place) 후
      ffmpeg pipe로 송출 + 최신 캔버스 JPEG 보관 (프리뷰용)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "tools"


def _find_ffmpeg() -> Optional[str]:
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = _TOOLS_DIR / "ffmpeg.exe"
    if local.is_file():
        return str(local)
    local_bin = _TOOLS_DIR / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local_bin.is_file():
        return str(local_bin)
    return None


class _FfmpegProc:
    """ffmpeg subprocess + stderr drain thread."""
    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._stderr_tail: list[bytes] = []
        self._stderr_lock = threading.Lock()
        self._drain_thread = threading.Thread(
            target=self._drain_stderr, daemon=True, name="compositor-ffmpeg-stderr",
        )
        self._drain_thread.start()

    def _drain_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        try:
            for line in iter(self.proc.stderr.readline, b""):
                if not line:
                    break
                with self._stderr_lock:
                    self._stderr_tail.append(line)
                    if len(self._stderr_tail) > 40:
                        self._stderr_tail.pop(0)
        except Exception:
            pass

    def stderr_tail(self) -> bytes:
        with self._stderr_lock:
            return b"".join(self._stderr_tail)[-600:]


def _spawn_ffmpeg_writer(output_path: Path, width: int, height: int, fps: float) -> Optional[_FfmpegProc]:
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        logger.warning("ffmpeg not found — compositor cannot encode H.264")
        return None
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    fps_val = fps if fps and fps > 0 else 30.0
    cmd = [
        ffmpeg, "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{int(width)}x{int(height)}",
        "-pix_fmt", "bgr24",
        "-r", f"{fps_val:.3f}",
        "-i", "-",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",
        str(output_path),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
            bufsize=0,
        )
        logger.info("Compositor ffmpeg spawned: %s (%dx%d @%.1ffps)", output_path, width, height, fps_val)
        return _FfmpegProc(proc)
    except Exception as e:
        logger.warning("Failed to spawn compositor ffmpeg: %s", e)
        return None


# ============================================================
# Source captures
# ============================================================
class _SourceBase:
    """공통 인터페이스. start/stop + get_latest_frame(BGR ndarray)."""
    def __init__(self, src_id: str, label: str = ""):
        self.id = src_id
        self.label = label
        self._latest: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        self._thread = None

    def get_latest_frame(self) -> Optional[np.ndarray]:
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    def _set_latest(self, frame: np.ndarray) -> None:
        with self._lock:
            self._latest = frame


class WebcamCapture(_SourceBase):
    """cv2.VideoCapture(DSHOW) 기반 백그라운드 캡처."""
    def __init__(self, src_id: str, device_index: int, capture_w: int = 0, capture_h: int = 0,
                 label: str = ""):
        super().__init__(src_id, label or f"Webcam {device_index}")
        self.device_index = int(device_index)
        self.capture_w = int(capture_w) if capture_w else 0
        self.capture_h = int(capture_h) if capture_h else 0
        self._cap: Optional[cv2.VideoCapture] = None
        self._actual_fps: float = 30.0

    def start(self) -> bool:
        cap = cv2.VideoCapture(self.device_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            logger.warning("Compositor: webcam %d open failed", self.device_index)
            return False
        if self.capture_w > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.capture_w)
        if self.capture_h > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.capture_h)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._actual_fps = float(fps) if fps > 0 else 30.0
        self._cap = cap
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"compositor-webcam-{self.device_index}",
        )
        self._thread.start()
        logger.info("Compositor source webcam %d opened (%.1ffps)", self.device_index, self._actual_fps)
        return True

    def _loop(self) -> None:
        interval = 1.0 / max(1.0, self._actual_fps)
        while not self._stop.is_set():
            cap = self._cap
            if cap is None or not cap.isOpened():
                time.sleep(0.05)
                continue
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.03)
                continue
            self._set_latest(frame)
            time.sleep(interval)

    def stop(self) -> None:
        super().stop()
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        with self._lock:
            self._latest = None


class WindowCapture(_SourceBase):
    """WinControlService.capture_hwnd_bgr를 polling.

    process_name + title_pattern 매칭 — 매 N프레임마다 hwnd 재탐색 (앱 재시작 대응).
    """
    def __init__(self, src_id: str, process_name: str = "", title_pattern: str = "",
                 hwnd: int = 0, capture_fps: float = 15.0, label: str = ""):
        super().__init__(src_id, label or process_name or "Window")
        self.process_name = process_name or ""
        self.title_pattern = title_pattern or ""
        self.preferred_hwnd = int(hwnd) if hwnd else 0
        self.capture_fps = float(capture_fps) if capture_fps and capture_fps > 0 else 15.0
        self._current_hwnd: int = 0
        self._last_resolve_ts: float = 0.0
        self._resolve_interval: float = 2.0  # 매 2초마다 hwnd 재확인

    def _resolve_hwnd(self) -> int:
        """preferred_hwnd 살아있으면 그대로, 아니면 process_name/title로 재탐색."""
        try:
            from .wincontrol_service import WinControlService
            import win32gui  # type: ignore
        except Exception:
            return 0
        if self.preferred_hwnd:
            try:
                if win32gui.IsWindow(self.preferred_hwnd):
                    return self.preferred_hwnd
            except Exception:
                pass
        helper = WinControlService()
        match = helper.find_window(
            process_name=self.process_name,
            title_pattern=self.title_pattern,
        )
        if match:
            return int(match["hwnd"])
        return 0

    def start(self) -> bool:
        # 초기 hwnd 결정 — 못 찾아도 일단 thread 띄움 (런타임에 해당 앱 실행될 수 있음)
        self._current_hwnd = self._resolve_hwnd()
        self._last_resolve_ts = time.monotonic()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name=f"compositor-window-{self.id}",
        )
        self._thread.start()
        logger.info("Compositor source window %r started (initial hwnd=%s)", self.process_name, self._current_hwnd)
        return True

    def _loop(self) -> None:
        from .wincontrol_service import WinControlService
        interval = 1.0 / max(1.0, self.capture_fps)
        while not self._stop.is_set():
            now = time.monotonic()
            if now - self._last_resolve_ts > self._resolve_interval:
                # hwnd 살아있는지 검사 — 죽었으면 재탐색
                self._current_hwnd = self._resolve_hwnd()
                self._last_resolve_ts = now
            hwnd = self._current_hwnd
            if hwnd:
                try:
                    img = WinControlService.capture_hwnd_bgr(hwnd)
                    if img is not None:
                        self._set_latest(img)
                except Exception as e:
                    logger.debug("Compositor window capture failed: %s", e)
            time.sleep(interval)


# ============================================================
# Compositor service
# ============================================================
class CompositorService:
    """다중 소스 합성 + 녹화 싱글톤."""

    def __init__(self) -> None:
        self._sources: list[_SourceBase] = []
        # 각 소스에 매핑되는 layout 정보 (캔버스 좌표계)
        # {id: {"x":..,"y":..,"w":..,"h":..,"crop":{"x","y","w","h"}|None,"z":int,"opacity":float,
        #        "label":str}}
        self._layout: dict[str, dict] = {}
        self._canvas_w: int = 1280
        self._canvas_h: int = 720
        self._fps: float = 30.0
        self._bg_bgr: tuple[int, int, int] = (0, 0, 0)
        self._show_labels: bool = True
        self._show_timestamp: bool = True

        self._compose_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._latest_canvas: Optional[np.ndarray] = None
        self._latest_canvas_lock = threading.Lock()

        self._ffmpeg_proc: Optional[_FfmpegProc] = None
        self._cv_writer: Optional[cv2.VideoWriter] = None
        self._recording_path: Optional[Path] = None
        self._recording_paused = False
        self._recording_lock = threading.Lock()
        self._record_start_ts: float = 0.0
        self._frames_written: int = 0

    # ------------------------------------------------------------
    # Layout config
    # ------------------------------------------------------------
    def configure(self, layout: dict) -> None:
        """레이아웃 설정. 이미 capture 중이면 stop 후 재시작 필요 (호출자 책임)."""
        canvas = layout.get("canvas") or {}
        self._canvas_w = max(2, int(canvas.get("width") or 1280))
        self._canvas_h = max(2, int(canvas.get("height") or 720))
        self._fps = float(canvas.get("fps") or 30.0)
        bg_hex = canvas.get("background") or "#000000"
        self._bg_bgr = self._hex_to_bgr(bg_hex)
        self._show_labels = bool(canvas.get("show_labels", True))
        self._show_timestamp = bool(canvas.get("show_timestamp", True))

        # 새 sources 정의
        new_sources: list[_SourceBase] = []
        new_layout: dict[str, dict] = {}
        for idx, item in enumerate(layout.get("sources") or []):
            src_id = str(item.get("id") or f"src_{idx}")
            stype = (item.get("type") or "").lower()
            label = str(item.get("label") or "")
            if stype == "webcam":
                src = WebcamCapture(
                    src_id=src_id,
                    device_index=int(item.get("device_index") or 0),
                    capture_w=int(item.get("capture_width") or 0),
                    capture_h=int(item.get("capture_height") or 0),
                    label=label,
                )
                new_sources.append(src)
            elif stype == "window":
                src = WindowCapture(
                    src_id=src_id,
                    process_name=str(item.get("process_name") or ""),
                    title_pattern=str(item.get("title_pattern") or ""),
                    hwnd=int(item.get("hwnd") or 0),
                    capture_fps=float(item.get("capture_fps") or 15.0),
                    label=label,
                )
                new_sources.append(src)
            else:
                logger.warning("Compositor: unknown source type %r — skip", stype)
                continue
            crop = item.get("crop") or None
            if crop:
                crop_norm = {
                    "x": int(crop.get("x") or 0),
                    "y": int(crop.get("y") or 0),
                    "w": int(crop.get("w") or crop.get("width") or 0),
                    "h": int(crop.get("h") or crop.get("height") or 0),
                }
                if crop_norm["w"] <= 0 or crop_norm["h"] <= 0:
                    crop_norm = None  # 무효 → full
            else:
                crop_norm = None
            new_layout[src_id] = {
                "x": int(item.get("x") or 0),
                "y": int(item.get("y") or 0),
                "w": int(item.get("width") or 320),
                "h": int(item.get("height") or 240),
                "crop": crop_norm,
                "z": int(item.get("z_order") or 0),
                "opacity": float(item.get("opacity") if item.get("opacity") is not None else 1.0),
                "label": label,
            }
        self._sources = new_sources
        self._layout = new_layout

    def get_layout(self) -> dict:
        sources_out = []
        for src in self._sources:
            l = self._layout.get(src.id, {})
            common = {
                "id": src.id,
                "label": l.get("label") or src.label,
                "x": l.get("x", 0), "y": l.get("y", 0),
                "width": l.get("w", 320), "height": l.get("h", 240),
                "crop": l.get("crop"),
                "z_order": l.get("z", 0),
                "opacity": l.get("opacity", 1.0),
            }
            if isinstance(src, WebcamCapture):
                common.update({
                    "type": "webcam",
                    "device_index": src.device_index,
                    "capture_width": src.capture_w,
                    "capture_height": src.capture_h,
                })
            elif isinstance(src, WindowCapture):
                common.update({
                    "type": "window",
                    "process_name": src.process_name,
                    "title_pattern": src.title_pattern,
                    "hwnd": src.preferred_hwnd,
                    "capture_fps": src.capture_fps,
                })
            sources_out.append(common)
        return {
            "canvas": {
                "width": self._canvas_w,
                "height": self._canvas_h,
                "fps": self._fps,
                "background": "#%02x%02x%02x" % (self._bg_bgr[2], self._bg_bgr[1], self._bg_bgr[0]),
                "show_labels": self._show_labels,
                "show_timestamp": self._show_timestamp,
            },
            "sources": sources_out,
        }

    @staticmethod
    def _hex_to_bgr(color_hex: str) -> tuple[int, int, int]:
        s = (color_hex or "").lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        try:
            r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16)
            return (b, g, r)
        except Exception:
            return (0, 0, 0)

    # ------------------------------------------------------------
    # Capture lifecycle
    # ------------------------------------------------------------
    def is_capturing(self) -> bool:
        return self._compose_thread is not None and self._compose_thread.is_alive()

    def start_capture(self) -> dict:
        """모든 소스 + compose 스레드 시작. 이미 실행 중이면 stop 후 재시작."""
        self.stop_capture()
        opened: list[str] = []
        failed: list[str] = []
        for src in self._sources:
            try:
                if src.start():
                    opened.append(src.id)
                else:
                    failed.append(src.id)
            except Exception as e:
                logger.warning("Compositor source %s start failed: %s", src.id, e)
                failed.append(src.id)
        self._stop_flag.clear()
        self._compose_thread = threading.Thread(
            target=self._compose_loop, daemon=True, name="compositor-compose",
        )
        self._compose_thread.start()
        return {"opened": opened, "failed": failed}

    def stop_capture(self) -> None:
        # 녹화 중이면 먼저 정지
        self.stop_recording()
        self._stop_flag.set()
        t = self._compose_thread
        if t and t.is_alive():
            try:
                t.join(timeout=2.0)
            except Exception:
                pass
        self._compose_thread = None
        for src in self._sources:
            try:
                src.stop()
            except Exception:
                pass
        with self._latest_canvas_lock:
            self._latest_canvas = None

    # ------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------
    def _compose_loop(self) -> None:
        interval = 1.0 / max(1.0, self._fps)
        next_deadline = time.monotonic()
        while not self._stop_flag.is_set():
            try:
                canvas = self._build_canvas()
                with self._latest_canvas_lock:
                    self._latest_canvas = canvas
                with self._recording_lock:
                    if (self._ffmpeg_proc is not None or self._cv_writer is not None) and not self._recording_paused:
                        self._write_frame_unlocked(canvas)
            except Exception as e:
                logger.debug("Compositor compose error: %s", e)
            now = time.monotonic()
            next_deadline += interval
            sleep_s = next_deadline - now
            if sleep_s > 0:
                time.sleep(sleep_s)
            else:
                next_deadline = now

    def _build_canvas(self) -> np.ndarray:
        canvas = np.zeros((self._canvas_h, self._canvas_w, 3), dtype=np.uint8)
        if self._bg_bgr != (0, 0, 0):
            canvas[:] = self._bg_bgr
        # z-order 정렬
        ordered = sorted(
            self._sources,
            key=lambda s: self._layout.get(s.id, {}).get("z", 0),
        )
        for src in ordered:
            l = self._layout.get(src.id)
            if not l:
                continue
            frame = src.get_latest_frame()
            if frame is None:
                # placeholder: 회색 박스 + 라벨
                self._draw_placeholder(canvas, src, l)
                continue
            self._blit_source(canvas, frame, l, src.label)
        if self._show_timestamp:
            self._draw_timestamp(canvas)
        return canvas

    def _blit_source(self, canvas: np.ndarray, frame: np.ndarray, l: dict, label: str) -> None:
        try:
            # 1) crop
            crop = l.get("crop")
            if crop and frame.shape[1] > 0 and frame.shape[0] > 0:
                cx = max(0, min(int(crop["x"]), frame.shape[1] - 1))
                cy = max(0, min(int(crop["y"]), frame.shape[0] - 1))
                cw = max(1, min(int(crop["w"]), frame.shape[1] - cx))
                ch = max(1, min(int(crop["h"]), frame.shape[0] - cy))
                frame = frame[cy:cy + ch, cx:cx + cw]
            # 2) 채널 정규화 (BGRA/grayscale → BGR)
            if frame.ndim == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            # 3) resize to dest
            dx = int(l["x"]); dy = int(l["y"])
            dw = max(1, int(l["w"])); dh = max(1, int(l["h"]))
            # 캔버스 경계로 클리핑
            cw = self._canvas_w; chh = self._canvas_h
            if dx >= cw or dy >= chh:
                return
            if dx + dw > cw:
                dw = cw - dx
            if dy + dh > chh:
                dh = chh - dy
            if dw <= 0 or dh <= 0:
                return
            resized = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_AREA)
            opacity = float(l.get("opacity", 1.0))
            if opacity >= 1.0 - 1e-3:
                canvas[dy:dy + dh, dx:dx + dw] = resized
            else:
                roi = canvas[dy:dy + dh, dx:dx + dw]
                cv2.addWeighted(resized, opacity, roi, 1.0 - opacity, 0, roi)
            # 4) 라벨
            if self._show_labels and (label or l.get("label")):
                text = label or l.get("label") or ""
                self._draw_label(canvas, dx, dy, dw, dh, text)
        except Exception as e:
            logger.debug("Compositor blit failed (%s): %s", label, e)

    def _draw_placeholder(self, canvas: np.ndarray, src: _SourceBase, l: dict) -> None:
        try:
            dx = int(l["x"]); dy = int(l["y"])
            dw = max(1, int(l["w"])); dh = max(1, int(l["h"]))
            cw = self._canvas_w; chh = self._canvas_h
            if dx >= cw or dy >= chh:
                return
            if dx + dw > cw:
                dw = cw - dx
            if dy + dh > chh:
                dh = chh - dy
            if dw <= 0 or dh <= 0:
                return
            cv2.rectangle(canvas, (dx, dy), (dx + dw - 1, dy + dh - 1), (60, 60, 60), -1)
            cv2.rectangle(canvas, (dx, dy), (dx + dw - 1, dy + dh - 1), (120, 120, 120), 2)
            text = f"[no signal] {l.get('label') or src.label}"
            font = cv2.FONT_HERSHEY_SIMPLEX
            scale = max(0.4, min(dw, dh) * 0.0035)
            cv2.putText(canvas, text, (dx + 8, dy + 24),
                        font, scale, (200, 200, 200), 1, cv2.LINE_AA)
        except Exception:
            pass

    def _draw_label(self, canvas: np.ndarray, dx: int, dy: int, dw: int, dh: int, text: str) -> None:
        if not text:
            return
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = max(0.4, min(dw, dh) * 0.0028)
        thickness = max(1, int(scale * 1.6))
        (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
        pad = 4
        bx = dx + 4
        by = dy + 4
        bw = tw + pad * 2
        bh = th + pad * 2
        # 반투명 박스
        sub = canvas[by:by + bh, bx:bx + bw]
        if sub.shape[0] == bh and sub.shape[1] == bw:
            black = np.zeros_like(sub)
            cv2.addWeighted(black, 0.5, sub, 0.5, 0, sub)
        cv2.putText(canvas, text, (bx + pad, by + pad + th),
                    font, scale, (255, 255, 255), thickness, cv2.LINE_AA)

    def _draw_timestamp(self, canvas: np.ndarray) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        h, w = canvas.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = max(0.5, h * 0.0014)
        thickness = max(1, int(scale * 2))
        (tw, th), _ = cv2.getTextSize(ts, font, scale, thickness)
        pad = 5
        bx = w - tw - pad * 2 - 8
        by = 8
        bw = tw + pad * 2
        bh = th + pad * 2
        bx = max(0, bx)
        sub = canvas[by:by + bh, bx:bx + bw]
        if sub.shape[0] == bh and sub.shape[1] == bw:
            black = np.zeros_like(sub)
            cv2.addWeighted(black, 0.5, sub, 0.5, 0, sub)
        cv2.putText(canvas, ts, (bx + pad, by + pad + th),
                    font, scale, (255, 255, 255), thickness, cv2.LINE_AA)

    # ------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------
    def get_latest_jpeg(self, quality: int = 75) -> Optional[bytes]:
        with self._latest_canvas_lock:
            canvas = self._latest_canvas
            if canvas is None:
                return None
            copy = canvas.copy()
        ok, buf = cv2.imencode(".jpg", copy, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        if not ok:
            return None
        return buf.tobytes()

    # ------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------
    def start_recording(self, output_path: str | Path) -> bool:
        with self._recording_lock:
            if self._ffmpeg_proc is not None or self._cv_writer is not None:
                logger.warning("Compositor recording already in progress")
                return False
            if not self.is_capturing():
                logger.warning("Compositor not capturing — cannot start recording")
                return False
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            proc = _spawn_ffmpeg_writer(path, self._canvas_w, self._canvas_h, self._fps)
            if proc is not None:
                self._ffmpeg_proc = proc
            else:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(path), fourcc, self._fps, (self._canvas_w, self._canvas_h))
                if not writer.isOpened():
                    logger.error("Compositor: failed to open VideoWriter %s", path)
                    return False
                self._cv_writer = writer
            self._recording_path = path
            self._recording_paused = False
            self._record_start_ts = time.monotonic()
            self._frames_written = 0
            logger.info("Compositor recording started: %s (%dx%d @%.1ffps mode=%s)",
                        path, self._canvas_w, self._canvas_h, self._fps,
                        "ffmpeg-h264" if self._ffmpeg_proc else "cv2-mp4v")
            return True

    def stop_recording(self) -> Optional[str]:
        with self._recording_lock:
            if self._ffmpeg_proc is None and self._cv_writer is None:
                return None
            path = self._recording_path
            duration = time.monotonic() - self._record_start_ts
            frames = self._frames_written
            proc = self._ffmpeg_proc
            cv_writer = self._cv_writer
            self._ffmpeg_proc = None
            self._cv_writer = None
            self._recording_path = None
            self._recording_paused = False
        if proc is not None:
            sp = proc.proc
            try:
                if sp.stdin and not sp.stdin.closed:
                    try: sp.stdin.flush()
                    except Exception: pass
                    try: sp.stdin.close()
                    except Exception: pass
                rc = sp.wait(timeout=60)
                if rc != 0:
                    logger.warning("Compositor ffmpeg rc=%d: %s", rc,
                                   proc.stderr_tail().decode(errors="replace"))
            except subprocess.TimeoutExpired:
                logger.warning("Compositor ffmpeg flush timeout — kill")
                try:
                    sp.kill(); sp.wait(timeout=3)
                except Exception:
                    pass
            except Exception as e:
                logger.warning("Compositor ffmpeg stop error: %s", e)
        if cv_writer is not None:
            try: cv_writer.release()
            except Exception: pass
        logger.info("Compositor recording stopped: %s frames=%d duration=%.1fs", path, frames, duration)
        return str(path) if path else None

    def pause_recording(self) -> None:
        with self._recording_lock:
            if self._ffmpeg_proc is not None or self._cv_writer is not None:
                self._recording_paused = True

    def resume_recording(self) -> None:
        with self._recording_lock:
            if self._ffmpeg_proc is not None or self._cv_writer is not None:
                self._recording_paused = False

    def is_recording(self) -> bool:
        with self._recording_lock:
            return self._ffmpeg_proc is not None or self._cv_writer is not None

    def _write_frame_unlocked(self, canvas: np.ndarray) -> None:
        if self._ffmpeg_proc is None and self._cv_writer is None:
            return
        # 캔버스 크기는 항상 self._canvas_w/h와 동일하지만 안전망으로 한 번 더 검증
        if canvas.shape[0] != self._canvas_h or canvas.shape[1] != self._canvas_w:
            canvas = cv2.resize(canvas, (self._canvas_w, self._canvas_h))
        if not canvas.flags["C_CONTIGUOUS"]:
            canvas = np.ascontiguousarray(canvas)
        if self._ffmpeg_proc is not None:
            sp = self._ffmpeg_proc.proc
            if sp.stdin is not None:
                try:
                    sp.stdin.write(canvas.tobytes())
                    self._frames_written += 1
                except (BrokenPipeError, OSError) as e:
                    logger.warning("Compositor ffmpeg pipe write failed: %s — abort (stderr: %s)",
                                   e, self._ffmpeg_proc.stderr_tail().decode(errors="replace"))
                    try:
                        sp.kill()
                    except Exception:
                        pass
                    self._ffmpeg_proc = None
                except Exception as e:
                    logger.warning("Compositor write error: %s", e)
        elif self._cv_writer is not None:
            try:
                self._cv_writer.write(canvas)
                self._frames_written += 1
            except Exception as e:
                logger.warning("Compositor cv writer error: %s", e)

    # ------------------------------------------------------------
    # Status
    # ------------------------------------------------------------
    def status(self) -> dict:
        with self._recording_lock:
            recording = self._ffmpeg_proc is not None or self._cv_writer is not None
            mode = "ffmpeg-h264" if self._ffmpeg_proc is not None else (
                "cv2-mp4v" if self._cv_writer is not None else "")
            rec_path = str(self._recording_path) if self._recording_path else ""
            duration = time.monotonic() - self._record_start_ts if recording else 0.0
            frames = self._frames_written
        return {
            "capturing": self.is_capturing(),
            "recording": recording,
            "recording_mode": mode,
            "recording_path": rec_path,
            "recording_duration_s": duration,
            "frames_written": frames,
            "canvas_width": self._canvas_w,
            "canvas_height": self._canvas_h,
            "fps": self._fps,
            "source_count": len(self._sources),
        }


# Singleton
_compositor_service: Optional[CompositorService] = None


def get_compositor_service() -> CompositorService:
    global _compositor_service
    if _compositor_service is None:
        _compositor_service = CompositorService()
    return _compositor_service
