from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
import subprocess
import tempfile
from statistics import median
from typing import Callable, Iterable

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

from . import reference
from .tactics import MAX_GRID_EDGE, MIN_GRID_EDGE, Tactic, TacticMarker, TacticStep
from .services.ocr_import import find_tesseract, ocr_subprocess_kwargs

ANALYSIS_MAX_EDGE = 2200
MAX_INPUT_PIXELS = 64_000_000

Progress = Callable[[str], None]


def _safe_progress(progress: Progress | None, text: str) -> None:
    if progress is not None:
        progress(str(text))



@dataclass(frozen=True)
class DetectedBoard:
    box: tuple[int, int, int, int]
    rows: int
    cols: int
    confidence: float
    markers: tuple[TacticMarker, ...] = ()


@dataclass(frozen=True)
class TacticImageImportResult:
    tactic: Tactic
    boards: tuple[DetectedBoard, ...]
    warnings: tuple[str, ...] = ()

    @property
    def confidence(self) -> float:
        if not self.boards:
            return 0.0
        return sum(board.confidence for board in self.boards) / len(self.boards)

    def selected_tactic(
        self,
        indexes: Iterable[int],
        *,
        formation_indexes: Iterable[int] | None = None,
    ) -> Tactic:
        selected = []
        seen: set[int] = set()
        for raw_index in indexes:
            index = int(raw_index)
            if index in seen or not (0 <= index < len(self.tactic.steps)):
                continue
            seen.add(index)
            selected.append(index)
        if not selected:
            raise ValueError("가져올 격자를 하나 이상 선택해 주세요.")

        formation_set = (
            {int(value) for value in formation_indexes}
            if formation_indexes is not None
            else {
                index
                for index, step in enumerate(self.tactic.steps)
                if str(step.name or "").strip() in {"배치", "제대 배치"}
            }
        )
        steps: list[TacticStep] = []
        combat_index = 0
        for output_index, source_index in enumerate(selected):
            source = self.tactic.steps[source_index]
            rows, cols = self.tactic.grid_size(source_index)
            is_formation = source_index in formation_set
            if is_formation:
                output_name = "제대 배치"
            else:
                combat_index += 1
                output_name = f"T{combat_index}"
            steps.append(
                TacticStep.from_dict(
                    {
                        "name": output_name,
                        "note": source.note,
                        "cycle": "" if is_formation else source.cycle,
                        "markers": [marker.__dict__ for marker in source.markers],
                        "rows": rows,
                        "cols": cols,
                    },
                    rows=rows,
                    cols=cols,
                    index=output_index,
                )
            )
        first_rows = int(steps[0].rows or self.tactic.rows)
        first_cols = int(steps[0].cols or self.tactic.cols)
        return Tactic(
            title=self.tactic.title,
            category=self.tactic.category,
            rows=first_rows,
            cols=first_cols,
            steps=steps,
            show_previous=self.tactic.show_previous,
        )


def _runs(bits: Iterable[bool]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start: int | None = None
    previous = -2
    for index, enabled in enumerate(bits):
        if enabled:
            if start is None:
                start = index
            previous = index
            continue
        if start is not None:
            out.append((start, previous))
            start = None
    if start is not None:
        out.append((start, previous))
    return out


def _mark_long_runs(candidate: bytearray, width: int, height: int, minimum: int) -> bytearray:
    network = bytearray(width * height)
    for y in range(height):
        base = y * width
        start = -1
        for x in range(width + 1):
            active = x < width and candidate[base + x]
            if active and start < 0:
                start = x
            elif not active and start >= 0:
                if x - start >= minimum:
                    network[base + start:base + x] = b"\x01" * (x - start)
                start = -1
    for x in range(width):
        start = -1
        for y in range(height + 1):
            active = y < height and candidate[y * width + x]
            if active and start < 0:
                start = y
            elif not active and start >= 0:
                if y - start >= minimum:
                    for yy in range(start, y):
                        network[yy * width + x] = 1
                start = -1
    return network


def _component_boxes(mask: bytearray, width: int, height: int) -> list[tuple[int, int, int, int]]:
    seen = bytearray(width * height)
    boxes: list[tuple[int, int, int, int]] = []
    for origin in range(width * height):
        if not mask[origin] or seen[origin]:
            continue
        stack = [origin]
        seen[origin] = 1
        min_x = max_x = origin % width
        min_y = max_y = origin // width
        count = 0
        while stack:
            current = stack.pop()
            count += 1
            x = current % width
            y = current // width
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            if x > 0:
                nxt = current - 1
                if mask[nxt] and not seen[nxt]:
                    seen[nxt] = 1
                    stack.append(nxt)
            if x + 1 < width:
                nxt = current + 1
                if mask[nxt] and not seen[nxt]:
                    seen[nxt] = 1
                    stack.append(nxt)
            if y > 0:
                nxt = current - width
                if mask[nxt] and not seen[nxt]:
                    seen[nxt] = 1
                    stack.append(nxt)
            if y + 1 < height:
                nxt = current + width
                if mask[nxt] and not seen[nxt]:
                    seen[nxt] = 1
                    stack.append(nxt)
        box_w = max_x - min_x + 1
        box_h = max_y - min_y + 1
        if count >= 100 and box_w >= 160 and box_h >= 160:
            boxes.append((min_x, min_y, box_w, box_h))
    return boxes


def _group_centers(values: list[int], threshold: float) -> list[int]:
    enabled = [value >= threshold for value in values]
    return [round((start + end) / 2) for start, end in _runs(enabled)]


def _estimate_spacing(points: list[int]) -> float | None:
    if len(points) < 5:
        return None
    diffs = [b - a for a, b in zip(points, points[1:]) if 8 <= b - a <= 110]
    if len(diffs) < 3:
        return None
    smallest = min(diffs)
    compact = [value for value in diffs if value <= smallest * 1.55]
    return float(median(compact or diffs))


def _projection(gray: Image.Image, box: tuple[int, int, int, int]) -> tuple[list[int], list[int]]:
    x0, y0, width, height = box
    pixels = gray.load()
    cols = [0] * width
    rows = [0] * height
    for yy in range(height):
        y = y0 + yy
        row_count = 0
        for xx in range(width):
            value = pixels[x0 + xx, y]
            if 70 <= value <= 235:
                cols[xx] += 1
                row_count += 1
        rows[yy] = row_count
    return cols, rows


def _grid_shape(gray: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, float] | None:
    _x, _y, width, height = box
    col_strength, row_strength = _projection(gray, box)
    vertical = _group_centers(col_strength, height * 0.50)
    horizontal = _group_centers(row_strength, width * 0.50)
    sx = _estimate_spacing(vertical)
    sy = _estimate_spacing(horizontal)
    candidates = [value for value in (sx, sy) if value is not None]
    if not candidates:
        return None
    spacing = float(median(candidates))
    cols = round((width - 1) / spacing)
    rows = round((height - 1) / spacing)
    if not (MIN_GRID_EDGE <= rows <= MAX_GRID_EDGE and MIN_GRID_EDGE <= cols <= MAX_GRID_EDGE):
        return None
    aspect_error = abs((width / max(1, cols)) - (height / max(1, rows))) / max(1.0, spacing)
    expected_lines = rows + cols + 2
    found_lines = len(vertical) + len(horizontal)
    line_score = min(1.0, found_lines / max(1, expected_lines))
    confidence = max(0.0, min(1.0, line_score * (1.0 - min(0.5, aspect_error))))
    return rows, cols, confidence


def _cell_bounds(box: tuple[int, int, int, int], rows: int, cols: int, row: int, col: int) -> tuple[int, int, int, int]:
    x0, y0, width, height = box
    left = round(x0 + col * (width - 1) / cols)
    right = round(x0 + (col + 1) * (width - 1) / cols)
    top = round(y0 + row * (height - 1) / rows)
    bottom = round(y0 + (row + 1) * (height - 1) / rows)
    return left, top, max(left + 1, right), max(top + 1, bottom)


def _detect_boss(rgb: Image.Image, box: tuple[int, int, int, int], rows: int, cols: int) -> TacticMarker | None:
    x0, y0, width, height = box
    pixels = rgb.load()
    count = 0
    left = x0 + width
    right = x0 - 1
    top = y0 + height
    bottom = y0 - 1
    for y in range(y0, y0 + height):
        for x in range(x0, x0 + width):
            r, g, b = pixels[x, y][:3]
            if r >= 205 and 70 <= g <= 175 and b <= 105 and r - b >= 100:
                count += 1
                left = min(left, x)
                right = max(right, x)
                top = min(top, y)
                bottom = max(bottom, y)
    if count < max(80, width * height // 250):
        return None
    cell_w = (width - 1) / cols
    cell_h = (height - 1) / rows
    col = max(0, min(cols - 1, round((left - x0) / cell_w)))
    row = max(0, min(rows - 1, round((top - y0) / cell_h)))
    marker_w = max(1, min(cols - col, round((right - left + 1) / cell_w)))
    marker_h = max(1, min(rows - row, round((bottom - top + 1) / cell_h)))
    return TacticMarker(kind="boss", row=row, col=col, width=marker_w, height=marker_h, label="보스")


def _dark_fraction(gray: Image.Image, bounds: tuple[int, int, int, int], *, threshold: int) -> float:
    left, top, right, bottom = bounds
    pixels = gray.load()
    count = 0
    dark = 0
    for y in range(top, bottom):
        for x in range(left, right):
            count += 1
            if pixels[x, y] <= threshold:
                dark += 1
    return dark / max(1, count)


def _detect_blocked(gray: Image.Image, box: tuple[int, int, int, int], rows: int, cols: int, boss: TacticMarker | None) -> list[TacticMarker]:
    markers: list[TacticMarker] = []
    for row in range(rows):
        for col in range(cols):
            if boss and boss.row <= row < boss.row + boss.height and boss.col <= col < boss.col + boss.width:
                continue
            left, top, right, bottom = _cell_bounds(box, rows, cols, row, col)
            pad_x = max(2, (right - left) // 5)
            pad_y = max(2, (bottom - top) // 5)
            inner = (left + pad_x, top + pad_y, right - pad_x, bottom - pad_y)
            if inner[2] <= inner[0] or inner[3] <= inner[1]:
                continue
            if _dark_fraction(gray, inner, threshold=70) >= 0.52:
                markers.append(TacticMarker(kind="blocked", row=row, col=col))
    return markers


def _edge_fraction(gray: Image.Image, bounds: tuple[int, int, int, int], edge: str) -> float:
    left, top, right, bottom = bounds
    width = right - left
    height = bottom - top
    band = max(2, round(min(width, height) * 0.16))
    if edge == "N":
        region = (left + 2, top, right - 2, min(bottom, top + band))
    elif edge == "S":
        region = (left + 2, max(top, bottom - band), right - 2, bottom)
    elif edge == "W":
        region = (left, top + 2, min(right, left + band), bottom - 2)
    else:
        region = (max(left, right - band), top + 2, right, bottom - 2)
    l, t, r, b = region
    if r <= l or b <= t:
        return 0.0
    pixels = gray.load()
    matched = 0
    total = 0
    for y in range(t, b):
        for x in range(l, r):
            total += 1
            value = pixels[x, y]
            if 75 <= value <= 185:
                matched += 1
    return matched / max(1, total)


def _detect_cover(
    gray: Image.Image,
    box: tuple[int, int, int, int],
    rows: int,
    cols: int,
    boss: TacticMarker | None,
) -> list[TacticMarker]:
    markers: list[TacticMarker] = []
    for row in range(rows):
        for col in range(cols):
            if boss and boss.row <= row < boss.row + boss.height and boss.col <= col < boss.col + boss.width:
                continue
            if row in {0, rows - 1} or col in {0, cols - 1}:
                continue
            bounds = _cell_bounds(box, rows, cols, row, col)
            edges = "".join(edge for edge in "NESW" if _edge_fraction(gray, bounds, edge) >= 0.58)
            if edges:
                markers.append(TacticMarker(kind="cover", row=row, col=col, edges=edges))
    return markers


def _detect_unit_placeholders(gray: Image.Image, box: tuple[int, int, int, int], rows: int, cols: int, occupied: set[tuple[int, int]]) -> list[TacticMarker]:
    markers: list[TacticMarker] = []
    for row in range(rows):
        for col in range(cols):
            if (row, col) in occupied:
                continue
            left, top, right, bottom = _cell_bounds(box, rows, cols, row, col)
            pad_x = max(3, (right - left) // 4)
            pad_y = max(3, (bottom - top) // 4)
            inner = (left + pad_x, top + pad_y, right - pad_x, bottom - pad_y)
            fraction = _dark_fraction(gray, inner, threshold=65)
            if 0.035 <= fraction <= 0.40:
                markers.append(TacticMarker(kind="unit", row=row, col=col, label="?"))
    return markers


def _analysis_image(path: str | Path) -> tuple[Image.Image, tuple[int, int]]:
    try:
        with Image.open(path) as opened:
            raw_width, raw_height = int(opened.width), int(opened.height)
            if raw_width <= 0 or raw_height <= 0:
                raise ValueError("이미지 크기가 올바르지 않습니다.")
            if raw_width * raw_height > MAX_INPUT_PIXELS:
                raise ValueError(
                    f"이미지가 너무 큽니다. {MAX_INPUT_PIXELS // 1_000_000}MP 이하 이미지를 사용해 주세요."
                )
            oriented = ImageOps.exif_transpose(opened)
            original_size = (int(oriented.width), int(oriented.height))
            source = oriented.convert("RGB")
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"이미지를 열지 못했습니다: {exc}") from exc

    if max(source.size) > ANALYSIS_MAX_EDGE:
        scale = ANALYSIS_MAX_EDGE / max(source.size)
        source = source.resize(
            (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return source, original_size


def _box_to_original(
    box: tuple[int, int, int, int],
    analysis_size: tuple[int, int],
    original_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    if analysis_size == original_size:
        return box
    x, y, width, height = box
    sx = original_size[0] / max(1, analysis_size[0])
    sy = original_size[1] / max(1, analysis_size[1])
    left = max(0, min(original_size[0] - 1, round(x * sx)))
    top = max(0, min(original_size[1] - 1, round(y * sy)))
    right = max(left + 1, min(original_size[0], round((x + width) * sx)))
    bottom = max(top + 1, min(original_size[1], round((y + height) * sy)))
    return left, top, right - left, bottom - top


def _normalize_ocr_polarity(gray: Image.Image) -> Image.Image:
    """Return a high-contrast grayscale image with dark glyphs on a light background.

    Community tactic sheets use both white-on-dark unit badges and dark-on-light
    skill tables. Tesseract is considerably more stable when the local crop is
    normalized to dark text on a light background before scaling.
    """
    prepared = ImageOps.autocontrast(gray.convert("L"))
    width, height = prepared.size
    if width <= 0 or height <= 0:
        return prepared
    band_x = max(1, width // 8)
    band_y = max(1, height // 8)
    border = Image.new("L", (width, max(1, band_y * 2)))
    border.paste(prepared.crop((0, 0, width, band_y)), (0, 0))
    border.paste(prepared.crop((0, max(0, height - band_y), width, height)), (0, band_y))
    border_mean = float(ImageStat.Stat(border).mean[0])
    if border_mean < 118:
        prepared = ImageOps.invert(prepared)
    return prepared


def _clean_ocr_text(text: str) -> str:
    lines: list[str] = []
    for raw in str(text or "").replace("\x0c", "\n").splitlines():
        line = " ".join(raw.strip().split())
        if not line:
            continue
        # Skill-cycle rows are compact. Reject long OCR prose/noise read from
        # notes below the board and heavily latin-dominated fragments.
        meaningful = sum(ch.isalnum() or ("가" <= ch <= "힣") for ch in line)
        hangul = sum("가" <= ch <= "힣" for ch in line)
        digits = sum(ch.isdigit() for ch in line)
        latin = sum(ch.isascii() and ch.isalpha() for ch in line)
        if meaningful < 2 or len(line) > 48:
            continue
        if latin > max(2, hangul + digits):
            continue
        lines.append(line)
    return " / ".join(lines)[:2000]


def _ocr_score(text: str) -> float:
    value = str(text or "")
    hangul = sum("가" <= ch <= "힣" for ch in value)
    digits = sum(ch.isdigit() for ch in value)
    latin = sum(ch.isascii() and ch.isalpha() for ch in value)
    punctuation = sum(ch in "()[]*+-/·.," for ch in value)
    # Skill rows are mostly short Korean doll aliases plus level digits. Latin
    # fragments are commonly OCR noise on Korean community sheets.
    noise_penalty = max(0, len(value) - 70) * 0.35
    return hangul * 4.0 + digits * 2.2 + punctuation * 0.3 - latin * 1.1 + min(len(value), 70) * 0.04 - noise_penalty


def _tesseract_executable() -> str | None:
    # Reuse the same locator as inventory/remolding OCR.  In packaged builds
    # Tesseract can live under .gfl2_runtime/ocr/engine and need not be present
    # on the system PATH. Keeping a single locator prevents tactic OCR from
    # silently degrading to '?' while the other OCR screen still works.
    return find_tesseract()


def _run_tesseract(
    executable: str,
    image: Image.Image,
    *,
    psm: int,
    language: str = "kor+eng",
    configs: dict[str, object] | None = None,
) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        image.save(temp_path, "PNG")
        command = [
            executable,
            str(temp_path),
            "stdout",
            "-l",
            language,
            "--psm",
            str(psm),
            "--dpi",
            "300",
        ]
        for key, value in (configs or {}).items():
            command.extend(["-c", f"{key}={value}"])
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            check=False,
            **ocr_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            return ""
        return _clean_ocr_text(completed.stdout or "")
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _tesseract_text(image: Image.Image) -> str | None:
    executable = _tesseract_executable()
    if not executable:
        return None
    prepared = _normalize_ocr_polarity(image.convert("L"))
    if max(prepared.size) < 2200:
        scale = min(3.0, 2200 / max(1, max(prepared.size)))
        prepared = prepared.resize(
            (max(1, round(prepared.width * scale)), max(1, round(prepared.height * scale))),
            Image.Resampling.LANCZOS,
        )
    prepared = ImageEnhance.Contrast(prepared).enhance(1.35)
    prepared = prepared.filter(ImageFilter.UnsharpMask(radius=1.2, percent=145, threshold=3))

    # Community sheets are usually downscaled several times before sharing.
    # Preserve antialiased Hangul while also trying two binarizations so thin
    # strokes and table borders do not dominate a single OCR pass.
    binary_mid = prepared.point(lambda value: 255 if value >= 178 else 0)
    binary_light = prepared.point(lambda value: 255 if value >= 205 else 0)
    code_configs = {"load_system_dawg": 0, "load_freq_dawg": 0}
    candidates = (
        _run_tesseract(executable, prepared, psm=7, configs=code_configs),
        _run_tesseract(executable, binary_mid, psm=7, configs=code_configs),
        _run_tesseract(executable, binary_mid, psm=6, configs=code_configs),
        _run_tesseract(executable, binary_light, psm=7, configs=code_configs),
        _run_tesseract(executable, prepared, psm=11, configs={**code_configs, "thresholding_method": 1}),
        _run_tesseract(executable, prepared, psm=11, configs={**code_configs, "thresholding_method": 2}),
    )
    return max(candidates, key=_ocr_score, default="")


def _cycle_band(source: Image.Image, board: DetectedBoard) -> Image.Image | None:
    x, y, width, height = board.box
    top = min(source.height, y + height + 2)
    if top >= source.height - 4:
        return None
    # Find the bottom border of the compact skill table instead of using a
    # fixed-height band. This prevents comments directly below T1/T4 from
    # leaking into OCR while keeping multi-line cells such as "1(흑)평3".
    gray = source.convert("L")
    pixels = gray.load()
    right = min(source.width, x + width)
    scan_bottom = min(source.height, top + 78)
    bottom = 0
    for yy in range(min(scan_bottom, top + 24), scan_bottom):
        matched = 0
        total = max(1, right - x)
        for xx in range(x, right):
            value = pixels[xx, yy]
            if 175 <= value <= 245:
                matched += 1
        if matched / total >= 0.62:
            bottom = yy + 1
            break
    if not bottom:
        bottom = min(source.height, top + 48)
    if bottom - top < 22:
        return None
    band = source.crop((max(0, x), top, right, bottom))
    # A real cycle strip has visible ink. Blank overview-map margins should not
    # be converted into strings like "00000" by OCR.
    ink = _dark_fraction(band.convert("L"), (0, 0, band.width, band.height), threshold=175)
    return band if ink >= 0.008 else None


def _leading_roster_alias(text: str, roster: tuple[str, ...]) -> str:
    match = re.match(r"^\s*([가-힣])", str(text or ""))
    if not match:
        return ""
    label = match.group(1)
    return label if label in roster else ""


def _clean_cycle_cell_value(text: str) -> str:
    value = " ".join(str(text or "").replace("\r", "").replace("\n", " / ").replace("/", " / ").split())
    # `미X` is the community-sheet shorthand for ending the doll's turn without
    # using a skill.  Korean-only OCR used to discard the Latin X entirely;
    # normalize the common Unicode/Latin lookalikes before scoring candidates.
    value = re.sub(r"([가-힣])\s*(?:[xX×✕✖])(?=\s*(?:/|$|·))", r"\1X", value)
    parts = [part.strip() for part in value.split("/")]

    # A wrapped cell may put the final skill digits on their own second line,
    # e.g. `린 고(미)` + `32`.  Keep it as one action rather than two unrelated
    # fragments.  Limit this to a pure short numeric continuation so legitimate
    # two-line prose remains untouched.
    if len(parts) == 2:
        first, second = parts
        continuation = re.sub(r"[^0-9]", "", second)
        second_noise = re.sub(r"[0-9\s.,'`~_+=\-]", "", second)
        if first and 1 <= len(continuation) <= 5 and len(second_noise) <= 1 and not re.search(r"\d{2,}$", first):
            value = f"{first.rstrip()}{continuation}"
            parts = [value]

    # Tesseract often splits a wrapped numeric suffix into a second line. Join
    # a single trailing digit even when the tiny glyph carries punctuation or
    # one junk consonant (e.g. `. 2344 / ㄱ- 4` -> `23444`). This is deliberately
    # narrow so real two-line skill text is not collapsed into a number.
    if len(parts) >= 2:
        head = parts[0].rstrip()
        head_digits = re.search(r"(\d{2,})$", head)
        tail_digits = [re.sub(r"\D", "", part) for part in parts[1:]]
        tail_visible = [re.sub(r"[\d\s.,'`~_+=\-]", "", part) for part in parts[1:]]
        if (
            head_digits
            and tail_digits
            and all(len(digits) == 1 for digits in tail_digits if digits)
            and all(len(noise) <= 1 for noise in tail_visible)
            and all(digits for digits in tail_digits)
        ):
            value = head[:head_digits.start(1)] + head_digits.group(1) + "".join(tail_digits)
            parts = [value]

    # Multi-line cells such as `1(흑) / 로 평3` describe one doll action. Put
    # the roster-like leading syllable first and keep the parenthetical action
    # together so the exported/overlay cycle reads naturally.
    if len(parts) == 2:
        first, second = parts
        action = re.fullmatch(r"\s*(\d+\s*\([가-힣*]\))\s*", first)
        tail = re.fullmatch(r"\s*([가-힣])\s*([^/]*)", second)
        if action and tail and "평" in tail.group(2):
            cleaned_tail = re.sub(r"[^0-9평]", "", tail.group(2))
            value = f"{tail.group(1)} {action.group(1).replace(' ', '')}{cleaned_tail}"

    # Isolated Hangul jamo are common tiny-font OCR debris (e.g. `ㅠ 2344`)
    # but are not valid doll aliases or skill notation in these compact cells.
    # Tiny asterisks are frequently read as 0/O/° inside parenthetical
    # summon notation (``로 3(*)1``). This substitution is intentionally
    # restricted to parentheses so legitimate skill digits remain untouched.
    value = re.sub(r"\((?:0|O|o|°|·)\)", "(*)", value)
    value = re.sub(r"([가-힣])\s*(?:[xX×✕✖])(?=\s*(?:$|·))", r"\1X", value)
    value = re.sub(r"[ㄱ-ㅎㅏ-ㅣ]+", " ", value)
    value = re.sub(r"(?<!\d)[.,;:'`~_+=-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" ·/")
    return value[:120]


def _cycle_cell_is_plausible(value: str) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 56:
        return False
    # A compact skill-cycle cell should contain an action marker: a skill
    # digit, no-op X, normal attack marker, or parenthetical target/summon.
    if not (re.search(r"\d", text) or "X" in text or "평" in text or "(" in text):
        return False
    latin = [ch for ch in text if ch.isascii() and ch.isalpha() and ch.upper() != "X"]
    if len(latin) > 1:
        return False
    return True


def _cycle_cell_text(executable: str, image: Image.Image, *, roster: tuple[str, ...] = ()) -> str:
    gray = image.convert("L")
    if _dark_fraction(gray, (0, 0, gray.width, gray.height), threshold=190) < 0.012:
        return ""
    prepared = _normalize_ocr_polarity(gray)
    scale = min(10.0, max(5.0, 360 / max(1, min(prepared.size))))
    prepared = prepared.resize(
        (max(1, round(prepared.width * scale)), max(1, round(prepared.height * scale))),
        Image.Resampling.LANCZOS,
    )
    prepared = ImageEnhance.Contrast(prepared).enhance(1.45)
    prepared = prepared.filter(ImageFilter.UnsharpMask(radius=0.9, percent=150, threshold=2))
    binary = prepared.point(lambda value: 255 if value >= 184 else 0)
    code_configs = {
        "load_system_dawg": 0,
        "load_freq_dawg": 0,
        "preserve_interword_spaces": 1,
    }
    # Accuracy-first ensemble.  These four passes were the most reliable mix in
    # the pre-fast-path importer: line/cell segmentation, Korean-only glyphs,
    # and one binarized fallback.  Release builds favor this stable voting over
    # the faster early-return heuristic because a wrong cycle is harder to spot
    # than a slightly longer import.
    candidates = [
        _run_tesseract(executable, prepared, psm=6, language="kor+eng", configs=code_configs),
        _run_tesseract(executable, prepared, psm=7, language="kor+eng", configs=code_configs),
        _run_tesseract(executable, prepared, psm=6, language="kor", configs=code_configs),
        _run_tesseract(executable, binary, psm=6, language="kor+eng", configs=code_configs),
    ]
    candidates = [_clean_cycle_cell_value(value) for value in candidates if value]
    candidates = [value for value in candidates if _cycle_cell_is_plausible(value)]
    if not candidates:
        return ""

    aliases = [alias for alias in (_leading_roster_alias(value, roster) for value in candidates) if alias]
    alias = next((value for value in aliases if aliases.count(value) >= 2), aliases[0] if aliases else "")

    def score(value: str) -> float:
        base = _ocr_score(value)
        base += sum(ch.isdigit() for ch in value) * 1.4
        if _leading_roster_alias(value, roster):
            # A correct alias is useful, but should not outweigh a much clearer
            # multi-digit skill sequence from another OCR pass. Missing aliases
            # are repaired by the five-cell roster consensus below.
            base += 3.0
        if roster:
            unexpected = sum(1 for ch in value if "가" <= ch <= "힣" and ch not in roster and ch != "평")
            base -= unexpected * 4.5
        if any(token in value for token in ("(", ")", "*", "평")):
            base += 1.8
        if re.search(r"[가-힣]X(?:$|\s|·)", value):
            base += 5.0
        return base

    best = max(candidates, key=score)
    if alias and not _leading_roster_alias(best, roster):
        best = f"{alias} {best}".strip()
    return _clean_cycle_cell_value(best)


def _segmented_cycle_text(
    executable: str,
    band: Image.Image,
    *,
    cells: int = 5,
    roster: tuple[str, ...] = (),
) -> str:
    chunks: list[str] = []
    for index in range(cells):
        left = round(index * band.width / cells)
        right = round((index + 1) * band.width / cells)
        pad_x = max(2, round((right - left) * 0.055))
        pad_y = max(1, round(band.height * 0.08))
        crop = band.crop(
            (
                min(right - 1, left + pad_x),
                min(band.height - 1, pad_y),
                max(left + pad_x + 1, right - pad_x),
                max(pad_y + 1, band.height - pad_y),
            )
        )
        chunks.append(_cycle_cell_text(executable, crop, roster=roster))

    # A five-person tactic row normally contains every roster alias once. If at
    # least three cells are already exact, use the remaining aliases to repair
    # the one/two noisy leading syllables without touching skill text.
    if len(roster) >= 3:
        leading = [_leading_roster_alias(chunk, roster) for chunk in chunks]
        present = {value for value in leading if value}
        missing = [value for value in roster if value not in present]
        ambiguous = [index for index, value in enumerate(leading) if not value and chunks[index]]
        if len(present) >= 3 and len(missing) == len(ambiguous) and 0 < len(missing) <= 2:
            remaining = list(missing)
            for idx in ambiguous:
                match = re.match(r"^(\s*)([가-힣])", chunks[idx])
                if len(remaining) == 1:
                    replacement = remaining.pop()
                elif match:
                    observed = match.group(2)
                    parts = _hangul_parts(observed)
                    def distance(candidate: str) -> int:
                        cparts = _hangul_parts(candidate)
                        if parts is None or cparts is None:
                            return 9
                        return sum(a != b for a, b in zip(parts, cparts))
                    replacement = min(remaining, key=distance)
                    remaining.remove(replacement)
                else:
                    # No leading Hangul at all: leave the more uncertain cell
                    # for last when there are two candidates.
                    continue
                if match:
                    start, end = match.span(2)
                    chunks[idx] = chunks[idx][:start] + replacement + chunks[idx][end:]
                else:
                    chunks[idx] = f"{replacement} {chunks[idx]}".strip()
            if len(remaining) == 1:
                unresolved = [i for i in ambiguous if not _leading_roster_alias(chunks[i], roster)]
                if len(unresolved) == 1:
                    idx = unresolved[0]
                    chunks[idx] = f"{remaining[0]} {chunks[idx]}".strip()

    nonempty = [chunk for chunk in chunks if chunk and _cycle_cell_is_plausible(chunk)]
    if len(nonempty) < 2:
        return ""
    return " · ".join(nonempty)[:2000]


def _cycle_roster_labels(boards: Iterable[DetectedBoard]) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    for board in boards:
        for marker in board.markers:
            label = str(marker.label or "").strip()
            if marker.kind == "unit" and len(label) == 1 and "가" <= label <= "힣":
                counts[label] = counts.get(label, 0) + 1
    return tuple(label for label, count in counts.items() if count >= 2)


def _normalize_cycle_aliases(text: str, roster: tuple[str, ...]) -> str:
    """Repair isolated first-syllable OCR errors using this sheet's roster.

    Only a segment-leading Hangul syllable followed by a digit, '(' or '평'
    is considered an alias. A replacement requires exactly one roster syllable
    with the same medial/final jamo, which fixes common tiny-text consonant
    swaps (e.g. 도→로) without rewriting arbitrary skill prose.
    """
    if len(roster) < 3 or not text:
        return text

    def repair_segment(segment: str) -> str:
        match = re.match(r"^(\s*)([가-힣])(?=\s*(?:\d|\(|평|[Xx×✕✖]))", segment)
        if not match:
            return segment
        label = match.group(2)
        if label in roster:
            return segment
        parts = _hangul_parts(label)
        if parts is None:
            return segment
        candidates = []
        for candidate in roster:
            candidate_parts = _hangul_parts(candidate)
            if candidate_parts and candidate_parts[1:] == parts[1:]:
                candidates.append(candidate)
        if len(candidates) != 1:
            return segment
        start, end = match.span(2)
        return segment[:start] + candidates[0] + segment[end:]

    chunks = []
    for chunk in text.split(" · "):
        chunks.append(" / ".join(repair_segment(part) for part in chunk.split(" / ")))
    return " · ".join(chunks)

def formation_board_indexes(boards: Iterable[DetectedBoard]) -> tuple[int, ...]:
    """Return overview/formation boards that should not be treated as cycle turns.

    Community sheets commonly place one larger full-map formation board before
    a sequence of smaller turn boards. It is useful content and must be kept,
    but OCRing the strip below it as a skill cycle creates bogus text.
    """
    rows = tuple(boards)
    if len(rows) < 3:
        return ()
    cell_counts = [board.rows * board.cols for board in rows]
    typical = float(median(cell_counts))
    first_cells = cell_counts[0]
    if typical > 0 and first_cells >= typical * 1.70 and first_cells - typical >= 40:
        return (0,)
    return ()


def detect_skill_cycles(path: str | Path, boards: Iterable[DetectedBoard]) -> tuple[tuple[str, ...], bool]:
    rows = tuple(boards)
    if not rows:
        return (), False
    executable = _tesseract_executable()
    if executable is None:
        return tuple("" for _ in rows), False
    try:
        with Image.open(path) as opened:
            source = ImageOps.exif_transpose(opened).convert("RGB")
    except OSError:
        return tuple("" for _ in rows), True

    cycles: list[str] = []
    roster = _cycle_roster_labels(rows)
    formation_indexes = set(formation_board_indexes(rows))
    for index, board in enumerate(rows):
        if index in formation_indexes:
            cycles.append("")
            continue
        band = _cycle_band(source, board)
        if band is None:
            cycles.append("")
            continue
        segmented = _segmented_cycle_text(executable, band, roster=roster)
        if segmented:
            cycles.append(_normalize_cycle_aliases(segmented, roster))
            continue
        # Whole-strip OCR is used only when it still looks like compact cycle
        # notation. This avoids importing commentary/table-border garbage as a
        # skill cycle when segmented cell OCR correctly found nothing.
        fallback = _clean_cycle_cell_value(_tesseract_text(band) or "")
        cycles.append(_normalize_cycle_aliases(fallback, roster) if _cycle_cell_is_plausible(fallback) else "")
    return tuple(cycles), True


def _clean_unit_label(text: str) -> str:
    value = str(text or "").strip()
    if any(char in value for char in ("*", "★", "✱", "✳")):
        return "*"
    for char in value:
        if "가" <= char <= "힣":
            return char
    for char in value:
        if char.isascii() and char.isalpha():
            return char.upper()
    return ""


def _hangul_parts(char: str) -> tuple[int, int, int] | None:
    if len(char) != 1 or not ("가" <= char <= "힣"):
        return None
    value = ord(char) - 0xAC00
    return value // 588, (value % 588) // 28, value % 28


def _normalize_unit_label(label: str) -> str:
    """Conservatively repair common one-syllable Hangul OCR vowel swaps."""
    if not label or not ("가" <= label <= "힣"):
        return label
    initials = {
        str(name).strip()[0]
        for name in reference.bundled_doll_display_names().values()
        if str(name).strip()
    }
    if label in initials:
        return label
    parts = _hangul_parts(label)
    if parts is None:
        return label
    # ㅐ↔ㅔ and ㅒ↔ㅖ are especially common on tiny bold community sheets.
    vowel_pairs = ({1, 5}, {3, 7})
    candidates: list[str] = []
    for candidate in initials:
        candidate_parts = _hangul_parts(candidate)
        if candidate_parts is None:
            continue
        if candidate_parts[0] != parts[0] or candidate_parts[2] != parts[2]:
            continue
        if any(parts[1] in pair and candidate_parts[1] in pair for pair in vowel_pairs):
            candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]

    # Final consonants disappear easily in a 9x9 community sheet (벡→베).
    # Repair this only when exactly one known doll initial shares the same
    # choseong+jungseong, which keeps the correction deterministic.
    final_candidates: list[str] = []
    for candidate in initials:
        candidate_parts = _hangul_parts(candidate)
        if candidate_parts is None:
            continue
        if candidate_parts[0] == parts[0] and candidate_parts[1] == parts[1]:
            final_candidates.append(candidate)
    return final_candidates[0] if len(final_candidates) == 1 else label


@lru_cache(maxsize=1)
def _known_unit_initials() -> frozenset[str]:
    return frozenset(
        str(name).strip()[0]
        for name in reference.bundled_doll_display_names().values()
        if str(name).strip()
    )


def _run_single_cell_ocr(executable: str, image: Image.Image, *, psm: int) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        image.save(temp_path, "PNG")
        completed = subprocess.run(
            [
                executable,
                str(temp_path),
                "stdout",
                "-l",
                "kor+eng",
                "--psm",
                str(psm),
                "--dpi",
                "300",
                "-c",
                "load_system_dawg=0",
                "-c",
                "load_freq_dawg=0",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            **ocr_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            return ""
        return _normalize_unit_label(_clean_unit_label(completed.stdout))
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _ocr_unit_cell(
    executable: str,
    source: Image.Image,
    board: DetectedBoard,
    row: int,
    col: int,
) -> str:
    left, top, right, bottom = _cell_bounds(board.box, board.rows, board.cols, row, col)
    pad_x = max(2, round((right - left) * 0.12))
    pad_y = max(2, round((bottom - top) * 0.12))
    if right - left > pad_x * 2 + 2 and bottom - top > pad_y * 2 + 2:
        left += pad_x
        right -= pad_x
        top += pad_y
        bottom -= pad_y
    crop = _normalize_ocr_polarity(source.crop((left, top, right, bottom)).convert("L"))
    scale = min(10.0, max(5.5, 320 / max(1, min(crop.size))))
    crop = crop.resize(
        (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
        Image.Resampling.LANCZOS,
    )
    crop = ImageEnhance.Contrast(crop).enhance(1.45)
    crop = crop.filter(ImageFilter.UnsharpMask(radius=1.0, percent=160, threshold=2))
    border = max(14, round(min(crop.size) * 0.10))
    prepared = ImageOps.expand(crop, border=border, fill=255)
    # Keep the two primary segmentation votes for single-character accuracy.
    # This path is only used for board cells that the faster whole-board TSV pass
    # could not resolve, so preserving the agreement check costs little overall.
    known = _known_unit_initials()
    first = _run_single_cell_ocr(executable, prepared, psm=10)
    second = _run_single_cell_ocr(executable, prepared, psm=13)
    candidates = [label for label in (first, second) if label]
    if first and second and first == second:
        return first

    threshold_variants = (
        prepared.point(lambda value: 255 if value >= 176 else 0),
        ImageOps.invert(prepared).point(lambda value: 255 if value >= 176 else 0),
    )
    for variant in threshold_variants:
        for psm in (10, 13):
            label = _run_single_cell_ocr(executable, variant, psm=psm)
            if label:
                candidates.append(label)
    if not candidates:
        return ""
    return max(
        set(candidates),
        key=lambda label: (candidates.count(label), label in known, label == "*"),
    )


def _run_unit_board_tsv(
    executable: str, image: Image.Image, *, psm: int = 11
) -> list[tuple[str, float, int, int, int, int]]:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        image.save(temp_path, "PNG")
        completed = subprocess.run(
            [
                executable, str(temp_path), "stdout", "-l", "kor+eng",
                "--psm", str(psm), "--dpi", "300",
                "-c", "load_system_dawg=0", "-c", "load_freq_dawg=0",
                "tsv",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=12, check=False,
            **ocr_subprocess_kwargs(),
        )
        if completed.returncode != 0:
            return []
        rows: list[tuple[str, float, int, int, int, int]] = []
        lines = (completed.stdout or "").splitlines()
        for line in lines[1:]:
            fields = line.split("\t")
            if len(fields) < 12:
                continue
            try:
                conf = float(fields[10])
                left, top, width, height = map(int, fields[6:10])
            except (TypeError, ValueError):
                continue
            label = _normalize_unit_label(_clean_unit_label(fields[11]))
            if conf < 12 or not label:
                continue
            rows.append((label, conf, left, top, width, height))
        return rows
    except (OSError, subprocess.SubprocessError):
        return []
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _ocr_unit_board_labels(
    executable: str, source: Image.Image, board: DetectedBoard
) -> dict[tuple[int, int], tuple[str, float]]:
    x, y, width, height = board.box
    board_crop = _normalize_ocr_polarity(source.crop((x, y, x + width, y + height)).convert("L"))
    scale = min(6.0, max(3.0, 1500 / max(1, max(board_crop.size))))
    board_crop = board_crop.resize(
        (max(1, round(board_crop.width * scale)), max(1, round(board_crop.height * scale))),
        Image.Resampling.LANCZOS,
    )
    board_crop = ImageEnhance.Contrast(board_crop).enhance(1.30)
    board_crop = board_crop.filter(ImageFilter.UnsharpMask(radius=0.9, percent=130, threshold=2))
    votes: dict[tuple[int, int], list[tuple[str, float]]] = {}

    def add_votes(rows: list[tuple[str, float, int, int, int, int]], *, weight: float = 1.0) -> None:
        for label, conf, left, top, token_w, token_h in rows:
            center_x = (left + token_w / 2.0) / max(1.0, scale)
            center_y = (top + token_h / 2.0) / max(1.0, scale)
            col = int(center_x / max(1e-6, width / board.cols))
            row = int(center_y / max(1e-6, height / board.rows))
            if not (0 <= row < board.rows and 0 <= col < board.cols):
                continue
            votes.setdefault((row, col), []).append((label, conf * weight))

    primary = _run_unit_board_tsv(executable, board_crop, psm=11)
    add_votes(primary)

    # Conservative second board vote: only pay for it when the first pass has
    # low-confidence tokens or too few recognizable unit initials. This catches
    # occasional anti-aliased one-syllable badges without changing the strong
    # path that already works well. The secondary vote is slightly discounted
    # so one noisy retry cannot overturn a decisive primary token by itself.
    known = _known_unit_initials()
    confident_known = sum(1 for label, conf, *_rest in primary if label in known and conf >= 72)
    weak_primary = not primary or confident_known < 4 or any(conf < 55 for _label, conf, *_rest in primary)
    if weak_primary:
        binary = board_crop.point(lambda value: 255 if value >= 188 else 0)
        add_votes(_run_unit_board_tsv(executable, binary, psm=6), weight=0.92)
    resolved: dict[tuple[int, int], tuple[str, float]] = {}
    known = _known_unit_initials()
    for cell, candidates in votes.items():
        totals: dict[str, float] = {}
        for label, conf in candidates:
            totals[label] = totals.get(label, 0.0) + max(1.0, conf) + (12.0 if label in known else 0.0)
        label = max(totals, key=totals.get)
        confidence = max(conf for candidate, conf in candidates if candidate == label)
        resolved[cell] = (label, confidence)
    return resolved


def _refine_unit_labels_from_cycles(
    boards: tuple[DetectedBoard, ...], cycles: tuple[str, ...]
) -> tuple[DetectedBoard, ...]:
    """Use the skill table as a second OCR vote for one-syllable doll labels.

    Board glyphs are much smaller than cycle-table glyphs. A final consonant
    can disappear on the grid (벡→베), while the cycle row still reads 벡
    clearly. We only repair labels when a cycle alias repeats and the Hangul
    choseong+jungseong identify a single repeated cycle alias.
    """
    cycle_counts: dict[str, int] = {}
    for cycle in cycles:
        for part in re.split(r"[·|/]", str(cycle or "")):
            match = re.match(r"\s*([가-힣])", part)
            if match:
                label = match.group(1)
                cycle_counts[label] = cycle_counts.get(label, 0) + 1
    reliable = {label for label, count in cycle_counts.items() if count >= 2}
    if not reliable:
        return boards

    refined: list[DetectedBoard] = []
    for board in boards:
        markers: list[TacticMarker] = []
        for raw in board.markers:
            marker = TacticMarker(**raw.__dict__)
            if marker.kind == "unit" and marker.label not in reliable:
                parts = _hangul_parts(marker.label)
                if parts is not None:
                    candidates = [
                        label
                        for label in reliable
                        if (candidate_parts := _hangul_parts(label)) is not None
                        and candidate_parts[0] == parts[0]
                        and candidate_parts[1] == parts[1]
                    ]
                    if len(candidates) == 1:
                        marker.label = candidates[0]
            markers.append(marker)
        units = [marker for marker in markers if marker.kind == "unit"]
        if reliable and len(units) == len(reliable):
            present = {marker.label for marker in units if marker.label in reliable}
            unresolved = [marker for marker in units if marker.label not in reliable]
            missing = [label for label in reliable if label not in present]
            if len(unresolved) == 1 and len(missing) == 1:
                unresolved[0].label = missing[0]
        refined.append(
            DetectedBoard(
                box=board.box, rows=board.rows, cols=board.cols,
                confidence=board.confidence, markers=tuple(markers),
            )
        )
    return tuple(refined)


def _refine_unit_label_consensus(boards: tuple[DetectedBoard, ...]) -> tuple[DetectedBoard, ...]:
    """Use repeated multi-turn sheets to repair isolated single-cell OCR errors.

    Community tactic sheets normally reuse the same five-person roster across
    many turns.  When four or more boards strongly agree on a small label set,
    a lone outlier can be replaced by the one missing roster label. Extra
    outliers (often a '*' annotation mistaken for a unit) are left as '?'.
    """
    if len(boards) < 4:
        return boards
    unit_counts = [sum(marker.kind == "unit" for marker in board.markers) for board in boards]
    nonzero = [count for count in unit_counts if count > 0]
    if not nonzero:
        return boards
    typical = max(1, int(median(nonzero)))
    frequencies: dict[str, int] = {}
    for board in boards:
        for marker in board.markers:
            if marker.kind != "unit" or not marker.label or marker.label == "?":
                continue
            frequencies[marker.label] = frequencies.get(marker.label, 0) + 1
    ranked = sorted(frequencies, key=lambda label: (-frequencies[label], label))
    common = tuple(label for label in ranked[:typical] if frequencies[label] >= 2)
    if len(common) < min(4, typical):
        return boards
    common_set = set(common)

    refined: list[DetectedBoard] = []
    for board in boards:
        markers = [TacticMarker(**marker.__dict__) for marker in board.markers]
        units = [marker for marker in markers if marker.kind == "unit"]
        present = {marker.label for marker in units if marker.label in common_set}
        rare = [marker for marker in units if marker.label not in common_set]
        missing = [label for label in common if label not in present]
        if len(rare) == 1 and len(missing) == 1:
            rare[0].label = missing[0]
        elif rare and not missing and len(units) > typical:
            # If the full repeated roster is already present, surplus glyphs
            # are normally summons/deployables (community sheets often use
            # '*'). Preserve them as explicit summon objects instead of a fake
            # sixth doll or silently deleting tactical information.
            for marker in rare:
                marker.kind = "summon"
                marker.label = "*"
                marker.unit_key = ""
        refined.append(
            DetectedBoard(
                box=board.box,
                rows=board.rows,
                cols=board.cols,
                confidence=board.confidence,
                markers=tuple(markers),
            )
        )
    return tuple(refined)


def detect_unit_labels(
    path: str | Path,
    boards: Iterable[DetectedBoard],
) -> tuple[tuple[DetectedBoard, ...], bool]:
    rows = tuple(boards)
    executable = _tesseract_executable()
    if not rows or executable is None:
        return rows, False
    try:
        with Image.open(path) as opened:
            source = ImageOps.exif_transpose(opened).convert("RGB")
    except OSError:
        return rows, True

    resolved: list[DetectedBoard] = []
    known = _known_unit_initials()
    for board in rows:
        board_labels = _ocr_unit_board_labels(executable, source, board)
        markers: list[TacticMarker] = []
        for marker in board.markers:
            cloned = TacticMarker(**marker.__dict__)
            if cloned.kind == "unit":
                broad_label, broad_conf = board_labels.get((cloned.row, cloned.col), ("", -1.0))
                # A confident whole-board token is generally more stable than
                # repeatedly segmenting the tiny one-character badge.  Skip the
                # expensive cell subprocesses when the board pass is decisive;
                # otherwise fall back to the polarity-aware cell vote.
                if broad_label in known and broad_conf >= 72:
                    label = broad_label
                else:
                    cell_label = _ocr_unit_cell(executable, source, board, cloned.row, cloned.col)
                    if broad_label and cell_label and broad_label == cell_label:
                        label = broad_label
                    elif broad_label in known and broad_conf >= 42:
                        label = broad_label
                    elif cell_label:
                        label = cell_label
                    else:
                        label = broad_label
                if label == "*":
                    cloned.kind = "summon"
                    cloned.label = "*"
                    cloned.unit_key = ""
                elif label:
                    cloned.label = label
            markers.append(cloned)
        resolved.append(
            DetectedBoard(
                box=board.box,
                rows=board.rows,
                cols=board.cols,
                confidence=board.confidence,
                markers=tuple(markers),
            )
        )
    return _refine_unit_label_consensus(tuple(resolved)), True


def suggested_board_indexes(boards: Iterable[DetectedBoard]) -> tuple[int, ...]:
    # Formation/overview boards are real tactic information, so every detected
    # board starts selected and the review dialog decides its step role.
    rows = tuple(boards)
    return tuple(range(len(rows)))


def detect_tactic_boards(path: str | Path) -> list[DetectedBoard]:
    source, original_size = _analysis_image(path)
    analysis_size = source.size
    gray = ImageOps.autocontrast(source.convert("L"))
    values = gray.tobytes()
    candidate = bytearray(1 if 70 <= value <= 235 else 0 for value in values)
    minimum = max(14, min(source.size) // 75)
    network = _mark_long_runs(candidate, source.width, source.height, minimum)
    raw_boxes = _component_boxes(network, source.width, source.height)
    boards: list[DetectedBoard] = []
    for box in raw_boxes:
        _x, _y, width, height = box
        aspect = width / max(1, height)
        if not 0.58 <= aspect <= 1.70:
            continue
        shape = _grid_shape(gray, box)
        if shape is None:
            continue
        rows, cols, confidence = shape
        if confidence < 0.52:
            continue
        boss = _detect_boss(source, box, rows, cols)
        markers: list[TacticMarker] = []
        if boss:
            markers.append(boss)
        markers.extend(_detect_blocked(gray, box, rows, cols, boss))
        markers.extend(_detect_cover(gray, box, rows, cols, boss))
        occupied: set[tuple[int, int]] = set()
        if boss:
            for rr in range(boss.row, boss.row + boss.height):
                for cc in range(boss.col, boss.col + boss.width):
                    occupied.add((rr, cc))
        occupied.update((marker.row, marker.col) for marker in markers if marker.kind == "blocked")
        markers.extend(_detect_unit_placeholders(gray, box, rows, cols, occupied))
        boards.append(
            DetectedBoard(
                box=_box_to_original(box, analysis_size, original_size),
                rows=rows,
                cols=cols,
                confidence=confidence,
                markers=tuple(markers),
            )
        )
    boards.sort(key=lambda item: (item.box[1], item.box[0]))
    return boards[:64]



def reimport_tactic_region(
    path: str | Path,
    region: tuple[int, int, int, int],
    *,
    expected_rows: int | None = None,
    expected_cols: int | None = None,
    progress: Progress | None = None,
) -> TacticImageImportResult:
    """Re-run tactic recognition only inside one user-selected source region.

    The returned board coordinates are translated back to the original image so
    the normal review dialog can keep previewing/correcting the candidate.  When
    the crop contains more than one grid, prefer the candidate matching the
    previous row/column shape and then the strongest detector confidence.
    """
    source_path = Path(path)
    try:
        with Image.open(source_path) as opened:
            source = ImageOps.exif_transpose(opened).convert("RGB")
    except OSError as exc:
        raise ValueError(f"원본 이미지를 열 수 없습니다: {exc}") from exc

    raw_x, raw_y, raw_w, raw_h = (int(value) for value in region)
    left = max(0, min(source.width - 1, raw_x))
    top = max(0, min(source.height - 1, raw_y))
    right = max(left + 1, min(source.width, raw_x + max(1, raw_w)))
    bottom = max(top + 1, min(source.height, raw_y + max(1, raw_h)))
    if right - left < 80 or bottom - top < 80:
        raise ValueError("다시 인식할 영역을 조금 더 크게 지정해 주세요.")

    _safe_progress(progress, "선택 영역 준비 중…")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        source.crop((left, top, right, bottom)).save(temp_path)
        local = import_tactic_image(temp_path, title=source_path.stem, progress=progress)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass

    if not local.boards:
        raise ValueError("선택한 영역에서 격자를 다시 찾지 못했습니다.")

    expected_r = int(expected_rows) if expected_rows is not None else None
    expected_c = int(expected_cols) if expected_cols is not None else None

    def candidate_score(item: tuple[int, DetectedBoard]) -> tuple[float, float, int]:
        index, board = item
        shape_penalty = 0.0
        if expected_r is not None:
            shape_penalty += abs(board.rows - expected_r) * 0.24
        if expected_c is not None:
            shape_penalty += abs(board.cols - expected_c) * 0.24
        area = max(1, int(board.box[2]) * int(board.box[3]))
        return (float(board.confidence) - shape_penalty, float(board.confidence), area - index)

    selected_index, selected_board = max(enumerate(local.boards), key=candidate_score)
    selected_step = local.tactic.steps[selected_index]
    translated_board = DetectedBoard(
        box=(
            left + int(selected_board.box[0]),
            top + int(selected_board.box[1]),
            int(selected_board.box[2]),
            int(selected_board.box[3]),
        ),
        rows=selected_board.rows,
        cols=selected_board.cols,
        confidence=selected_board.confidence,
        markers=tuple(TacticMarker(**marker.__dict__) for marker in selected_board.markers),
    )
    copied_step = TacticStep.from_dict(
        {
            "name": selected_step.name,
            "note": selected_step.note,
            "cycle": selected_step.cycle,
            "markers": [marker.__dict__ for marker in selected_step.markers],
            "rows": selected_board.rows,
            "cols": selected_board.cols,
        },
        rows=selected_board.rows,
        cols=selected_board.cols,
        index=0,
    )
    tactic = Tactic(
        title=source_path.stem[:100],
        rows=selected_board.rows,
        cols=selected_board.cols,
        steps=[copied_step],
    )
    return TacticImageImportResult(
        tactic=tactic,
        boards=(translated_board,),
        warnings=local.warnings,
    )

def import_tactic_image(
    path: str | Path,
    *,
    title: str | None = None,
    progress: Progress | None = None,
) -> TacticImageImportResult:
    _safe_progress(progress, "격자 탐색 중…")
    boards = detect_tactic_boards(path)
    if not boards:
        raise ValueError("격자 영역을 찾지 못했습니다. 격자 선이 선명한 원본 이미지를 사용해 주세요.")
    _safe_progress(progress, f"배치 OCR 중… ({len(boards)}개 격자)")
    labeled_boards, unit_ocr_available = detect_unit_labels(path, boards)
    boards = list(labeled_boards)
    default_rows = boards[0].rows
    default_cols = boards[0].cols
    _safe_progress(progress, "스킬 사이클 OCR 중…")
    cycles, ocr_available = detect_skill_cycles(path, boards)
    # Cycle text is larger than grid initials and provides an independent OCR
    # vote. Apply it before the repeated-roster consensus so a board-star that
    # was misread as a sixth Hangul glyph can be recognized as a summon.
    before_labels = tuple(
        (marker.kind, marker.label, marker.row, marker.col)
        for board in boards for marker in board.markers
        if marker.kind in {"unit", "summon"}
    )
    boards = list(_refine_unit_labels_from_cycles(tuple(boards), cycles))
    boards = list(_refine_unit_label_consensus(tuple(boards)))
    after_labels = tuple(
        (marker.kind, marker.label, marker.row, marker.col)
        for board in boards for marker in board.markers
        if marker.kind in {"unit", "summon"}
    )
    # If the board/cycle cross-check repaired a roster glyph, run the cycle
    # segmentation once more with the corrected roster.  This is deliberately
    # slower than the v0.86 fast path, but restored because it recovers aliases
    # and wrapped numeric cells more consistently.
    if ocr_available and before_labels != after_labels:
        refined_cycles, _available = detect_skill_cycles(path, boards)
        if sum(bool(value.strip()) for value in refined_cycles) >= sum(bool(value.strip()) for value in cycles):
            cycles = refined_cycles
    formation_indexes = set(formation_board_indexes(boards))
    steps: list[TacticStep] = []
    combat_index = 0
    for index, board in enumerate(boards):
        if index in formation_indexes:
            step_name = "제대 배치"
        else:
            combat_index += 1
            step_name = f"T{combat_index}"
        steps.append(
            TacticStep(
                name=step_name,
                note="이미지 자동 인식 · '?' 인형 표기는 확인 후 수정하세요.",
                cycle="" if index in formation_indexes else (cycles[index] if index < len(cycles) else ""),
                markers=[TacticMarker(**marker.__dict__) for marker in board.markers],
                rows=board.rows,
                cols=board.cols,
            )
        )
    tactic = Tactic(
        title=(title or Path(path).stem)[:100],
        rows=default_rows,
        cols=default_cols,
        steps=steps,
    )
    warnings: list[str] = []
    placeholder_count = sum(marker.kind == "unit" and marker.label == "?" for step in steps for marker in step.markers)
    recognized_labels = sum(marker.kind == "unit" and marker.label != "?" for step in steps for marker in step.markers)
    if unit_ocr_available:
        warnings.append(
            f"격자 인형 글자 OCR: {recognized_labels}칸 인식 · {placeholder_count}칸 미확인. "
            "한 글자 표기는 원본 해상도에 따라 오인식될 수 있으므로 원본과 비교해 주세요."
        )
    elif placeholder_count:
        warnings.append(f"격자 내부 인형 후보 {placeholder_count}칸은 '?'로 표시했습니다. 택틱 사용 인형을 연결해 확인해 주세요.")
    cycle_count = sum(bool(step.cycle.strip()) for step in steps)
    cycle_targets = max(0, len(steps) - len(formation_indexes))
    if ocr_available and cycle_count:
        warnings.append(f"격자 아래 스킬 사이클 OCR: {cycle_count}/{cycle_targets}전투 단계에서 텍스트를 찾았습니다. 숫자·한글은 원본에 따라 오인식될 수 있습니다.")
    elif ocr_available:
        warnings.append("스킬 사이클 표가 없어 제대 배치형 택틱으로 인식했습니다. 사이클 정보는 추가하지 않습니다.")
    else:
        warnings.append("스킬 사이클 OCR 엔진(Tesseract)을 찾지 못해 사이클 자동 인식은 건너뛰었습니다. 편집기에서 직접 입력할 수 있습니다.")
    warnings.append("자동 인식된 엄폐 방향과 이동 불가 칸은 이미지 스타일에 따라 오검출될 수 있으므로 편집기에서 확인하세요.")
    _safe_progress(progress, "인식 결과 정리 중…")
    return TacticImageImportResult(tactic=tactic, boards=tuple(boards), warnings=tuple(warnings))
