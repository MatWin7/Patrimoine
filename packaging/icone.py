"""Icône de Patrimoine.exe (D-126) et de Patrimoine.app (D-137), dessinée ici plutôt que rangée
en binaire dans le dépôt.

Un carré arrondi à la couleur d'accent de l'app (--accent de web/styles.css) et trois barres
montantes. Le fichier .ico contient une image PNG par taille affichée par Windows (barre des
tâches, Explorateur, titre de la fenêtre) ; le fichier .icns, celles du Dock et du Finder de
macOS, avec la marge que macOS laisse autour de chaque icône. Écrit avec la bibliothèque
standard seule : `uv run python packaging/icone.py SORTIE.ico` (ou SORTIE.icns).
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
# macOS : carré de 824 px sur 1024 (gabarit des icônes d'Apple), coins un peu moins ronds.
MAC_MARGIN, MAC_RADIUS = 100, 180
# Types .icns d'images PNG : (code, côté en pixels). Les codes en double couvrent les écrans
# Retina (16 px à 2x = 32 px...).
ICNS_TYPES = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic11", 32),
    (b"ic12", 64),
    (b"ic07", 128),
    (b"ic13", 256),
    (b"ic08", 256),
    (b"ic14", 512),
    (b"ic09", 512),
    (b"ic10", 1024),
)
BARS = ((220, 560, 360, 760), (430, 420, 570, 760), (640, 260, 780, 760))


def _inside_rounded(x: int, y: int, margin: int = MARGIN, radius: int = RADIUS) -> bool:
    low, high = margin + radius, 1000 - margin - radius
    if not (margin <= x <= 1000 - margin and margin <= y <= 1000 - margin):
        return False
    cx, cy = min(max(x, low), high), min(max(y, low), high)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def _inside_bar(x: int, y: int) -> bool:
    return any(left <= x <= right and top <= y <= bottom for left, top, right, bottom in BARS)


def pixels(size: int, mac: bool = False) -> bytes:
    """Image RGBA, ligne par ligne, chaque ligne précédée de l'octet de filtre PNG (0)."""
    margin, radius = (MAC_MARGIN, MAC_RADIUS) if mac else (MARGIN, RADIUS)
    # Au-delà de 256 px, deux sous-points par côté suffisent à lisser et divisent le temps par 4.
    samples = SAMPLES if size <= 256 else 2
    rows = bytearray()
    step = size * samples
    for py in range(size):
        rows.append(0)
        for px in range(size):
            shape = bar = 0
            for sy in range(samples):
                y = ((py * samples + sy) * 2 + 1) * 1000 // (2 * step)
                for sx in range(samples):
                    x = ((px * samples + sx) * 2 + 1) * 1000 // (2 * step)
                    if _inside_rounded(x, y, margin, radius):
                        shape += 1
                        bar += _inside_bar(x, y)
            total = samples * samples
            if shape == 0:
                rows += b"\0\0\0\0"
                continue
            # Couleur moyenne des sous-points de la forme, opacité = part couverte.
            mix = zip(ACCENT, WHITE, strict=True)
            rgb = (((shape - bar) * a + bar * w) // shape for a, w in mix)
            rows += bytes((*rgb, shape * 255 // total))
    return bytes(rows)


def png(size: int, mac: bool = False) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8 bits, RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels(size, mac), 9))
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


def icns(types: tuple[tuple[bytes, int], ...] = ICNS_TYPES) -> bytes:
    """Fichier .icns (macOS) : « icns » et la taille totale, puis chaque image PNG précédée de
    son code et de sa taille (gros-boutiste). Une taille dessinée une seule fois."""
    images: dict[int, bytes] = {}
    body = bytearray()
    for code, size in types:
        image = images.setdefault(size, png(size, mac=True))
        body += code + struct.pack(">I", 8 + len(image)) + image
    return b"icns" + struct.pack(">I", 8 + len(body)) + bytes(body)


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "patrimoine.ico")
    target.write_bytes(icns() if target.suffix == ".icns" else ico())
    print(f"Icône écrite : {target}")
