"""Generate assets/icon.ico (run once; requires PySide6 + Pillow)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402

from vidshrink.theme import draw_logo  # noqa: E402

app = QGuiApplication([])
img = QImage(512, 512, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHints(QPainter.Antialiasing)
draw_logo(p, QRectF(32, 32, 448, 448), 0.0)
p.end()
(ROOT / "assets").mkdir(exist_ok=True)
img.save(str(ROOT / "assets" / "logo.png"))
Image.open(ROOT / "assets" / "logo.png").save(
    ROOT / "assets" / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("assets written")
