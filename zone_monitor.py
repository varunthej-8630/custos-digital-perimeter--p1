# zone_monitor.py
# Watches your drawn zones for two things:
# 1. Occlusion — someone blocking the camera view
# 2. Object moved — the thing you're protecting got moved/stolen

import cv2
import numpy as np
import time

class ZoneMonitor:
    def __init__(self):
        self.reference_crops = {}   # zone_index -> reference image
        self.last_check_time = 0
        self.check_interval  = 3.0  # check every 3 seconds
        self.occluded_since  = {}   # zone_index -> time occlusion started
        self.object_moved    = {}   # zone_index -> True/False

    def set_reference(self, frame, zones):
        """
        Call this ONCE after zones are drawn.
        Takes a snapshot of each zone — this is what 'normal' looks like.
        Like setting a baseline reading on a sensor.
        """
        self.reference_crops = {}
        for i, zone in enumerate(zones):
            x1, y1, x2, y2 = zone
            crop = frame[y1:y2, x1:x2]
            if crop.size > 0:
                # Convert to grayscale — colour changes (lighting) won't confuse it
                self.reference_crops[i] = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                print(f"[MONITOR] Reference saved for Zone {i+1}")

    def update(self, frame, zones, occupied_zones=None):
        """
        Call every frame. Returns dict with occlusion and movement flags per zone.
        occupied_zones: set of zone indices with a person inside.
        Object-moved check is SKIPPED for those zones.
        """
        occupied_zones = occupied_zones or set()
        now = time.time()
        results = {}

        for i, zone in enumerate(zones):
            results[i] = {
                "zone_index": i,
                "occluded":   False,
                "object_moved": False,
            }

        # Only do heavy check every 3 seconds (saves CPU)
        if now - self.last_check_time < self.check_interval:
            return results

        self.last_check_time = now

        for i, zone in enumerate(zones):
            if i not in self.reference_crops:
                continue

            x1, y1, x2, y2 = zone
            current_crop = frame[y1:y2, x1:x2]

            if current_crop.size == 0:
                continue

            current_gray = cv2.cvtColor(current_crop, cv2.COLOR_BGR2GRAY)
            ref_gray     = self.reference_crops[i]

            # Resize to same size in case frame resolution changed
            ref_gray = cv2.resize(ref_gray, (current_gray.shape[1], current_gray.shape[0]))

            # ── OCCLUSION CHECK ────────────────────────────────
            # Occlusion = zone is nearly all one dark colour (blocked)
            # Check brightness of current zone
            mean_brightness = np.mean(current_gray)

            # Also check if the zone is very uniform (solid colour = something covering it)
            std_brightness  = np.std(current_gray)

            # Dark + very uniform = someone covered the camera or zone
            if mean_brightness < 30 and std_brightness < 10:
                if i not in self.occluded_since:
                    self.occluded_since[i] = now
                    print(f"[MONITOR] Zone {i+1}: possible occlusion starting...")
                elif now - self.occluded_since[i] > 2.0:
                    results[i]["occluded"] = True
                    print(f"[MONITOR] Zone {i+1}: OCCLUDED! Zone is being blocked.")
            else:
                # Zone is visible again — clear occlusion
                if i in self.occluded_since:
                    del self.occluded_since[i]

            # ── OBJECT MOVED CHECK ─────────────────────────────
            # Skip if a person is currently in this zone —
            # they will always look different from the empty reference.
            if i in occupied_zones:
                continue

            diff = cv2.absdiff(current_gray, ref_gray)
            diff_score = np.mean(diff)

            if diff_score > 45:
                results[i]["object_moved"] = True
                print(f"[MONITOR] Zone {i+1}: OBJECT MOVED! diff_score={diff_score:.1f}")

        return results