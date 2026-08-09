"""Account navigation and per-account task status."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStackedLayout,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .presentation import AccountView, format_duration
from .theme import ACCOUNT_STATE_COLORS, ACCOUNT_STATE_LABELS
from .widgets import SearchField, StateDot, set_standard_icon


class AccountCard(QWidget):
    def __init__(self, account: AccountView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.account_id = account.stable_id
        self._dot = StateDot(
            ACCOUNT_STATE_COLORS.get(account.state, ACCOUNT_STATE_COLORS["unknown"]), self
        )
        self._name = QLabel(self)
        self._name.setObjectName("sectionTitle")
        self._username = QLabel(self)
        self._username.setObjectName("muted")
        self._state = QLabel(self)
        self._state.setObjectName("muted")
        self._countdown = QLabel(self)
        self._countdown.setObjectName("accountCountdown")
        self._countdown.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._countdown.setVisible(False)
        # Keep a descriptive handle for UI tests and integrations without
        # exposing the layout implementation details.
        self.countdown_label = self._countdown
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)

        state_row = QHBoxLayout()
        state_row.setSpacing(7)
        state_row.addWidget(self._dot)
        state_row.addWidget(self._state)
        state_row.addStretch(1)
        state_row.addWidget(self._countdown)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.addWidget(self._name)
        text_layout.addWidget(self._username)
        text_layout.addLayout(state_row)
        text_layout.addWidget(self._progress)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addLayout(text_layout, 1)
        self.set_account(account)

    def set_account(self, account: AccountView) -> None:
        self.account_id = account.stable_id
        self._name.setText(account.display_name)
        self._username.setText(account.username if account.nickname.strip() else "")
        self._username.setVisible(bool(account.nickname.strip()))
        self._state.setText(
            ACCOUNT_STATE_LABELS.get(account.state, ACCOUNT_STATE_LABELS["unknown"])
        )
        show_countdown = account.state == "time_study"
        if show_countdown and account.estimated_seconds > 0:
            countdown_text = f"剩余 {format_duration(account.remaining_seconds)}"
        elif show_countdown:
            countdown_text = "剩余 --:--:--"
        else:
            countdown_text = ""
        self._countdown.setText(countdown_text)
        self._countdown.setVisible(show_countdown)
        self._dot.set_color(
            ACCOUNT_STATE_COLORS.get(account.state, ACCOUNT_STATE_COLORS["unknown"])
        )
        progress = max(0, min(100, account.progress))
        self._progress.setValue(progress)
        self._progress.setVisible(progress > 0 or account.state in {"homework", "time_study"})


class AccountSidebar(QFrame):
    accountSelected = Signal(str)
    addRequested = Signal()
    importRequested = Signal()
    removeRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setMinimumWidth(220)
        self.setMaximumWidth(310)
        self._accounts: dict[str, AccountView] = {}

        title = QLabel("账号", self)
        title.setObjectName("pageTitle")
        self.search = SearchField("搜索账号", self)
        self.search.setObjectName("accountSearch")
        self.list = QListWidget(self)
        self.list.setObjectName("accountList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.empty_label = QLabel("暂无账号", self)
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.add_button = QPushButton("添加", self)
        self.add_button.setObjectName("addAccountButton")
        self.import_button = QPushButton("导入", self)
        self.import_button.setObjectName("importAccountsButton")
        self.remove_button = QPushButton("移除", self)
        self.remove_button.setObjectName("removeAccountButton")
        self.remove_button.setProperty("danger", True)
        self.remove_button.setEnabled(False)
        set_standard_icon(self.add_button, QStyle.StandardPixmap.SP_FileDialogNewFolder)
        set_standard_icon(self.import_button, QStyle.StandardPixmap.SP_DialogOpenButton)
        set_standard_icon(self.remove_button, QStyle.StandardPixmap.SP_TrashIcon)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        action_row.addWidget(self.add_button)
        action_row.addWidget(self.import_button)
        action_row.addWidget(self.remove_button)

        list_area = QStackedLayout()
        list_area.setContentsMargins(0, 0, 0, 0)
        list_area.setStackingMode(QStackedLayout.StackingMode.StackAll)
        list_area.addWidget(self.list)
        list_area.addWidget(self.empty_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(self.search)
        layout.addLayout(list_area, 1)
        layout.addLayout(action_row)

        self.search.textChanged.connect(self._apply_search)
        self.list.currentItemChanged.connect(self._selection_changed)
        self.add_button.clicked.connect(self.addRequested)
        self.import_button.clicked.connect(self.importRequested)
        self.remove_button.clicked.connect(self._request_remove)

    def set_accounts(self, accounts: list[AccountView], selected_id: str | None = None) -> None:
        previous = selected_id or self.selected_account_id()
        self._accounts = {account.stable_id: account for account in accounts}
        self.list.clear()
        for account in accounts:
            item = QListWidgetItem(self.list)
            item.setData(Qt.ItemDataRole.UserRole, account.stable_id)
            card = AccountCard(account, self.list)
            item.setSizeHint(card.sizeHint())
            self.list.setItemWidget(item, card)
            if account.stable_id == previous:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self._apply_search(self.search.text())
        self._refresh_empty_state()

    def update_account(self, account: AccountView) -> None:
        self._accounts[account.stable_id] = account
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == account.stable_id:
                card = self.list.itemWidget(item)
                if isinstance(card, AccountCard):
                    card.set_account(account)
                    item.setSizeHint(card.sizeHint())
                break
        self._apply_search(self.search.text())

    def selected_account_id(self) -> str | None:
        item = self.list.currentItem()
        return None if item is None else str(item.data(Qt.ItemDataRole.UserRole))

    def _apply_search(self, text: str) -> None:
        query = text.strip().casefold()
        visible = 0
        for row in range(self.list.count()):
            item = self.list.item(row)
            account = self._accounts.get(str(item.data(Qt.ItemDataRole.UserRole)))
            matched = account is not None and (
                not query
                or query in account.username.casefold()
                or query in account.nickname.casefold()
            )
            item.setHidden(not matched)
            visible += int(matched)
        self.empty_label.setText("未找到账号" if self.list.count() else "暂无账号")
        self.empty_label.setVisible(visible == 0)

    def _selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        self.remove_button.setEnabled(current is not None)
        if current is not None:
            self.accountSelected.emit(str(current.data(Qt.ItemDataRole.UserRole)))

    def _request_remove(self) -> None:
        account_id = self.selected_account_id()
        if account_id is not None:
            self.removeRequested.emit(account_id)

    def _refresh_empty_state(self) -> None:
        self.empty_label.setVisible(self.list.count() == 0)
