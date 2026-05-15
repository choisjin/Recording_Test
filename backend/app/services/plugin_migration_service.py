"""플러그인 일괄 마이그레이션 서비스.

레거시 플러그인(예: SerialPlugin)이 신규 통합 플러그인(SerialLogging)으로
교체될 때, 기존에 저장된 다음 파일들의 module/function 참조를 한 번에 정리한다:

  - backend/auxiliary_devices.json   (등록된 보조 디바이스의 info.module)
  - backend/scan_settings.json       (builtin/custom 스캔 항목의 module)
  - backend/device_catalog.json      (module_visibility의 모듈 키)
  - backend/scenarios/*.json         (module_command 스텝의 params.module / params.function)

자동 호출 경로 (idempotent — 매 부팅마다 안전):
  - DeviceManager._load_auxiliary_devices  → migrate_auxiliary_devices_inplace
  - device.py::_load_scan_settings         → migrate_scan_settings_inplace
  - device.py::_load_device_catalog        → migrate_device_catalog_inplace

수동 일괄 적용:
  - POST /api/device/migrate-plugin  → run_full_migration (시나리오 포함 전체 스캔/재기록)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── 매핑 테이블 ─────────────────────────────────────────────────────────────
# 새 매핑을 추가할 때는 MODULE_RENAMES 와 FUNCTION_RENAMES만 손대면 된다.
# (function rename은 module 단위로 분리 — 다른 모듈에서 같은 함수명이 충돌하지 않도록.)

MODULE_RENAMES: dict[str, str] = {
    # 레거시 → 통합 후 모듈명
    "SerialPlugin": "SerialLogging",
}

FUNCTION_RENAMES: dict[str, dict[str, str]] = {
    # 마이그레이션 후 모듈명 기준 — module rename이 먼저 적용되므로 신모듈 이름으로 등록.
    "SerialLogging": {
        # 레거시 SerialPlugin에만 있던 함수 → 신규 함수에 매핑
        "SendHex": "Send_Packet",
        # 일부 함수(ReadLine, ReadAll, SendAndRead, LOG_SERIAL, StartMonitor, StopMonitor,
        # GetMonitorResult, ReadHex, SetBaudrate, GetPortInfo)는 대응 함수가 없거나
        # 시그니처가 달라 자동 매핑 불가 — 사용자가 직접 수정해야 한다.
        # 이런 함수가 발견되면 run_full_migration이 'unmapped_functions' 리스트로 보고.
    },
}

# 신모듈에서 *사용 가능*한 함수 화이트리스트 — 자동 매핑되지 않은 레거시 함수를 식별할 때 사용.
KNOWN_FUNCTIONS: dict[str, set[str]] = {
    "SerialLogging": {
        "Connect", "Disconnect", "IsConnected",
        "StartLogging", "StopLogging",
        "SendCommand", "Send_Packet",
        "SendCommand_fail_on_keyword", "SendCommand_pass_on_keyword",
    },
}


# ── 경로 헬퍼 ───────────────────────────────────────────────────────────────

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent  # backend/
AUX_DEVICES_FILE = _BACKEND_DIR / "auxiliary_devices.json"
SCAN_SETTINGS_FILE = _BACKEND_DIR / "scan_settings.json"
DEVICE_CATALOG_FILE = _BACKEND_DIR / "device_catalog.json"
SCENARIOS_DIR = _BACKEND_DIR / "scenarios"


# ── 단위 변환 함수 (in-place, mutates dict) ─────────────────────────────────

def _rename_module(name: str) -> str:
    return MODULE_RENAMES.get(name, name)


def _rename_function(module_name: str, func_name: str) -> str:
    """module rename이 이미 끝난 *신규* 모듈명 기준으로 함수 rename."""
    return FUNCTION_RENAMES.get(module_name, {}).get(func_name, func_name)


def migrate_auxiliary_devices_inplace(data: list) -> int:
    """등록된 보조 디바이스 dict 리스트를 in-place로 변환. 변경된 항목 수 반환."""
    changed = 0
    for d in data:
        if not isinstance(d, dict):
            continue
        info = d.get("info")
        if not isinstance(info, dict):
            continue
        old_mod = info.get("module")
        if isinstance(old_mod, str) and old_mod in MODULE_RENAMES:
            info["module"] = MODULE_RENAMES[old_mod]
            changed += 1
    return changed


def migrate_scan_settings_inplace(data: dict) -> int:
    """scan_settings dict를 in-place로 변환. 변경 항목 수 반환."""
    changed = 0
    builtin = data.get("builtin") or {}
    if isinstance(builtin, dict):
        for entry in builtin.values():
            if isinstance(entry, dict):
                mod = entry.get("module")
                if isinstance(mod, str) and mod in MODULE_RENAMES:
                    entry["module"] = MODULE_RENAMES[mod]
                    changed += 1
    custom = data.get("custom") or []
    if isinstance(custom, list):
        for entry in custom:
            if isinstance(entry, dict):
                mod = entry.get("module")
                if isinstance(mod, str) and mod in MODULE_RENAMES:
                    entry["module"] = MODULE_RENAMES[mod]
                    changed += 1
    return changed


def migrate_device_catalog_inplace(data: dict) -> int:
    """device_catalog의 module_visibility 키를 rename. 충돌 시 OR로 병합."""
    vis = data.get("module_visibility")
    if not isinstance(vis, dict):
        return 0
    changed = 0
    for old, new in MODULE_RENAMES.items():
        if old in vis:
            old_val = vis.pop(old)
            # 신규 키가 이미 있으면 OR — 둘 중 하나라도 표시였으면 표시 유지
            if new in vis:
                vis[new] = bool(vis[new]) or bool(old_val)
            else:
                vis[new] = old_val
            changed += 1
    return changed


def migrate_scenario_inplace(scenario: dict) -> tuple[int, list[dict]]:
    """단일 시나리오 dict의 module_command 스텝을 변환.

    Returns:
        (changed_count, unmapped_functions)
        unmapped_functions: [{"step_id": int, "module": str, "function": str}, ...]
            — 모듈은 매핑됐지만 함수가 KNOWN_FUNCTIONS에 없는 경우. 호출자에게 노출하여
              사용자가 수동 보정할 수 있도록 한다.
    """
    changed = 0
    unmapped: list[dict] = []
    steps = scenario.get("steps")
    if not isinstance(steps, list):
        return 0, []

    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") != "module_command":
            continue
        params = step.get("params")
        if not isinstance(params, dict):
            continue

        old_mod = params.get("module")
        if not isinstance(old_mod, str):
            continue

        new_mod = _rename_module(old_mod)
        if new_mod != old_mod:
            params["module"] = new_mod
            changed += 1

        # 함수명 rename (module rename 이후 신규 모듈명 기준)
        func = params.get("function")
        if isinstance(func, str):
            new_func = _rename_function(new_mod, func)
            if new_func != func:
                params["function"] = new_func
                changed += 1
                func = new_func
            # 화이트리스트 검사 — module이 rename 대상이었던 경우만
            if old_mod in MODULE_RENAMES:
                known = KNOWN_FUNCTIONS.get(new_mod)
                if known is not None and func not in known:
                    unmapped.append({
                        "step_id": step.get("id"),
                        "module": new_mod,
                        "function": func,
                    })

    return changed, unmapped


# ── 디스크 I/O 래퍼 ─────────────────────────────────────────────────────────

def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[plugin_migration] read failed %s: %s", path, e)
        return None


def _write_json(path: Path, data: Any) -> bool:
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        logger.error("[plugin_migration] write failed %s: %s", path, e)
        return False


# ── 일괄 실행 진입점 ────────────────────────────────────────────────────────

def run_full_migration(dry_run: bool = False) -> dict:
    """전체 파일을 스캔하여 일괄 마이그레이션 수행.

    Args:
        dry_run: True면 변경 사항만 집계하고 디스크에 쓰지 않는다 (미리보기 용도).

    Returns:
        {
            "auxiliary_devices": {"changed": int, "saved": bool},
            "scan_settings":     {"changed": int, "saved": bool},
            "device_catalog":    {"changed": int, "saved": bool},
            "scenarios": [
                {"file": str, "changed": int, "saved": bool,
                 "unmapped_functions": [...]},
                ...
            ],
            "summary": {
                "files_changed": int,
                "total_changes": int,
                "scenarios_with_unmapped": int,
                "dry_run": bool,
            },
        }
    """
    report: dict[str, Any] = {
        "auxiliary_devices": {"changed": 0, "saved": False},
        "scan_settings":     {"changed": 0, "saved": False},
        "device_catalog":    {"changed": 0, "saved": False},
        "scenarios":         [],
    }

    # 1) auxiliary_devices
    if AUX_DEVICES_FILE.exists():
        data = _read_json(AUX_DEVICES_FILE)
        if isinstance(data, list):
            changed = migrate_auxiliary_devices_inplace(data)
            report["auxiliary_devices"]["changed"] = changed
            if changed > 0 and not dry_run:
                report["auxiliary_devices"]["saved"] = _write_json(AUX_DEVICES_FILE, data)

    # 2) scan_settings
    if SCAN_SETTINGS_FILE.exists():
        data = _read_json(SCAN_SETTINGS_FILE)
        if isinstance(data, dict):
            changed = migrate_scan_settings_inplace(data)
            report["scan_settings"]["changed"] = changed
            if changed > 0 and not dry_run:
                report["scan_settings"]["saved"] = _write_json(SCAN_SETTINGS_FILE, data)

    # 3) device_catalog
    if DEVICE_CATALOG_FILE.exists():
        data = _read_json(DEVICE_CATALOG_FILE)
        if isinstance(data, dict):
            changed = migrate_device_catalog_inplace(data)
            report["device_catalog"]["changed"] = changed
            if changed > 0 and not dry_run:
                report["device_catalog"]["saved"] = _write_json(DEVICE_CATALOG_FILE, data)

    # 4) scenarios/*.json — groups.json/folders.json은 제외
    excluded_names = {"groups.json", "folders.json"}
    files_changed = 0
    total_changes = (
        report["auxiliary_devices"]["changed"]
        + report["scan_settings"]["changed"]
        + report["device_catalog"]["changed"]
    )
    scenarios_with_unmapped = 0

    if SCENARIOS_DIR.is_dir():
        for sf in sorted(SCENARIOS_DIR.glob("*.json")):
            if sf.name in excluded_names:
                continue
            data = _read_json(sf)
            if not isinstance(data, dict):
                continue
            changed, unmapped = migrate_scenario_inplace(data)
            saved = False
            if changed > 0 and not dry_run:
                saved = _write_json(sf, data)
            entry = {
                "file": sf.name,
                "changed": changed,
                "saved": saved,
                "unmapped_functions": unmapped,
            }
            report["scenarios"].append(entry)
            total_changes += changed
            if changed > 0:
                files_changed += 1
            if unmapped:
                scenarios_with_unmapped += 1

    if report["auxiliary_devices"]["changed"]:
        files_changed += 1
    if report["scan_settings"]["changed"]:
        files_changed += 1
    if report["device_catalog"]["changed"]:
        files_changed += 1

    report["summary"] = {
        "files_changed": files_changed,
        "total_changes": total_changes,
        "scenarios_with_unmapped": scenarios_with_unmapped,
        "dry_run": dry_run,
        "module_renames": dict(MODULE_RENAMES),
        "function_renames": {k: dict(v) for k, v in FUNCTION_RENAMES.items()},
    }
    return report
