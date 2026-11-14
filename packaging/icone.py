"""Icône de Patrimoine.exe (D-126), dessinée ici plutôt que rangée en binaire dans le dépôt.

Un carré arrondi à la couleur d'accent de l'app (--accent de web/styles.css) et trois barres
montantes. Le fichier .ico contient une image PNG par taille affichée par Windows (barre des
tâches, Explorateur, titre de la fenêtre). Écrit avec la bibliothèque standard seule :
`uv run python packaging/icone.py SORTIE.ico`.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

ACCENT = (0x2A, 0x78, 0xD6)
WHITE = (0xFF, 0xFF, 0xFF)
SIZES = (16, 24, 32, 48, 64, 128, 256)
SAMPLES = 4  # sous-échantillons par côté de pixel : bords lissés
# Dessin en millièmes du côté : carré arrondi, puis barres (gauche, haut, droite, bas).
MARGIN, RADIUS = 60, 220
BARS = ((220, 560, 360, 760), (430, 420, 570, 760), (640, 260, 780, 760))


def _inside_rounded(x: int, y: int) -> bool:
    low, high = MARGIN + RADIUS, 1000 - MARGIN - RADIUS
    if not (MARGIN <= x <= 1000 - MARGIN and MARGIN <= y <= 1000 - MARGIN):
        return False
    cx, cy = min(max(x, low), high), min(max(y, low), high)
    return (x - cx) ** 2 + (y - cy) ** 2 <= RADIUS**2


def _inside_bar(x: int, y: int) -> bool:
    return any(left <= x <= right and top <= y <= bottom for left, top, right, bottom in BARS)


def pixels(size: int) -> bytes:
    """Image RGBA, ligne par ligne, chaque ligne précédée de l'octet de filtre PNG (0)."""
    rows = bytearray()
    step = size * SAMPLES
    for py in range(size):
        rows.append(0)
        for px in range(size):
            shape = bar = 0
            for sy in range(SAMPLES):
                y = ((py * SAMPLES + sy) * 2 + 1) * 1000 // (2 * step)
                for sx in range(SAMPLES):
                    x = ((px * SAMPLES + sx) * 2 + 1) * 1000 // (2 * step)
                    if _inside_rounded(x, y):
                        shape += 1
                        bar += _inside_bar(x, y)
            total = SAMPLES * SAMPLES
            if shape == 0:
                rows += b"\0\0\0\0"
                continue
            # Couleur moyenne des sous-points de la forme, opacité = part couverte.
            mix = zip(ACCENT, WHITE, strict=True)
            rgb = (((shape - bar) * a + bar * w) // shape for a, w in mix)
            rows += bytes((*rgb, shape * 255 // total))
    return bytes(rows)


def png(size: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8 bits, RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels(size), 9))
        + chunk(b"IEND", b"")
    )


def ico(sizes: tuple[int, ...] = SIZES) -> bytes:
    """Fichier .ico : en-tête, une entrée par taille, puis les images PNG à la suite."""
    images = [png(size) for size in sizes]
    offset = 6 + 16 * len(images)
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, image in zip(sizes, images, strict=True):
        side = size % 256  # 0 veut dire 256 dans le format .ico
        out += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    for image in images:
        out += image
    return bytes(out)


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "patrimoine.ico")
    target.write_bytes(ico())
    print(f"Icône écrite : {target}")
