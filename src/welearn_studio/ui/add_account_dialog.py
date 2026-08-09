"""Account credentials dialog kept at the presentation boundary."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


class AddAccountDialog(QDialog):
    credentialsAccepted = Signal(str, str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("addAccountDialog")
        self.setWindowTitle("添加账号")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(True)
        self.setMinimumWidth(400)

        title = QLabel("添加账号", self)
        title.setObjectName("pageTitle")
        self.username = QLineEdit(self)
        self.username.setObjectName("accountUsername")
        self.username.setPlaceholderText("请输入账号")
        self.username.setClearButtonEnabled(True)
        self.password = QLineEdit(self)
        self.password.setObjectName("accountPassword")
        self.password.setPlaceholderText("请输入密码")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.nickname = QLineEdit(self)
        self.nickname.setObjectName("accountNickname")
        self.nickname.setPlaceholderText("可不填")
        self.nickname.setClearButtonEnabled(True)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("账号", self.username)
        form.addRow("密码", self.password)
        form.addRow("昵称（可选）", self.nickname)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        self.submit_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.submit_button.setText("添加")
        self.submit_button.setProperty("primary", True)
        self.submit_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        self.username.textChanged.connect(self._update_submit)
        self.password.textChanged.connect(self._update_submit)
        self.buttons.accepted.connect(self._accept_credentials)
        self.buttons.rejected.connect(self.reject)
        self.username.setFocus()

    def credentials(self) -> tuple[str, str, str]:
        return self.username.text().strip(), self.password.text(), self.nickname.text().strip()

    def _update_submit(self, _text: str = "") -> None:
        username, password, _nickname = self.credentials()
        self.submit_button.setEnabled(bool(username and password))

    def _accept_credentials(self) -> None:
        username, password, nickname = self.credentials()
        if not username or not password:
            return
        self.credentialsAccepted.emit(username, password, nickname)
        self.accept()
