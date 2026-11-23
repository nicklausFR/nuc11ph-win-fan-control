# SPDX-License-Identifier: GPL-3.0-only
"""NUC Cooling: compact Qt monitoring and cooling controls in the system tray."""
from __future__ import annotations

import argparse
from datetime import datetime
from math import ceil
from pathlib import Path
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSettings, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QActionGroup, QColor, QCursor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
    QLayout, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget,
)

from backend import PollWorker
from system_load import LoadWorker
from i18n import Translator, profile_name, resolve_language
from telemetry import NAMES, cooling_mode, format_rpm, format_temperature, number, storage_rows, temperature

APP_NAME = "NUC11phi7 Cooling"


def fan_icon(size=64, color="#69b5f5"):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / 64, size / 64)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.translate(32, 32)
    for _ in range(3):
        blade = QPainterPath(QPointF(3, -6))
        blade.cubicTo(25, -35, 38, -7, 19, 1)
        blade.cubicTo(12, 6, 8, 2, 3, -6)
        painter.drawPath(blade)
        painter.rotate(120)
    painter.drawEllipse(QPointF(0, 0), 6, 6)
    painter.end()
    return QIcon(pixmap)


def control_icon(kind, color):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if kind == "pin":
        painter.drawLine(9, 4, 15, 4)
        painter.drawLine(9, 4, 9, 10)
        painter.drawLine(15, 4, 15, 10)
        painter.drawLine(9, 10, 6, 14)
        painter.drawLine(15, 10, 18, 14)
        painter.drawLine(6, 14, 18, 14)
        painter.drawLine(12, 14, 12, 21)
    elif kind == "refresh":
        painter.drawArc(QRectF(5, 5, 14, 14), 35 * 16, 290 * 16)
        painter.drawLine(19, 5, 19, 10)
        painter.drawLine(14, 10, 19, 10)
    elif kind == "hide":
        painter.drawLine(7, 10, 12, 15)
        painter.drawLine(12, 15, 17, 10)
    else:
        painter.setBrush(QColor(color))
        for y in (5, 12, 19):
            painter.drawEllipse(QPointF(12, y), 1, 1)
    painter.end()
    return QIcon(pixmap)


def label(text="", name="", parent=None):
    widget = QLabel(text, parent)
    widget.setTextFormat(Qt.PlainText)
    widget.setObjectName(name)
    return widget


class SensorCard(QFrame):
    """Temperature and optional utilization only; fan readings live separately."""
    def __init__(self, title, subtitle, *, has_load=False, language="fr"):
        super().__init__()
        self.tr = Translator(language)
        self.source_title, self.source_subtitle = title, subtitle
        self.load_value, self.load_tooltip = None, ""
        self.has_load = has_load
        self.setObjectName("sensorCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        self.title = label(title, "cardTitle")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.subtitle = label(subtitle, "muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)
        self.value = label("—", "temperature")
        layout.addWidget(self.value)
        self.load = label(self.tr("Charge : —"), "muted")
        layout.addWidget(self.load)
        self.load_bar = QProgressBar()
        self.load_bar.setRange(0, 100)
        self.load_bar.setTextVisible(False)
        self.load_bar.setFixedHeight(4)
        layout.addWidget(self.load_bar)
        self.load.setVisible(has_load)
        self.load_bar.setVisible(has_load)
        self.retranslate(language)

    def retranslate(self, language):
        self.tr = Translator(language)
        self.title.setText(self.tr(self.source_title))
        self.subtitle.setText(self.tr(self.source_subtitle))
        self.setAccessibleName(self.title.text())
        self.update_load(self.load_value, self.load_tooltip)

    def update_temperature(self, sensor):
        value = temperature(sensor)
        self.value.setText(format_temperature(value))
        self.value.setProperty("hot", value is not None and value >= 80)
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)

    def update_load(self, value, tooltip=""):
        self.load_value, self.load_tooltip = value, tooltip
        value = number(value)
        valid = value is not None and 0 <= value <= 100
        self.load.setText(self.tr("Charge : {value:.0f} %", value=value) if valid else self.tr("Charge : —"))
        self.load_bar.setValue(round(value) if valid else 0)
        self.load.setToolTip(self.tr.message(tooltip))


def anchored_position(area, size, anchor):
    x = anchor.center().x() - size.width() // 2
    y = anchor.top() - size.height() - 8
    if y < area.top():
        y = anchor.bottom() + 8
    return QPoint(max(area.left(), min(x, area.right() - size.width() + 1)),
                  max(area.top(), min(y, area.bottom() - size.height() + 1)))


class CoolingWindow(QWidget):
    refresh_requested = Signal()
    interval_requested = Signal(int)
    engine_requested = Signal(str)
    quit_requested = Signal()
    profile_requested = Signal(str)
    duty_requested = Signal(int)
    restore_requested = Signal()

    def __init__(self, settings=None, *, create_tray=True, monitor_only=False, language=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings("nicklausFR", "NucCooling")
        self.language_preference = language or self.settings.value("language", "auto")
        if self.language_preference not in ("auto", "fr", "en"):
            self.language_preference = "auto"
        self.language = resolve_language(self.language_preference)
        self.tr = Translator(self.language)
        self.connection_error = None
        self.control_error = None
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(fan_icon())
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedWidth(414)
        self.setMinimumHeight(380)
        self.pinned = self.settings.value("pinned", False, type=bool)
        self.quitting = False
        self.dialog_open = False
        self.last_payload = None
        self.last_update = None
        self.last_hide = None
        self.monitor_only = monitor_only
        self.control_busy = False
        self.current_load = {}
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self.fit_to_contents)
        self._profile_signature = None
        self.theme = self.settings.value("theme", "auto")
        self.interval = self.settings.value("interval", 2000, type=int)
        if self.interval not in (1000, 2000, 5000):
            self.interval = 2000
        self.tray = None
        self._build()
        self._make_menu()
        self._retranslate()
        if create_tray:
            self.tray = QSystemTrayIcon(fan_icon(), self)
            self.tray.setToolTip(APP_NAME + " · " + self.tr("connexion en cours"))
            self.tray.setContextMenu(self.menu)
            self.tray.activated.connect(self._tray_activated)
            self.tray.show()
        QApplication.instance().styleHints().colorSchemeChanged.connect(self._apply_theme)
        self._apply_theme()
        self.installEventFilter(self)

    def _button(self, kind, text, callback):
        button = QToolButton()
        button.setProperty("kind", kind)
        button.setFixedSize(30, 30)
        button.setToolTip(text)
        button.setAccessibleName(text)
        button.clicked.connect(callback)
        return button

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        self.panel_scroll = QScrollArea()
        self.panel_scroll.setObjectName("panelScroll")
        self.panel_scroll.setWidgetResizable(True)
        self.panel_scroll.setFrameShape(QFrame.NoFrame)
        self.panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.panel_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.panel_scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(self.panel_scroll)
        self.surface = QFrame()
        self.surface.setObjectName("surface")
        self.panel_scroll.setWidget(self.surface)
        layout = QVBoxLayout(self.surface)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        header = QHBoxLayout()
        self.logo = label()
        header.addWidget(self.logo)
        self.title = label(APP_NAME, "title")
        self.title.setWordWrap(True)
        header.addWidget(self.title, 1)
        self.options = self._button("menu", "Options", self._open_menu)
        header.addWidget(self.options)
        layout.addLayout(header)

        mode_card = self.mode_card = QFrame()
        mode_card.setObjectName("modeCard")
        mode_layout = QVBoxLayout(mode_card)
        mode_layout.setContentsMargins(14, 12, 14, 12)
        mode_layout.setSpacing(6)
        self.ventilation_heading = label(self.tr("VENTILATION"), "section")
        mode_layout.addWidget(self.ventilation_heading)

        self.fan_readings = QWidget()
        fan_layout = QVBoxLayout(self.fan_readings)
        fan_layout.setContentsMargins(0, 4, 0, 4)
        fan_layout.setSpacing(5)
        speed_row = QHBoxLayout()
        self.fan_speed_caption = label(self.tr("Vitesse :"), "muted")
        speed_row.addWidget(self.fan_speed_caption)
        self.fan_speed = label("—", "muted")
        speed_row.addWidget(self.fan_speed, 1)
        fan_layout.addLayout(speed_row)
        self.fan_bar = QProgressBar()
        self.fan_scale = 3000
        self.fan_bar.setRange(0, self.fan_scale)
        self.fan_bar.setTextVisible(False)
        self.fan_bar.setFixedHeight(4)
        fan_layout.addWidget(self.fan_bar)
        mode_layout.addWidget(self.fan_readings)
        self.other_fans = label("", "muted")
        self.other_fans.setWordWrap(True)
        self.other_fans.hide()
        mode_layout.addWidget(self.other_fans)
        self.control_row = QWidget()
        self.control_row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        control_row = QHBoxLayout(self.control_row)
        control_row.setSizeConstraint(QLayout.SetMinimumSize)
        control_row.setContentsMargins(0, 4, 0, 0)
        control_row.setSpacing(8)
        self.mode_choice = QComboBox()
        self.mode_choice.setAccessibleName(self.tr("Mode à appliquer"))
        self.mode_choice.addItem(self.tr("Vitesse forcée commune"), "duty")
        self.mode_choice.currentIndexChanged.connect(self._selection_changed)
        control_row.addWidget(self.mode_choice, 1)
        self.apply_button = QPushButton(self.tr("Appliquer"))
        self.apply_button.setMinimumWidth(86)
        self.apply_button.clicked.connect(self._apply_control)
        control_row.addWidget(self.apply_button)
        mode_layout.addWidget(self.control_row)

        self.duty_row = QWidget()
        self.duty_row.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        duty_layout = QHBoxLayout(self.duty_row)
        duty_layout.setSizeConstraint(QLayout.SetMinimumSize)
        duty_layout.setContentsMargins(0, 2, 0, 2)
        self.duty_slider = QSlider(Qt.Horizontal)
        self.duty_slider.setRange(50, 99)
        self.duty_slider.setAccessibleName(self.tr("Consigne commune des ventilateurs"))
        self.duty_percent = QSpinBox()
        self.duty_percent.setRange(50, 99)
        self.duty_percent.setSuffix(" %")
        self.duty_percent.setKeyboardTracking(False)
        self.duty_percent.setMinimumWidth(84)
        self.duty_percent.setAccessibleName(self.tr("Consigne commune de ventilation en pourcentage"))
        self.duty_slider.valueChanged.connect(self.duty_percent.setValue)
        self.duty_percent.valueChanged.connect(self.duty_slider.setValue)
        self.duty_percent.valueChanged.connect(self._draft_changed)
        self.duty_slider.sliderReleased.connect(self._schedule_fit)
        self.duty_percent.editingFinished.connect(self._schedule_fit)
        self.duty_percent.setValue(75)
        duty_layout.addWidget(self.duty_slider, 1)
        duty_layout.addWidget(self.duty_percent)
        mode_layout.addWidget(self.duty_row)
        self.duty_detail = label(self.tr("Consigne commune CPU + GPU · 50–99 %"), "muted")
        self.duty_detail.setWordWrap(True)
        mode_layout.addWidget(self.duty_detail)
        self.restore_button = QPushButton(self.tr("Rétablir l’automatique initial"))
        self.restore_button.clicked.connect(self._restore_control)
        mode_layout.addWidget(self.restore_button)
        self.control_notice = label(self.tr("Connexion au moteur…"), "muted")
        self.control_notice.setWordWrap(True)
        mode_layout.addWidget(self.control_notice)
        self.apply_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        layout.addWidget(mode_card)

        self.sensors_panel = QWidget()
        self.sensors_panel.setAccessibleName(self.tr("Capteurs de température et de charge"))
        self.sensor_grid = QGridLayout(self.sensors_panel)
        self.sensor_grid.setContentsMargins(0, 0, 0, 0)
        self.sensor_grid.setSpacing(10)
        self.sensor_grid.setColumnStretch(0, 1)
        self.sensor_grid.setColumnStretch(1, 1)
        self.cpu = SensorCard("CPU", "Processeur", has_load=True, language=self.language)
        self.gpu = SensorCard("GPU NVIDIA", "Carte graphique", has_load=True, language=self.language)
        self.igpu = SensorCard("GPU INTÉGRÉ", "Graphique intégré", language=self.language)
        self.sensor_cards = {"cpu": self.cpu, "nvidia_gpu": self.gpu, "integrated_gpu": self.igpu}
        for index, card in enumerate(self.sensor_cards.values()):
            self.sensor_grid.addWidget(card, index // 2, index % 2)
        self.disk_cards = {}
        self._storage_signature = None
        layout.insertWidget(1, self.sensors_panel)
        self._selection_changed()

        self.error = label("", "error")
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.error.hide()
        layout.addWidget(self.error)

    def _make_menu(self):
        self.menu = QMenu(self)
        self.menu_texts = []

        def action(menu, text, callback=None):
            item = menu.addAction(self.tr(text))
            if callback:
                item.triggered.connect(callback)
            self.menu_texts.append((item, text, {}))
            return item

        def submenu(text):
            menu = self.menu.addMenu(self.tr(text))
            self.menu_texts.append((menu.menuAction(), text, {}))
            return menu

        self.pin_action = action(self.menu, "Épingler au premier plan", self._set_pinned)
        self.pin_action.setCheckable(True)
        self.pin_action.setChecked(self.pinned)
        theme_menu = submenu("Apparence")
        self.theme_group = QActionGroup(self)
        for value, name in (("auto", "Automatique (Windows)"), ("light", "Clair"), ("dark", "Sombre")):
            item = action(theme_menu, name, lambda checked=False, choice=value: self._set_theme(choice))
            item.setCheckable(True)
            item.setChecked(value == self.theme)
            self.theme_group.addAction(item)
        interval_menu = submenu("Actualisation")
        self.interval_group = QActionGroup(self)
        for value in (1000, 2000, 5000):
            source = "{seconds} seconde" if value == 1000 else "{seconds} secondes"
            item = interval_menu.addAction(self.tr(source, seconds=value // 1000))
            self.menu_texts.append((item, source, {"seconds": value // 1000}))
            item.setCheckable(True)
            item.setChecked(value == self.interval)
            self.interval_group.addAction(item)
            item.triggered.connect(lambda checked=False, choice=value: self._set_interval(choice))
        language_menu = submenu("Langue")
        self.language_group = QActionGroup(self)
        self.language_actions = {}
        for value, name in (("auto", "Langue de Windows"), ("fr", "Français"), ("en", "English")):
            item = action(language_menu, name, lambda checked=False, choice=value: self._set_language(choice))
            item.setCheckable(True)
            item.setChecked(value == self.language_preference)
            self.language_group.addAction(item)
            self.language_actions[value] = item
        self.menu.addSeparator()
        action(self.menu, "Quitter", self.quit_requested.emit)
        self.menu.aboutToHide.connect(lambda: QTimer.singleShot(150, self._hide_if_inactive))

    def _open_menu(self):
        self.menu.popup(self.options.mapToGlobal(QPoint(0, self.options.height())))

    def _set_language(self, preference):
        if preference not in ("auto", "fr", "en"):
            return
        self.language_preference = preference
        self.language = resolve_language(preference)
        self.tr = Translator(self.language)
        self.settings.setValue("language", preference)
        self.language_actions[preference].setChecked(True)
        self._retranslate()

    def _retranslate(self):
        # Update existing widgets in place: drafts, focus, leases and signals stay intact.
        for item, source, values in self.menu_texts:
            item.setText(self.tr(source, **values))
        self.ventilation_heading.setText(self.tr("VENTILATION"))
        self.fan_speed_caption.setText(self.tr("Vitesse :"))
        self.fan_speed.setAccessibleName(self.tr("Vitesse de ventilation"))
        self.fan_bar.setAccessibleName(self.tr("Vitesse de ventilation"))
        self.mode_choice.setAccessibleName(self.tr("Mode à appliquer"))
        self.duty_slider.setAccessibleName(self.tr("Consigne commune des ventilateurs"))
        self.duty_percent.setAccessibleName(self.tr("Consigne commune de ventilation en pourcentage"))
        self.restore_button.setText(self.tr("Rétablir l’automatique initial"))
        self.apply_button.setText(self.tr("Envoi…") if self.control_busy else self.tr("Appliquer"))
        self.sensors_panel.setAccessibleName(self.tr("Capteurs de température et de charge"))
        for card in self.sensor_cards.values():
            card.retranslate(self.language)
        profiles = {p["id"]: p for p in (self.last_payload or {}).get("profiles", [])}
        for index in range(self.mode_choice.count()):
            key = self.mode_choice.itemData(index)
            if key == "duty":
                self.mode_choice.setItemText(index, self.tr("Vitesse forcée commune"))
            elif key in profiles:
                self.mode_choice.setItemText(index, profile_name(profiles[key], self.language))
        caps = (self.last_payload or {}).get("capabilities", {})
        self.duty_detail.setText(self.tr("Retour auto dès CPU {cpu_limit} °C / GPU {gpu_limit} °C",
                                       cpu_limit=caps.get("manual_cpu_limit_celsius", 85),
                                       gpu_limit=caps.get("manual_gpu_limit_celsius", 80)))
        error = self.control_error
        if self.connection_error is not None:
            self.show_error(self.connection_error)
        elif self.last_payload is not None:
            self._render_telemetry(self.last_payload)
        else:
            self._update_fan_readings({})
            self.mode_choice.setToolTip(self.tr("Connexion en cours…"))
            self._set_control_notice("Connexion au moteur…")
            self.apply_button.setText(self.tr("Appliquer"))
            self.mode_choice.setItemText(0, self.tr("Vitesse forcée commune"))
            self.duty_detail.setText(self.tr("Consigne commune CPU + GPU · 50–99 %"))
            if self.tray:
                self.tray.setToolTip(APP_NAME + " · " + self.tr("connexion en cours"))
        if error:
            self.show_control_error(error)
        self._schedule_fit()

    def _selection_changed(self, *_):
        manual = self.mode_choice.currentData() == "duty"
        self.duty_row.setVisible(manual)
        self.duty_detail.setVisible(manual)
        self._draft_changed()
        self._schedule_fit()

    def _draft_changed(self, *_):
        if self.last_payload is not None:
            self._update_controls(self.last_payload)

    def _schedule_fit(self):
        if not self._fit_timer.isActive():
            self._fit_timer.start(0)

    @Slot()
    def fit_to_contents(self):
        if (self.duty_slider.isSliderDown() or self.duty_percent.hasFocus()
                or self.duty_percent.lineEdit().hasFocus() or QApplication.activePopupWidget()):
            return
        layout = self.surface.layout()
        layout.activate()
        desired = layout.totalHeightForWidth(self.width() - 2)
        desired = max(desired, layout.minimumSize().height(), self.surface.sizeHint().height()) + 2
        area = self.screen().availableGeometry()
        height = max(self.minimumHeight(), min(desired, area.height() - 16))
        if height != self.height():
            previous_bottom = self.geometry().bottom()
            self.resize(self.width(), height)
            if self.isVisible():
                y = max(area.top() + 8, min(previous_bottom - height + 1, area.bottom() - height - 7))
                self.move(max(area.left(), min(self.x(), area.right() - self.width() + 1)), y)

    def _apply_control(self):
        if not self.apply_button.isEnabled():
            return
        choice = self.mode_choice.currentData()
        self.duty_percent.interpretText()
        self.set_control_busy(True)
        if choice == "duty":
            self.duty_requested.emit(self.duty_percent.value())
        elif choice:
            self.profile_requested.emit(choice)

    def _restore_control(self):
        if self.restore_button.isEnabled():
            self.set_control_busy(True)
            self.restore_requested.emit()

    @Slot(bool)
    def set_control_busy(self, busy):
        self.control_busy = busy
        if self.last_payload is not None:
            self._update_controls(self.last_payload)

    @Slot(str)
    def show_control_error(self, message):
        self.control_error = message
        self._set_control_notice(message)

    def _set_control_notice(self, message):
        self.control_notice.setText(self.tr.message(message))
        self.control_notice.setToolTip(self.tr.message(message))
        self.control_notice.setVisible(bool(message))
        self._schedule_fit()

    def _update_controls(self, payload):
        self.control_error = None
        profiles = payload.get("profiles", [])
        signature = tuple((p["id"], profile_name(p, self.language)) for p in profiles)
        if signature != self._profile_signature:
            choice = self.mode_choice.currentData() if self.mode_choice.count() > 1 else "balanced"
            self._profile_signature = signature
            self.mode_choice.blockSignals(True)
            self.mode_choice.clear()
            for identifier, name in signature:
                self.mode_choice.addItem(name, identifier)
            self.mode_choice.addItem(self.tr("Vitesse forcée commune"), "duty")
            index = self.mode_choice.findData(choice or "balanced")
            self.mode_choice.setCurrentIndex(max(0, index))
            self.mode_choice.blockSignals(False)
            self._selection_changed()
        caps = payload.get("capabilities", {})
        control = payload["status"].get("control", {})
        foreign = bool(control.get("active_lease")) and not payload.get("control_owned")
        enabled = bool(caps.get("write_enabled")) and not self.monitor_only and not foreign
        cpu_limit = caps.get("manual_cpu_limit_celsius", 85)
        gpu_limit = caps.get("manual_gpu_limit_celsius", 80)
        self.duty_detail.setText(self.tr("Retour auto dès CPU {cpu_limit} °C / GPU {gpu_limit} °C", cpu_limit=cpu_limit, gpu_limit=gpu_limit))
        self.mode_choice.setEnabled(enabled)
        self.apply_button.setEnabled(enabled and not self.control_busy)
        self.apply_button.setText(self.tr("Envoi…") if self.control_busy else self.tr("Appliquer"))
        self.duty_slider.setEnabled(enabled)
        self.duty_percent.setEnabled(enabled)
        self.restore_button.setEnabled(bool(payload.get("control_owned")) and not self.control_busy)
        notice = payload.get("control_notice", "")
        if self.monitor_only:
            notice = self.tr("Surveillance seule : commandes désactivées.")
        elif not caps.get("write_enabled"):
            notice = self.tr("Moteur en lecture seule : relancer avec --allow-write pour activer les commandes.")
        elif foreign:
            notice = self.tr("Une autre application contrôle le refroidissement.")
        elif self.control_busy:
            notice = self.tr("Application de la commande…")
        elif not notice and self.mode_choice.currentData() == "duty" and (
                control.get("mode") != "duty" or not any(
                    c.get("duty_percent") == self.duty_percent.value()
                    for c in payload["status"].get("manual_channels", []))):
            notice = self.tr("À appliquer : vitesse forcée à {percent} %.", percent=self.duty_percent.value())
        elif not notice and payload.get("control_owned"):
            notice = self.tr("Commande maintenue tant que l’application reste ouverte.")
        self._set_control_notice(notice)

    def _set_theme(self, theme):
        self.theme = theme
        self.settings.setValue("theme", theme)
        self._apply_theme()

    def _apply_theme(self, *_):
        dark = self.theme == "dark" or (self.theme == "auto" and
                QApplication.instance().styleHints().colorScheme() == Qt.ColorScheme.Dark)
        bg, card, text, muted, border, accent, mode = (
            ("#171c24", "#202833", "#f0f4fa", "#9baabc", "#33404f", "#7bc1ff", "#1c2e3e") if dark else
            ("#f8fafc", "#ffffff", "#162637", "#63778b", "#dce4ec", "#176fba", "#eaf3fc")
        )
        self.setStyleSheet(f"""
            QWidget {{ color: {text}; font-family: 'Segoe UI'; font-size: 12px; }}
            QFrame#surface {{ background: {bg}; border: 1px solid {border}; border-radius: 14px; }}
            QLabel {{ background: transparent; border: none; }}
            QLabel#title {{ font-size: 17px; font-weight: 650; }}
            QLabel#muted {{ color: {muted}; font-size: 11px; }}
            QLabel#section, QLabel#cardTitle {{ color: {muted}; font-size: 10px; font-weight: 650; }}
            QFrame#sensorCard {{ background: {card}; border: 1px solid {border}; border-radius: 10px; }}
            QLabel#temperature {{ font-size: 27px; font-weight: 600; padding-top: 4px; padding-bottom: 3px; }}
            QLabel#temperature[hot=true] {{ color: {'#ffb276' if dark else '#ae4b0b'}; }}
            QProgressBar {{ border: none; background: {border}; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 2px; }}
            QLabel#rowValue {{ font-weight: 600; }}
            QFrame#modeCard {{ background: {mode}; border-radius: 10px; }}
            QLabel#modeTitle {{ color: {accent}; font-size: 15px; font-weight: 600; }}
            QLabel#status {{ color: {muted}; font-size: 11px; }}
            QLabel#error {{ color: {'#ffb5a7' if dark else '#a43c2f'}; font-size: 11px; }}
            QToolButton {{ border: none; border-radius: 6px; background: transparent; }}
            QToolButton:hover, QToolButton:checked {{ background: {mode}; }}
            QPushButton, QComboBox, QSpinBox {{ background: {card}; border: 1px solid {border}; border-radius: 5px; padding: 5px; }}
            QPushButton:hover {{ border-color: {accent}; }}
            QPushButton:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {muted}; }}
            QComboBox QAbstractItemView {{ background: {card}; selection-background-color: {mode}; }}
            QSlider::groove:horizontal {{ height: 4px; background: {border}; border-radius: 2px; }}
            QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
            QSlider::handle:horizontal {{ width: 12px; margin: -4px 0; border-radius: 6px; background: {accent}; }}
            QScrollArea#panelScroll {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ width: 8px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {border}; min-height: 20px; border-radius: 4px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QMenu {{ background: {card}; border: 1px solid {border}; padding: 5px; }}
            QMenu::item {{ padding: 7px 24px 7px 12px; }}
            QMenu::item:selected {{ background: {mode}; }}
            QMenu::separator {{ height: 1px; background: {border}; margin: 4px; }}
        """)
        self.logo.setPixmap(fan_icon(color=accent).pixmap(32, 32))
        self.options.setIcon(control_icon("menu", muted))
        if self.tray:
            self.tray.setIcon(fan_icon(color="#9bd4ff" if dark else "#236ca8"))
        for widget in (self.mode_choice, self.duty_percent, self.apply_button, self.restore_button):
            widget.ensurePolished()
            widget.setMinimumHeight(max(30, widget.sizeHint().height()))
        self.control_notice.setMinimumHeight(self.control_notice.fontMetrics().lineSpacing() * 2)
        self._schedule_fit()

    def _set_interval(self, value):
        self.interval = value
        self.settings.setValue("interval", value)
        self.interval_requested.emit(value)

    def _set_pinned(self, value):
        self.pinned = value
        self.pin_action.setChecked(value)
        self.settings.setValue("pinned", value)
        self._apply_theme()

    def _set_storage(self, rows):
        signature = tuple((key, name) for key, name, _, _ in rows)
        if signature != self._storage_signature:
            self._storage_signature = signature
            for key, card in self.disk_cards.items():
                self.sensor_grid.removeWidget(card)
                self.sensor_cards.pop("storage:" + key, None)
                card.deleteLater()
            self.disk_cards = {}
            for index, (key, name, _, _) in enumerate(rows):
                card = SensorCard(f"SSD {index + 1}", name, language=self.language)
                self.disk_cards[key] = card
                self.sensor_cards["storage:" + key] = card
                position = index + 3
                self.sensor_grid.addWidget(card, position // 2, position % 2)
        for key, _, value, tooltip in rows:
            card = self.disk_cards[key]
            card.update_temperature({"celsius": value})
            card.setToolTip(tooltip)

    def _update_fan_readings(self, fans):
        readings = {}
        for key in ("cpu", "nvidia_gpu"):
            sensor = fans.get(key, {})
            rpm = number(sensor.get("rpm"))
            readings[key] = rpm if sensor.get("available", True) and rpm is not None and rpm >= 0 else None
        available = [rpm for rpm in readings.values() if rpm is not None]
        mean = sum(available) / len(available) if available else None
        # One shared visual. The exact raw counters remain available in its tooltip.
        if mean is not None:
            self.fan_scale = max(self.fan_scale, ceil(mean / 1000) * 1000)
        self.fan_bar.setMaximum(self.fan_scale)
        self.fan_bar.setValue(round(mean) if mean is not None else 0)
        self.fan_bar.setEnabled(mean is not None)
        self.fan_speed.setText(format_rpm(mean, self.language))
        tooltip = self.tr("Ventilation commune : moyenne des relevés disponibles.\nCPU : {cpu} · GPU : {gpu}\nÉchelle d’affichage automatique : 0–{maximum}.",
                          cpu=format_rpm(readings["cpu"], self.language),
                          gpu=format_rpm(readings["nvidia_gpu"], self.language),
                          maximum=format_rpm(self.fan_scale, self.language))
        for widget in (self.fan_readings, self.fan_speed, self.fan_bar):
            widget.setToolTip(tooltip)

    @Slot(dict)
    def update_telemetry(self, payload):
        self.last_payload = payload
        self.last_update = datetime.now().astimezone()
        self.connection_error = None
        self._render_telemetry(payload)

    def _render_telemetry(self, payload):
        status = payload["status"]
        caps = payload.get("capabilities", {})
        sensors = {s["id"]: s for s in status.get("temperatures", [])}
        fans = {f["id"]: f for f in status.get("fans", [])}
        self.cpu.update_temperature(sensors.get("cpu", {}))
        self.gpu.update_temperature(sensors.get("nvidia_gpu", {}))
        self.igpu.update_temperature(sensors.get("integrated_gpu", {}))
        self._update_fan_readings(fans)
        if payload.get("load"):
            self.update_loads(payload["load"])
        else:
            payload["load"] = self.current_load
        additional = [f"{self.tr(NAMES.get(key, key))}: {format_rpm(number(fan.get('rpm')), self.language)}"
                      for key, fan in fans.items() if key not in ("cpu", "nvidia_gpu")]
        self.other_fans.setText(" · ".join(additional))
        self.other_fans.setVisible(bool(additional))
        self._set_storage(storage_rows(status, self.language))
        mode = cooling_mode(status, payload.get("profiles", []), self.language)
        self.mode_choice.setToolTip(mode.title + "\n" + mode.detail)
        simulated = caps.get("simulated", False)
        self.title.setText(APP_NAME + (" · SIMULATION" if simulated else ""))
        self.setWindowTitle(self.title.text())
        self.title.setToolTip(self.tr("Dernier relevé : {time}\nMoteur {version} · Relevé toutes les {seconds} s\nTempératures SSD mises en cache 10 s par le moteur",
                                      time=self.last_update.strftime("%H:%M:%S"), version=status.get("version", "?"), seconds=self.interval // 1000))
        self.error.hide()
        self._update_controls(payload)
        if self.tray:
            self.tray.setToolTip(f"{APP_NAME} · {self.tr('Ventilation')}\n{mode.title}\n" +
                                 self.tr("Ventilation : {rpm}", rpm=self.fan_speed.text()) + "\n" +
                                 f"CPU {self.cpu.value.text()} · {self.cpu.load.text()}\n"
                                 f"GPU {self.gpu.value.text()} · {self.gpu.load.text()}" +
                                 ("\nSIMULATION" if simulated else ""))
        self._schedule_fit()

    @Slot(dict)
    def update_loads(self, load):
        self.current_load = load
        self.cpu.update_load(load.get("cpu_percent"), load.get("cpu_error", "Charge CPU moyenne depuis le précédent relevé"))
        self.gpu.update_load(load.get("gpu_percent"), load.get("gpu_error", "Occupation du GPU NVIDIA mesurée par NVML"))
        if self.last_payload is not None:
            self.last_payload["load"] = load

    @Slot(str)
    def show_error(self, message):
        self.connection_error = message
        for card in self.sensor_cards.values():
            card.update_temperature({})
        self._update_fan_readings({})
        self.cpu.update_load(None)
        self.gpu.update_load(None)
        self.apply_button.setEnabled(False)
        self.restore_button.setEnabled(False)
        self.mode_choice.setEnabled(False)
        self._set_control_notice("Commandes indisponibles pendant la déconnexion.")
        self.other_fans.hide()
        self.mode_choice.setToolTip(self.tr("Indisponible"))
        self.error.setText(self.tr.message(message))
        self.error.setToolTip(self.tr.message(message))
        self.error.show()
        self.title.setToolTip(self.tr("Déconnecté\nDernier relevé : {time}",
            time=self.last_update.strftime("%H:%M:%S") if self.last_update else self.tr("aucun")))
        if self.tray:
            self.tray.setToolTip(APP_NAME + " · " + self.tr("déconnecté") + "\n" + self.tr.message(message)[:160])
        self._schedule_fit()

    def show_panel(self):
        anchor = self.tray.geometry() if self.tray else QRect()
        screen = QApplication.screenAt(anchor.center()) if not anchor.isNull() else QApplication.screenAt(QCursor.pos())
        screen = screen or QApplication.primaryScreen()
        area = screen.availableGeometry()
        self.fit_to_contents()
        if anchor.isNull():
            anchor = QRect(area.right() - 40, area.bottom(), 1, 1)
        self.move(anchored_position(area, self.size(), anchor))
        self.show()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            elif not self.last_hide or (datetime.now() - self.last_hide).total_seconds() > 0.2:
                self.show_panel()

    def _hide_if_inactive(self):
        if (self.isVisible() and not self.pinned and not self.dialog_open and not self.quitting
                and not self.isActiveWindow() and QApplication.activePopupWidget() is None):
            self.hide()

    def eventFilter(self, watched, event):
        if watched is self and event.type() == QEvent.WindowDeactivate:
            QTimer.singleShot(150, self._hide_if_inactive)
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def hideEvent(self, event):
        self.last_hide = datetime.now()
        super().hideEvent(event)

    def closeEvent(self, event):
        if self.quitting:
            event.accept()
        else:
            event.ignore()
            self.hide()


class CoolingController(QObject):
    stop_requested = Signal()
    stop_load_requested = Signal()

    def __init__(self, window, worker):
        super().__init__()
        self.window = window
        self.thread = QThread(self)
        self.worker = worker
        self.load_thread = QThread(self)
        self.load_worker = LoadWorker(interval=worker.interval)
        self.load_worker.moveToThread(self.load_thread)
        self.load_thread.started.connect(self.load_worker.start)
        self.load_worker.updated.connect(window.update_loads)
        window.interval_requested.connect(self.load_worker.set_interval)
        self.stop_load_requested.connect(self.load_worker.stop)
        self.load_worker.stopped.connect(self.load_thread.quit, Qt.DirectConnection)
        self.load_thread.finished.connect(self.load_worker.deleteLater)
        self.load_thread.finished.connect(self._maybe_quit)
        worker.moveToThread(self.thread)
        self.thread.started.connect(worker.start)
        worker.updated.connect(window.update_telemetry)
        worker.failed.connect(window.show_error)
        window.refresh_requested.connect(worker.poll)
        window.interval_requested.connect(worker.set_interval)
        window.engine_requested.connect(worker.set_engine)
        window.profile_requested.connect(worker.set_profile)
        window.duty_requested.connect(worker.set_duty)
        window.restore_requested.connect(worker.restore)
        worker.control_failed.connect(window.show_control_error)
        worker.busy.connect(window.set_control_busy)
        window.quit_requested.connect(self.stop)
        self.stop_requested.connect(worker.stop)
        worker.stopped.connect(self.thread.quit, Qt.DirectConnection)
        self.thread.finished.connect(worker.deleteLater)
        self.thread.finished.connect(self._maybe_quit)
        QApplication.instance().aboutToQuit.connect(self.cleanup)
        self.thread.start()
        self.load_thread.start()

    @Slot()
    def _maybe_quit(self):
        if self.window.quitting and not self.thread.isRunning() and not self.load_thread.isRunning():
            QApplication.instance().quit()

    @Slot()
    def stop(self):
        if self.window.quitting:
            return
        self.window.quitting = True
        self.window.hide()
        if self.window.tray:
            self.window.tray.hide()
        self.thread.requestInterruption()
        self.load_thread.requestInterruption()
        self.stop_requested.emit()
        self.stop_load_requested.emit()

    def cleanup(self):
        if self.load_thread.isRunning():
            self.load_thread.requestInterruption()
            self.stop_load_requested.emit()
        if self.thread.isRunning():
            self.thread.requestInterruption()
            self.stop_requested.emit()
            # The worker closes its owned engine, then directly quits its thread.
            self.thread.wait()
        if self.load_thread.isRunning():
            self.load_thread.wait()


def main():
    parser = argparse.ArgumentParser(description="NUC temperatures, load and fan control in the Windows system tray")
    parser.add_argument("--show", action="store_true", help="Show the panel at startup")
    parser.add_argument("--engine", type=Path, help="Path to nuc-engine.exe")
    parser.add_argument("--no-start", action="store_true", help="Connect only to an already running engine")
    parser.add_argument("--read-only", action="store_true", help="Disable cooling controls")
    parser.add_argument("--language", choices=("auto", "fr", "en"), help="Override the interface language for this launch")
    args = parser.parse_args()
    application = QApplication([sys.argv[0]])
    application.setApplicationName(APP_NAME)
    application.setOrganizationName("nicklausFR")
    application.setQuitOnLastWindowClosed(False)
    application.setFont(QFont("Segoe UI", 10))
    window = CoolingWindow(monitor_only=args.read_only, language=args.language)
    engine = args.engine or window.settings.value("engine", "") or None
    worker = PollWorker(engine, autostart=not args.no_start, allow_write=not args.read_only,
                        interval=window.interval, control_allowed=not args.read_only)
    controller = CoolingController(window, worker)
    if args.show or not QSystemTrayIcon.isSystemTrayAvailable():
        QTimer.singleShot(0, window.show_panel)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
