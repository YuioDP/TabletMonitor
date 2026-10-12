# -*- coding: utf-8 -*-
"""
Tablet Monitor (lado PC)
Captura una pantalla de Windows y la envia a la tablet. Vive en la bandeja del
sistema (iconos ocultos), sin ventana de consola. Doble clic para abrirlo.
"""
import ctypes
from ctypes import wintypes
import io
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time

try:
    import mss
    import pystray
    from PIL import Image, ImageChops, ImageDraw
except ImportError as _e:
    ctypes.windll.user32.MessageBoxW(
        0, "Faltan librerias. Ejecuta instalar.bat y vuelve a abrir.\n\n" + str(_e),
        "Tablet Monitor", 0x10)
    sys.exit(1)

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "log.txt")
CREATE_NO_WINDOW = 0x08000000

DEFAULTS = {
    "mode": "usb",              # "usb" o "wifi"
    "wifi_ip": "192.168.1.50",  # IP que muestra la tablet (solo modo wifi)
    "port": 5000,
    "monitor": "auto",          # "auto" = ultimo monitor (el virtual); o un numero (ver_monitores.bat)
    "fps": 25,
    "quality": 70,              # calidad JPEG 10-95
    "fit": "letterbox",         # "letterbox" = barras negras sin deformar; "stretch" = rellena y deforma
    "cursor": "real",           # "real" = tu puntero actual; "simple" = flecha basica; "none" = sin puntero
    "adb_path": "",             # vacio = busca platform-tools\adb.exe junto al programa, o adb del PATH
}
CFG = dict(DEFAULTS)

# ---------------------------------------------------------------- utilidades

_log_lock = threading.Lock()


def log(msg):
    try:
        with _log_lock, open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


def trim_log():
    try:
        if os.path.getsize(LOG_PATH) > 500_000:
            os.remove(LOG_PATH)
    except Exception:
        pass


def save_config():
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(CFG, f, indent=2)


def load_config():
    cfg = dict(DEFAULTS)
    missing = False
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        missing = True
    except Exception as e:
        log("config.json invalido, uso valores por defecto: %s" % e)
    CFG.clear()
    CFG.update(cfg)
    if missing:
        save_config()


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("La app de la tablet no esta abierta o cerro la conexion")
        buf += chunk
    return buf


# ---------------------------------------------------------------- cursor (API de Windows)

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HANDLE),
                ("hbmColor", wintypes.HANDLE)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", wintypes.LPVOID)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32 = None
gdi32 = None
if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wintypes.BOOL
    user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.DrawIconEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_int, wintypes.HANDLE,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HANDLE,
                                  wintypes.UINT]
    user32.DrawIconEx.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HANDLE]
    user32.GetDC.restype = wintypes.HANDLE
    user32.ReleaseDC.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HANDLE]
    gdi32.CreateCompatibleDC.restype = wintypes.HANDLE
    gdi32.CreateDIBSection.argtypes = [wintypes.HANDLE, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                       wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HANDLE
    gdi32.SelectObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteDC.argtypes = [wintypes.HANDLE]
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]


def cursor_pos():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def cursor_state():
    """(x, y, hcursor) o None si el puntero esta oculto."""
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if user32.GetCursorInfo(ctypes.byref(ci)):
        if not (ci.flags & 1):  # CURSOR_SHOWING
            return None
        return ci.ptScreenPos.x, ci.ptScreenPos.y, ci.hCursor
    x, y = cursor_pos()
    return x, y, None


def _delete(h):
    if h:
        gdi32.DeleteObject(h)


def _render_cursor(hcursor, w, h, white):
    """Dibuja el puntero sobre fondo negro o blanco y devuelve una imagen RGB."""
    hdc_screen = user32.GetDC(None)
    hdc = gdi32.CreateCompatibleDC(hdc_screen)
    try:
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # de arriba hacia abajo
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bits = ctypes.c_void_p()
        hbm = gdi32.CreateDIBSection(hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not hbm or not bits.value:
            raise OSError("CreateDIBSection fallo")
        try:
            old = gdi32.SelectObject(hdc, hbm)
            size = w * h * 4
            ctypes.memset(bits.value, 0xFF if white else 0x00, size)
            user32.DrawIconEx(hdc, 0, 0, hcursor, w, h, 0, None, 3)  # DI_NORMAL
            gdi32.GdiFlush()
            raw = ctypes.string_at(bits.value, size)
            gdi32.SelectObject(hdc, old)
        finally:
            gdi32.DeleteObject(hbm)
    finally:
        gdi32.DeleteDC(hdc)
        user32.ReleaseDC(None, hdc_screen)
    return Image.frombytes("RGB", (w, h), raw, "raw", "BGRX")


_sprites = {}


def get_sprite(hcursor):
    """(sobre_negro, transparencia_invertida, hotspot_x, hotspot_y) o None."""
    if hcursor in _sprites:
        return _sprites[hcursor]
    sprite = None
    try:
        ii = ICONINFO()
        if user32.GetIconInfo(hcursor, ctypes.byref(ii)):
            try:
                bm = BITMAP()
                src = ii.hbmColor or ii.hbmMask
                gdi32.GetObjectW(src, ctypes.sizeof(BITMAP), ctypes.byref(bm))
                w = bm.bmWidth
                h = bm.bmHeight if ii.hbmColor else bm.bmHeight // 2
            finally:
                _delete(ii.hbmColor)
                _delete(ii.hbmMask)
            if 0 < w <= 256 and 0 < h <= 256:
                black = _render_cursor(hcursor, w, h, False)
                white = _render_cursor(hcursor, w, h, True)
                diff = ImageChops.subtract(white, black)  # = 255 * (1 - alfa)
                sprite = (black, diff, int(ii.xHotspot), int(ii.yHotspot))
    except Exception as e:
        log("cursor: no pude leer el puntero: %r" % (e,))
    if len(_sprites) > 64:
        _sprites.clear()
    _sprites[hcursor] = sprite
    return sprite


def paste_cursor(img, sprite, px, py):
    black, diff, hx, hy = sprite
    x0, y0 = px - hx, py - hy
    w, h = black.size
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(img.width, x0 + w), min(img.height, y0 + h)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    box = (cx0 - x0, cy0 - y0, cx1 - x0, cy1 - y0)
    region = img.crop((cx0, cy0, cx1, cy1))
    out = ImageChops.add(ImageChops.multiply(region, diff.crop(box)), black.crop(box))
    img.paste(out, (cx0, cy0))


ARROW = [(0, 0), (0, 16), (4, 12), (7, 19), (10, 18), (7, 11), (12, 11)]


def draw_cursor(img, mode, st, mon, fw, fh, ox, oy):
    if mode == "none" or st is None:
        return
    cx, cy, hc = st
    x = ox + int((cx - mon["left"]) * fw / mon["width"])
    y = oy + int((cy - mon["top"]) * fh / mon["height"])
    try:
        sprite = get_sprite(hc) if (mode == "real" and hc) else None
        if sprite is not None:
            paste_cursor(img, sprite, x, y)
            return
    except Exception as e:
        log("cursor: error al dibujar: %r" % (e,))
    pts = [(x + px, y + py) for px, py in ARROW]
    ImageDraw.Draw(img).polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))


def inside(mon, cx, cy):
    return (mon["left"] <= cx < mon["left"] + mon["width"]
            and mon["top"] <= cy < mon["top"] + mon["height"])


def fit_size(sw, sh, tw, th, mode):
    """Tamano con el que se dibuja la captura dentro de la tablet y su desplazamiento."""
    if mode == "stretch":
        return tw, th, 0, 0
    s = min(tw / sw, th / sh)
    w = max(1, int(round(sw * s)))
    h = max(1, int(round(sh * s)))
    return w, h, (tw - w) // 2, (th - h) // 2


# ---------------------------------------------------------------- ADB (USB)

def adb_exe():
    custom = (CFG.get("adb_path") or "").strip()
    if custom and os.path.isfile(custom):
        return custom
    local = os.path.join(BASE, "platform-tools", "adb.exe")
    if os.path.isfile(local):
        return local
    return "adb"


def adb_run(args, capture=True, timeout=15):
    cmd = [adb_exe()] + list(args)
    kw = dict(stdin=subprocess.DEVNULL, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    if capture:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              encoding="utf-8", errors="replace", **kw)
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def ensure_usb(port):
    try:
        adb_run(["start-server"], capture=False, timeout=25)
        r = adb_run(["devices"])
    except FileNotFoundError:
        raise RuntimeError("No encuentro adb.exe (copia platform-tools junto al programa)")
    states = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] in ("device", "unauthorized", "offline"):
            states.append(parts)
    ready = [p for p in states if p[1] == "device"]
    if not ready:
        if any(p[1] == "unauthorized" for p in states):
            raise RuntimeError("Acepta 'Permitir depuracion USB' en la tablet")
        raise RuntimeError("Tablet no detectada por USB")
    adb_run(["-s", ready[0][0], "forward", "tcp:%d" % port, "tcp:%d" % port])


# ---------------------------------------------------------------- transmision

def pick_monitor(sct):
    mons = sct.monitors  # [0] = todos juntos, [1..] = cada monitor
    sel = str(CFG.get("monitor", "auto")).strip().lower()
    if sel == "auto":
        if len(mons) > 2:
            i = len(mons) - 1
            return mons[i], "monitor %d" % i
        return mons[1], "monitor principal (espejo)"
    try:
        i = int(sel)
    except ValueError:
        i = 1
    i = max(1, min(i, len(mons) - 1))
    return mons[i], "monitor %d" % i


class Streamer:
    def __init__(self, on_state):
        self.on_state = on_state
        self.enabled = False
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self.enabled:
            return
        self.enabled = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), daemon=True)
        self._thread.start()

    def stop(self):
        self.enabled = False
        self._stop.set()
        self.on_state("off", "Detenido")

    def _run(self, stop):
        def rep(state, text):
            if not stop.is_set():
                self.on_state(state, text)

        while not stop.is_set():
            try:
                self._session(stop, rep)
            except Exception as e:
                log("sesion terminada: %r" % (e,))
                rep("wait", "%s" % e)
            stop.wait(2)

    def _session(self, stop, rep):
        port = int(CFG["port"])
        if CFG["mode"] == "usb":
            rep("wait", "Buscando tablet por USB...")
            ensure_usb(port)
            host = "127.0.0.1"
        else:
            host = str(CFG["wifi_ip"]).strip()
        rep("wait", "Conectando a %s:%d..." % (host, port))
        sock = socket.create_connection((host, port), timeout=5)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            tw, th = struct.unpack(">II", recv_exact(sock, 8))
            if tw < 16 or th < 16:
                raise RuntimeError("La tablet envio un tamano invalido")
            sock.settimeout(10)
            self._stream(sock, tw, th, stop, rep)
        finally:
            sock.close()

    def _stream(self, sock, tw, th, stop, rep):
        fps = max(1, min(60, int(CFG["fps"])))
        quality = max(10, min(95, int(CFG["quality"])))
        fit = str(CFG.get("fit", "letterbox")).strip().lower()
        cmode = str(CFG.get("cursor", "real")).strip().lower()
        interval = 1.0 / fps
        with mss.mss() as sct:
            mon, desc = pick_monitor(sct)
            rep("live", "Transmitiendo %s -> tablet %dx%d" % (desc, tw, th))
            log("transmitiendo %s %s -> %dx%d (fit=%s, cursor=%s)" % (desc, mon, tw, th, fit, cmode))
            last_raw = None
            last_cur = None
            last_frame = None
            last_send = 0.0
            while not stop.is_set():
                t0 = time.perf_counter()
                shot = sct.grab(mon)
                raw = shot.bgra

                st = cursor_state() if cmode != "none" else None
                if st is not None and not inside(mon, st[0], st[1]):
                    st = None
                cur = (st[0], st[1], st[2]) if st is not None else None

                if raw == last_raw and cur == last_cur and last_frame is not None:
                    # nada cambio: reenviar el ultimo cuadro cada ~1 s (detecta desconexiones)
                    if time.perf_counter() - last_send >= 1.0:
                        sock.sendall(last_frame)
                        last_send = time.perf_counter()
                    stop.wait(0.03)
                    continue
                last_raw, last_cur = raw, cur

                sw, sh = shot.width, shot.height
                fw, fh, ox, oy = fit_size(sw, sh, tw, th, fit)
                img = Image.frombytes("RGB", (sw, sh), raw, "raw", "BGRX")
                if (fw, fh) != (sw, sh):
                    f = min(sw // fw, sh // fh)
                    if f >= 2:
                        img = img.reduce(f)  # reduccion rapida antes del ajuste fino
                    img = img.resize((fw, fh), Image.BILINEAR)
                if (fw, fh) != (tw, th):
                    canvas = Image.new("RGB", (tw, th), (0, 0, 0))
                    canvas.paste(img, (ox, oy))
                    img = canvas
                draw_cursor(img, cmode, st, mon, fw, fh, ox, oy)

                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=quality)
                data = buf.getvalue()
                last_frame = struct.pack(">I", len(data)) + data
                sock.sendall(last_frame)
                last_send = time.perf_counter()

                dt = time.perf_counter() - t0
                if dt < interval:
                    stop.wait(interval - dt)


# ---------------------------------------------------------------- bandeja

def make_icon(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 10, 58, 46), radius=5, fill=(40, 40, 40, 255),
                        outline=(230, 230, 230, 255), width=4)
    d.rectangle((26, 46, 38, 54), fill=(230, 230, 230, 255))
    d.rectangle((18, 54, 46, 58), fill=(230, 230, 230, 255))
    d.ellipse((38, 16, 52, 30), fill=color)
    return img


_mutex = None


def single_instance():
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\TabletMonitorPC")
    return ctypes.windll.kernel32.GetLastError() != 183  # 183 = ya existe


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # escalado por monitor
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    if not single_instance():
        ctypes.windll.user32.MessageBoxW(
            0, "Tablet Monitor ya esta abierto. Buscalo en los iconos ocultos de la barra de tareas.",
            "Tablet Monitor", 0x40)
        return
    trim_log()
    load_config()

    icons = {
        "off": make_icon((150, 150, 150, 255)),
        "wait": make_icon((240, 180, 0, 255)),
        "live": make_icon((40, 200, 80, 255)),
    }
    state = {"text": "Iniciando..."}
    icon = pystray.Icon("TabletMonitor", icons["off"], "Tablet Monitor")

    def on_state(st, text):
        state["text"] = text
        log("[%s] %s" % (st, text))
        try:
            icon.icon = icons[st]
            icon.title = ("Tablet Monitor - " + text)[:120]
            icon.update_menu()
        except Exception:
            pass

    streamer = Streamer(on_state)

    def restart():
        if streamer.enabled:
            streamer.stop()
            streamer.start()

    def toggle(_icon, _item):
        if streamer.enabled:
            streamer.stop()
        else:
            streamer.start()

    def set_mode(mode):
        def handler(_icon, _item):
            CFG["mode"] = mode
            save_config()
            restart()
        return handler

    def open_cfg(_icon, _item):
        os.startfile(CONFIG_PATH)

    def reload_cfg(_icon, _item):
        load_config()
        restart()

    def open_log(_icon, _item):
        if os.path.exists(LOG_PATH):
            os.startfile(LOG_PATH)

    def quit_(_icon, _item):
        streamer.stop()
        _icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem(lambda item: "Estado: " + state["text"], None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Transmitir a la tablet", toggle, checked=lambda item: streamer.enabled),
        pystray.MenuItem("Conexion", pystray.Menu(
            pystray.MenuItem("USB (cable)", set_mode("usb"),
                             checked=lambda item: CFG["mode"] == "usb", radio=True),
            pystray.MenuItem("WiFi", set_mode("wifi"),
                             checked=lambda item: CFG["mode"] == "wifi", radio=True),
        )),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Abrir configuracion", open_cfg),
        pystray.MenuItem("Recargar configuracion", reload_cfg),
        pystray.MenuItem("Ver registro", open_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Salir", quit_),
    )

    def setup(_icon):
        _icon.visible = True
        streamer.start()

    icon.run(setup)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("ERROR FATAL: %r" % (exc,))
        ctypes.windll.user32.MessageBoxW(0, "Error: %s\n\nMira log.txt" % exc, "Tablet Monitor", 0x10)
