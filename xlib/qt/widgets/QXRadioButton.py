from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

from ._part_QXWidget import _part_QXWidget


class QXRadioButton(QRadioButton, _part_QXWidget):
    def __init__(self, text=None,
                       disabled_color=None,
                       auto_exclusive=False,
                       clicked=None, toggled=None,
                       font=None, size_policy=None, hided=False, enabled=True):
        super().__init__()
        if text is not None:
            self.setText(text)
        self.setAutoExclusive(auto_exclusive)

        style_sheet = ''
        if disabled_color is not None:
            style_sheet += f'QRadioButton::disabled {{color: {disabled_color};}}'

        if len(style_sheet) != 0:
            self.setStyleSheet(style_sheet)

        _part_QXWidget.connect_signal(clicked, self.clicked)
        _part_QXWidget.connect_signal(toggled, self.toggled)
        _part_QXWidget.__init__(self, font=font, size_policy=size_policy, hided=hided, enabled=enabled )

    def focusInEvent(self, ev : QFocusEvent):
        super().focusInEvent(ev)
        _part_QXWidget.focusInEvent(self, ev)

    def resizeEvent(self, ev : QResizeEvent):
        super().resizeEvent(ev)
        _part_QXWidget.resizeEvent(self, ev)
