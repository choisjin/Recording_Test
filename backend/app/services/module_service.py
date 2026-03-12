"""lge.auto module introspection and execution service."""

from __future__ import annotations

import asyncio
import inspect
import functools
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Cache: module_name -> class instance
_instances: dict[str, Any] = {}

# Cache: module_name -> list of function info
_module_functions_cache: dict[str, list[dict]] = {}


def list_available_modules() -> list[dict]:
    """List all available lge.auto modules."""
    # connect_params: fields required when adding a device with this module
    #   "serial" = needs COM port + baudrate (default connection type)
    #   "socket" = needs IP host address
    #   custom list = specific fields [{name, label, type, default?}]
    # connect_fields: extra fields shown in the UI when adding a device
    #   Each field: {name, label, type("text"|"number"|"select"), default?, options?[]}
    modules = [
        {"name": "POWER", "label": "전원 공급기 (POWER)", "connect_type": "serial",
         "connect_fields": []},
        {"name": "RIDEN", "label": "전원 공급기 (RIDEN RD6006)", "connect_type": "serial",
         "connect_fields": []},
        {"name": "CAN", "label": "CAN 통신", "connect_type": "can",
         "connect_fields": [
             {"name": "interface", "label": "Interface", "type": "select", "default": "pcan",
              "options": ["pcan", "vector", "kvaser", "socketcan", "ixxat"]},
             {"name": "channel", "label": "Channel", "type": "text", "default": "PCAN_USBBUS1"},
             {"name": "bitrate", "label": "Bitrate", "type": "select", "default": "500000",
              "options": ["125000", "250000", "500000", "1000000"]},
             {"name": "fd", "label": "CAN FD", "type": "select", "default": "False",
              "options": ["True", "False"]},
         ]},
        {"name": "CANOE", "label": "CANoe", "connect_type": "none",
         "connect_fields": []},
        {"name": "CANAT", "label": "CAN Analyzer (CANAT)", "connect_type": "serial",
         "connect_fields": []},
        {"name": "BENCH", "label": "벤치 장비 (BENCH)", "connect_type": "socket",
         "connect_fields": []},
        {"name": "IVIQEBenchIOClient", "label": "IVIQE 벤치 IO", "connect_type": "serial",
         "connect_fields": []},
        {"name": "SP25Bench", "label": "SP25 벤치", "connect_type": "serial",
         "connect_fields": []},
        {"name": "Uart", "label": "UART 시리얼", "connect_type": "serial",
         "connect_fields": []},
        {"name": "Ignition", "label": "Ignition 제어", "connect_type": "serial",
         "connect_fields": []},
        {"name": "KeysightPower", "label": "Keysight 전원 공급기", "connect_type": "socket",
         "connect_fields": []},
        {"name": "SSHManager", "label": "SSH 매니저", "connect_type": "socket",
         "connect_fields": []},
        {"name": "AudioLibrary", "label": "오디오 분석", "connect_type": "none",
         "connect_fields": []},
        {"name": "ImageProcessing", "label": "이미지 처리/OCR", "connect_type": "none",
         "connect_fields": []},
        {"name": "DLTLogging", "label": "DLT 로깅", "connect_type": "none",
         "connect_fields": []},
        {"name": "MLP", "label": "MLP 로깅", "connect_type": "none",
         "connect_fields": []},
        {"name": "PCANClient", "label": "PCAN 클라이언트", "connect_type": "none",
         "connect_fields": []},
        {"name": "TigrisCheck", "label": "Tigris 체크", "connect_type": "none",
         "connect_fields": []},
        {"name": "Trace", "label": "Trace 분석", "connect_type": "none",
         "connect_fields": []},
        {"name": "COMMON_WINDOWS", "label": "Windows 공통", "connect_type": "none",
         "connect_fields": []},
        {"name": "Android", "label": "Android (ADB)", "connect_type": "none",
         "connect_fields": []},
    ]
    available = []
    for m in modules:
        try:
            __import__(f"lge.auto.{m['name']}", fromlist=[m["name"]])
            available.append(m)
        except Exception:
            pass
    return available


def get_module_functions(module_name: str) -> list[dict]:
    """Get all public callable methods of a module's main class."""
    if module_name in _module_functions_cache:
        return _module_functions_cache[module_name]

    try:
        mod = __import__(f"lge.auto.{module_name}", fromlist=[module_name])
        cls = getattr(mod, module_name, None)
        if cls is None:
            return []
    except Exception as e:
        logger.warning("Cannot import lge.auto.%s: %s", module_name, e)
        return []

    functions = []
    for name in sorted(dir(cls)):
        if name.startswith("_"):
            continue
        attr = getattr(cls, name, None)
        if not callable(attr):
            continue
        try:
            sig = inspect.signature(attr)
        except (ValueError, TypeError):
            continue

        params = []
        for pname, p in sig.parameters.items():
            if pname == "self":
                continue
            param_info: dict[str, Any] = {"name": pname, "required": True}
            if p.default is not inspect.Parameter.empty:
                param_info["required"] = False
                param_info["default"] = repr(p.default)
            params.append(param_info)

        functions.append({
            "name": name,
            "params": params,
        })

    _module_functions_cache[module_name] = functions
    return functions


def _get_instance(module_name: str, constructor_kwargs: Optional[dict] = None) -> Any:
    """Get or create a singleton instance of the module class."""
    if module_name not in _instances:
        mod = __import__(f"lge.auto.{module_name}", fromlist=[module_name])
        cls = getattr(mod, module_name)
        # Try to pass constructor kwargs (e.g. port, bps) if the class needs them
        if constructor_kwargs:
            sig = inspect.signature(cls.__init__)
            ctor_args = {}
            for pname, p in sig.parameters.items():
                if pname == "self":
                    continue
                if pname in constructor_kwargs:
                    ctor_args[pname] = constructor_kwargs[pname]
            if ctor_args:
                instance = cls(**ctor_args)
                # Serial modules (e.g. IVIQEBenchIOClient): constructor sets port/bps
                # but doesn't open the connection — call Connect() afterward
                for method_name in ("Connect", "connect"):
                    connect_fn = getattr(instance, method_name, None)
                    if callable(connect_fn):
                        try:
                            sig = inspect.signature(connect_fn)
                            # Only call if it takes no args (besides self)
                            non_self = [p for p in sig.parameters if p != "self"]
                            if len(non_self) == 0:
                                connect_fn()
                                logger.info("Auto-called %s.%s()", module_name, method_name)
                        except Exception as e:
                            logger.warning("Auto-connect %s.%s() failed: %s", module_name, method_name, e)
                        break
                _instances[module_name] = instance
            else:
                # Constructor doesn't accept the provided kwargs (e.g. BENCH)
                # Create instance normally, then try auto-connect if host is provided
                instance = cls()
                if "host" in constructor_kwargs:
                    # Socket-based modules: auto-call connect method
                    for method_name in ("socket_connect", "connect", "Connect"):
                        connect_fn = getattr(instance, method_name, None)
                        if callable(connect_fn):
                            connect_fn(constructor_kwargs["host"])
                            break
                _instances[module_name] = instance
        else:
            _instances[module_name] = cls()
    return _instances[module_name]


def _execute_sync(module_name: str, function_name: str, args: dict,
                  constructor_kwargs: Optional[dict] = None) -> Any:
    """Execute a module function synchronously."""
    instance = _get_instance(module_name, constructor_kwargs)
    func = getattr(instance, function_name, None)
    if func is None:
        raise ValueError(f"Function '{function_name}' not found in {module_name}")

    # Build call args from the function signature
    sig = inspect.signature(func)
    call_args = {}
    for pname, p in sig.parameters.items():
        if pname in args:
            val = args[pname]
            # Try to cast to the expected type based on annotation
            if p.annotation is not inspect.Parameter.empty:
                try:
                    if p.annotation in (int, float, bool, str):
                        val = p.annotation(val)
                except (ValueError, TypeError):
                    pass
            call_args[pname] = val
        elif p.default is inspect.Parameter.empty:
            raise ValueError(f"Missing required parameter: {pname}")

    result = func(**call_args)
    return result


async def execute_module_function(
    module_name: str, function_name: str, args: dict,
    constructor_kwargs: Optional[dict] = None,
) -> str:
    """Execute a module function asynchronously (runs in thread pool)."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None,
            functools.partial(_execute_sync, module_name, function_name, args,
                              constructor_kwargs),
        )
        return str(result) if result is not None else "OK"
    except Exception as e:
        logger.error("Module execution error: %s.%s -> %s", module_name, function_name, e)
        raise


def reset_instance(module_name: str) -> None:
    """Remove cached instance (e.g. on device disconnect)."""
    _instances.pop(module_name, None)
