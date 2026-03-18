"""CMD 모듈 — 시스템 명령어 실행 플러그인.

두 가지 실행 방식:
  - Run(command): 명령어 실행 후 완료까지 대기 (blocking). 결과 반환.
  - RunBackground(command): 서브프로세스로 실행 (non-blocking). PID 반환.
"""

import subprocess
import sys


class CMD:
    """시스템 명령어 실행 모듈."""

    def __init__(self):
        self._bg_processes: dict[int, subprocess.Popen] = {}

    def Run(self, command: str, timeout: int = 300) -> str:
        """명령어를 실행하고 완료될 때까지 대기.

        Args:
            command: 실행할 명령어 (예: "ping 127.0.0.1 -n 3")
            timeout: 최대 대기 시간 (초, 기본 300)

        Returns:
            stdout + stderr 출력 결과
        """
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            output = (result.stdout.strip() + "\n" + result.stderr.strip()).strip()
            return output or f"(exit code: {result.returncode})"
        except subprocess.TimeoutExpired:
            return f"TIMEOUT ({timeout}s)"
        except Exception as e:
            return f"ERROR: {e}"

    def RunBackground(self, command: str) -> str:
        """명령어를 서브프로세스로 실행 (백그라운드, non-blocking).

        Args:
            command: 실행할 명령어

        Returns:
            실행된 프로세스의 PID
        """
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self._bg_processes[proc.pid] = proc
            return f"PID:{proc.pid}"
        except Exception as e:
            return f"ERROR: {e}"

    def Kill(self, pid: int) -> str:
        """백그라운드 프로세스를 종료.

        Args:
            pid: 종료할 프로세스 PID

        Returns:
            결과 메시지
        """
        proc = self._bg_processes.pop(pid, None)
        if proc:
            try:
                proc.kill()
                return f"Killed PID:{pid}"
            except Exception as e:
                return f"ERROR: {e}"
        # bg_processes에 없으면 시스템에서 직접 종료 시도
        try:
            import os
            os.kill(pid, 9)
            return f"Killed PID:{pid}"
        except Exception as e:
            return f"ERROR: {e}"

    def ListBackground(self) -> str:
        """실행 중인 백그라운드 프로세스 목록.

        Returns:
            PID 목록 (alive 상태만)
        """
        alive = []
        dead = []
        for pid, proc in list(self._bg_processes.items()):
            if proc.poll() is None:
                alive.append(str(pid))
            else:
                dead.append(pid)
        for pid in dead:
            self._bg_processes.pop(pid, None)
        return ", ".join(alive) if alive else "(none)"
