"""ICAS HU LayerManagerControl 진단 도구.

사용 목적:
- screen 0 / screen 2가 어떤 콘텐츠를 그리는지(홈 vs 맵 등) 확인
- 화면 합성 구조 점검 (screens / layers / surfaces)
- 캡처가 가능한 screen 번호 탐색

사용 예:
    python scripts/diag_icas_layers.py 192.168.1.4
    python scripts/diag_icas_layers.py 192.168.1.4 --port 22 --user root
    python scripts/diag_icas_layers.py 192.168.1.4 --capture-all  # screen 0~5 dump 시도

캡처된 PNG는 ./icas_diag_<timestamp>/ 폴더로 저장.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:
    print("[ERROR] paramiko required: pip install paramiko", file=sys.stderr)
    sys.exit(1)

try:
    from scp import SCPClient
except ImportError:
    print("[ERROR] scp required: pip install scp", file=sys.stderr)
    sys.exit(1)


WESTON_ENV = "export XDG_RUNTIME_DIR=/run/platform/weston"


def run(ssh: paramiko.SSHClient, cmd: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """exec_command 래퍼. (exit_status, stdout, stderr) 반환."""
    full = f"{WESTON_ENV} && {cmd}"
    stdin, stdout, stderr = ssh.exec_command(full, timeout=timeout)
    try:
        stdin.close()
    except Exception:
        pass
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(f" {title}")
    print("=" * 70)


def diag(host: str, port: int, user: str, password: str, capture_all: bool) -> int:
    out_dir = Path(f"icas_diag_{int(time.time())}")
    out_dir.mkdir(exist_ok=True)
    print(f"[INFO] 결과 저장 폴더: {out_dir.resolve()}")
    print(f"[INFO] 접속: {user}@{host}:{port}")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=host, port=port, username=user, password=password,
            timeout=10, banner_timeout=10, auth_timeout=10,
            look_for_keys=False, allow_agent=False,
        )
    except Exception as e:
        print(f"[FATAL] SSH connect 실패: {type(e).__name__}: {e}")
        return 1

    try:
        section("1) LayerManagerControl 가용성")
        code, out, err = run(ssh, "which LayerManagerControl && LayerManagerControl --help 2>&1 | head -5")
        print(out or "(no stdout)")
        if err:
            print(f"[stderr] {err.strip()}")

        section("2) 화면(screen) 구조 — get screens")
        code, out, err = run(ssh, "LayerManagerControl get screens")
        print(out or "(no stdout)")
        if err:
            print(f"[stderr] {err.strip()}")

        section("3) 레이어 구조 — get layers")
        code, out, err = run(ssh, "LayerManagerControl get layers")
        print(out or "(no stdout)")
        if err:
            print(f"[stderr] {err.strip()}")

        section("4) 서피스 구조 — get surfaces")
        code, out, err = run(ssh, "LayerManagerControl get surfaces")
        print(out or "(no stdout)")
        if err:
            print(f"[stderr] {err.strip()}")

        section("5) 각 screen 별 상세 (get screen N)")
        for n in range(6):
            code, out, err = run(ssh, f"LayerManagerControl get screen {n}")
            if code == 0 and out.strip():
                print(f"\n--- screen {n} ---\n{out}")

        if capture_all:
            section("6) screen 0~5 dump 시도 — 어느 번호가 유효한지 확인")
            with SCPClient(ssh.get_transport()) as scp:
                for n in range(6):
                    remote = f"/tmp/diag_screen{n}.png"
                    code, out, err = run(ssh, f"LayerManagerControl dump screen {n} to {remote}", timeout=20)
                    print(f"\n[screen {n}] dump exit={code}", end="")
                    if err.strip():
                        print(f" stderr={err.strip()[:120]}")
                    else:
                        print()
                    local = out_dir / f"screen{n}.png"
                    try:
                        scp.get(remote, str(local))
                        size = local.stat().st_size if local.exists() else 0
                        sig = b""
                        if size >= 8:
                            with open(local, "rb") as fp:
                                sig = fp.read(8)
                        is_png = sig == b"\x89PNG\r\n\x1a\n"
                        print(f"  → 로컬: {local.name}  size={size:,}B  PNG_OK={is_png}")
                    except Exception as e:
                        print(f"  → SCP pull 실패: {type(e).__name__}: {e}")
                    finally:
                        run(ssh, f"rm -f {remote}", timeout=5)

        section("7) 화면 출력 환경(weston) 상태")
        code, out, err = run(ssh, "ps -e | grep -E 'weston|LayerManager' | grep -v grep")
        print(out or "(weston/LayerManager 프로세스 없음)")

        print()
        print(f"[DONE] PNG는 {out_dir.resolve()} 에서 직접 확인.")
        if capture_all:
            print("       → 어느 screen 번호가 실제 AVN 화면 전체를 담는지 PNG로 비교.")
        return 0
    finally:
        try:
            ssh.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="ICAS HU LayerManagerControl 진단")
    ap.add_argument("host", help="ICAS HU IP (예: 192.168.1.4)")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", default="", help="기본값: 빈 패스워드 (ICAS QNX 패턴)")
    ap.add_argument("--capture-all", action="store_true",
                    help="screen 0~5 모두 dump 시도하여 PNG 비교")
    args = ap.parse_args()
    return diag(args.host, args.port, args.user, args.password, args.capture_all)


if __name__ == "__main__":
    sys.exit(main())
