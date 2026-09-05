from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .images import PortraitLoader
from . import theme
from .widgets import PortraitLabel


class RemoldingPieceCard(QFrame):
    """One concrete physical remolding assignment, colored by its major family."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RemoldingPieceCard")
        self.setMinimumWidth(170)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(3)

        self.title = QLabel("")
        self.title.setObjectName("SectionTitle")
        layout.addWidget(self.title)

        self.stats = QLabel("")
        self.stats.setWordWrap(True)
        self.stats.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.stats)

    def set_piece(self, piece: dict[str, Any]) -> None:
        self._piece = dict(piece)
        factor = str(piece.get("factor") or "")
        label = str(piece.get("label") or factor or "분류 미확인")
        index = max(1, int(piece.get("display_index") or 1))
        accent = theme.FACTOR_COLORS.get(factor, theme.BORDER)
        background = theme.FACTOR_PANEL_COLORS.get(factor, theme.PANEL_ALT)
        self.setStyleSheet(
            "QFrame#RemoldingPieceCard {"
            f"background:{background}; border:1px solid {accent}; border-radius:7px;"
            "}"
        )
        self.title.setText(f"{label} {index}")
        self.title.setStyleSheet(f"color:{accent}; font-weight:800;")

        if bool(piece.get("missing")):
            text = "자동 배치 후\n장착 옵션 표시"
        else:
            stats = [row for row in list(piece.get("stats") or []) if int(row.get("level") or 0) > 0]
            if stats:
                # Keep the major option first and make each physical roll easy to
                # scan instead of collapsing six pieces into one aggregate level.
                text = " · ".join(
                    f"{row.get('name') or row.get('option_key') or '스탯'} +{int(row.get('level') or 0)}"
                    for row in stats
                )
            else:
                text = "옵션 정보 없음"
        self.stats.setText(text)
        uid = str(piece.get("uid") or "")
        self.setToolTip(f"리몰딩 UID · {uid}" if uid else "자동 배치를 실행하면 실제 장착할 리몰딩 옵션이 표시됩니다.")

    def refresh_theme(self) -> None:
        piece = getattr(self, "_piece", None)
        if piece is not None:
            self.set_piece(dict(piece))


class RemoldingPieceSummaryWidget(QWidget):
    """Render the six concrete remolding pieces in in-game family order."""

    def __init__(self, parent=None, *, columns: int = 3):
        super().__init__(parent)
        self.columns = max(1, int(columns))
        self._cards: list[RemoldingPieceCard] = []
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(7)
        self._layout.setVerticalSpacing(6)

    def _clear_cards(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._cards.clear()

    def set_summary(self, summary: dict[str, Any] | None) -> None:
        self._clear_cards()
        flat: list[dict[str, Any]] = []
        for group in list(dict(summary or {}).get("groups") or []):
            flat.extend(dict(piece) for piece in list(group.get("pieces") or []))
        if not flat:
            flat = [{"label": "장착 정보 없음", "display_index": 1, "missing": True}]
        for index, piece in enumerate(flat):
            card = RemoldingPieceCard()
            card.set_piece(piece)
            self._layout.addWidget(card, index // self.columns, index % self.columns)
            self._cards.append(card)
        for column in range(self.columns):
            self._layout.setColumnStretch(column, 1)


class MemberArtwork(QFrame):
    """Tall formation artwork panel using a user-selected fullbody/skin asset."""

    changeRequested = Signal()

    def __init__(
        self,
        portraits: PortraitLoader,
        artwork_path: str | Path | None,
        portrait_path: str | Path | None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setMinimumWidth(280)
        self.setMaximumWidth(330)
        self.portraits = portraits
        self._requested_path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(7)

        title = QLabel("캐릭터 전신 · 스킨")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.image = PortraitLabel()
        self.image.setMinimumSize(235, 430)
        self.image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.image, 1)

        artwork = Path(str(artwork_path)) if artwork_path else None
        portrait = Path(str(portrait_path)) if portrait_path else None
        self._artwork_path = artwork
        self._portrait_path = portrait
        self.note = QLabel("")
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        change = QPushButton("이미지 변경…")
        change.setToolTip("이 인형의 fullbody 또는 skins 이미지 중 자세히 보기에서 사용할 이미지를 선택합니다.")
        change.clicked.connect(self.changeRequested)
        layout.addWidget(change)

        self.portraits.imageReady.connect(self._image_ready)
        self.portraits.imageFailed.connect(self._image_failed)
        self._set_path(artwork or portrait)

        if artwork:
            self.note.setText("동기화된 fullbody / skins 중 선택한 이미지를 표시합니다.")
        elif portrait:
            self.note.setText("전신 이미지가 없어 선택된 초상화를 대신 표시합니다.")
        else:
            self.note.setText("표시할 캐릭터 이미지가 없습니다.")

    def _set_path(self, path: Path | None) -> None:
        self._requested_path = str(path) if path else ""
        image = self.portraits.get(path) if path else None
        self.image.set_image(image, 320, 540)
        if path and image is None:
            self.portraits.request(path)

    def _image_ready(self, path: str, image) -> None:
        if path == self._requested_path:
            self.image.set_image(image, 320, 540)

    def _image_failed(self, path: str) -> None:
        if path != self._requested_path:
            return
        if self._artwork_path is not None and path == str(self._artwork_path) and self._portrait_path is not None:
            self.note.setText("선택한 전신/스킨 이미지를 불러오지 못해 초상화로 대체합니다.")
            self._set_path(self._portrait_path)


class MemberCard(QFrame):
    detailRequested = Signal(int)
    levelRequested = Signal(int)
    portraitRequested = Signal(int)
    skillCycleRequested = Signal(int)

    def __init__(self, position: int, portraits: PortraitLoader, parent=None):
        super().__init__(parent)
        self.position = position
        self.portraits = portraits
        self._portrait_path = ""
        self._has_member = False

        self.setObjectName("Panel")
        self.setMinimumHeight(238)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.portraits.imageReady.connect(self._image_ready)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(11, 10, 11, 10)
        outer.setSpacing(12)

        portrait_column = QVBoxLayout()
        portrait_column.setSpacing(0)
        portrait_column.setContentsMargins(0, 0, 0, 0)

        self.portrait = PortraitLabel()
        self.portrait.setFixedSize(156, 150)
        self.portrait.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        portrait_column.addWidget(self.portrait)
        outer.addLayout(portrait_column)

        content = QVBoxLayout()
        content.setSpacing(6)

        self.name = QLabel("비어 있음")
        self.name.setObjectName("SectionTitle")
        self.name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        content.addWidget(self.name)

        self.level_info = QLabel("계산 레벨 · —")
        self.level_info.setObjectName("Muted")
        self.level_info.setWordWrap(True)
        self.level_info.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        content.addWidget(self.level_info)

        pattern_title = QLabel("장착할 리몰딩 6개")
        pattern_title.setObjectName("Muted")
        pattern_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        content.addWidget(pattern_title)

        self.patterns = RemoldingPieceSummaryWidget(columns=3)
        self.patterns.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        content.addWidget(self.patterns)

        actions_top = QHBoxLayout()
        actions_top.setSpacing(6)
        self.detail = QPushButton("자세히 보기")
        self.detail.setObjectName("AccentButton")
        self.detail.clicked.connect(lambda: self.detailRequested.emit(self.position))
        self.level = QPushButton("계산 레벨")
        self.level.setToolTip("이 인형만 전역 계산 레벨과 다른 레벨로 시험할 수 있습니다.")
        self.level.clicked.connect(lambda: self.levelRequested.emit(self.position))
        self.skill_cycle = QPushButton("스킬 사이클")
        self.skill_cycle.setToolTip("이 제대에서만 사용할 T1~Tn 스킬 사이클을 편집합니다.")
        self.skill_cycle.clicked.connect(lambda: self.skillCycleRequested.emit(self.position))
        actions_top.addWidget(self.detail)
        actions_top.addWidget(self.level)
        actions_top.addWidget(self.skill_cycle)
        actions_top.addStretch(1)
        content.addLayout(actions_top)

        actions_bottom = QHBoxLayout()
        actions_bottom.setSpacing(6)
        self.portrait_change = QPushButton("초상화 변경…")
        self.portrait_change.setToolTip("이 인형의 portrait 변형 중 제대 카드에 표시할 초상화를 선택합니다.")
        self.portrait_change.clicked.connect(lambda: self.portraitRequested.emit(self.position))
        self.clear = QPushButton("비우기")
        self.clear.setObjectName("DangerButton")
        actions_bottom.addWidget(self.portrait_change)
        actions_bottom.addWidget(self.clear)
        actions_bottom.addStretch(1)
        content.addLayout(actions_bottom)
        outer.addLayout(content, 1)
        # Empty slots are an implementation detail.  FormationPage reveals a
        # card only after a doll is actually assigned to that position.
        self.setVisible(False)

    def set_member(
        self,
        member: dict[str, Any] | None,
        display_name: str = "",
        portrait_path: str | Path | None = None,
        *,
        piece_summary: dict[str, Any] | None = None,
        level_text: str = "",
    ) -> None:
        if not member:
            self._has_member = False
            self._portrait_path = ""
            self.name.setText("비어 있음")
            self.level_info.setText("계산 레벨 · —")
            self.patterns.set_summary(None)
            self.portrait.set_image(None)
            self.clear.setEnabled(False)
            self.detail.setEnabled(False)
            self.level.setEnabled(False)
            self.skill_cycle.setEnabled(False)
            self.portrait_change.setEnabled(False)
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setToolTip("")
            self.setVisible(False)
            return

        self._has_member = True
        self.setVisible(True)
        self.clear.setEnabled(True)
        self.detail.setEnabled(True)
        self.level.setEnabled(True)
        self.skill_cycle.setEnabled(True)
        self.portrait_change.setEnabled(True)
        self.name.setText(display_name or str(member.get("doll_name") or member.get("doll_id")))
        self.level_info.setText(level_text or "리몰딩 계산 레벨 · 전역 Lv.60")
        self.patterns.set_summary(piece_summary)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("초상화나 자세히 보기를 누르면 장착할 리몰딩 6개의 실제 옵션과 상세 결과를 확인합니다.")

        path = Path(str(portrait_path)) if portrait_path else None
        self._portrait_path = str(path) if path else ""
        image = self.portraits.get(path) if path else None
        self.portrait.set_image(image, 152, 146)
        if path and image is None:
            self.portraits.request(path)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._has_member and event.button() == Qt.MouseButton.LeftButton:
            self.detailRequested.emit(self.position)
        super().mouseReleaseEvent(event)

    def _image_ready(self, path: str, image) -> None:
        if path == self._portrait_path:
            self.portrait.set_image(image, 152, 146)
