# HANDOFF — 다음 대화에서 이어갈 작업

## 마지막 작업 상태
- 커밋: `609d21b` — CMD 백그라운드 실행 stdout 폴링
- 브랜치: main
- 빌드: 프론트엔드 빌드 필요 (`cd frontend && npm run build`)

## 이어갈 작업

### 시나리오 재생에서 백그라운드 CMD 결과 표시
- **요청**: 시나리오 실행 결과(ResultsPage)에서도 CMD 백그라운드 결과를 표시
- **현재 상태**:
  - 스텝 테스트에서는 폴링으로 백그라운드 결과 업데이트 구현 완료
  - 시나리오 재생에서는 `step_result.message`에 `[BG_TASK:bg_1]` 마커만 남고, 프론트엔드 폴링 없음
- **구현 방향**:
  1. 시나리오 재생 완료 후 ResultsPage에서 `[BG_TASK:*]` 마커가 있는 스텝 감지
  2. 폴링으로 백그라운드 결과 업데이트 (완료 시 message 갱신)
  3. CMD_CHECK인 경우 expected 비교 후 pass/fail 재판정

### 관련 파일
- `backend/app/services/playback_service.py` — `_bg_tasks` 딕셔너리, `bg_cmd_get()`, `_bg_cmd_start()`
- `backend/app/routers/scenario.py` — `/cmd-result/{task_id}` 폴링 API
- `frontend/src/pages/ResultsPage.tsx` — 결과 표시 (StepResult.message에 CMD 결과)

## 오늘 완료된 작업 요약

### VisionCamera
- harvesters GenTL 기반 스트리밍 (GevSCPSPacketSize=1500)
- BayerRG8 디모자이킹, 프레임 thread-safety

### 이미지 비교
- screenshot_device_id 필드, 디바이스 비종속 스텝 추적
- 멀티크롭/영역제외 모달 개선, 파일명 타임스탬프

### HKMC Hard Key
- 하드키 UI (MKBD/CCP/SWRC), 3단계 시퀀스, _capture_lock
- 연결 시 reqScreenSize 대기, 커스텀 키

### CMD_SEND / CMD_CHECK
- StepType 추가, 기대값 비교 (contains/exact), 하이라이트
- Windows cp949 인코딩, Common 가상 디바이스
- 백그라운드 실행 + stdout 폴링 업데이트 (스텝 테스트)

### 기타
- 프로젝트 파일 정리, settings 권한 정리, 로그 레벨 조정
- wait time 인라인 편집, 스텝 테스트 중 UI 비활성화
