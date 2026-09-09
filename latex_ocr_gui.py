"""
Minimal screen-snip OCR GUI using Ollama's glm-ocr model.

Requirements:
    pip install PyQt5 ollama pillow

Usage:
    python latex_ocr_gui.py

Make sure `ollama serve` is running and you've pulled the model:
    ollama pull glm-ocr

Made with Claude Sonnet 5
"""

import sys
import io
from PyQt5 import QtWidgets, QtGui, QtCore
import ollama


# ---------- Region-selection overlay ----------

class SnipOverlay(QtWidgets.QWidget):
    """Full-screen semi-transparent overlay for drag-selecting a region."""

    region_selected = QtCore.pyqtSignal(QtCore.QRect)

    def __init__(self, screen_pixmap: QtGui.QPixmap, screen_geometry: QtCore.QRect):
        super().__init__()
        self.screen_pixmap = screen_pixmap
        self.setGeometry(screen_geometry)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setCursor(QtCore.Qt.CrossCursor)
        self.origin = None
        self.current = None
        self.selecting = False

    def mousePressEvent(self, event):
        self.origin = event.pos()
        self.current = event.pos()
        self.selecting = True
        self.update()

    def mouseMoveEvent(self, event):
        if self.selecting:
            self.current = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        self.selecting = False
        if self.origin and self.current:
            rect = QtCore.QRect(self.origin, self.current).normalized()
            self.region_selected.emit(rect)
        self.close()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.drawPixmap(0, 0, self.screen_pixmap)

        overlay = QtGui.QColor(0, 0, 0, 100)
        painter.fillRect(self.rect(), overlay)

        if self.origin and self.current:
            rect = QtCore.QRect(self.origin, self.current).normalized()
            # show the un-darkened selection by redrawing that portion sharp
            painter.drawPixmap(rect, self.screen_pixmap, rect)
            pen = QtGui.QPen(QtGui.QColor(255, 0, 0), 2)
            painter.setPen(pen)
            painter.drawRect(rect)


# ---------- Main window ----------

class OcrWindow(QtWidgets.QWidget):
    PROMPTS = {
        "Text Recognition": "Text Recognition:",
        "Table Recognition": "Table Recognition:",
        "Figure Recognition": "Figure Recognition:",
        "Custom prompt": None,  # user-supplied text below
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("GLM-OCR Snip Tool")
        self.setMinimumWidth(500)
        self._build_ui()
        self.overlay = None

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Task selector
        task_row = QtWidgets.QHBoxLayout()
        task_row.addWidget(QtWidgets.QLabel("Task:"))
        self.task_combo = QtWidgets.QComboBox()
        self.task_combo.addItems(self.PROMPTS.keys())
        self.task_combo.currentTextChanged.connect(self._on_task_changed)
        task_row.addWidget(self.task_combo)
        layout.addLayout(task_row)

        # Custom prompt field (only enabled when "Custom prompt" selected)
        self.custom_prompt_edit = QtWidgets.QLineEdit()
        self.custom_prompt_edit.setPlaceholderText(
            "e.g. Transcribe the math in this image as LaTeX only, no commentary."
        )
        self.custom_prompt_edit.setEnabled(False)
        layout.addWidget(self.custom_prompt_edit)

        # Model name field
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(QtWidgets.QLabel("Model:"))
        self.model_edit = QtWidgets.QLineEdit("glm-ocr")
        model_row.addWidget(self.model_edit)
        layout.addLayout(model_row)

        # Capture button
        self.capture_btn = QtWidgets.QPushButton("New Snip (select region)")
        self.capture_btn.clicked.connect(self.start_snip)
        layout.addWidget(self.capture_btn)

        # Preview of captured image
        self.image_preview = QtWidgets.QLabel()
        self.image_preview.setFixedHeight(150)
        self.image_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.image_preview.setStyleSheet("border: 1px solid gray;")
        self.image_preview.setText("No image captured yet")
        layout.addWidget(self.image_preview)

        # Result output
        layout.addWidget(QtWidgets.QLabel("Result (auto-copied to clipboard):"))
        self.result_edit = QtWidgets.QPlainTextEdit()
        self.result_edit.setReadOnly(False)  # editable in case you want to tweak
        layout.addWidget(self.result_edit)

        # Status label
        self.status_label = QtWidgets.QLabel("")
        layout.addWidget(self.status_label)

        self.setLayout(layout)

    def _on_task_changed(self, text):
        self.custom_prompt_edit.setEnabled(text == "Custom prompt")

    def start_snip(self):
        # Hide main window so it doesn't appear in the screenshot
        self.hide()
        QtCore.QTimer.singleShot(200, self._grab_screen)

    def _grab_screen(self):
        screen = QtWidgets.QApplication.primaryScreen()
        geometry = screen.geometry()
        pixmap = screen.grabWindow(0)

        self.overlay = SnipOverlay(pixmap, geometry)
        self.overlay.region_selected.connect(self._on_region_selected)
        self.overlay.show()

    def _on_region_selected(self, rect: QtCore.QRect):
        self.show()
        if rect.width() < 5 or rect.height() < 5:
            self.status_label.setText("Selection too small, try again.")
            return

        screen = QtWidgets.QApplication.primaryScreen()
        full_pixmap = screen.grabWindow(0)
        cropped = full_pixmap.copy(rect)

        # show preview
        self.image_preview.setPixmap(
            cropped.scaled(
                self.image_preview.width(),
                self.image_preview.height(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )

        # convert QPixmap -> PNG bytes
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        cropped.save(buffer, "PNG")
        img_bytes = bytes(buffer.data())

        self.status_label.setText("Running OCR...")
        QtWidgets.QApplication.processEvents()

        try:
            result_text = self.run_ocr(img_bytes)
        except Exception as e:
            self.status_label.setText(f"Error: {e}")
            return
