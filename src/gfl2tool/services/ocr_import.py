from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .. import reference
from ..runtime_paths import install_root


@dataclass(frozen=True)
class OcrDollCandidate:
    doll_id: int
    name: str
    level: int = 60
    rank: int = 1


@dataclass(frozen=True)
class OcrOptionCandidate:
    option_key: str
    name: str
    level: int
    factor_type: str
    element_type: str
    source_line: str


def _project_root() -> Path:
    return install_root()


def find_tesseract() -> str | None:
    configured = str(os.environ.get("GFL2_TESSERACT_EXE") or "").strip()
    bundled_root = _project_root() / "ocr" / "engine"
    local_root = _project_root() / ".gfl2_runtime" / "ocr" / "engine"
    candidates = [configured]
    for name in ("tesseract.exe", "tesseract"):
        bundled = bundled_root / name
        if bundled.is_file():
            candidates.append(str(bundled))
    for name in ("tesseract.exe", "tesseract"):
        direct = local_root / name
        if direct.is_file():
            candidates.append(str(direct))
    if local_root.is_dir():
        try:
            candidates.extend(str(path) for path in local_root.rglob("tesseract.exe"))
        except OSError:
            pass
    candidates.append(shutil.which("tesseract") or "")
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                str(Path.home() / "AppData/Local/Programs/Tesseract-OCR/tesseract.exe"),
            ]
        )
    for raw in candidates:
        if raw and Path(raw).is_file():
            return str(Path(raw))
    return None




def ocr_subprocess_kwargs() -> dict[str, object]:
    """Keep Tesseract subprocesses invisible in Windows GUI builds."""
    if os.name != "nt":
        return {}
    kwargs: dict[str, object] = {
        "creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    }
    startup_cls = getattr(subprocess, "STARTUPINFO", None)
    if startup_cls is not None:
        startup = startup_cls()
        startup.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
        startup.wShowWindow = 0
        kwargs["startupinfo"] = startup
    return kwargs

def ocr_engine_status() -> dict[str, Any]:
    executable = find_tesseract()
    if not executable:
        return {"available": False, "executable": "", "languages": []}
    try:
        result = subprocess.run(
            [executable, "--list-langs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            env=os.environ.copy(),
            **ocr_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "executable": executable, "languages": []}
    stdout = result.stdout or ""
    languages = [line.strip() for line in stdout.splitlines()[1:] if line.strip()]
    return {
        "available": result.returncode == 0,
        "executable": executable,
        "languages": languages,
        "korean": "kor" in languages or "Hangul" in languages,
        "english": "eng" in languages,
    }


def _prepared_variants(path: str | Path) -> list[tuple[Image.Image, int]]:
    with Image.open(path) as source:
        gray = ImageOps.exif_transpose(source).convert("L")
    max_edge = max(gray.size)
    scale = min(5.0, max(2.0, 2200 / max(1, max_edge)))
    enlarged = gray.resize(
        (max(1, round(gray.width * scale)), max(1, round(gray.height * scale))),
        Image.Resampling.LANCZOS,
    )
    normal = ImageEnhance.Contrast(enlarged).enhance(1.55)
    sharp = normal.filter(ImageFilter.SHARPEN)
    high = ImageEnhance.Contrast(enlarged).enhance(2.15).filter(ImageFilter.SHARPEN)
    # Dark GF2 panels often OCR better in sparse-text mode; the normal full-row
    # pass remains first for screenshots containing several option rows.
    return [(sharp, 6), (normal, 11), (high, 11)]


def _run_ocr_variant(executable: str, image: Image.Image, *, language: str, psm: int) -> str:
    with tempfile.TemporaryDirectory(prefix="gfl2-ocr-") as temp_dir:
        temp = Path(temp_dir) / "source.png"
        image.save(temp, "PNG")
        try:
            result = subprocess.run(
                [executable, str(temp), "stdout", "-l", language, "--psm", str(psm)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
                env=os.environ.copy(),
                **ocr_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("OCR 처리 시간이 너무 오래 걸려 중단했습니다.") from exc
        except OSError as exc:
            raise RuntimeError(f"OCR 엔진을 실행하지 못했습니다: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()[-700:]
        raise RuntimeError(f"OCR 엔진이 오류를 반환했습니다: {detail}")
    return (result.stdout or "").strip()


def ocr_image(path: str | Path) -> str:
    executable = find_tesseract()
    if not executable:
        raise RuntimeError("OCR 엔진(Tesseract)을 찾지 못했습니다. 시작 프로그램에서 자동 설치를 다시 시도해 주세요.")
    status = ocr_engine_status()
    languages = set(status.get("languages") or [])
    if "kor" in languages and "eng" in languages:
        language = "kor+eng"
    elif "kor" in languages:
        language = "kor"
    elif "Hangul" in languages and "eng" in languages:
        language = "Hangul+eng"
    elif "Hangul" in languages:
        language = "Hangul"
    elif "eng" in languages:
        language = "eng"
    else:
        raise RuntimeError("Tesseract 언어 데이터가 없습니다. OCR 자동 설치/복구를 다시 실행해 주세요.")

    outputs: list[str] = []
    seen: set[str] = set()
    for image, psm in _prepared_variants(path):
        text = _run_ocr_variant(executable, image, language=language, psm=psm)
        key = re.sub(r"\s+", " ", text).strip()
        if key and key not in seen:
            seen.add(key)
            outputs.append(text)
        # A clean pass that already finds 2-3 known option names needs no third
        # subprocess; this keeps continuous OCR responsive.
        if len(parse_inventory_ocr("\n".join(outputs))[1]) >= 3:
            break
    return "\n--- OCR 보조 패스 ---\n".join(outputs).strip()


def _compact(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", str(text or "")).casefold()


def _explicit_option_level(line: str, *, maximum: int = 6) -> int | None:
    """Return a level only when the OCR line makes the number unambiguous.

    Full-screen GF2 screenshots contain many UI counters near the option rows. The
    old generic nearby-number search could therefore turn ``탁류 저항`` into
    Lv.5 simply because an unrelated 5 was printed on the same noisy OCR line.
    Prefer an explicit Lv token; plain trailing digits are accepted only on a
    short option-like line (needed for manual text such as ``공격 강화 3``).
    """
    match = re.search(r"(?i)(?:lv|[lwvi]{1,3})\s*[\.\)]?\s*([1-6])", line)
    if match:
        value = int(match.group(1))
        return value if 1 <= value <= maximum else None
    numbers = [int(value) for value in re.findall(r"(?<!\d)([1-6])(?!\d)", line)]
    compact_text = re.sub(r"\s+", " ", line).strip()
    if len(compact_text) <= 34 and len(numbers) == 1:
        return numbers[0]
    return None


def parse_inventory_ocr(text: str) -> tuple[list[OcrDollCandidate], list[OcrOptionCandidate]]:
    raw = str(text or "")
    compact = _compact(raw)
    dolls: list[OcrDollCandidate] = []
    seen_dolls: set[int] = set()
    for doll_id, name in reference.bundled_doll_display_names().items():
        token = _compact(name)
        if not token or token not in compact:
            continue
        if int(doll_id) in seen_dolls:
            continue
        seen_dolls.add(int(doll_id))
        dolls.append(OcrDollCandidate(int(doll_id), str(name), 60, 1))

    options = reference.remolding_options()
    aliases: list[tuple[str, str]] = []
    for key, meta in options.items():
        for alias in (meta.get("nameKR"), meta.get("nickname")):
            token = _compact(str(alias or ""))
            if len(token) >= 2:
                aliases.append((token, str(key)))
    # Current Korean client wording uses '저항' for the bulwark elemental row
    # in some menus even though older reference data called it '내성'. Keep both
    # aliases mapped to the same verified option key.
    element_prefixes = {
        7: "탁류", 8: "연소", 9: "빙결", 10: "전도", 11: "산성", 12: "물리",
    }
    for index, prefix in element_prefixes.items():
        key = f"bulwark_{index}"
        if key in options:
            aliases.append((_compact(f"{prefix} 저항"), key))
            aliases.append((_compact(f"{prefix} 내성"), key))
    aliases.sort(key=lambda item: len(item[0]), reverse=True)

    best: dict[str, OcrOptionCandidate] = {}
    recent_level: int | None = None
    recent_level_age = 99
    for line in raw.splitlines():
        line_compact = _compact(line)
        if not line_compact or line.startswith("--- OCR"):
            if line.startswith("--- OCR"):
                recent_level = None
                recent_level_age = 99
            continue
        level_match = re.search(r"(?i)(?:lv|[lwvi]{1,3})\s*[\.\)]?\s*([1-6])", line)
        if level_match:
            recent_level = int(level_match.group(1))
            recent_level_age = 0
        else:
            recent_level_age += 1
        matched_option = False
        for token, key in aliases:
            if token not in line_compact:
                continue
            meta = dict(options.get(key) or {})
            level = _explicit_option_level(line, maximum=6)
            # A separate Lv-only OCR line may precede the option text. Carry it
            # forward only until one option consumes it; never leak Lv.2 from
            # the previous option into a following row whose digit was missed.
            if level is None and recent_level is not None and recent_level_age <= 2:
                level = recent_level
            level = level or 1
            row = OcrOptionCandidate(
                option_key=key,
                name=str(meta.get("nameKR") or key),
                level=level,
                factor_type=str(meta.get("factorType") or ""),
                element_type=str(meta.get("elementType") or ""),
                source_line=line.strip(),
            )
            current = best.get(key)
            # Prefer the pass that retained an explicit Lv digit and more Hangul.
            score = (1 if re.search(r"(?i)lv\.?\s*[1-6]", line) else 0, sum("가" <= ch <= "힣" for ch in line), -len(line))
            if current is None:
                best[key] = row
            else:
                old_line = current.source_line
                old_score = (1 if re.search(r"(?i)lv\.?\s*[1-6]", old_line) else 0, sum("가" <= ch <= "힣" for ch in old_line), -len(old_line))
                if score > old_score:
                    best[key] = row
            matched_option = True
            break
        if matched_option:
            recent_level = None
            recent_level_age = 99
    # Recover partially read minor rows (e.g. ``lv1 AA 엘리트``) by using
    # the verified major-option family from the same remolding. This is much
    # safer than globally guessing from a generic word like "엘리트".
    factor_hint = ""
    for row in best.values():
        meta = options.get(row.option_key) or {}
        if bool(meta.get("isMajor")):
            factor_hint = str(meta.get("factorType") or "")
            break
    if not factor_hint and best:
        factors = [row.factor_type for row in best.values() if row.factor_type]
        if factors and len(set(factors)) == 1:
            factor_hint = factors[0]
    if factor_hint:
        generic_tokens = ("엘리트", "마스터", "특화", "태세", "대책", "협동", "휴식")
        factor_rows = [(key, dict(meta)) for key, meta in options.items() if str(meta.get("factorType") or "") == factor_hint]
        for line in raw.splitlines():
            compact_line = _compact(line)
            if not compact_line:
                continue
            for generic in generic_tokens:
                if _compact(generic) not in compact_line:
                    continue
                matches = [(key, meta) for key, meta in factor_rows if generic in str(meta.get("nameKR") or "")]
                if len(matches) != 1:
                    continue
                key, meta = matches[0]
                level = _explicit_option_level(line, maximum=6) or 1
                candidate = OcrOptionCandidate(
                    option_key=key, name=str(meta.get("nameKR") or key), level=level,
                    factor_type=factor_hint, element_type=str(meta.get("elementType") or ""), source_line=line.strip(),
                )
                current = best.get(key)
                if current is None or (level != current.level and re.search(r"\d", line)):
                    best[key] = candidate
                break

    candidates = list(best.values())
    # A single in-game remolding has at most three option rows. OCR of the
    # remolding title can contain an option-like fragment (e.g. "연소방출"
    # being mistaken for "연소 강화"). When more than three candidates are
    # present, prefer rows with an explicit Lv token and then concise
    # option-like lines. This keeps continuous OCR from rejecting a clean
    # three-option panel merely because its title produced one false positive.
    if len(candidates) > 3:
        def candidate_score(row: OcrOptionCandidate) -> tuple[int, int, int]:
            line = row.source_line
            explicit = 1 if re.search(r"(?i)(?:lv|[lwvi]{1,3})\s*[\.\)]?\s*[1-6]", line) else 0
            hangul = sum("가" <= ch <= "힣" for ch in line)
            return explicit, -max(0, len(line) - 24), hangul
        candidates = sorted(candidates, key=candidate_score, reverse=True)[:3]
    candidates.sort(key=lambda row: raw.find(row.source_line) if row.source_line in raw else 10**9)
    return dolls, candidates

