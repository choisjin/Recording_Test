# CLAUDE.md

이 파일은 Claude Code (claude.ai/code)가 이 저장소에서 작업할 때 참고하는 지침서입니다.

## 빌드 및 실행 명령어

```bash
# 초기 설정 (최초 1회)
setup.bat                          # venv 생성, Python/Node 의존성 설치

# 개발 - 방법 1: 서버 관리 GUI (백엔드+프론트엔드 동시 관리)
python server.py                   # Tkinter GUI로 두 프로세스를 함께 실행

# 개발 - 방법 2: 수동 실행
venv/Scripts/activate
python -m uvicorn backend.app.main:app --reload --port 8000   # 백엔드
cd frontend && npm run dev                                     # 프론트엔드 (포트 5173)

# 프로덕션 빌드
cd frontend && npm run build       # frontend/dist/에 출력

# 타입 체크
cd frontend && npx tsc --noEmit

# 동기화 후 실행 (git pull + 의존성 업데이트 + 서버 시작)
sync_and_run.bat
```

별도의 테스트 프레임워크는 미구성 상태. 검증은 앱 내 재생 시스템(SSIM 이미지 비교)으로 수행.

## 아키텍처

**Android 테스트 자동화 플랫폼**: Android/IVI 디바이스에서 사용자 조작을 녹화하고, 스크린샷 기반 검증(SSIM)으로 재생한 뒤, Excel 리포트를 생성하는 시스템.

### 백엔드 (FastAPI, 포트 8000)

```
backend/app/
├── main.py              # FastAPI 앱, WebSocket 엔드포인트 (/ws/screen, /ws/playback)
├── dependencies.py      # 서비스 싱글톤 공유
├── models/scenario.py   # Step, Scenario, ScenarioResult, StepType 열거형
├── routers/             # /api/ 하위 REST 엔드포인트
│   ├── device.py        # 디바이스 관리, ADB 액션, 모듈 함수
│   ├── scenario.py      # 녹화, 재생, 시나리오 CRUD
│   ├── results.py       # 테스트 결과 조회, Excel 내보내기
│   └── settings.py      # 앱 설정
└── services/
    ├── adb_service.py          # 순수 Python ADB 구현 (adb.exe 불필요)
    ├── device_manager.py       # 통합 디바이스 관리 (ADB + Serial + HKMC6th)
    ├── recording_service.py    # 사용자 조작을 Step 시퀀스로 캡처
    ├── playback_service.py     # 시나리오 실행, 조건부 점프 지원
    ├── image_compare_service.py # SSIM 비교, ROI, 템플릿 매칭, 멀티 크롭
    ├── hkmc6th_service.py      # IVI HKMC 6th 시스템용 TCP 소켓 프로토콜
    └── module_service.py       # lge.auto 모듈 동적 로딩 (CAN, POWER 등)
```

### 프론트엔드 (React + TypeScript + Ant Design, 포트 5173)

```
frontend/src/
├── pages/               # 5개 페이지: Device, Record, Scenario, Results, Settings
├── services/api.ts      # 모든 /api/ 호출용 Axios 클라이언트
├── services/websocket.ts # ScreenMirrorWS (실시간 화면) + PlaybackWS (단계별 결과)
├── context/             # DeviceContext (폴링), SettingsContext (테마/경로)
└── i18n/translations.ts # 한국어 UI 텍스트 (200개 이상 키)
```

Vite가 `/api/*`, `/screenshots/*`, `/ws/*` 요청을 백엔드(localhost:8000)로 프록시 처리.

### 데이터 흐름

1. **녹화**: WebSocket으로 디바이스 화면 스트리밍 → 사용자 조작 수행 → 백엔드에서 Step 캡처 → Scenario JSON 저장
2. **재생**: Step 순차 실행 → 실제 스크린샷 캡처 → 기대 이미지와 SSIM 비교 → WebSocket으로 결과 스트리밍 → ScenarioResult JSON 저장
3. **내보내기**: ScenarioResult → 스크린샷 포함 Excel 리포트, 또는 시나리오 배포용 ZIP 패키지

### Step 유형 (StepType 열거형)
`tap`, `long_press`, `swipe`, `input_text`, `key_event`, `wait`, `adb_command`, `serial_command`, `module_command`, `hkmc_touch`, `hkmc_swipe`, `hkmc_key`

### 이미지 비교 모드
- `FULL`: 전체 스크린샷 SSIM 비교
- `SINGLE_CROP`: 단일 크롭 이미지로 템플릿 매칭
- `FULL_EXCLUDE`: 지정 영역 제외 후 SSIM 비교
- `MULTI_CROP`: 복수 크롭, 모두 임계값 통과 필요

### 런타임 데이터 (gitignore 대상)
- `backend/scenarios/` - 녹화된 시나리오 JSON 파일
- `backend/screenshots/` - 기대/실제 이미지
- `backend/results/` - 테스트 결과 JSON 파일
- `backend/auxiliary_devices.json` - 시리얼 디바이스 영구 설정

## 컨벤션

- UI 텍스트는 한국어; 모든 사용자 표시 문자열은 `frontend/src/i18n/translations.ts` 사용
- Python 주석은 한국어 허용
- 루트 레벨 Python 파일(`IVIHKMC6th*.py`, `IVIQEBenchIO*.py`, `hkmc6th.py`, `CAN*.py`)은 레거시/참조용 프로토콜 구현체 - 실제 활성 코드는 `backend/app/services/`에 위치
- 백엔드 의존성: `backend/requirements.txt` (앱 전용) vs 루트 `requirements.txt` (전체 잠금 파일)
- OpenCV는 `opencv-python-headless`로 로드; Windows 환경에서 .pyd 직접 로딩 폴백 있음

## 에이전트 시스템

### /auto — 자동 파이프라인
`/auto <요청>`으로 호출하면 요청을 분석하여 기획→조언→구현→검증까지 자동 실행.
사용자 확인이 필요한 시점(대규모 변경 계획 승인, 커밋)에서만 멈춤.

### 개별 에이전트
- `/검증` — 코드 변경 품질 게이트 (구문, 타입, 패턴, 보안)
- `/조언` — 아키텍처/설계 자문 (읽기 전용, 코드 수정 안 함)
- `/기획` — 기능 구현 계획 수립 및 작업 분할
- `/운영` — 빌드/배포/서버 관리 (상태|빌드|의존성|배포준비)
- `/플러그인` — 플러그인 개발/디버깅 (새로만들기|디버그|분석|목록)
- `/진단` — 런타임 오류 및 연결 문제 해결

### 표준 개발 흐름
```
새 기능: /기획 → 구현 → /검증 → /커밋 → /운영 빌드
자동화: /auto <요청> → 자동 실행 → /커밋
장애:   /진단 → /auto <수정 요청> → /커밋
```

### 에이전트 규칙
- `/auto`는 커밋 전에 반드시 사용자 확인을 받는다
- `/조언`과 `/기획`은 코드를 수정하지 않는다
- 검증 실패 시 자동 수정 후 재검증 (최대 2회, 이후 사용자에게 보고)
- 대화가 길어지면 `/handoff`로 HANDOFF.md를 생성하고 새 대화에서 `/기획 --resume`

### 플러그인 개발 규칙
- 플러그인 파일명 == 클래스명 (module_service.py 로딩 규칙)
- 필수 메서드: Connect() → str, Disconnect() → str
- 위치: backend/app/plugins/, 외부 DLL: backend/app/modules/
