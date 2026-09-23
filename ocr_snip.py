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
    ollama pull glm-ocr   (The following is probably not necessary: 'and ensure `ollama serve` is running')
    ollama create glm-ocr-no-repeat -f ./Modelfile

Made with Claude Sonnet 5.
"""

import sys
import argparse
import subprocess
from PyQt5 import QtWidgets, QtGui, QtCore
import ollama

DEFAULT_MODEL="glm-ocr-no-repeat"

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

import subprocess
import tempfile
import os


def grab_region_screencapture(x: int, y: int, w: int, h: int) -> bytes:
    """
    Capture an exact screen region using macOS's native `screencapture` tool.
    This correctly handles Retina/scaled-resolution displays, unlike Qt's
    grabWindow() on some macOS/Qt version combinations.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        region = f"{x},{y},{w},{h}"
        subprocess.run(
            ["screencapture", "-x", "-R", region, tmp_path],
            check=True,
        )
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

TASK_PROMPTS = {
    "text": "Text Recognition:",#"\"Extract all text from this image, exactly as it appears, one field per line.\"",#
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
            # self.screen_pixmap is in physical pixels (dpr may be > 1 on
            # Retina displays), while `rect` is in logical points. Scale the
            # source rect into physical pixel space so the un-dimmed preview
            # shows the real region instead of a magnified top-left corner.
            dpr = self.screen_pixmap.devicePixelRatio()
            source_rect = QtCore.QRectF(
                rect.x() * dpr,
                rect.y() * dpr,
                rect.width() * dpr,
                rect.height() * dpr,
            )
            painter.drawPixmap(QtCore.QRectF(rect), self.screen_pixmap, source_rect)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 0, 0), 2))
            painter.drawRect(rect)


def logical_rect_to_physical(rect: QtCore.QRect, pixmap: QtGui.QPixmap) -> QtCore.QRect:
    """Convert a logical (point-based) rect into the pixmap's physical pixel space."""
    dpr = pixmap.devicePixelRatio()
    return QtCore.QRect(
        int(rect.x() * dpr),
        int(rect.y() * dpr),
        int(rect.width() * dpr),
        int(rect.height() * dpr),
    )

# ---------- OCR call ----------

def _normalize_line(line: str) -> str:
    """Loose normalization so near-identical repeats compare equal."""
    return "".join(line.split()).strip("`").lower()


def _clean_ocr_text(raw: str) -> str:
    """
    glm-ocr transcribes the region correctly once, then falls into a loop
    re-emitting the same content (interleaved with stray ``` / ```markdown
    fences and blank lines) until it exhausts the context window. The first
    occurrence is the real result; everything after is loop noise.

    Walk the lines and stop at the point where the model starts repeating
    content it has already produced. Also drop markdown code-fence lines,
    which glm-ocr injects but are never part of a screenshot's text.
    """
    kept = []
    seen = set()
    for line in raw.splitlines():
        stripped = line.strip()
        # Skip code-fence lines the model injects (```, ```markdown, etc.)
        if stripped.startswith("```"):
            continue
        norm = _normalize_line(line)
        if not norm:
            # Preserve blank lines only if we haven't started repeating.
            kept.append(line)
            continue
        if norm in seen:
            # We've hit a line identical to one already transcribed: the
            # repetition loop has begun, so stop here.
            break
        seen.add(norm)
        kept.append(line)
    return "\n".join(kept).strip()


def run_ocr(img_bytes: bytes, prompt: str, model: str) -> str:
    # glm-ocr can fall into a repetition loop where it re-emits the same
    # content until it exhausts the context window, which makes a single OCR
    # call take a very long time and appear to hang. We defend against this
    # two ways:
    #   1. Stream the response and abort generation as soon as a line repeats
    #      one already produced (early exit -> fast).
    #   2. num_predict as a hard backstop so it can never run unbounded.
    # A final _clean_ocr_text pass strips fences and any residual repeats.
    seen = set()
    parts = []
    buffer = ""

    stream = ollama.chat(
        model=model,
        messages=[{
            "role": "user",
            "content": prompt,
            "images": [img_bytes],
        }],
        stream=True,
        options={
            "temperature": 0.1,
            "top_p": 0.00001,
            "top_k": 1,
            "repeat_penalty": 1.3,
            "repeat_last_n": 256,
            # Hard ceiling on generated tokens: a backstop in case the
            # repetition detector below somehow misses.
            "num_predict": 1024,
        },
    )

    repeating = False
    for chunk in stream:
        buffer += chunk["message"]["content"]
        # Process complete lines as they arrive.
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            norm = _normalize_line(line)
            if norm and norm in seen:
                repeating = True
                break
            if norm:
                seen.add(norm)
            parts.append(line)
        if repeating:
            # Close the stream early; the loop has started.
            try:
                stream.close()
            except Exception:
                pass
            break

    if not repeating and buffer:
        parts.append(buffer)

    return _clean_ocr_text("\n".join(parts))


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
    
        try:
            img_bytes = grab_region_screencapture(
                rect.x(), rect.y(), rect.width(), rect.height()
            )
        except Exception:
            import traceback
            traceback.print_exc()
            app.quit()
            return
    
        try:
            result_text = run_ocr(img_bytes, prompt, model)
        except Exception as e:
            import traceback
            traceback.print_exc()
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
    def __init__(self, default_model=DEFAULT_MODEL):
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
    
        try:
            img_bytes = grab_region_screencapture(
                rect.x(), rect.y(), rect.width(), rect.height()
            )
        except Exception as e:
            self.status_label.setText(f"Capture error: {e}")
            return
    
        pixmap = QtGui.QPixmap()
        pixmap.loadFromData(img_bytes)
        self.image_preview.setPixmap(
            pixmap.scaled(
                self.image_preview.width(),
                self.image_preview.height(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )
        
        prompt = self._current_prompt()
        model = self.model_edit.text().strip() or DEFAULT_MODEL
    
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
        default=DEFAULT_MODEL,
        help="Ollama model name/tag to use (default: {DEFAULT_MODEL})",
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
