from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class StepType(str, Enum):
    TAP = "tap"
    LONG_PRESS = "long_press"
    SWIPE = "swipe"
    INPUT_TEXT = "input_text"
    KEY_EVENT = "key_event"
    WAIT = "wait"
    ADB_COMMAND = "adb_command"
    SERIAL_COMMAND = "serial_command"
    MODULE_COMMAND = "module_command"


class TapParams(BaseModel):
    x: int
    y: int


class SwipeParams(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    duration_ms: int = 300


class InputTextParams(BaseModel):
    text: str


class KeyEventParams(BaseModel):
    keycode: str  # e.g. "KEYCODE_HOME", "KEYCODE_BACK"


class WaitParams(BaseModel):
    duration_ms: int = 1000


class AdbCommandParams(BaseModel):
    command: str


class SerialCommandParams(BaseModel):
    data: str
    read_timeout: float = 1.0


class ROI(BaseModel):
    """Region of Interest for image comparison."""
    x: int
    y: int
    width: int
    height: int


class Step(BaseModel):
    id: int
    type: StepType
    device_id: Optional[str] = None  # target device for this step
    params: dict[str, Any]
    delay_after_ms: int = 1000
    expected_image: Optional[str] = None
    description: str = ""
    roi: Optional[ROI] = None  # optional region for verification
    similarity_threshold: float = 0.95
    on_pass_goto: Optional[int] = None  # step ID to jump to on pass (None = next)
    on_fail_goto: Optional[int] = None  # step ID to jump to on fail (None = next)


class Scenario(BaseModel):
    name: str
    description: str = ""
    device_serial: Optional[str] = None
    resolution: Optional[dict[str, int]] = None  # {"width": 1080, "height": 1920}
    steps: list[Step] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class StepResult(BaseModel):
    step_id: int
    repeat_index: int = 1  # which cycle (1-based)
    timestamp: Optional[str] = None  # ISO timestamp when step started
    device_id: str = ""  # which device executed this step
    command: str = ""  # human-readable action description
    description: str = ""  # user remark for the step
    status: str  # "pass", "fail", "warning", "error"
    similarity_score: Optional[float] = None
    expected_image: Optional[str] = None
    actual_image: Optional[str] = None
    actual_annotated_image: Optional[str] = None  # actual with match box drawn
    diff_image: Optional[str] = None
    roi: Optional[ROI] = None  # ROI used for comparison (for frontend display)
    match_location: Optional[dict] = None  # {x, y, width, height} of matched region
    message: str = ""
    delay_ms: int = 0  # configured delay_after_ms
    execution_time_ms: int = 0  # actual duration


class ScenarioResult(BaseModel):
    scenario_name: str
    device_serial: str
    status: str  # "pass", "fail", "error"
    total_steps: int  # steps per cycle
    total_repeat: int = 1
    passed_steps: int = 0
    failed_steps: int = 0
    warning_steps: int = 0
    error_steps: int = 0
    step_results: list[StepResult] = Field(default_factory=list)  # ALL cycles combined
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
