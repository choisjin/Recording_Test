# OCR 다국어 모델 디렉토리

이 폴더의 ONNX 모델/사전 파일들은 **git에 커밋**되어 있어
체크아웃 즉시 다국어 OCR이 동작합니다 (별도 다운로드 불필요).
빌드 시 `build_dist.py`가 자동으로 dist 패키지에 포함시킵니다.

## 구조

```
ocr_models/
  korean/    rec_infer.onnx + rec_keys.txt   ← 한국어 + 영어 + 숫자 (기본)
  english/   rec_infer.onnx + rec_keys.txt   ← 영어 전용 (가장 정확)
  japan/     rec_infer.onnx + rec_keys.txt   ← 일본어
  chinese/   rec_infer.onnx + rec_keys.txt   ← 중국어
  # 선택 (수동 다운로드 후 커밋):
  latin/     ...    ← 라틴 문자권 (ES/FR/DE/IT...)
  cyrillic/  ...    ← 키릴 (러시아어 등)
  arabic/    ...    ← 아랍어
  devanagari/ ...   ← 데바나가리 (힌디 등)
  _tmp/             ← 다운로드 임시 폴더 (gitignore, build 제외)
```

## 모델 추가/갱신 흐름 (개발자)

새 언어 추가 또는 기존 모델 업데이트 시:

```bash
# 1) paddle2onnx 설치 (변환 도구, 1회만)
venv/Scripts/python.exe -m pip install paddle2onnx

# 2) 원하는 언어 다운로드 + ONNX 변환
venv/Scripts/python.exe scripts/download_ocr_models.py korean japan
# 또는 기본 4종 (korean/english/japan/chinese)
venv/Scripts/python.exe scripts/download_ocr_models.py
# 또는 지원하는 모든 언어
venv/Scripts/python.exe scripts/download_ocr_models.py --all

# 3) 변경분 커밋
git add backend/app/services/ocr_models/
git commit -m "feat: OCR <언어> 모델 추가"
git push
```

## 동작 원리

- `ocr_service.py`가 시작 시 이 폴더를 스캔, 각 언어별 `RapidOCR(rec_model_path=..., rec_keys_path=...)` 인스턴스를 lazy 로드.
- OCR 스텝의 `language` 파라미터로 선택 (기본 `korean`).
- 미설치 언어를 요청하면 `rapidocr_onnxruntime` 번들에 포함된 중국어 PP-OCRv4 엔진으로 폴백되며, 로그에 경고가 남음.

## 검출 모델은 공용

text detection (DB) / classification (cls) 모델은 언어 무관하므로 `rapidocr_onnxruntime` 번들 그대로 사용. 이 폴더에는 인식(rec) 모델 + 사전(keys)만 들어갑니다.

## ReplayKit.bat의 자동 다운로드

배포된 `C:\ReplayKit\`에서 `ocr_models/korean/rec_infer.onnx`가 누락된 경우(예: 빌드 산출물에 깜빡 빠뜨림),
ReplayKit.bat이 부팅 시 `paddle2onnx` 설치 + 다운로드 스크립트를 자동 실행하여 폴백 복구합니다.
정상 빌드/배포 흐름에서는 이미 모델이 포함되어 있어 이 경로를 타지 않습니다.
