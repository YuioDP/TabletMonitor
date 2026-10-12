# Tablet Monitor

Convierte una tablet Android 4.2.2+ en pantalla de tu PC con Windows (USB o WiFi).

- `android/`  -> app de la tablet (se compila sola en GitHub, no necesitas Android Studio)
- `pc/`       -> programa de la PC (Python, icono en la bandeja, sin consola)
- `.github/`  -> receta que compila el APK en la nube

## Parte 1: obtener el APK (en GitHub)

1. En github.com: **New repository**, nombre `tablet-monitor` (publico o privado, da igual).
2. Descomprime este zip. En el repositorio nuevo: **Add file > Upload files** y arrastra
   TODO el contenido de la carpeta (incluida `.github`, `android` y `pc`). **Commit changes**.
   - Si `.github` no se subio: **Add file > Create new file**, escribe como nombre
     `.github/workflows/build-apk.yml` y pega ahi el contenido de ese archivo.
3. Pestana **Actions**. El flujo "Build APK" arranca solo (si pide habilitar workflows, aceptalo).
   Tarda unos 3-6 minutos. Si falla, abre el run, copia el error y mandamelo.
4. Entra al run terminado > seccion **Artifacts** > `TabletMonitor-apk` (baja un zip con `app-debug.apk`).
5. Pasa el APK a la tablet (cable/microSD), activa **Ajustes > Seguridad > Origenes desconocidos**
   y abrelo para instalar. (O por ADB: `adb install -r app-debug.apk`.)

## Parte 2: la PC

1. Instala **Python 3.12 o superior** desde python.org. Marca "Add python.exe to PATH".
2. Doble clic en `pc\instalar.bat` (instala mss, pillow y pystray).
3. Solo para USB: descarga "SDK Platform-Tools" para Windows (developer.android.com/tools/releases/platform-tools),
   descomprimelo dentro de `pc` para que exista `pc\platform-tools\adb.exe`.
4. En la tablet: Ajustes > Acerca de la tablet > toca 7 veces "Numero de compilacion" (activa Opciones de
   desarrollador) > activa **Depuracion USB**. Conecta el cable y acepta el aviso "Permitir depuracion USB".
   Si Windows no reconoce la tablet, puede hacer falta su driver USB (dime el resultado y lo vemos).
5. Abre la app **Tablet Monitor** en la tablet (muestra "Esperando a la PC", su IP y su resolucion).
6. Doble clic en `pc\tablet_monitor.pyw`. Aparece un icono en la bandeja (flecha ^ de iconos ocultos):
   gris = detenido, amarillo = conectando, verde = transmitiendo. Clic derecho para el menu.

## WiFi

Menu del icono > Conexion > WiFi, y en `config.json` pon `wifi_ip` con la IP que muestra la tablet
(PC y tablet en la misma red). No requiere ningun driver ni cambios en el firewall de la PC.

## Monitor extendido (no solo espejo)

Sin driver de pantalla virtual, el programa solo duplica tu monitor principal (sirve para probar).
Para un segundo monitor real hay que instalar un driver de pantalla virtual y dejarlo con la misma
resolucion que la tablet (se muestra en su pantalla de espera). El programa en modo `auto` captura
el ultimo monitor de Windows, que sera el virtual.

## Problemas

Abre `pc\log.txt` (o menu del icono > Ver registro) y mandamelo.
