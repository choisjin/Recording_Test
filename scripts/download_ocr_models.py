"""OCR 다국어 모델 다운로드 스크립트.

각 언어에 대해:
  1) PaddleOCR 공식 미러(paddleocr.bj.bcebos.com)에서 `.tar` 다운로드 + 압축 해제
  2) paddle2onnx로 ONNX 변환
  3) PaddleOCR github에서 dict 파일 다운로드
  4) backend/app/services/ocr_models/{lang}/ 아래 배치

요구사항:
    pip install paddle2onnx
    (paddlepaddle은 inference 모델 변환에 필수가 아님 — paddle2onnx만으로 충분)

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


def _check_paddle2onnx() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "paddle2onnx", "--version"],
            check=True, capture_output=True, text=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


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
    """paddle2onnx로 .pdmodel + .pdiparams → .onnx 변환."""
    cmd = [
        sys.executable, "-m", "paddle2onnx",
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

    if not _check_paddle2onnx():
        print("paddle2onnx가 설치되어 있지 않습니다.", file=sys.stderr)
        print(f"  설치: {sys.executable} -m pip install paddle2onnx", file=sys.stderr)
        return 1

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
