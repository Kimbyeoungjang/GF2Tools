from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
import uuid

from PySide6.QtCore import QRect, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import reference
from ..models import Doll, Remolding, RemoldingSlot
from ..repository import Repository
from ..services.ocr_import import ocr_engine_status, ocr_image, parse_inventory_ocr
from .data import OwnedDollCatalog
from .dialogs.doll_picker import DollPickerDialog
from .images import PortraitLoader
from .screen_region import ScreenRegionSelector
from .widgets import BusyButton, section_panel, show_error
from .workers import run_worker


_FACTOR_PSEUDO_IDS = {
    "bulwark": 985100000,
    "vanguard": 985200000,
    "support": 985300000,
    "sentinel": 985400000,
}


def _manual_remolding(option_rows: list[dict]) -> Remolding:
    if not option_rows:
        raise ValueError("리몰딩 옵션을 하나 이상 선택해 주세요.")
    options = reference.remolding_options()
    slots: list[RemoldingSlot] = []
    primary_factor = ""
    for row in option_rows[:3]:
        key = str(row.get("option_key") or "")
        meta = dict(options.get(key) or {})
        if not meta:
            continue
        level = max(1, min(6, int(row.get("level") or 1)))
        codes = [str(value) for value in (meta.get("codes") or []) if value]
        code = codes[min(len(codes), max(1, level)) - 1] if codes else f"manual:{key}"
        factor = str(meta.get("factorType") or row.get("factor_type") or "")
        if not primary_factor and bool(meta.get("isMajor")):
            primary_factor = factor
        if not primary_factor:
            primary_factor = factor
        slots.append(
            RemoldingSlot(
                code=code,
                name=f"{meta.get('nameKR') or key}{level}",
                option_key=key,
                variant=min(level, 3),
                factor_type=factor or None,
                element_type=str(meta.get("elementType") or row.get("element_type") or "") or None,
                level_contribution=level,
            )
        )
    if not slots:
        raise ValueError("유효한 리몰딩 옵션을 찾지 못했습니다.")
    serial = int(time.time_ns() % 99_999_999)
    remolding_id = _FACTOR_PSEUDO_IDS.get(primary_factor, 989900000) + serial % 99_999
    return Remolding(
        uid=f"manual:{uuid.uuid4().hex}",
        remolding_id=remolding_id,
        raw_contents_hex="manual",
        slots=slots,
    )


class ManualInventoryWidget(QWidget):
    dataChanged = Signal()

    def __init__(
        self,
        repo: Repository,
        catalog: OwnedDollCatalog | None = None,
        portraits: PortraitLoader | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.catalog = catalog or OwnedDollCatalog(repo)
        self.portraits = portraits or PortraitLoader(self)
        self._selected_doll_ids: set[int] = set()
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        doll_panel, doll_layout = section_panel(
            "인형 수동 추가",
            "전체 인형을 초상화로 여러 개 선택할 수 있습니다. 직업·속성 필터와 전체 선택을 이용해 보유하지 않은 인형만 빠르게 해제하세요.",
        )
        pick_row = QHBoxLayout()
        self.doll_pick = QPushButton("인형 여러 개 선택")
        self.doll_pick.setObjectName("AccentButton")
        self.doll_quick = QLineEdit()
        self.doll_quick.setPlaceholderText("인형 이름 입력 · 자동완성에서 선택하거나 Enter")
        doll_names = reference.bundled_doll_display_names()
        self._doll_name_to_id = {
            str(name).strip().casefold(): int(did)
            for did, name in doll_names.items()
            if str(name).strip()
        }
        doll_completer = QCompleter(
            sorted(str(name) for name in doll_names.values() if str(name).strip()),
            self.doll_quick,
        )
        doll_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        doll_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        doll_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.doll_quick.setCompleter(doll_completer)
        doll_completer.activated.connect(self._quick_doll_selected)
        self.doll_quick.returnPressed.connect(self._quick_doll_entered)
        self.doll_summary = QLabel("선택된 인형 없음")
        self.doll_summary.setObjectName("Muted")
        self.doll_summary.setWordWrap(True)
        pick_row.addWidget(self.doll_pick)
        pick_row.addWidget(self.doll_quick, 1)
        doll_layout.addLayout(pick_row)
        doll_layout.addWidget(self.doll_summary)
        form = QFormLayout()
        self.doll_level = QSpinBox()
        self.doll_level.setRange(1, 60)
        self.doll_level.setValue(60)
        self.doll_rank = QSpinBox()
        self.doll_rank.setRange(0, 6)
        self.doll_rank.setValue(1)
        form.addRow("선택 인형 공통 레벨", self.doll_level)
        form.addRow("선택 인형 공통 돌파/랭크", self.doll_rank)
        doll_layout.addLayout(form)
        add_doll = QPushButton("선택 인형 일괄 추가 · 갱신")
        add_doll.clicked.connect(self._add_dolls)
        doll_layout.addWidget(add_doll)
        self.doll_pick.clicked.connect(self._pick_dolls)
        root.addWidget(doll_panel)

        rem_panel, rem_layout = section_panel(
            "리몰딩 수동 추가",
            "불워크/뱅가드/서포트/센티넬 계열로 먼저 좁힌 뒤 한 리몰딩의 옵션을 최대 3개 선택합니다.",
        )
        factor_row = QHBoxLayout()
        factor_row.addWidget(QLabel("리몰딩 계열"))
        self.rem_factor = QComboBox()
        factor_names = reference.remolding_rules().get("factor_names", {})
        self.rem_factor.addItem("전체", "")
        for key in ("bulwark", "vanguard", "support", "sentinel"):
            self.rem_factor.addItem(str(factor_names.get(key, key)), key)
        factor_row.addWidget(self.rem_factor)
        factor_row.addStretch(1)
        rem_layout.addLayout(factor_row)

        self.option_rows: list[tuple[QComboBox, QSpinBox]] = []
        for index in range(3):
            row = QHBoxLayout()
            option = QComboBox()
            option.setEditable(True)
            option.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            level = QSpinBox()
            level.setRange(1, 6)
            level.setValue(3 if index == 0 else 1)
            row.addWidget(QLabel(f"옵션 {index + 1}"))
            row.addWidget(option, 1)
            row.addWidget(QLabel("Lv"))
            row.addWidget(level)
            rem_layout.addLayout(row)
            self.option_rows.append((option, level))
        self.rem_factor.currentIndexChanged.connect(self._rebuild_option_choices)
        self._rebuild_option_choices()
        add_remolding = QPushButton("리몰딩 1개 추가")
        add_remolding.setObjectName("AccentButton")
        add_remolding.clicked.connect(self._add_remolding)
        rem_layout.addWidget(add_remolding)
        root.addWidget(rem_panel)
        root.addStretch(1)

    def _update_doll_summary(self) -> None:
        names = reference.bundled_doll_display_names()
        labels = [str(names.get(did) or did) for did in sorted(self._selected_doll_ids)]
        if labels:
            preview = " · ".join(labels[:8])
            if len(labels) > 8:
                preview += f" · +{len(labels) - 8}"
            self.doll_summary.setText(f"선택 {len(labels)}명 · {preview}")
        else:
            self.doll_summary.setText("선택된 인형 없음")
        self.doll_summary.setObjectName("AccentText" if labels else "Muted")
        self.doll_summary.style().unpolish(self.doll_summary)
        self.doll_summary.style().polish(self.doll_summary)

    def _quick_doll_selected(self, value: str) -> None:
        did = self._doll_name_to_id.get(str(value or "").strip().casefold())
        if did is None:
            return
        self._selected_doll_ids.add(int(did))
        self.doll_quick.clear()
        self._update_doll_summary()

    def _quick_doll_entered(self) -> None:
        text = self.doll_quick.text().strip()
        if not text:
            return
        exact = self._doll_name_to_id.get(text.casefold())
        if exact is not None:
            self._quick_doll_selected(text)
            return
        matches = [did for name, did in self._doll_name_to_id.items() if text.casefold() in name]
        if len(set(matches)) == 1:
            self._selected_doll_ids.add(int(matches[0]))
            self.doll_quick.clear()
            self._update_doll_summary()
            return
        QMessageBox.information(
            self, "인형 빠른 선택",
            "자동완성 목록에서 인형을 선택하거나 이름을 더 구체적으로 입력해 주세요.",
        )

    def _pick_dolls(self) -> None:
        dialog = DollPickerDialog(
            self.repo, self.catalog, self.portraits, parent=self, multi_select=True,
            selected_ids=set(self._selected_doll_ids), include_unowned=True,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._selected_doll_ids = {int(value) for value in dialog.result_ids}
        self._update_doll_summary()

    def _add_dolls(self) -> None:
        if not self._selected_doll_ids:
            QMessageBox.information(self, "인형 수동 추가", "먼저 인형을 여러 개 선택해 주세요.")
            return
        names = reference.bundled_doll_display_names()
        records = [
            Doll(
                did,
                str(names.get(did) or reference.bundled_dolls().get(did) or did),
                self.doll_level.value(),
                self.doll_rank.value(),
            )
            for did in sorted(self._selected_doll_ids)
        ]
        try:
            self.repo.merge_dolls(records)
        except Exception as exc:
            show_error(self, "인형 수동 추가 실패", exc)
            return
        self.catalog.invalidate()
        self.dataChanged.emit()
        QMessageBox.information(self, "인형 수동 추가", f"{len(records)}명의 인형 데이터를 반영했습니다.")

    def _rebuild_option_choices(self) -> None:
        factor_filter = str(self.rem_factor.currentData() or "")
        options = reference.remolding_options()
        for combo, _level in self.option_rows:
            previous = str(combo.currentData() or "")
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("미사용", "")
            for key, meta in sorted(options.items(), key=lambda item: str(item[1].get("nameKR") or item[0])):
                factor = str(meta.get("factorType") or "")
                if factor_filter and factor != factor_filter:
                    continue
                combo.addItem(f"{meta.get('nameKR') or key} · {factor}", key)
            index = combo.findData(previous)
            combo.setCurrentIndex(index if index >= 0 else 0)
            completer = combo.completer()
            if completer is not None:
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                completer.setFilterMode(Qt.MatchFlag.MatchContains)
                completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            combo.blockSignals(False)

    @staticmethod
    def _combo_option_key(combo: QComboBox) -> str:
        current = str(combo.currentData() or "")
        if current:
            return current
        typed = combo.currentText().strip().casefold()
        if not typed or typed == "미사용":
            return ""
        exact: list[str] = []
        contains: list[str] = []
        for index in range(combo.count()):
            key = str(combo.itemData(index) or "")
            if not key:
                continue
            label = str(combo.itemText(index) or "").strip().casefold()
            option_name = label.split(" · ", 1)[0]
            if typed in {label, option_name}:
                exact.append(key)
            elif typed in label:
                contains.append(key)
        if len(set(exact)) == 1:
            return exact[0]
        if not exact and len(set(contains)) == 1:
            return contains[0]
        return ""

    def _add_remolding(self) -> None:
        rows = [
            {"option_key": key, "level": level.value()}
            for combo, level in self.option_rows
            if (key := self._combo_option_key(combo))
        ]
        try:
            piece = _manual_remolding(rows)
            self.repo.merge_remoldings([piece])
        except Exception as exc:
            show_error(self, "리몰딩 수동 추가 실패", exc)
            return
        self.dataChanged.emit()
        QMessageBox.information(self, "리몰딩 수동 추가", "리몰딩 1개를 보유 데이터에 추가했습니다.")


class OcrInventoryWidget(QWidget):
    dataChanged = Signal()

    def __init__(
        self,
        repo: Repository,
        catalog: OwnedDollCatalog | None = None,
        portraits: PortraitLoader | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.catalog = catalog or OwnedDollCatalog(repo)
        self.portraits = portraits or PortraitLoader(self)
        self.pool = QThreadPool.globalInstance()
        self._working = False
        self._live_enabled = False
        self._live_region: QRect | None = None
        self._live_screen_index = 0
        self._live_last_seen: bytes | None = None
        self._live_candidate: bytes | None = None
        self._live_candidate_since = 0.0
        self._live_last_ocr: bytes | None = None
        self._live_groups: list[list[dict]] = []
        self._live_snapshots = 0
        self._live_failures = 0
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(500)
        self._live_timer.timeout.connect(self._poll_live_scan)
        self._raw_parse_timer = QTimer(self)
        self._raw_parse_timer.setSingleShot(True)
        self._raw_parse_timer.setInterval(450)
        self._raw_parse_timer.timeout.connect(self._parse_text)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        controls = QHBoxLayout()
        self.open_button = BusyButton("스크린샷 OCR")
        self.clipboard_button = BusyButton("클립보드 이미지 OCR")
        self.parse_button = QPushButton("수정 내용 적용 · 후보 갱신")
        self.repair_button = QPushButton("OCR 엔진 복구")
        controls.addWidget(self.open_button)
        controls.addWidget(self.clipboard_button)
        controls.addStretch(1)
        controls.addWidget(self.repair_button)
        root.addLayout(controls)

        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        live_panel, live_layout = section_panel(
            "연속 화면 OCR",
            "게임의 리몰딩 상세 영역을 지정한 뒤 다음 항목으로 넘기세요. 화면이 바뀌고 잠시 안정되면 자동으로 OCR하며, 한 화면에서 1~3개 옵션을 찾으면 리몰딩 1개 후보로 대기열에 쌓습니다.",
        )
        live_controls = QHBoxLayout()
        self.live_screen = QComboBox()
        for index, screen in enumerate(QApplication.screens()):
            name = screen.name() or f"모니터 {index + 1}"
            size = screen.geometry().size()
            self.live_screen.addItem(f"{name} · {size.width()}×{size.height()}", index)
        self.live_region_button = QPushButton("캡처 영역 선택")
        self.live_once = QPushButton("현재 영역 1회 인식")
        self.live_toggle = QPushButton("연속 인식 시작")
        self.live_toggle.setObjectName("AccentButton")
        self.live_controls_hint = QLabel("영역 미지정")
        self.live_controls_hint.setObjectName("Muted")
        live_controls.addWidget(self.live_screen)
        live_controls.addWidget(self.live_region_button)
        live_controls.addWidget(self.live_once)
        live_controls.addWidget(self.live_toggle)
        live_controls.addWidget(self.live_controls_hint, 1)
        live_layout.addLayout(live_controls)

        self.live_status = QLabel("영역을 선택하면 화면 변화를 감시할 수 있습니다.")
        self.live_status.setObjectName("Muted")
        self.live_status.setWordWrap(True)
        live_layout.addWidget(self.live_status)
        self.live_queue = QListWidget()
        self.live_queue.setMaximumHeight(126)
        self.live_queue.setToolTip("각 줄은 서로 다른 화면 변화에서 인식한 리몰딩 1개 후보입니다.")
        live_layout.addWidget(self.live_queue)
        live_actions = QHBoxLayout()
        self.live_add_all = QPushButton("대기열 전체 반영")
        self.live_add_all.setEnabled(False)
        self.live_clear = QPushButton("대기열 비우기")
        live_actions.addWidget(self.live_add_all)
        live_actions.addWidget(self.live_clear)
        live_actions.addStretch(1)
        live_layout.addLayout(live_actions)
        root.addWidget(live_panel)

        raw_panel, raw_layout = section_panel(
            "현재 OCR 원문 · 직접 교정",
            "연속 인식에서는 가장 최근 화면의 OCR 원문만 표시합니다. 잘못 읽힌 글자를 고치면 후보가 자동으로 다시 분석되며, 수정한 결과를 대기열에 직접 추가할 수도 있습니다.",
        )
        self.raw_text = QTextEdit()
        self.raw_text.setPlaceholderText("가장 최근 OCR 결과가 여기에 표시됩니다. 직접 수정해도 됩니다.")
        self.raw_text.setMinimumHeight(210)
        self.raw_text.setMaximumHeight(340)
        self.raw_text.textChanged.connect(lambda: self._raw_parse_timer.start())
        raw_layout.addWidget(self.raw_text, 1)
        raw_actions = QHBoxLayout()
        self.raw_queue_add = QPushButton("수정 원문 → 대기열 추가")
        self.raw_queue_add.clicked.connect(self._queue_current_raw)
        raw_actions.addWidget(self.parse_button)
        raw_actions.addWidget(self.raw_queue_add)
        raw_actions.addStretch(1)
        raw_layout.addLayout(raw_actions)
        root.addWidget(raw_panel)

        option_panel, option_layout = section_panel(
            "인식된 리몰딩 옵션",
            "두 가지 게임 리몰딩 화면의 어두운 옵션 행에 맞춘 다중 OCR 패스를 사용합니다. 같은 리몰딩의 옵션을 최대 3개 선택해 추가하세요.",
        )
        self.options = QListWidget()
        self.options.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        option_layout.addWidget(self.options, 1)
        add_options = QPushButton("선택 옵션 → 리몰딩 1개 추가")
        add_options.clicked.connect(self._add_selected_options)
        option_layout.addWidget(add_options)
        root.addWidget(option_panel, 1)

        self.open_button.clicked.connect(self._choose_image)
        self.clipboard_button.clicked.connect(self._clipboard_ocr)
        self.parse_button.clicked.connect(self._parse_text)
        self.repair_button.clicked.connect(self._repair_ocr)
        self.live_screen.currentIndexChanged.connect(self._live_screen_changed)
        self.live_region_button.clicked.connect(self._select_live_region)
        self.live_once.clicked.connect(self._recognize_live_once)
        self.live_toggle.clicked.connect(self._toggle_live_scan)
        self.live_add_all.clicked.connect(self._add_live_queue)
        self.live_clear.clicked.connect(self._clear_live_queue)
        self.refresh_ocr_status()

    def refresh_ocr_status(self) -> None:
        status = ocr_engine_status()
        if status.get("available"):
            languages = set(status.get("languages") or [])
            language_text = "한국어+영어" if {"kor", "eng"}.issubset(languages) else ", ".join(sorted(languages)) or "언어 미확인"
            self.status.setText(f"OCR 엔진 준비됨 · {language_text} · {status.get('executable')}")
            self.status.setObjectName("SuccessText")
        else:
            self.status.setText("OCR 엔진을 찾지 못했습니다. 시작 시 자동 설치를 시도하며, 실패한 경우 오른쪽 'OCR 엔진 복구'를 누르면 다시 시도합니다.")
            self.status.setObjectName("WarningText")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    @staticmethod
    def _image_signature(image) -> bytes:
        scaled = image.scaled(
            64,
            36,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ).convertToFormat(QImage.Format.Format_Grayscale8)
        return scaled.constBits().tobytes()[: scaled.bytesPerLine() * scaled.height()]

    @staticmethod
    def _signature_delta(left: bytes | None, right: bytes | None) -> float:
        if left is None or right is None or len(left) != len(right) or not left:
            return 255.0
        return sum(abs(a - b) for a, b in zip(left, right)) / len(left)

    def _live_screen_changed(self, index: int) -> None:
        self.stop_continuous()
        self._live_screen_index = max(0, int(self.live_screen.itemData(index) or 0))
        self._live_region = None
        self.live_controls_hint.setText("영역 미지정")

    def _selected_screen(self):
        screens = QApplication.screens()
        if not screens:
            return None
        return screens[max(0, min(self._live_screen_index, len(screens) - 1))]

    def _select_live_region(self) -> None:
        screen = self._selected_screen()
        if screen is None:
            QMessageBox.information(self, "연속 화면 OCR", "사용 가능한 화면을 찾지 못했습니다.")
            return
        was_enabled = self._live_enabled
        self.stop_continuous()
        selector = ScreenRegionSelector(screen, self)
        if selector.exec() == selector.DialogCode.Accepted and selector.selected_rect is not None:
            self._live_region = QRect(selector.selected_rect)
            rect = self._live_region
            self.live_controls_hint.setText(f"{rect.width()}×{rect.height()} · ({rect.x()}, {rect.y()})")
            self.live_status.setText("영역 선택 완료 · 시작하면 화면이 안정적으로 바뀔 때만 OCR합니다.")
        if was_enabled and self._live_region is not None:
            self._start_continuous()

    def _recognize_live_once(self) -> None:
        if self._working:
            return
        if self._live_region is None:
            self._select_live_region()
            if self._live_region is None:
                return
        image = self._grab_live_image()
        if image is None or image.isNull():
            self.live_status.setText("현재 영역을 캡처하지 못했습니다. 영역을 다시 지정해 주세요.")
            return
        signature = self._image_signature(image)
        self._start_live_ocr(image, signature)

    def _toggle_live_scan(self) -> None:
        if self._live_enabled:
            self.stop_continuous()
        else:
            self._start_continuous()

    def _start_continuous(self) -> None:
        if self._live_region is None:
            self._select_live_region()
            if self._live_region is None:
                return
        if not ocr_engine_status().get("available"):
            QMessageBox.information(
                self,
                "연속 화면 OCR",
                "OCR 엔진이 아직 준비되지 않았습니다. OCR 엔진 복구를 실행한 뒤 다시 시도하세요.",
            )
            return
        self._live_enabled = True
        self._live_last_seen = None
        self._live_candidate = None
        self._live_last_ocr = None
        self._live_candidate_since = 0.0
        self._live_failures = 0
        self.live_toggle.setText("연속 인식 중지")
        self.live_status.setText("화면 감시 중 · 현재 항목부터 약 0.5초 안정되면 인식하고, 이후 작은 옵션 변화도 자동 감지합니다.")
        self._live_timer.start()

    def stop_continuous(self) -> None:
        self._live_enabled = False
        self._live_timer.stop()
        if hasattr(self, "live_toggle"):
            self.live_toggle.setText("연속 인식 시작")
        if hasattr(self, "live_status") and self._live_region is not None:
            self.live_status.setText("연속 인식 중지됨 · 같은 영역에서 다시 시작할 수 있습니다.")

    def _grab_live_image(self):
        screen = self._selected_screen()
        rect = self._live_region
        if screen is None or rect is None:
            return None

        # Let Qt map the selector's device-independent screen coordinates to
        # the native framebuffer. The previous implementation grabbed the full
        # screen and multiplied by DPR manually; on several Windows DPI /
        # borderless-fullscreen combinations that double-scaled the crop and
        # OCR received a completely different area from the one the user drew.
        pixmap = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
        if not pixmap.isNull() and pixmap.width() >= 8 and pixmap.height() >= 8:
            return pixmap.toImage()

        # Conservative fallback for platforms where region-grab is not
        # implemented: map using the actual pixmap/logical-screen ratio rather
        # than assuming devicePixelRatio() describes the backing image size.
        full = screen.grabWindow(0)
        if full.isNull():
            return None
        logical = screen.geometry()
        scale_x = full.width() / max(1, logical.width())
        scale_y = full.height() / max(1, logical.height())
        device_rect = QRect(
            round(rect.x() * scale_x),
            round(rect.y() * scale_y),
            max(1, round(rect.width() * scale_x)),
            max(1, round(rect.height() * scale_y)),
        )
        bounded = device_rect.intersected(QRect(0, 0, full.width(), full.height()))
        if bounded.width() < 8 or bounded.height() < 8:
            return None
        return full.copy(bounded).toImage()

    def _poll_live_scan(self) -> None:
        if not self._live_enabled or self._working:
            return
        image = self._grab_live_image()
        if image is None or image.isNull():
            self.live_status.setText("화면 캡처 실패 · 모니터 또는 캡처 영역을 다시 선택해 주세요.")
            return
        signature = self._image_signature(image)
        now = time.monotonic()
        if self._live_last_seen is None:
            self._live_last_seen = signature
            self._live_candidate = signature
            self._live_candidate_since = now
            return
        delta = self._signature_delta(signature, self._live_last_seen)
        self._live_last_seen = signature
        if delta >= 2.4:
            self._live_candidate = signature
            self._live_candidate_since = now
            self.live_status.setText(f"화면 변화 감지 · 안정화 대기 ({delta:.1f})")
            return
        if self._live_candidate is None or now - self._live_candidate_since < 0.55:
            return
        if self._signature_delta(signature, self._live_candidate) >= 1.8:
            self._live_candidate = signature
            self._live_candidate_since = now
            return
        if self._signature_delta(signature, self._live_last_ocr) < 1.25:
            self._live_candidate = None
            return
        self._live_candidate = None
        self._start_live_ocr(image, signature)

    def _start_live_ocr(self, image, signature: bytes) -> None:
        path = self.repo.path.parent / ".ocr_live_scan.png"
        if not image.save(str(path), "PNG"):
            self.live_status.setText("연속 OCR 임시 이미지를 저장하지 못했습니다.")
            return
        self._working = True
        self.live_status.setText(
            f"캡처 {image.width()}×{image.height()} px · OCR 중… "
            "(인식 실패 시 저장된 .ocr_live_scan.png를 확인하세요.)"
        )
        run_worker(
            self.pool,
            lambda: ocr_image(path),
            on_result=lambda result, sig=signature: self._live_ocr_ready(sig, result),
            on_error=lambda error, sig=signature: self._live_ocr_failed(sig, error),
            on_finished=self._ocr_finished,
        )

    def _live_ocr_failed(self, signature: bytes, error: str) -> None:
        self._live_last_ocr = signature
        self._live_failures += 1
        self.live_status.setText(f"연속 OCR 실패 ({self._live_failures}/3) · {error}")
        if self._live_failures >= 3:
            self.stop_continuous()
            self.live_status.setText(
                "연속 OCR이 3회 연속 실패해 자동 중지했습니다. OCR 엔진 복구 또는 캡처 영역을 확인한 뒤 다시 시작하세요. "
                f"마지막 오류: {error}"
            )

    def _live_ocr_ready(self, signature: bytes, result: object) -> None:
        self._live_last_ocr = signature
        self._live_failures = 0
        text = str(result or "").strip()
        self._live_snapshots += 1
        if text:
            self.raw_text.blockSignals(True)
            self.raw_text.setPlainText(text)
            self.raw_text.blockSignals(False)
        _dolls, options = parse_inventory_ocr(text)
        if 1 <= len(options) <= 3:
            group = [
                {
                    "option_key": row.option_key,
                    "name": row.name,
                    "level": row.level,
                    "factor_type": row.factor_type,
                    "element_type": row.element_type,
                }
                for row in options
            ]
            self._live_groups.append(group)
            label = " · ".join(f"{row['name']} Lv.{row['level']}" for row in group)
            self.live_queue.addItem(f"#{len(self._live_groups)} · {label}")
            self.live_add_all.setEnabled(True)
            self.live_status.setText(
                f"연속 OCR {self._live_snapshots}회 · 리몰딩 후보 {len(self._live_groups)}개 누적 · 다음 항목으로 넘겨도 됩니다."
            )
        else:
            self.live_status.setText(
                f"연속 OCR {self._live_snapshots}회 · 옵션 {len(options)}개 검출. 1~3개가 아니어서 자동 대기열에는 넣지 않았습니다."
            )
        self._parse_text()

    def _add_live_queue(self) -> None:
        if not self._live_groups:
            return
        try:
            pieces = [_manual_remolding(group) for group in self._live_groups]
            self.repo.merge_remoldings(pieces)
        except Exception as exc:
            show_error(self, "연속 OCR 일괄 반영 실패", exc)
            return
        count = len(pieces)
        self._clear_live_queue()
        self.dataChanged.emit()
        QMessageBox.information(self, "연속 화면 OCR", f"리몰딩 {count}개를 보유 데이터에 반영했습니다.")

    def _clear_live_queue(self) -> None:
        self._live_groups.clear()
        self.live_queue.clear()
        self.live_add_all.setEnabled(False)

    def _choose_image(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "리몰딩 OCR 이미지 선택",
            "",
            "이미지 (*.png *.jpg *.jpeg *.webp *.bmp);;모든 파일 (*)",
        )
        if path:
            self._start_ocr(path)

    def _clipboard_ocr(self) -> None:
        image = QApplication.clipboard().image()
        if image.isNull():
            QMessageBox.information(self, "클립보드 OCR", "클립보드에 이미지가 없습니다.")
            return
        path = self.repo.path.parent / ".ocr_clipboard.png"
        if not image.save(str(path), "PNG"):
            QMessageBox.information(self, "클립보드 OCR", "클립보드 이미지를 임시 저장하지 못했습니다.")
            return
        self._start_ocr(path)

    def _start_ocr(self, path: str | Path) -> None:
        if self._working:
            return
        self._working = True
        self.open_button.set_busy(True, "OCR 중…")
        self.clipboard_button.set_busy(True, "OCR 중…")
        run_worker(
            self.pool,
            lambda: ocr_image(path),
            on_result=self._ocr_ready,
            on_error=lambda error: show_error(self, "리몰딩 OCR 실패", error),
            on_finished=self._ocr_finished,
        )

    def _ocr_ready(self, result: object) -> None:
        self.raw_text.setPlainText(str(result or ""))
        self._parse_text()

    def _ocr_finished(self) -> None:
        self._working = False
        self.open_button.set_busy(False)
        self.clipboard_button.set_busy(False)
        self.refresh_ocr_status()

    def _parse_text(self) -> None:
        _dolls, options = parse_inventory_ocr(self.raw_text.toPlainText())
        self.options.clear()
        for row in options:
            item = QListWidgetItem(f"{row.name} Lv.{row.level} · {row.source_line}")
            item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "option_key": row.option_key,
                    "name": row.name,
                    "level": row.level,
                    "factor_type": row.factor_type,
                    "element_type": row.element_type,
                },
            )
            self.options.addItem(item)
            item.setSelected(True)
        self.status.setText(f"리몰딩 OCR 분석 · 옵션 {len(options)}개 후보")
        self.status.setObjectName("AccentText")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _queue_current_raw(self) -> None:
        _dolls, options = parse_inventory_ocr(self.raw_text.toPlainText())
        if not (1 <= len(options) <= 3):
            QMessageBox.information(
                self, "OCR 원문 대기열",
                f"현재 수정 원문에서 리몰딩 옵션 {len(options)}개를 찾았습니다. 1~3개가 되도록 원문을 수정해 주세요.",
            )
            return
        group = [
            {
                "option_key": row.option_key, "name": row.name, "level": row.level,
                "factor_type": row.factor_type, "element_type": row.element_type,
            }
            for row in options
        ]
        signature = tuple((row["option_key"], int(row["level"])) for row in group)
        if any(
            tuple((item["option_key"], int(item["level"])) for item in existing) == signature
            for existing in self._live_groups
        ):
            QMessageBox.information(self, "OCR 원문 대기열", "같은 옵션/레벨 조합이 이미 대기열에 있습니다.")
            return
        self._live_groups.append(group)
        label = " · ".join(f"{row['name']} Lv.{row['level']}" for row in group)
        self.live_queue.addItem(f"#{len(self._live_groups)} · {label} · 수동 교정")
        self.live_add_all.setEnabled(True)
        self.live_status.setText(f"수정한 OCR 원문을 대기열에 추가했습니다 · 총 {len(self._live_groups)}개")

    def _add_selected_options(self) -> None:
        selected = [dict(item.data(Qt.ItemDataRole.UserRole) or {}) for item in self.options.selectedItems()]
        if not selected:
            QMessageBox.information(self, "OCR 리몰딩 추가", "같은 리몰딩에 속한 옵션을 선택해 주세요.")
            return
        if len(selected) > 3:
            QMessageBox.information(self, "OCR 리몰딩 추가", "한 번에 최대 3개 옵션만 한 리몰딩으로 묶을 수 있습니다.")
            return
        try:
            self.repo.merge_remoldings([_manual_remolding(selected)])
        except Exception as exc:
            show_error(self, "OCR 리몰딩 추가 실패", exc)
            return
        self.dataChanged.emit()
        QMessageBox.information(self, "OCR 리몰딩 추가", "선택한 옵션을 리몰딩 1개로 추가했습니다.")

    def _repair_ocr(self) -> None:
        if getattr(sys, "frozen", False):
            QMessageBox.information(
                self,
                "OCR 엔진 복구",
                "공식 실행형 배포본에는 Tesseract OCR이 함께 포함됩니다.\n"
                "OCR 엔진을 찾지 못한다면 Release ZIP을 새 폴더에 다시 압축 해제해 주세요.",
            )
            return
        bootstrap = Path.cwd() / "bootstrap.py"
        if not bootstrap.is_file():
            QMessageBox.information(self, "OCR 엔진 복구", "bootstrap.py를 찾지 못했습니다.")
            return
        try:
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            subprocess.Popen(
                [sys.executable, str(bootstrap), "--repair-ocr", "--no-launch"],
                cwd=str(Path.cwd()),
                creationflags=flags,
            )
        except OSError as exc:
            show_error(self, "OCR 엔진 복구", exc)
            return
        QMessageBox.information(self, "OCR 엔진 복구", "OCR 엔진 설치/복구를 시작했습니다. 완료 후 이 화면으로 돌아와 다시 OCR을 실행하세요.")
