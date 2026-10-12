# -*- coding: utf-8 -*-
"""
Tablet Monitor (lado PC)
Captura una pantalla de Windows y la envia a la tablet. Vive en la bandeja del
sistema (iconos ocultos), sin ventana de consola. Doble clic para abrirlo.
"""
import ctypes
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
    from PIL import Image, ImageDraw
except ImportError as _e:
    ctypes.windll.user32.MessageBoxW(
        0, "Faltan librerias. Ejecuta instalar.bat y vuelve a abrir.\n\n" + str(_e),
        "Tablet Monitor", 0x10)
    sys.exit(1)

BASE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE, "config.json")
LOG_PATH = os.path.join(BASE, "log.txt")
CREATE_NO_WINDOW = 0x08000000

DEFAULTS = {
    "mode": "usb",            # "usb" o "wifi"
    "wifi_ip": "192.168.1.50",  # IP que muestra la tablet (solo modo wifi)
    "port": 5000,
    "monitor": "auto",        # "auto" = ultimo monitor (el virtual); o un numero (ver_monitores.bat)
    "fps": 25,
    "quality": 70,            # calidad JPEG 10-95
    "adb_path": "",           # vacio = busca platform-tools\adb.exe junto al script, o adb del PATH
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


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor_pos():
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


ARROW = [(0, 0), (0, 16), (4, 12), (7, 19), (10, 18), (7, 11), (12, 11)]


def draw_cursor(img, cx, cy, mon, tw, th):
    x = int((cx - mon["left"]) * tw / mon["width"])
    y = int((cy - mon["top"]) * th / mon["height"])
    pts = [(x + px, y + py) for px, py in ARROW]
    ImageDraw.Draw(img).polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))


def inside(mon, cx, cy):
    return (mon["left"] <= cx < mon["left"] + mon["width"]
            and mon["top"] <= cy < mon["top"] + mon["height"])


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("La app de la tablet no esta abierta o cerro la conexion")
        buf += chunk
    return buf


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
        raise RuntimeError("No encuentro adb.exe (copia platform-tools junto al script)")
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
        interval = 1.0 / fps
        with mss.mss() as sct:
            mon, desc = pick_monitor(sct)
            rep("live", "Transmitiendo %s -> tablet %dx%d" % (desc, tw, th))
            log("transmitiendo %s %s -> %dx%d" % (desc, mon, tw, th))
            last_raw = None
            last_cur = None
            last_frame = None
            last_send = 0.0
            while not stop.is_set():
                t0 = time.perf_counter()
                shot = sct.grab(mon)
                raw = shot.bgra
                cx, cy = cursor_pos()
                cur = (cx, cy) if inside(mon, cx, cy) else None

                if raw == last_raw and cur == last_cur and last_frame is not None:
                    # nada cambio: reenviar el ultimo cuadro cada ~1 s (detecta desconexiones)
                    if time.perf_counter() - last_send >= 1.0:
                        sock.sendall(last_frame)
                        last_send = time.perf_counter()
                    stop.wait(0.03)
                    continue

                last_raw, last_cur = raw, cur
                img = Image.frombytes("RGB", shot.size, raw, "raw", "BGRX")
                if shot.size != (tw, th):
                    img = img.resize((tw, th), Image.BILINEAR)
                if cur is not None:
                    draw_cursor(img, cx, cy, mon, tw, th)
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
