"""Scenario management API routes."""

import base64
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..dependencies import adb_service as adb_svc
from ..dependencies import playback_service as playback_svc
from ..dependencies import recording_service as recording_svc
from ..models.scenario import ROI, Scenario, StepType
from ..services.recording_service import SCREENSHOTS_DIR

router = APIRouter(prefix="/api/scenario", tags=["scenario"])


# ------------------------------------------------------------------
# Recording
# ------------------------------------------------------------------

class StartRecordingRequest(BaseModel):
    name: str
    description: str = ""


class AddStepRequest(BaseModel):
    type: StepType
    device_id: str = ""
    params: dict
    description: str = ""
    delay_after_ms: int = 1000
    roi: Optional[dict] = None
    similarity_threshold: float = 0.95
    skip_execute: bool = False


@router.post("/record/start")
async def start_recording(req: StartRecordingRequest):
    """Start a new recording session."""
    try:
        scenario = await recording_svc.start_recording(req.name, req.description)
        return {"status": "recording", "scenario": scenario.model_dump()}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/record/step")
async def add_step(req: AddStepRequest):
    """Add a step to the current recording."""
    try:
        step = await recording_svc.add_step(
            step_type=req.type,
            params=req.params,
            device_id=req.device_id,
            description=req.description,
            delay_after_ms=req.delay_after_ms,
            roi=req.roi,
            similarity_threshold=req.similarity_threshold,
            skip_execute=req.skip_execute,
        )
        return {"status": "ok", "step": step.model_dump()}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ResumeRecordingRequest(BaseModel):
    name: str


@router.post("/record/resume")
async def resume_recording(req: ResumeRecordingRequest):
    """Resume recording on an existing scenario."""
    try:
        scenario = await recording_svc.resume_recording(req.name)
        return {"status": "recording", "scenario": scenario.model_dump()}
    except (RuntimeError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/record/stop")
async def stop_recording():
    """Stop recording and save the scenario."""
    try:
        scenario = await recording_svc.stop_recording()
        return {"status": "saved", "scenario": scenario.model_dump()}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


class DeleteStepRequest(BaseModel):
    step_index: int  # 0-based


@router.post("/record/delete-step")
async def delete_step(req: DeleteStepRequest):
    """Delete a step from the current recording session."""
    if not recording_svc.is_recording or not recording_svc._current_scenario:
        raise HTTPException(status_code=400, detail="Not recording")
    scenario = recording_svc._current_scenario
    if req.step_index < 0 or req.step_index >= len(scenario.steps):
        raise HTTPException(status_code=400, detail=f"Invalid step index: {req.step_index}")
    removed = scenario.steps.pop(req.step_index)
    # Re-number step IDs sequentially
    for i, step in enumerate(scenario.steps):
        step.id = i + 1
    await recording_svc.save_scenario(scenario)
    return {"status": "ok", "removed_step_id": removed.id, "remaining": len(scenario.steps)}


@router.get("/record/status")
async def recording_status():
    """Check if recording is in progress."""
    return {"recording": recording_svc.is_recording}


class SaveExpectedImageRequest(BaseModel):
    scenario_name: str
    step_index: int  # 0-based
    image_base64: str  # PNG base64 data (without data:image/png;base64, prefix)
    crop: Optional[dict] = None  # {x, y, width, height} in image pixels


@router.post("/record/save-expected-image")
async def save_expected_image(req: SaveExpectedImageRequest):
    """Manually save an expected image for a step."""
    # During recording: use in-memory scenario; otherwise load from disk
    scenario = None
    if recording_svc.is_recording and recording_svc._current_scenario:
        # Always use in-memory scenario during recording (it may not be on disk yet)
        scenario = recording_svc._current_scenario
    else:
        try:
            scenario = await recording_svc.load_scenario(req.scenario_name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Scenario '{req.scenario_name}' not found")

    if req.step_index < 0 or req.step_index >= len(scenario.steps):
        raise HTTPException(status_code=400, detail=f"Invalid step index: {req.step_index}")

    step = scenario.steps[req.step_index]

    # Decode base64 PNG
    try:
        raw = req.image_base64
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        png_bytes = base64.b64decode(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")

    # Optionally crop
    if req.crop:
        import cv2
        import numpy as np
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(status_code=400, detail="Cannot decode image")
        x, y, w, h = req.crop["x"], req.crop["y"], req.crop["width"], req.crop["height"]
        cropped = img[y:y + h, x:x + w]
        _, png_bytes = cv2.imencode(".png", cropped)
        png_bytes = png_bytes.tobytes()

    # Save to file
    filename = f"{req.scenario_name}_step_{step.id:03d}.png"
    save_dir = SCREENSHOTS_DIR / req.scenario_name
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / filename).write_bytes(png_bytes)

    # Update scenario step
    step.expected_image = filename
    # Store crop region as ROI so playback can crop actual screenshot to match
    if req.crop:
        step.roi = ROI(x=int(req.crop["x"]), y=int(req.crop["y"]),
                       width=int(req.crop["width"]), height=int(req.crop["height"]))
    else:
        step.roi = None
    # Save to disk (also persists during recording so it's not lost)
    await recording_svc.save_scenario(scenario)

    return {"status": "ok", "filename": filename, "step_id": step.id}


class CaptureExpectedImageRequest(BaseModel):
    scenario_name: str
    step_index: int  # 0-based
    device_id: str  # ADB serial to take screenshot from
    crop: Optional[dict] = None  # {x, y, width, height} in device pixels


@router.post("/record/capture-expected-image")
async def capture_expected_image(req: CaptureExpectedImageRequest):
    """Capture a screenshot from the device and save as expected image.

    Unlike save-expected-image, this takes the screenshot server-side
    so no large base64 transfer is needed.
    """
    # Resolve scenario
    scenario = None
    if recording_svc.is_recording and recording_svc._current_scenario:
        scenario = recording_svc._current_scenario
    else:
        try:
            scenario = await recording_svc.load_scenario(req.scenario_name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Scenario '{req.scenario_name}' not found")

    if req.step_index < 0 or req.step_index >= len(scenario.steps):
        raise HTTPException(status_code=400, detail=f"Invalid step index: {req.step_index}")

    step = scenario.steps[req.step_index]

    # Take screenshot via ADB
    try:
        png_bytes = await adb_svc.screencap_bytes(serial=req.device_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screenshot failed: {e}")

    # Optionally crop
    if req.crop:
        import cv2
        import numpy as np
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(status_code=400, detail="Cannot decode screenshot")
        x, y, w, h = int(req.crop["x"]), int(req.crop["y"]), int(req.crop["width"]), int(req.crop["height"])
        cropped = img[y:y + h, x:x + w]
        _, buf = cv2.imencode(".png", cropped)
        png_bytes = buf.tobytes()

    # Save to file
    scenario_name = scenario.name
    filename = f"{scenario_name}_step_{step.id:03d}.png"
    save_dir = SCREENSHOTS_DIR / scenario_name
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / filename).write_bytes(png_bytes)

    # Update scenario step
    step.expected_image = filename
    # Store crop region as ROI so playback can crop actual screenshot to match
    if req.crop:
        step.roi = ROI(x=int(req.crop["x"]), y=int(req.crop["y"]),
                       width=int(req.crop["width"]), height=int(req.crop["height"]))
    else:
        step.roi = None
    await recording_svc.save_scenario(scenario)

    return {"status": "ok", "filename": filename, "step_id": step.id}


# ------------------------------------------------------------------
# Groups
# ------------------------------------------------------------------

@router.get("/groups")
async def get_groups():
    """Get all scenario groups."""
    return {"groups": recording_svc.get_groups()}


class CreateGroupRequest(BaseModel):
    name: str


@router.post("/groups")
async def create_group(req: CreateGroupRequest):
    groups = recording_svc.create_group(req.name)
    return {"groups": groups}


class RenameGroupRequest(BaseModel):
    old_name: str
    new_name: str


@router.put("/groups")
async def rename_group(req: RenameGroupRequest):
    groups = recording_svc.rename_group(req.old_name, req.new_name)
    return {"groups": groups}


@router.delete("/groups/{group_name}")
async def delete_group(group_name: str):
    groups = recording_svc.delete_group(group_name)
    return {"groups": groups}


class GroupScenarioRequest(BaseModel):
    scenario_name: str


@router.post("/groups/{group_name}/add")
async def add_to_group(group_name: str, req: GroupScenarioRequest):
    groups = recording_svc.add_to_group(group_name, req.scenario_name)
    return {"groups": groups}


@router.post("/groups/{group_name}/remove")
async def remove_from_group(group_name: str, req: GroupScenarioRequest):
    groups = recording_svc.remove_from_group(group_name, req.scenario_name)
    return {"groups": groups}


class ReorderGroupRequest(BaseModel):
    ordered: list[str]


@router.post("/groups/{group_name}/reorder")
async def reorder_group(group_name: str, req: ReorderGroupRequest):
    groups = recording_svc.reorder_group(group_name, req.ordered)
    return {"groups": groups}


class JumpTarget(BaseModel):
    scenario: int  # group index (0-based), -1 = END
    step: int = 0  # step index within the scenario (0-based)


class UpdateGroupJumpsRequest(BaseModel):
    index: int
    on_pass_goto: Optional[JumpTarget] = None
    on_fail_goto: Optional[JumpTarget] = None


@router.post("/groups/{group_name}/jumps")
async def update_group_jumps(group_name: str, req: UpdateGroupJumpsRequest):
    pass_goto = req.on_pass_goto.model_dump() if req.on_pass_goto else None
    fail_goto = req.on_fail_goto.model_dump() if req.on_fail_goto else None
    groups = recording_svc.update_group_jumps(group_name, req.index, pass_goto, fail_goto)
    return {"groups": groups}


class UpdateGroupStepJumpsRequest(BaseModel):
    index: int        # scenario index in group
    step_id: int      # step id within scenario
    on_pass_goto: Optional[JumpTarget] = None
    on_fail_goto: Optional[JumpTarget] = None


@router.post("/groups/{group_name}/step-jumps")
async def update_group_step_jumps(group_name: str, req: UpdateGroupStepJumpsRequest):
    pass_goto = req.on_pass_goto.model_dump() if req.on_pass_goto else None
    fail_goto = req.on_fail_goto.model_dump() if req.on_fail_goto else None
    groups = recording_svc.update_group_step_jumps(
        group_name, req.index, req.step_id, pass_goto, fail_goto
    )
    return {"groups": groups}


# ------------------------------------------------------------------
# Copy & Merge
# ------------------------------------------------------------------

class CopyScenarioRequest(BaseModel):
    target_name: str


@router.post("/copy/{name}")
async def copy_scenario(name: str, req: CopyScenarioRequest):
    """Copy a scenario with a new name."""
    try:
        scenario = await recording_svc.copy_scenario(name, req.target_name)
        return {"status": "ok", "scenario": scenario.model_dump()}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Scenario '{name}' not found")


class MergeRequest(BaseModel):
    names: list[str]
    target_name: str


@router.post("/merge")
async def merge_scenarios(req: MergeRequest):
    """Merge multiple scenarios into one."""
    try:
        scenario = await recording_svc.merge_scenarios(req.names, req.target_name)
        return {"status": "ok", "scenario": scenario.model_dump()}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ------------------------------------------------------------------
# Playback & Verification (before /{name} to avoid conflicts)
# ------------------------------------------------------------------

class TestStepRequest(BaseModel):
    scenario_name: str
    step_index: int  # 0-based
    step_data: Optional[dict] = None  # current (unsaved) step data from frontend


@router.post("/test-step")
async def test_step(req: TestStepRequest):
    """Execute a single step on the device and verify against expected image."""
    if req.step_data:
        # Use the step data sent from frontend (may differ from saved file)
        from ..models.scenario import Step
        step = Step(**req.step_data)
        scenario_name = req.scenario_name
    else:
        # Fallback: load from saved scenario file
        try:
            scenario = await recording_svc.load_scenario(req.scenario_name)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Scenario '{req.scenario_name}' not found")

        if req.step_index < 0 or req.step_index >= len(scenario.steps):
            raise HTTPException(status_code=400, detail=f"Invalid step index: {req.step_index}")

        step = scenario.steps[req.step_index]
        scenario_name = scenario.name

    result = await playback_svc.execute_single_step(step, scenario_name)
    return result.model_dump()


class PlaybackRequest(BaseModel):
    verify: bool = True


@router.post("/playback/stop")
async def stop_playback():
    """Stop the currently running playback."""
    await playback_svc.stop()
    return {"status": "stopping"}


@router.get("/playback/status")
async def playback_status():
    """Check if playback is running."""
    return {"running": playback_svc.is_running}


# ------------------------------------------------------------------
# Scenario CRUD (/{name} wildcard routes MUST be last)
# ------------------------------------------------------------------

@router.get("/list")
async def list_scenarios():
    """List all saved scenarios."""
    names = await recording_svc.list_scenarios()
    return {"scenarios": names}


@router.get("/{name}")
async def get_scenario(name: str):
    """Load a scenario by name."""
    try:
        scenario = await recording_svc.load_scenario(name)
        return scenario.model_dump()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Scenario '{name}' not found")


@router.delete("/{name}")
async def delete_scenario(name: str):
    """Delete a scenario."""
    deleted = await recording_svc.delete_scenario(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Scenario '{name}' not found")
    return {"status": "deleted"}


class RenameScenarioRequest(BaseModel):
    new_name: str


@router.post("/{name}/rename")
async def rename_scenario(name: str, req: RenameScenarioRequest):
    """Rename a scenario."""
    try:
        ok = await recording_svc.rename_scenario(name, req.new_name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail=f"Scenario '{name}' not found")
    return {"status": "renamed", "old_name": name, "new_name": req.new_name}


@router.put("/{name}")
async def update_scenario(name: str, scenario: Scenario):
    """Update a scenario."""
    await recording_svc.save_scenario(scenario)
    return {"status": "updated"}


@router.post("/{name}/play")
async def play_scenario(name: str, req: PlaybackRequest):
    """Execute a saved scenario."""
    try:
        scenario = await recording_svc.load_scenario(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Scenario '{name}' not found")

    try:
        result = await playback_svc.execute_scenario(scenario, verify=req.verify)
        return result.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
