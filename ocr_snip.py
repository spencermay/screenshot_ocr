"""
GLM-OCR screen-snip tool.

Terminal usage:
    python3 ocr_snip.py                      # snip immediately, task=text, copies to clipboard
    python3 ocr_snip.py --task table          # table recognition
    python3 ocr_snip.py --task figure         # figure recognition
    python3 ocr_snip.py --prompt "Formula Recognition:"   # custom prompt
    python3 ocr_snip.py --gui                 # launch the full window UI instead
    python3 ocr_snip.py --model glm-ocr:latest --task text

Requirements:
    pip install PyQt5 ollama
    ollama pull glm-ocr   (and ensure `ollama serve` is running)
"""

import sys
import argparse
import subprocess
from PyQt5 import QtWidgets, QtGui, QtCore
import ollama

try:
    from AppKit import NSApplication
    HAS_APPKIT = True
except ImportError:
    HAS_APPKIT = False

def activate_mac_app():
    """Force this app to the foreground on macOS."""
    if HAS_APPKIT:
        app = NSApplication.sharedApplication()
        app.activateIgnoringOtherApps_(True)

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
    safe_message = message.replace('\\', '\\\\').replace('"', '\\"')
    safe_title = title.replace('\\', '\\\\').replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    try:
        subprocess.run(["osascript", "-e", script], check=False)
    except FileNotFoundError:
        pass  # not on macOS


def qpixmap_to_png_bytes(pixmap: QtGui.QPixmap) -> bytes:
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


# ---------- Direct snip-and-run mode (for keyboard shortcuts / terminal) ----------

def direct_snip_flow(prompt: str, model: str):
    app = QtWidgets.QApplication(sys.argv)
    activate_mac_app()
    
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
        img_bytes = qpixmap_to_png_bytes(cropped)

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
    overlay.raise_()
    overlay.activateWindow()
    app.exec_()

# ---------- Full GUI window mode ----------

class OcrWindow(QtWidgets.QWidget):
    def __init__(self, default_model="glm-ocr"):
        super().__init__()
        self.setWindowTitle("GLM-OCR Snip Tool")
        self.setMinimumWidth(520)
        self.overlay = None
        self._build_ui(default_model)

    def _build_ui(self, default_model):
        layout = QtWidgets.QVBoxLayout(self)

        # Task selector
        task_row = QtWidgets.QHBoxLayout()
        task_row.addWidget(QtWidgets.QLabel("Task:"))
        self.task_combo = QtWidgets.QComboBox()
        self.task_combo.addItems(["text", "table", "figure", "custom"])
        self.task_combo.currentTextChanged.connect(self._on_task_changed)
        task_row.addWidget(self.task_combo)
        layout.addLayout(task_row)

        # Custom prompt field
        self.custom_prompt_edit = QtWidgets.QLineEdit()
        self.custom_prompt_edit.setPlaceholderText(
            "e.g. Formula Recognition:  (or any prompt the model supports)"
        )
        self.custom_prompt_edit.setEnabled(False)
        layout.addWidget(self.custom_prompt_edit)

        # Model name field
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(QtWidgets.QLabel("Model:"))
        self.model_edit = QtWidgets.QLineEdit(default_model)
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
        layout.addWidget(self.result_edit)

        # Status label
        self.status_label = QtWidgets.QLabel("")
        layout.addWidget(self.status_label)

        self.setLayout(layout)

    def _on_task_changed(self, text):
        self.custom_prompt_edit.setEnabled(text == "custom")

    def _current_prompt(self) -> str:
        task = self.task_combo.currentText()
        if task == "custom":
            return self.custom_prompt_edit.text().strip() or "Text Recognition:"
        return TASK_PROMPTS[task]

    def start_snip(self):
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
        if rect.isNull() or rect.width() < 5 or rect.height() < 5:
            self.status_label.setText("Selection too small or cancelled.")
            return

        screen = QtWidgets.QApplication.primaryScreen()
        full_pixmap = screen.grabWindow(0)
        cropped = full_pixmap.copy(rect)

        self.image_preview.setPixmap(
            cropped.scaled(
                self.image_preview.width(),
                self.image_preview.height(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )

        img_bytes = qpixmap_to_png_bytes(cropped)
        prompt = self._current_prompt()
        model = self.model_edit.text().strip() or "glm-ocr"

        self.status_label.setText(f'Running OCR (prompt: "{prompt}")...')
        QtWidgets.QApplication.processEvents()

        try:
            result_text = run_ocr(img_bytes, prompt, model)
        except Exception as e:
            self.status_label.setText(f"Error: {e}")
            return

        self.result_edit.setPlainText(result_text)
        QtWidgets.QApplication.clipboard().setText(result_text)
        self.status_label.setText("Done. Copied to clipboard.")


# ---------- Entry point ----------

def main():
    parser = argparse.ArgumentParser(description="GLM-OCR screen-snip tool")
    parser.add_argument(
        "--task",
        choices=list(TASK_PROMPTS.keys()),
        default="text",
        help="Which built-in recognition task to run (ignored if --prompt is given).",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help='Custom prompt to send instead of a --task preset, e.g. "Formula Recognition:"',
    )
    parser.add_argument(
        "--model",
        default="glm-ocr",
        help="Ollama model name/tag to use (default: glm-ocr)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the full window UI instead of an immediate snip-and-run.",
    )
    args = parser.parse_args()

    prompt = args.prompt if args.prompt else TASK_PROMPTS[args.task]

    if args.gui:
        app = QtWidgets.QApplication(sys.argv)
        window = OcrWindow(default_model=args.model)
        window.show()
        sys.exit(app.exec_())
    else:
        direct_snip_flow(prompt=prompt, model=args.model)


if __name__ == "__main__":
    main()
