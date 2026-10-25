import ctypes
import ctypes.util
from pathlib import Path


_loaded = False


class _FontSet(ctypes.Structure):
    _fields_ = [("nfont", ctypes.c_int), ("sfont", ctypes.c_int),
                ("fonts", ctypes.POINTER(ctypes.c_void_p))]


def load_fonts():
    global _loaded
    if _loaded:
        return
    library = ctypes.util.find_library("fontconfig")
    if not library:
        raise RuntimeError("Uygulama fontları için Fontconfig bulunamadı.")
    fc = ctypes.CDLL(library)
    signatures = {
        "FcConfigGetCurrent": ([], ctypes.c_void_p),
        "FcConfigAppFontAddFile": ([ctypes.c_void_p, ctypes.c_char_p], ctypes.c_int),
        "FcConfigGetFonts": ([ctypes.c_void_p, ctypes.c_int], ctypes.POINTER(_FontSet)),
        "FcPatternGetString": ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int,
                                ctypes.POINTER(ctypes.c_char_p)], ctypes.c_int),
        "FcPatternDel": ([ctypes.c_void_p, ctypes.c_char_p], ctypes.c_int),
        "FcPatternAddString": ([ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p], ctypes.c_int),
    }
    for name, (args, result) in signatures.items():
        function = getattr(fc, name)
        function.argtypes = args
        function.restype = result
    root = Path(__file__).resolve().parents[2] / "data/fonts"
    families = {str(root / filename).encode(): family for filename, family in (
        ("adaletvpn-text.ttf", b"AdaletVPN Text"),
    )}
    config = fc.FcConfigGetCurrent()
    for path in families:
        if not fc.FcConfigAppFontAddFile(config, path):
            raise RuntimeError("Uygulama fontu yüklenemedi: " + Path(path.decode()).name)
    font_set = fc.FcConfigGetFonts(config, 1).contents
    for index in range(font_set.nfont):
        pattern = font_set.fonts[index]
        filename = ctypes.c_char_p()
        if fc.FcPatternGetString(pattern, b"file", 0, ctypes.byref(filename)) != 0:
            continue
        family = families.get(filename.value)
        if family is None:
            continue
        fc.FcPatternDel(pattern, b"family")
        fc.FcPatternAddString(pattern, b"family", family)
    import gi
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo
    PangoCairo.FontMap.get_default().changed()
    _loaded = True
