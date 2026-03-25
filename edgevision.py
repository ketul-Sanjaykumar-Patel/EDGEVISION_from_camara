#!/usr/bin/env python3
"""
EDGEVISION v7 — Real-time Edge Detection for Raspberry Pi 5

This program captures live video and performs:
- Edge detection (Canny)
- Line detection (Hough Transform)
- Object detection (Contours)

It displays results in different visualization modes.
"""

import os, sys, time, argparse

# ─────────────────────────────────────────────
# DISPLAY SETUP (Wayland / X11 / Framebuffer)
# ─────────────────────────────────────────────
def _set_display():
    """
    Automatically selects display backend depending on system.
    Required for OpenCV GUI to work on Raspberry Pi.
    """
    if os.environ.get("QT_QPA_PLATFORM"):
        return

    if os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        os.environ.setdefault("QT_WAYLAND_DISABLE_WINDOWDECORATION","1")
        print("[display] Wayland → wayland")

    elif os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        print("[display] X11 → xcb")

    else:
        os.environ["QT_QPA_PLATFORM"] = "linuxfb"
        print("[display] No display → linuxfb")

_set_display()

import cv2
import numpy as np

# ─────────────────────────────────────────────
# CAMERA HANDLING
# ─────────────────────────────────────────────
USING_PICAM = False
picam2 = None

def init_camera(args):
    """
    Initialize camera:
    - Try Raspberry Pi Camera (Picamera2)
    - If fails → fallback to USB camera
    """
    global USING_PICAM, picam2
    W, H = args.width, args.height

    if args.usb is None:
        try:
            from picamera2 import Picamera2
            picam2 = Picamera2()

            # Configure camera resolution + format
            picam2.configure(picam2.create_video_configuration(
                main={"size": (W, H), "format": "BGR888"},
                controls={"FrameRate": 30}
            ))

            picam2.start()
            USING_PICAM = True
            print(f"[✓] Pi Camera {W}x{H}")
            return None

        except Exception as e:
            print(f"[!] Picamera2 failed ({e}), trying USB camera")

    # USB camera fallback
    dev = 0 if args.usb is None else args.usb
    cap = cv2.VideoCapture(dev)

    if not cap.isOpened():
        print(f"[✗] Cannot open camera {dev}")
        sys.exit(1)

    # Set resolution + FPS
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
    cap.set(cv2.CAP_PROP_FPS, 30)

    print(f"[✓] USB cam {int(cap.get(3))}x{int(cap.get(4))}")
    return cap


def grab_frame(cap):
    """Get frame from camera"""
    if USING_PICAM:
        return True, picam2.capture_array()
    return cap.read()


def rotate_frame(frame, angle):
    """Rotate frame if needed"""
    if angle == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


# ─────────────────────────────────────────────
# IMAGE PREPROCESSING (CLAHE)
# ─────────────────────────────────────────────
# CLAHE = Contrast Limited Adaptive Histogram Equalization
# Improves contrast in low-light areas
_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16, 16))


# ─────────────────────────────────────────────
# MAIN PROCESSING FUNCTION
# ─────────────────────────────────────────────
def process_frame(frame, state):
    """
    Full image processing pipeline:
    1. Convert to grayscale
    2. Apply contrast enhancement (CLAHE)
    3. Blur to reduce noise
    4. Detect edges (Canny)
    5. Detect lines (Hough)
    6. Detect objects (Contours)
    7. Render output visualization
    """

    H, W = frame.shape[:2]

    # Convert to grayscale
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Minimum blur strength
    k = max(state['blur_k'], 2)

    # ─── CLAHE ───
    if state['clahe']:
        gray = _clahe.apply(gray)

    # ─── BLUR (noise reduction) ───
    ks = k * 2 + 1

    if state['bilateral']:
        # Keeps edges sharp while smoothing noise
        blurred = cv2.bilateralFilter(gray, ks, 55, 55)
    else:
        blurred = cv2.GaussianBlur(gray, (ks, ks), 0)

    # ─── EDGE DETECTION (Canny) ───
    edges = cv2.Canny(blurred, state['canny_lo'], state['canny_hi'])

    # ─── HOUGH LINE DETECTION ───
    hough_lines = None
    if state['show_lines']:
        hough_lines = _detect_lines(edges, W, H, state)

    # ─── CONTOUR DETECTION ───
    contours = []
    if state['show_contours']:
        raw, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Filter small noise objects
        contours = [c for c in raw if cv2.contourArea(c) > state['min_area']]

    # ─── OUTPUT VISUALIZATION ───
    mode = state['mode']
    edge_mask = edges > 0

    if mode == 'clean':
        out = frame.copy()

    elif mode == 'edges':
        out = np.zeros_like(frame)
        out[edge_mask] = state['edge_color']

    else:
        out = frame.copy()

    # Draw lines
    if hough_lines is not None:
        _draw_lines(out, hough_lines)

    # Draw objects
    if contours:
        _draw_objects(out, contours, state)

    return out, edges, len(contours)


# ─────────────────────────────────────────────
# LINE DETECTION (HOUGH TRANSFORM)
# ─────────────────────────────────────────────
def _detect_lines(edges, W, H, state):
    """
    Detect long structural lines using Hough Transform.
    Filters:
    - Horizontal
    - Vertical
    - Strong diagonals
    """

    min_len = int(max(W, H) * 0.15)

    raw = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=60,
        minLineLength=min_len,
        maxLineGap=15
    )

    if raw is None:
        return None

    lines = []

    for seg in raw:
        x1, y1, x2, y2 = seg[0]

        dx, dy = x2 - x1, y2 - y1
        length = np.hypot(dx, dy)
        angle = abs(np.degrees(np.arctan2(dy, dx)))

        lines.append((x1, y1, x2, y2, angle, length))

    # Filter useful directions
    filtered = []

    for (x1, y1, x2, y2, ang, ln) in lines:
        if ang < 20 or ang > 160:
            filtered.append((x1, y1, x2, y2, 'H'))  # horizontal
        elif 70 < ang < 110:
            filtered.append((x1, y1, x2, y2, 'V'))  # vertical
        elif 25 < ang < 155:
            filtered.append((x1, y1, x2, y2, 'D'))  # diagonal

    return filtered[:state['max_lines']]


def _draw_lines(out, lines):
    """Draw detected lines with color coding"""

    for x1, y1, x2, y2, kind in lines:
        if kind == 'H':
            color = (0, 230, 230)  # yellow
        elif kind == 'V':
            color = (50, 120, 255)  # orange
        else:
            color = (200, 200, 200)  # white

        cv2.line(out, (x1, y1), (x2, y2), color, 2)


# ─────────────────────────────────────────────
# OBJECT DETECTION (CONTOURS)
# ─────────────────────────────────────────────
def _draw_objects(out, contours, state):
    """Draw bounding boxes around detected objects"""

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)

        cv2.rectangle(out, (x, y), (x+w, y+h),
                      state['contour_color'], 2)


# ─────────────────────────────────────────────
# DEFAULT PARAMETERS
# ─────────────────────────────────────────────
def default_state():
    return {
        'mode': 'color',
        'canny_lo': 25,
        'canny_hi': 75,
        'blur_k': 3,
        'bilateral': True,
        'clahe': True,
        'show_lines': True,
        'show_contours': True,
        'min_area': 2000,
        'max_lines': 15,
        'edge_color': (212,245,0),
        'contour_color': (53,107,255),
    }


# ─────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)

    args = parser.parse_args()

    cap = init_camera(args)
    state = default_state()

    while True:
        ok, frame = grab_frame(cap)
        if not ok:
            continue

        out, edges, objs = process_frame(frame, state)

        cv2.imshow("EDGEVISION", out)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    if cap:
        cap.release()

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
