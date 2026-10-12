package com.tabletmonitor;

import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.Bundle;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.WindowManager;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.NetworkInterface;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.Enumeration;

/**
 * Tablet Monitor: la tablet escucha en el puerto 5000. La PC se conecta (por USB con
 * "adb forward" o por WiFi), recibe el tamano de pantalla y envia fotogramas JPEG.
 * Protocolo: tablet -> PC: int ancho, int alto. PC -> tablet: [int largo][bytes JPEG]...
 */
public class MainActivity extends Activity implements SurfaceHolder.Callback {

    private static final int PORT = 5000;
    private static final int MAX_FRAME = 8 * 1024 * 1024;

    private SurfaceView surfaceView;
    private SurfaceHolder holder;

    private volatile boolean running = false;
    private volatile boolean connected = false;
    private volatile int surfW = 0;
    private volatile int surfH = 0;
    private volatile String netStatus = "";

    private final Object lock = new Object();
    private byte[] pendingFrame = null;

    private volatile ServerSocket serverSocket;
    private volatile Socket clientSocket;
    private Thread netThread;
    private Thread drawThread;
    private final Rect dst = new Rect();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_FULLSCREEN);
        surfaceView = new SurfaceView(this);
        setContentView(surfaceView);
        holder = surfaceView.getHolder();
        holder.addCallback(this);
        hideBars();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            hideBars();
        }
    }

    private void hideBars() {
        try {
            surfaceView.setSystemUiVisibility(View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    | View.SYSTEM_UI_FLAG_LOW_PROFILE);
        } catch (Throwable t) {
            // no pasa nada
        }
    }

    // ---------- Surface ----------

    @Override
    public void surfaceCreated(SurfaceHolder h) {
        startWork();
    }

    @Override
    public void surfaceChanged(SurfaceHolder h, int format, int width, int height) {
        surfW = width;
        surfH = height;
    }

    @Override
    public void surfaceDestroyed(SurfaceHolder h) {
        stopWork();
    }

    private void startWork() {
        stopWork();
        running = true;
        netThread = new Thread(new Runnable() {
            public void run() {
                serverLoop();
            }
        }, "net");
        drawThread = new Thread(new Runnable() {
            public void run() {
                drawLoop();
            }
        }, "draw");
        netThread.start();
        drawThread.start();
    }

    private void stopWork() {
        running = false;
        try {
            if (serverSocket != null) serverSocket.close();
        } catch (IOException e) {
            // ignorar
        }
        try {
            if (clientSocket != null) clientSocket.close();
        } catch (IOException e) {
            // ignorar
        }
        synchronized (lock) {
            lock.notifyAll();
        }
        joinQuiet(netThread);
        joinQuiet(drawThread);
        netThread = null;
        drawThread = null;
    }

    private void joinQuiet(Thread t) {
        if (t == null) return;
        try {
            t.join(1500);
        } catch (InterruptedException e) {
            // ignorar
        }
    }

    private void sleepQuiet(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            // ignorar
        }
    }

    // ---------- Red ----------

    private void serverLoop() {
        while (running) {
            ServerSocket ss = null;
            try {
                ss = new ServerSocket();
                ss.setReuseAddress(true);
                ss.bind(new InetSocketAddress(PORT));
                serverSocket = ss;
                if (!running) {
                    break;
                }
                netStatus = "";
                while (running) {
                    Socket s = ss.accept();
                    clientSocket = s;
                    handleClient(s);
                    clientSocket = null;
                }
            } catch (IOException e) {
                if (running) {
                    netStatus = "Error de red: " + e.getMessage();
                    sleepQuiet(1000);
                }
            } finally {
                if (ss != null) {
                    try {
                        ss.close();
                    } catch (IOException e2) {
                        // ignorar
                    }
                }
            }
        }
    }

    private void handleClient(Socket s) {
        try {
            s.setTcpNoDelay(true);
            for (int i = 0; i < 40 && surfW <= 0 && running; i++) {
                sleepQuiet(50);
            }
            DataOutputStream out = new DataOutputStream(s.getOutputStream());
            out.writeInt(surfW);
            out.writeInt(surfH);
            out.flush();
            connected = true;

            DataInputStream in = new DataInputStream(new BufferedInputStream(s.getInputStream(), 1 << 16));
            while (running) {
                int len = in.readInt();
                if (len <= 0 || len > MAX_FRAME) {
                    break;
                }
                byte[] buf = new byte[len];
                in.readFully(buf);
                synchronized (lock) {
                    pendingFrame = buf; // si habia uno sin dibujar, se descarta (evita retraso)
                    lock.notifyAll();
                }
            }
        } catch (IOException e) {
            // la PC se desconecto
        } finally {
            connected = false;
            synchronized (lock) {
                pendingFrame = null;
            }
            try {
                s.close();
            } catch (IOException e2) {
                // ignorar
            }
        }
    }

    // ---------- Dibujo ----------

    private void drawLoop() {
        BitmapFactory.Options opts = new BitmapFactory.Options();
        opts.inPreferredConfig = Bitmap.Config.RGB_565;
        opts.inMutable = true;
        Bitmap reuse = null;

        while (running) {
            byte[] data;
            synchronized (lock) {
                if (pendingFrame == null) {
                    try {
                        lock.wait(connected ? 1000 : 500);
                    } catch (InterruptedException e) {
                        return;
                    }
                }
                data = pendingFrame;
                pendingFrame = null;
            }
            if (!running) {
                break;
            }
            if (data == null) {
                if (!connected) {
                    drawWaiting();
                }
                continue;
            }

            Bitmap bmp = null;
            try {
                opts.inBitmap = reuse;
                bmp = BitmapFactory.decodeByteArray(data, 0, data.length, opts);
            } catch (Throwable t) {
                bmp = null;
            }
            if (bmp == null && reuse != null) {
                reuse = null;
                opts.inBitmap = null;
                try {
                    bmp = BitmapFactory.decodeByteArray(data, 0, data.length, opts);
                } catch (Throwable t2) {
                    bmp = null;
                }
            }
            if (bmp == null) {
                continue;
            }
            reuse = bmp;
            drawBitmap(bmp);
        }
    }

    private void drawBitmap(Bitmap bmp) {
        Canvas c = null;
        try {
            c = holder.lockCanvas();
            if (c == null) return;
            dst.set(0, 0, c.getWidth(), c.getHeight());
            c.drawBitmap(bmp, null, dst, null);
        } catch (Throwable t) {
            // superficie no disponible
        } finally {
            if (c != null) {
                try {
                    holder.unlockCanvasAndPost(c);
                } catch (Throwable t2) {
                    // ignorar
                }
            }
        }
    }

    private void drawWaiting() {
        Canvas c = null;
        try {
            c = holder.lockCanvas();
            if (c == null) return;
            c.drawColor(Color.BLACK);
            float h = c.getHeight();
            float cx = c.getWidth() / 2f;
            Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
            p.setTextAlign(Paint.Align.CENTER);
            p.setColor(Color.WHITE);
            p.setTextSize(h / 12f);
            c.drawText("Tablet Monitor", cx, h * 0.30f, p);
            p.setTextSize(h / 20f);
            c.drawText("Esperando a la PC...", cx, h * 0.45f, p);
            p.setColor(0xFFAAAAAA);
            c.drawText("IP: " + localIps() + "   Puerto: " + PORT, cx, h * 0.58f, p);
            c.drawText("Pantalla: " + surfW + "x" + surfH, cx, h * 0.68f, p);
            if (netStatus.length() > 0) {
                c.drawText(netStatus, cx, h * 0.78f, p);
            }
        } catch (Throwable t) {
            // ignorar
        } finally {
            if (c != null) {
                try {
                    holder.unlockCanvasAndPost(c);
                } catch (Throwable t2) {
                    // ignorar
                }
            }
        }
    }

    private String localIps() {
        StringBuilder sb = new StringBuilder();
        try {
            Enumeration<NetworkInterface> en = NetworkInterface.getNetworkInterfaces();
            while (en != null && en.hasMoreElements()) {
                NetworkInterface ni = en.nextElement();
                Enumeration<InetAddress> addrs = ni.getInetAddresses();
                while (addrs.hasMoreElements()) {
                    InetAddress a = addrs.nextElement();
                    if (!a.isLoopbackAddress() && a instanceof Inet4Address) {
                        if (sb.length() > 0) sb.append(", ");
                        sb.append(a.getHostAddress());
                    }
                }
            }
        } catch (Exception e) {
            // ignorar
        }
        return sb.length() == 0 ? "sin red" : sb.toString();
    }
}
