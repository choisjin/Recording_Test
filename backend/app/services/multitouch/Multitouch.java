/**
 * 멀티터치 제스처 주입기 — root 없이 ADB shell에서 실행 가능.
 *
 * ADB shell 사용자는 INJECT_EVENTS 권한을 가지고 있어
 * InputManager.injectInputEvent()를 직접 호출할 수 있음.
 * (sendevent와 달리 /dev/input/eventX 접근 불필요)
 *
 * 빌드:
 *   javac -source 1.8 -target 1.8 -cp <android-sdk>/platforms/android-34/android.jar Multitouch.java
 *   d8 --min-api 21 --output . Multitouch.class
 *   => classes.dex 생성됨 → multitouch.dex로 이름 변경
 *
 * 실행:
 *   adb push multitouch.dex /data/local/tmp/
 *   adb shell CLASSPATH=/data/local/tmp/multitouch.dex app_process / Multitouch \
 *       <f1_x1> <f1_y1> <f1_x2> <f1_y2> <f2_x1> <f2_y1> <f2_x2> <f2_y2> <duration_ms> [display_id]
 */

import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.MotionEvent;
import java.lang.reflect.Method;

public class Multitouch {

    private static Method sInjectMethod;
    private static Object sInputManager;
    private static Method sSetDisplayId;

    static {
        try {
            Class<?> cls = Class.forName("android.hardware.input.InputManager");
            sInputManager = cls.getMethod("getInstance").invoke(null);
            sInjectMethod = cls.getMethod("injectInputEvent", InputEvent.class, int.class);
        } catch (Exception e) {
            throw new RuntimeException("InputManager init failed", e);
        }
        try {
            sSetDisplayId = MotionEvent.class.getMethod("setDisplayId", int.class);
        } catch (NoSuchMethodException ignored) {
            // API < 29
        }
    }

    private static void setDisplay(MotionEvent ev, int displayId) {
        if (sSetDisplayId != null && displayId != 0) {
            try { sSetDisplayId.invoke(ev, displayId); } catch (Exception ignored) {}
        }
    }

    private static final int INJECT_MODE_WAIT_FOR_FINISH = 2;

    private static boolean inject(MotionEvent ev) {
        try {
            return (Boolean) sInjectMethod.invoke(sInputManager, ev, INJECT_MODE_WAIT_FOR_FINISH);
        } catch (Exception e) {
            System.err.println("inject failed: " + e);
            return false;
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 9) {
            System.err.println("Usage: Multitouch f1x1 f1y1 f1x2 f1y2 f2x1 f2y1 f2x2 f2y2 durationMs [displayId]");
            System.exit(1);
        }

        float f1x1 = Float.parseFloat(args[0]);
        float f1y1 = Float.parseFloat(args[1]);
        float f1x2 = Float.parseFloat(args[2]);
        float f1y2 = Float.parseFloat(args[3]);
        float f2x1 = Float.parseFloat(args[4]);
        float f2y1 = Float.parseFloat(args[5]);
        float f2x2 = Float.parseFloat(args[6]);
        float f2y2 = Float.parseFloat(args[7]);
        int durationMs = Integer.parseInt(args[8]);
        int displayId = args.length > 9 ? Integer.parseInt(args[9]) : 0;

        int steps = Math.max(1, durationMs / 16);  // ~60fps
        int source = InputDevice.SOURCE_TOUCHSCREEN;

        // Pointer properties
        MotionEvent.PointerProperties[] pp = new MotionEvent.PointerProperties[2];
        for (int i = 0; i < 2; i++) {
            pp[i] = new MotionEvent.PointerProperties();
            pp[i].id = i;
            pp[i].toolType = MotionEvent.TOOL_TYPE_FINGER;
        }

        // Pointer coords
        MotionEvent.PointerCoords[] pc = new MotionEvent.PointerCoords[2];
        for (int i = 0; i < 2; i++) {
            pc[i] = new MotionEvent.PointerCoords();
            pc[i].pressure = 1.0f;
            pc[i].size = 1.0f;
        }

        long downTime = SystemClock.uptimeMillis();

        // 1) ACTION_DOWN — 첫 번째 손가락
        pc[0].x = f1x1; pc[0].y = f1y1;
        MotionEvent ev = MotionEvent.obtain(downTime, downTime,
                MotionEvent.ACTION_DOWN, 1,
                new MotionEvent.PointerProperties[]{pp[0]},
                new MotionEvent.PointerCoords[]{pc[0]},
                0, 0, 1f, 1f, 0, 0, source, 0);
        setDisplay(ev, displayId);
        inject(ev);
        ev.recycle();

        // 2) ACTION_POINTER_DOWN — 두 번째 손가락 추가
        pc[1].x = f2x1; pc[1].y = f2y1;
        ev = MotionEvent.obtain(downTime, SystemClock.uptimeMillis(),
                MotionEvent.ACTION_POINTER_DOWN | (1 << MotionEvent.ACTION_POINTER_INDEX_SHIFT),
                2, pp, pc, 0, 0, 1f, 1f, 0, 0, source, 0);
        setDisplay(ev, displayId);
        inject(ev);
        ev.recycle();

        // 3) ACTION_MOVE — 보간 이동
        long sleepMs = Math.max(1, durationMs / steps);
        for (int s = 1; s <= steps; s++) {
            Thread.sleep(sleepMs);
            float t = (float) s / steps;
            pc[0].x = f1x1 + (f1x2 - f1x1) * t;
            pc[0].y = f1y1 + (f1y2 - f1y1) * t;
            pc[1].x = f2x1 + (f2x2 - f2x1) * t;
            pc[1].y = f2y1 + (f2y2 - f2y1) * t;

            ev = MotionEvent.obtain(downTime, SystemClock.uptimeMillis(),
                    MotionEvent.ACTION_MOVE, 2, pp, pc,
                    0, 0, 1f, 1f, 0, 0, source, 0);
            setDisplay(ev, displayId);
            inject(ev);
            ev.recycle();
        }

        // 4) ACTION_POINTER_UP — 두 번째 손가락 떼기
        ev = MotionEvent.obtain(downTime, SystemClock.uptimeMillis(),
                MotionEvent.ACTION_POINTER_UP | (1 << MotionEvent.ACTION_POINTER_INDEX_SHIFT),
                2, pp, pc, 0, 0, 1f, 1f, 0, 0, source, 0);
        setDisplay(ev, displayId);
        inject(ev);
        ev.recycle();

        // 5) ACTION_UP — 첫 번째 손가락 떼기
        ev = MotionEvent.obtain(downTime, SystemClock.uptimeMillis(),
                MotionEvent.ACTION_UP, 1,
                new MotionEvent.PointerProperties[]{pp[0]},
                new MotionEvent.PointerCoords[]{pc[0]},
                0, 0, 1f, 1f, 0, 0, source, 0);
        setDisplay(ev, displayId);
        inject(ev);
        ev.recycle();
    }
}
