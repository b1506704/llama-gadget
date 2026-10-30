"""Image attachments for the vision profile: downscale + embed as data URLs."""
import base64
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QColor, QImage, QPainter

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
MAX_SIDE = 1568


def is_image(path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def encode_image(img: QImage, name: str, path: str | None = None) -> dict:
    if img.isNull():
        raise ValueError("not a readable image")
    if max(img.width(), img.height()) > MAX_SIDE:
        img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    flat = QImage(img.size(), QImage.Format.Format_RGB32)  # JPEG has no alpha: flatten on white
    flat.fill(QColor("white"))
    p = QPainter(flat)
    p.drawImage(0, 0, img)
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    flat.save(buf, "JPEG", 88)
    data = "data:image/jpeg;base64," + base64.b64encode(bytes(ba)).decode()
    att = dict(kind="image", name=name, chars=0, truncated=False, data=data, w=flat.width(), h=flat.height())
    if path:
        att["path"] = str(path)
    return att


def load_image(path) -> dict:
    return encode_image(QImage(str(path)), Path(path).name, path=str(path))


def decode_attachment_image(attachment) -> QImage:
    """Decode the stored image, independent of the original attachment path."""
    try:
        prefix, encoded = attachment['data'].split(',', 1)
        if not prefix.startswith('data:image/') or not prefix.endswith(';base64'):
            raise ValueError('Unsupported image data URL')
        raw = base64.b64decode(encoded, validate=True)
        image = QImage.fromData(raw)
        if image.isNull():
            raise ValueError('Image attachment is not readable')
        return image
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Image preview is unavailable: invalid stored image data') from exc
