"""
GLM-OCR screen-snip tool.

Terminal usage:
    python3 ocr_snip.py                      # snip immediately, task=text, copies to clipboard
    python3 ocr_snip.py --task table          # table recognition
    python3 ocr_snip.py --task figure         # figure recognition
    python3 ocr_snip.py --prompt "Formula Recognition:"   # custom prompt
    python3 ocr_snip.py --gui                 # launch the full window UI instead

Requirements:
    pip install PyQt5 ollama
    ollama pull glm-ocr   (and ensure `ollama serve` is running)

Created with Claude Sonnet 5.
"""

import sys
import argparse
import subprocess
from PyQt5 import QtWidgets, QtGui, QtCore
import ollama

TASK_PROMPTS = {
    "text": "Text Recognition:",
    "table": "Table Recognition:",
    "figure": "Figure Recognition:",
}


# ---------- Region-selection overlay ----------

class SnipOverlay(QtWidgets.QWidget):
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
            self.region_selected.emit(QtCore.QRect())  # empty = cancelled
            self.close()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.drawPixmap(0, 0, self.screen_pixmap)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 100))

        if self.origin and self.current:
            rect = QtCore.QRect(self.origin, self.current).normalized()
            painter.drawPixmap(rect, self.screen_pixmap, rect)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 0, 0), 2))
            painter.drawRect(rect)


# ---------- OCR call ----------

def run_ocr(img_bytes: bytes, prompt: str, model: str) -> str:
    response = ollama.chat(
        model=model,
        messages=[{
            "role": "user",
            "content": prompt,
            "images": [img_bytes],
        }],
    )
    return response["message"]["content"].strip()


def notify_mac(title: str, message: str):
    """Show a native macOS notification banner."""
    safe_message = message.replace('"', '\\"')
    safe_title = title.replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    try:
        subprocess.run(["osascript", "-e", script], check=False)
    except FileNotFoundError:
        pass  # not on macOS


# ---------- Direct snip-and-run mode (for keyboard shortcuts / terminal) ----------

def direct_snip_flow(prompt: str, model: str):
    app = QtWidgets.QApplication(sys.argv)

    screen = QtWidgets.QApplication.primaryScreen()
    geometry = screen.geometry()
    pixmap = screen.grabWindow(0)

    overlay = SnipOverlay(pixmap, geometry)

    def on_selected(rect: QtCore.QRect):
        if rect.isNull() or rect.width() < 5 or rect.height() < 5:
            app.quit()
            return

        full_pixmap = screen.grabWindow(0)
        cropped = full_pixmap.copy(rect)

        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        cropped.save(buffer, "PNG")
        img_bytes = bytes(buffer.data())

        try:
            result_text = run_ocr(img_bytes, prompt, model)
        except Exception as e:
            notify_mac("GLM-OCR Error", str(e))
            app.quit()
            return

        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText(result_text)

        preview = result_text if len(result_text) < 120 else result_text[:117] + "..."
        notify_mac("GLM-OCR: copied to clipboard", preview)
        app.quit()

    overlay.region_selected.connect(on_selected)
    overlay.show()
    app.exec_()


# ---------- Full GUI window mode ----------

class OcrWindow(QtWidgets.QWidget):
    def __init__(self, default_model="glm-ocr"):
        super().__init__()
        self.setWindowTitle("GLM-OCR Snip Tool")
        self.setMinimumWidth(500)
        self._build_ui(default_model)
        self.overlay = None

    def _build_ui(self, default_model):
        layout = QtWidgets.QVBoxLayout(self)

        task_row = QtWidgets.QHBoxLayout()
        task_row.addWidget(QtWidgets.QLabel("Task:"))
        self.task_combo = QtWidgets.QComboBox()
        self.task_combo.addItems(["text", "table", "figure", "custom"])
        self.task_combo.currentTextChanged.connect(self._on_task_changed)
        task_row.addWidget(self.task_combo)
        layout.addLayout(task_row)

        self.custom
