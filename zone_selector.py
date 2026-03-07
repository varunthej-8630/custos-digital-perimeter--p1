# zone_selector.py
# Controls:
#   Click + Drag  = draw a zone
#   H key         = set last zone as HIGH SECURITY
#   W key         = set last zone as WATCH
#   A key         = clear all zones
#   Q key         = done, start guarding

import cv2
import numpy as np
import config


class ZoneSelector:
    def __init__(self):
        self.zones       = []
        self.zone_types  = []
        self.drawing     = False
        self.start_x     = 0
        self.start_y     = 0
        self.current_box = None

    def mouse_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drawing     = True
            self.start_x     = x
            self.start_y     = y
            self.current_box = [x, y, x, y]

        elif event == cv2.EVENT_MOUSEMOVE:
            if self.drawing:
                self.current_box = [self.start_x, self.start_y, x, y]

        elif event == cv2.EVENT_LBUTTONUP:
            self.drawing = False
            if self.current_box:
                x1 = min(self.start_x, x)
                y1 = min(self.start_y, y)
                x2 = max(self.start_x, x)
                y2 = max(self.start_y, y)
                if (x2 - x1) > 20 and (y2 - y1) > 20:
                    self.zones.append([x1, y1, x2, y2])
                    self.zone_types.append(config.ZONE_TYPE_WATCH)
                    print(f'[ZONE] Zone {len(self.zones)} drawn — press H=HIGH or W=WATCH')
                self.current_box = None

    def select_zones(self, cap):
        """
        Live zone drawing. Pass cv2.VideoCapture object.
        Returns (zones, zone_types) when Q is pressed.
        """
        WIN = 'ZoneSetup'  # short window name — no spaces/symbols (fixes mouse on some systems)

        # Create fullscreen window and show a frame BEFORE setting mouse callback
        cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        ret, boot_frame = cap.read()
        if ret:
            cv2.imshow(WIN, boot_frame)
            cv2.waitKey(1)

        # Set mouse callback AFTER window is visible
        cv2.setMouseCallback(WIN, self.mouse_callback)

        print('=' * 55)
        print('  ZONE DRAWING MODE')
        print('  Drag to draw  |  H=HIGH  |  W=WATCH  |  Q=Done')
        print('=' * 55)

        F  = cv2.FONT_HERSHEY_DUPLEX
        FS = cv2.FONT_HERSHEY_SIMPLEX

        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            display = frame.copy()
            h, w    = display.shape[:2]

            # ── Draw confirmed zones ──────────────────────
            for i, zone in enumerate(self.zones):
                x1, y1, x2, y2 = zone
                z_type  = self.zone_types[i]
                is_high = z_type == config.ZONE_TYPE_HIGH
                color   = (60, 60, 255) if is_high else (50, 220, 120)

                # Semi-transparent fill
                overlay = display.copy()
                cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
                cv2.addWeighted(overlay, 0.15, display, 0.85, 0, display)

                # Border
                cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)

                # Label badge
                label = f'Zone {i+1}  {z_type}'
                (tw, th), _ = cv2.getTextSize(label, FS, 0.52, 1)
                cv2.rectangle(display, (x1, y1 - th - 14), (x1 + tw + 12, y1), color, -1)
                cv2.putText(display, label, (x1 + 6, y1 - 5),
                            FS, 0.52, (255, 255, 255), 1, cv2.LINE_AA)

            # ── Box being drawn right now ─────────────────
            if self.drawing and self.current_box:
                x1, y1, x2, y2 = self.current_box
                cv2.rectangle(display, (x1, y1), (x2, y2), (255, 200, 50), 2)

            # ── Bottom HUD ────────────────────────────────
            bar_h = 75
            ov = display.copy()
            cv2.rectangle(ov, (0, h - bar_h), (w, h), (10, 10, 10), -1)
            cv2.addWeighted(ov, 0.82, display, 0.18, 0, display)
            cv2.line(display, (0, h - bar_h), (w, h - bar_h), (55, 55, 55), 1)

            # Zone count
            badge_col = (50, 220, 120) if len(self.zones) == 0 else (255, 200, 50)
            cv2.circle(display, (30, h - 50), 18, badge_col, -1)
            cnt = str(len(self.zones))
            cv2.putText(display, cnt, (30 - 6 * len(cnt), h - 44),
                        F, 0.65, (10, 10, 10), 2, cv2.LINE_AA)
            cv2.putText(display, 'zones', (54, h - 44),
                        FS, 0.42, (170, 170, 170), 1, cv2.LINE_AA)

            # Key hints
            hints = [
                ('DRAG', 'draw',   (200, 200, 200)),
                ('H',    'HIGH',   (100, 100, 255)),
                ('W',    'WATCH',  (80,  210, 120)),
                ('A',    'clear',  (170, 170, 170)),
                ('Q',    'start',  (100, 220, 255)),
            ]
            xp = 115
            for k, d, col in hints:
                cv2.rectangle(display, (xp, h - 62), (xp + 30, h - 40), col, -1)
                cv2.putText(display, k, (xp + 4, h - 45),
                            FS, 0.38, (10, 10, 10), 1, cv2.LINE_AA)
                cv2.putText(display, d, (xp + 36, h - 44),
                            FS, 0.42, (200, 200, 200), 1, cv2.LINE_AA)
                xp += 120

            # Bottom tip
            if self.zones:
                last  = self.zone_types[-1]
                tcol  = (100, 100, 255) if last == config.ZONE_TYPE_HIGH else (80, 210, 120)
                tip   = f'Last zone: {last}  — press H or W to change'
                cv2.putText(display, tip, (14, h - 12),
                            FS, 0.42, tcol, 1, cv2.LINE_AA)
            else:
                cv2.putText(display, 'Click and drag to draw a protection zone',
                            (14, h - 12), FS, 0.42, (130, 130, 130), 1, cv2.LINE_AA)

            cv2.imshow(WIN, display)
            key = cv2.waitKey(1) & 0xFF   # waitKey(1) not 20 — keeps mouse responsive

            if key == ord('q') or key == ord('Q'):
                break
            elif key == ord('h') or key == ord('H'):
                if self.zones:
                    self.zone_types[-1] = config.ZONE_TYPE_HIGH
                    print(f'[ZONE] Zone {len(self.zones)} -> HIGH SECURITY')
            elif key == ord('w') or key == ord('W'):
                if self.zones:
                    self.zone_types[-1] = config.ZONE_TYPE_WATCH
                    print(f'[ZONE] Zone {len(self.zones)} -> WATCH')
            elif key == ord('a') or key == ord('A'):
                self.zones      = []
                self.zone_types = []
                print('[ZONE] All zones cleared.')

        cv2.destroyWindow(WIN)
        print(f'[ZONE] {len(self.zones)} zone(s) confirmed:')
        for i, z in enumerate(self.zones):
            print(f'  Zone {i+1}: {self.zone_types[i]}  {z}')
        return self.zones, self.zone_types

    def is_inside_any_zone(self, box):
        for i, zone in enumerate(self.zones):
            if self._iou(box, zone) > config.TOUCH_IOU_THRESHOLD:
                return True, zone, i
        return False, None, -1

    def _iou(self, box_a, box_b):
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b
        ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        return inter / ((ax2-ax1)*(ay2-ay1) + (bx2-bx1)*(by2-by1) - inter + 1e-6)
