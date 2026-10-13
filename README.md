# Tablet Monitor

Turn an old Android tablet (Android 4.2+) into a second monitor for a Windows PC, over a USB cable or over WiFi.

It was built for very weak hardware: a generic 7" tablet with 512 MB of RAM and an 800x480 screen. The tablet app is a tiny viewer with no dependencies, and the PC program lives in the system tray with no console window.

> **Heads up:** a true *extended* desktop needs a **virtual display driver** on the PC. It is **not included** in this project and is downloaded separately (see [Step 1](#step-1--install-the-virtual-display-driver-separate-download)). Without it, the program still works, but only as a *mirror* of your main monitor.

---

## How it works

```
 Windows PC                                              Android tablet
┌──────────────────────────────┐                      ┌─────────────────────┐
│ Virtual monitor (driver)     │                      │  Tablet Monitor app │
│        │ screen capture      │   JPEG frames        │  (TCP server :5000) │
│        ▼                     │ ───────────────────► │  decodes + draws    │
│ TabletMonitor.exe (tray icon)│   USB tunnel (adb)   │  full screen        │
│  resize → JPEG → TCP client  │   or WiFi            │                     │
└──────────────────────────────┘                      └─────────────────────┘
```

1. A virtual display driver makes Windows believe a second monitor is plugged in, so you can drag windows onto it ("Extend these displays").
2. `TabletMonitor.exe` captures that monitor, shrinks it to the tablet's resolution, compresses each frame as JPEG and sends it over a TCP socket.
3. The tablet app listens on port `5000`. When the PC connects, it reports its screen size and then draws every frame it receives. If the tablet falls behind, it drops old frames instead of building up delay.
4. **USB mode:** the PC uses `adb forward` to create a tunnel through the cable (this is why `adb.exe` is bundled; it is *not* a driver and installs nothing). **WiFi mode:** the PC connects directly to the tablet's IP. WiFi needs no driver and no firewall changes on the PC, because the PC is the one connecting out.

Extra details:

- The real mouse pointer (including custom cursors) is drawn on the tablet image.
- If the virtual monitor has a different aspect ratio than the tablet, the image is letterboxed (black bars) instead of being stretched.
- If nothing changed on screen, no new frames are encoded; the last one is just re-sent about once per second to detect disconnections.
- The program only sends video. There is no audio and no touch input.

---

## What to download

Download these two files (from the *Releases* / downloads section of this repository):

| File | What it is | Where it goes |
|---|---|---|
| `app-debug.apk` | The tablet app | Your Android tablet |
| `TabletMonitor-PC.zip` | `TabletMonitor.exe` + `platform-tools` (Google's `adb`, used for the USB tunnel) | Your Windows PC, extracted to a permanent folder such as `C:\TabletMonitor` |

And this one comes **from a different project** (not included here):

| File | What it is | Where to get it |
|---|---|---|
| `VDD.Control.<version>.zip` | Virtual Display Driver + control app | The **Releases** page of [VirtualDrivers/Virtual-Display-Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver) |

This project was tested with release `25.7.23`. Always download the driver from its official repository and check the SHA-256 shown on the release page (PowerShell: `Get-FileHash <file>`).

---

## Requirements

- **Tablet:** Android 4.2 or newer, with "Unknown sources" allowed to install the APK. For USB mode, USB debugging must be enabled.
- **PC:** Windows 10 or 11 (64-bit, Intel/AMD). Nothing else to install: no Python, no runtime.
- **WiFi mode only:** PC and tablet on the same network.

---

## Setup

### Step 1 — Install the virtual display driver (separate download)

1. Create a Windows restore point (recommended before installing any driver).
2. Download `VDD.Control.<version>.zip` from the [driver's releases page](https://github.com/VirtualDrivers/Virtual-Display-Driver/releases), extract it to a permanent folder and run the control app.
3. Use it to install the **display** driver only (the audio driver is not needed).
4. Open **Settings > System > Display**. A new monitor should appear. Set **Extend these displays**.
5. Select the new monitor and choose its resolution (for example **1920x1080**, so your apps keep their normal layout) and set its scale to 100%.

The driver can be turned off, or uninstalled completely, from its control app or from Device Manager > Display adapters.

### Step 2 — Install the tablet app

1. Copy `TabletMonitor.apk` to the tablet.
2. Enable **Settings > Security > Unknown sources**.
3. Open the APK and install it, then launch **Tablet Monitor**. It shows a black screen with "Waiting for the PC", the tablet's IP address and its screen size.

### Step 3 — Prepare USB (skip for WiFi)

1. On the tablet: **Settings > About tablet**, tap **Build number** 7 times to unlock **Developer options**, then enable **USB debugging**.
2. Connect the cable and accept the "Allow USB debugging?" prompt (tick *Always allow*).
3. If Windows does not detect the tablet, a generic USB/ADB driver for your tablet model may be required. This is separate from the display driver.

### Step 4 — Run the PC program

1. Extract `TabletMonitor-PC.zip` to a permanent folder. Keep `TabletMonitor.exe` and the `platform-tools` folder together.
2. Keep the app open on the tablet and double-click `TabletMonitor.exe`.
3. Look for the icon in the hidden tray icons (the `^` arrow on the taskbar):
   - **Gray** = stopped
   - **Yellow** = connecting
   - **Green** = streaming
4. Right-click the icon for the menu: toggle streaming, switch **USB / WiFi**, open the config file, open the log, quit.

Drag a window onto the second monitor and it appears on the tablet.

> Windows SmartScreen or your antivirus may warn about `TabletMonitor.exe`, because it is not code-signed. If you built it yourself from this repository, "More info > Run anyway" is expected.

### WiFi mode

Right-click the tray icon > **Connection > WiFi**, then set `wifi_ip` in `config.json` to the IP shown on the tablet screen.

---

## Configuration

On first run, `config.json` is created next to `TabletMonitor.exe`. Edit it with Notepad, then use **Reload configuration** in the tray menu.

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"usb"` | `"usb"` or `"wifi"` |
| `wifi_ip` | `"192.168.1.50"` | Tablet IP, WiFi mode only |
| `port` | `5000` | TCP port (must match the app; do not change unless you rebuild it) |
| `monitor` | `"auto"` | `"auto"` = the last monitor Windows reports (the virtual one); or a number (1 = main monitor, 2 = next...) |
| `fps` | `25` | Maximum frames per second |
| `quality` | `70` | JPEG quality, 10-95. Lower = less data and faster decoding on the tablet |
| `fit` | `"letterbox"` | `"letterbox"` keeps the aspect ratio (black bars); `"stretch"` fills the screen and distorts |
| `cursor` | `"real"` | `"real"` = your current pointer; `"simple"` = basic arrow; `"none"` = no pointer |
| `adb_path` | `""` | Optional path to `adb.exe`; empty = use the bundled `platform-tools` |

A `log.txt` is also written next to the program; attach it when reporting a problem.

---

## Performance and limitations

- Frames are decoded by the tablet's CPU, so very old tablets will not reach smooth video. If it lags, lower `fps` and `quality`, or use a smaller virtual resolution.
- A 1920x1080 monitor shown on an 800x480 screen is reduced more than twice. It is great for layout and video, but small text can be hard to read; 1280x720 is a good compromise.
- No audio and no touch input.
- Protected (DRM) video may appear black in a screen capture.
- If no virtual monitor exists, the program falls back to mirroring the main monitor.

---

## Troubleshooting

| Status text / symptom | What to do |
|---|---|
| "Tablet not detected over USB" | Check the cable, USB debugging, and that the tablet is not set to charge-only. Try another port. |
| "Allow USB debugging" | Accept the prompt on the tablet. |
| "The tablet app is not open or closed the connection" | Open the Tablet Monitor app on the tablet first. |
| Cannot find `adb.exe` | Keep the `platform-tools` folder next to `TabletMonitor.exe`. |
| It only mirrors my main screen | Install the virtual display driver and set **Extend these displays**, then toggle streaming off and on. |
| Image looks cut off | Check that the virtual monitor's scale is 100% and update to the latest `TabletMonitor.exe`. |
| Pointer flickers | Set `"cursor": "none"` to find out whether it comes from this program or from Windows. |

---

## Building from source

Everything is built in the cloud by GitHub Actions (`.github/workflows/build-apk.yml`), with no local tools needed:

- `android/` — the tablet app (plain Java, `minSdk 17`, no libraries). Output: `TabletMonitor-apk` artifact (`app-debug.apk`).
- `pc/tablet_monitor.pyw` — the PC program (Python: `mss`, `Pillow`, `pystray`), packaged with PyInstaller. Output: `TabletMonitor-PC` artifact (`TabletMonitor.exe` + `platform-tools`).

The APK is a debug build signed with a throwaway debug key, which is fine for personal use.

---

## Credits

- **Virtual Display Driver** by the [VirtualDrivers](https://github.com/VirtualDrivers/Virtual-Display-Driver) project (separate download, not part of this repository).
- **Android SDK Platform-Tools (`adb`)** by Google, downloaded during the build.
