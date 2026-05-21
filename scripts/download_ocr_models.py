"""OCR 다국어 모델 다운로드 스크립트.

각 언어에 대해:
  1) PaddleOCR 공식 미러(paddleocr.bj.bcebos.com)에서 `.tar` 다운로드 + 압축 해제
  2) paddle2onnx로 ONNX 변환
  3) PaddleOCR github에서 dict 파일 다운로드
  4) backend/app/services/ocr_models/{lang}/ 아래 배치

요구사항:
    pip install "paddle2onnx<2.0" paddlepaddle
    (paddle2onnx 1.x — `python -m paddle2onnx.command` CLI 사용.
     paddle2onnx 2.x는 CLI 진입점이 제거됐고 인자도 바뀌어 이 스크립트와 호환되지 않음.
     paddle2onnx는 import 시 paddle을 필요로 함 — paddlepaddle CPU 빌드 ~150MB.
     변환만 끝나면 더 이상 paddle은 필요 없으므로 dist에는 포함하지 않는다.)

실행:
    python scripts/download_ocr_models.py              # 기본 4종(korean/english/japan/chinese)
    python scripts/download_ocr_models.py --all        # 지원하는 모든 언어
    python scripts/download_ocr_models.py korean japan # 특정 언어만

결과 디렉토리 구조:
    backend/app/services/ocr_models/
      korean/
        rec_infer.onnx
        rec_keys.txt
      english/
        rec_infer.onnx
        rec_keys.txt
      ...
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

# 프로젝트 루트(scripts/의 부모)
ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "backend" / "app" / "services" / "ocr_models"
TEMP_DIR = MODELS_DIR / "_tmp"

# (model_url, dict_url, dict_filename)
# - PP-OCRv3 multilingual: paddleocr.bj.bcebos.com/PP-OCRv3/multilingual/{lang}_PP-OCRv3_rec_infer.tar
# - PP-OCRv4 chinese/english: paddleocr.bj.bcebos.com/PP-OCRv4/{lang}/{lang}_PP-OCRv4_rec_infer.tar
# - dict 파일: github.com/PaddlePaddle/PaddleOCR release/2.7 브랜치
PADDLE_BASE = "https://paddleocr.bj.bcebos.com"
GH_DICT_BASE = "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/release/2.7/ppocr/utils"

LANG_MODELS = {
    "korean": {
        "model_url": f"{PADDLE_BASE}/PP-OCRv3/multilingual/korean_PP-OCRv3_rec_infer.tar",
        "dict_url":  f"{GH_DICT_BASE}/dict/korean_dict.txt",
    },
    "english": {
        # PP-OCRv4 영어 모델 (가장 정확)
        "model_url": f"{PADDLE_BASE}/PP-OCRv4/english/en_PP-OCRv4_rec_infer.tar",
        "dict_url":  f"{GH_DICT_BASE}/en_dict.txt",
    },
    "japan": {
        "model_url": f"{PADDLE_BASE}/PP-OCRv3/multilingual/japan_PP-OCRv3_rec_infer.tar",
        "dict_url":  f"{GH_DICT_BASE}/dict/japan_dict.txt",
    },
    "chinese": {
        # PP-OCRv4 중국어 (rapidocr_onnxruntime 번들과 동일하지만 일관성을 위해 별도 배치)
        "model_url": f"{PADDLE_BASE}/PP-OCRv4/chinese/ch_PP-OCRv4_rec_infer.tar",
        "dict_url":  f"{GH_DICT_BASE}/ppocr_keys_v1.txt",
    },
    "latin": {
        "model_url": f"{PADDLE_BASE}/PP-OCRv3/multilingual/latin_PP-OCRv3_rec_infer.tar",
        "dict_url":  f"{GH_DICT_BASE}/dict/latin_dict.txt",
    },
    "cyrillic": {
        "model_url": f"{PADDLE_BASE}/PP-OCRv3/multilingual/cyrillic_PP-OCRv3_rec_infer.tar",
        "dict_url":  f"{GH_DICT_BASE}/dict/cyrillic_dict.txt",
    },
    "arabic": {
        "model_url": f"{PADDLE_BASE}/PP-OCRv3/multilingual/arabic_PP-OCRv3_rec_infer.tar",
        "dict_url":  f"{GH_DICT_BASE}/dict/arabic_dict.txt",
    },
    "devanagari": {
        "model_url": f"{PADDLE_BASE}/PP-OCRv3/multilingual/devanagari_PP-OCRv3_rec_infer.tar",
        "dict_url":  f"{GH_DICT_BASE}/dict/devanagari_dict.txt",
    },
}

DEFAULT_LANGS = ["korean", "english", "japan", "chinese"]


def _check_paddle2onnx() -> tuple[bool, str]:
    """paddle2onnx + paddle 둘 다 import 가능 + CLI 호출 가능한지 확인.

    paddle2onnx 1.x: `python -m paddle2onnx.command --version` 동작.
    paddle2onnx 2.x: CLI 진입점이 제거되어 호환 안 됨 — 이 스크립트와 사용 불가.

    Returns:
        (ok, diagnostic): ok가 False면 diagnostic에 사용자에게 보여줄 진단 메시지.
    """
    # 1) import 가능 + 버전 확인
    p2o_version = None
    try:
        r = subprocess.run(
            [sys.executable, "-c", "import paddle2onnx; print(paddle2onnx.__version__)"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0:
            p2o_version = r.stdout.strip()
        else:
            return False, (
                "paddle2onnx import 실패:\n"
                f"  {r.stderr.strip() or '(no stderr)'}"
            )
    except Exception as e:
        return False, f"paddle2onnx import 시도 중 예외: {e}"

    # 2) 2.x는 CLI 구조가 바뀌어 미지원
    if p2o_version and p2o_version.startswith("2."):
        return False, (
            f"paddle2onnx {p2o_version} 감지 — 이 스크립트는 1.x만 지원합니다.\n"
            f"  다운그레이드:\n"
            f"    {sys.executable} -m pip uninstall -y paddle2onnx\n"
            f'    {sys.executable} -m pip install "paddle2onnx<2.0"'
        )

    # 3) CLI 호출 확인
    try:
        r = subprocess.run(
            [sys.executable, "-m", "paddle2onnx.command", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return True, f"paddle2onnx {p2o_version or '?'} 확인됨"
        return False, (
            f"paddle2onnx.command CLI 호출 실패 (rc={r.returncode}):\n"
            f"  stdout: {r.stdout.strip()}\n"
            f"  stderr: {r.stderr.strip()}"
        )
    except FileNotFoundError:
        return False, "Python interpreter를 찾을 수 없습니다."
    except Exception as e:
        return False, f"paddle2onnx CLI 실행 중 예외: {e}"


def _download(url: str, dest: Path) -> None:
    print(f"  fetching {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"  saved   {dest.name} ({size_mb:.2f} MB)")


def _extract_tar(tar_path: Path, extract_to: Path) -> Path:
    """tar 해제. 안에 단일 디렉토리가 들어있는 표준 PaddleOCR 구조 가정.
    .pdmodel + .pdiparams가 든 디렉토리 경로를 반환."""
    with tarfile.open(tar_path, "r") as tf:
        tf.extractall(extract_to)
    # 첫 번째 디렉토리 찾기 (압축 안의 inference 폴더)
    for child in extract_to.iterdir():
        if child.is_dir():
            return child
    raise RuntimeError(f"tar 안에 디렉토리 없음: {tar_path}")


def _convert_to_onnx(infer_dir: Path, out_onnx: Path) -> None:
    """paddle2onnx로 .pdmodel + .pdiparams → .onnx 변환.
    CLI 진입점은 `paddle2onnx.command` (entry point)."""
    cmd = [
        sys.executable, "-m", "paddle2onnx.command",
        "--model_dir", str(infer_dir),
        "--model_filename", "inference.pdmodel",
        "--params_filename", "inference.pdiparams",
        "--save_file", str(out_onnx),
        "--opset_version", "14",
        "--enable_onnx_checker", "True",
    ]
    print(f"  converting → {out_onnx.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  paddle2onnx stderr:\n{result.stderr}", file=sys.stderr)
        raise RuntimeError(f"paddle2onnx 변환 실패: {infer_dir}")
    size_mb = out_onnx.stat().st_size / (1024 * 1024)
    print(f"  saved   {out_onnx.name} ({size_mb:.2f} MB)")


def install_language(lang: str) -> bool:
    cfg = LANG_MODELS.get(lang)
    if cfg is None:
        print(f"[{lang}] 지원하지 않는 언어 — 건너뜀")
        return False
    out_dir = MODELS_DIR / lang
    out_onnx = out_dir / "rec_infer.onnx"
    out_dict = out_dir / "rec_keys.txt"
    if out_onnx.exists() and out_dict.exists():
        print(f"[{lang}] 이미 설치됨 — 건너뜀 ({out_onnx})")
        return True

    print(f"[{lang}] 다운로드 시작")
    out_dir.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    # 1) dict 다운로드
    _download(cfg["dict_url"], out_dict)

    # 2) tar 다운로드 + 압축 해제
    tar_path = TEMP_DIR / f"{lang}_rec_infer.tar"
    _download(cfg["model_url"], tar_path)
    extract_root = TEMP_DIR / f"{lang}_extracted"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    infer_dir = _extract_tar(tar_path, extract_root)

    # 3) ONNX 변환
    try:
        _convert_to_onnx(infer_dir, out_onnx)
    except Exception as e:
        print(f"[{lang}] 변환 실패: {e}", file=sys.stderr)
        return False
    finally:
        # 임시 파일 정리
        try:
            tar_path.unlink(missing_ok=True)
            shutil.rmtree(extract_root, ignore_errors=True)
        except Exception:
            pass

    print(f"[{lang}] 완료 → {out_dir}")
    return True


def main():
    parser = argparse.ArgumentParser(description="다국어 OCR 모델 다운로드 + ONNX 변환")
    parser.add_argument("langs", nargs="*", help="설치할 언어 (생략 시 기본 4종)")
    parser.add_argument("--all", action="store_true", help="지원하는 모든 언어 설치")
    parser.add_argument("--force", action="store_true", help="이미 설치된 언어도 재다운로드")
    args = parser.parse_args()

    if args.all:
        langs = list(LANG_MODELS.keys())
    elif args.langs:
        langs = args.langs
    else:
        langs = DEFAULT_LANGS

    unknown = [l for l in langs if l not in LANG_MODELS]
    if unknown:
        print(f"지원하지 않는 언어: {unknown}", file=sys.stderr)
        print(f"지원 목록: {list(LANG_MODELS.keys())}", file=sys.stderr)
        return 2

    ok, diag = _check_paddle2onnx()
    if not ok:
        print("paddle2onnx + paddlepaddle이 동작 가능한 상태가 아닙니다.", file=sys.stderr)
        print(f"  진단: {diag}", file=sys.stderr)
        print(file=sys.stderr)
        print("  최초 설치:", file=sys.stderr)
        print(f'    {sys.executable} -m pip install "paddle2onnx<2.0" paddlepaddle', file=sys.stderr)
        print("  (paddle2onnx 1.x 필수 — 2.x는 CLI 구조 변경으로 호환 안 됨)", file=sys.stderr)
        print("  (paddle2onnx는 paddle을 import하므로 paddlepaddle CPU 빌드 ~150MB 필요)", file=sys.stderr)
        return 1
    print(f"[check] {diag}")

    if args.force:
        for lang in langs:
            out_dir = MODELS_DIR / lang
            shutil.rmtree(out_dir, ignore_errors=True)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    failed: list[str] = []
    for lang in langs:
        try:
            if not install_language(lang):
                failed.append(lang)
        except Exception as e:
            print(f"[{lang}] 예외: {e}", file=sys.stderr)
            failed.append(lang)

    # 임시 디렉토리 청소
    shutil.rmtree(TEMP_DIR, ignore_errors=True)

    print("\n=== 요약 ===")
    print(f"성공: {[l for l in langs if l not in failed]}")
    if failed:
        print(f"실패: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
