# tracker.py
import time
import config


class PersonTracker:
    def __init__(self):
        self.tracks  = {}
        self.next_id = 1

    def update(self, detections):
        persons = [d for d in detections if d['class_id'] == config.CLASS_PERSON]
        now     = time.time()
        matched = set()
        result  = []

        for det in persons:
            box = det['box']
            cx  = (box[0] + box[2]) / 2
            cy  = (box[1] + box[3]) / 2
            bw  = box[2] - box[0]
            bh  = box[3] - box[1]

            # ── Match to existing track ───────────────────
            best_id, best_d = None, 120
            for tid, t in self.tracks.items():
                if tid in matched: continue
                if now - t['last_seen'] > 3.0: continue
                d = ((cx - t['cx'])**2 + (cy - t['cy'])**2) ** 0.5
                if d < best_d:
                    best_d  = d
                    best_id = tid

            # ── New person — create track ─────────────────
            if best_id is None:
                best_id = self.next_id
                self.next_id += 1
                self.tracks[best_id] = {
                    'first_seen':      now,
                    'last_seen':       now,
                    'cx': cx, 'cy': cy,
                    'prev_cx': cx, 'prev_cy': cy,

                    # Dwell
                    'zone_total_time': 0.0,
                    'zone_enter_time': None,
                    'in_zone_prev':    False,

                    # Visit counting
                    'visit_times':     [],
                    'stop_timer':      None,
                    'visit_counted':   False,

                    # Crouching
                    'stand_height':    bh,
                    'crouch_timer':    None,
                    'is_crouching':    False,

                    # Pacing detection
                    # Store last N positions to detect back-and-forth
                    'position_history': [],     # list of (cx, cy, time)
                    'direction_changes': 0,     # how many times direction reversed
                    'last_direction':    None,  # 'left' or 'right'
                    'pacing_score':      0,     # accumulated pacing score

                    # Freeze detection
                    # Person was moving fast, then suddenly stopped
                    'was_moving':        False,
                    'freeze_timer':      None,
                    'is_frozen':         False,

                    # Movement history for erratic detection
                    'speed_history':     [],    # last N speeds
                }

            matched.add(best_id)
            t    = self.tracks[best_id]
            move = ((cx - t['cx'])**2 + (cy - t['cy'])**2) ** 0.5

            # ── Update position ───────────────────────────
            t['prev_cx']   = t['cx']
            t['prev_cy']   = t['cy']
            t['cx']        = cx
            t['cy']        = cy
            t['last_seen'] = now

            # ── Speed history (last 10 readings) ─────────
            t['speed_history'].append(move)
            if len(t['speed_history']) > 10:
                t['speed_history'].pop(0)

            # ── Position history (last 30 readings) ──────
            t['position_history'].append((cx, cy, now))
            if len(t['position_history']) > 30:
                t['position_history'].pop(0)

            # ── Crouching ────────────────────────────────
            crouching = self._check_crouching(t, bh, now)

            # ── Pacing detection ──────────────────────────
            pacing = self._check_pacing(t)

            # ── Freeze detection ──────────────────────────
            frozen = self._check_freeze(t, move, now)

            # ── Erratic movement ──────────────────────────
            erratic = self._check_erratic(t)

            # ── Running (very fast) ───────────────────────
            running = move > config.RUNNING_SPEED_PX

            result.append({
                'track_id':     best_id,
                'box':          box,
                'dwell_time':   t['zone_total_time'],
                'movement':     move,
                'visit_count':  self._recent_visits(t, now),
                'is_crouching': crouching,
                'is_pacing':    pacing,
                'is_frozen':    frozen,
                'is_erratic':   erratic,
                'is_running':   running,
                'cx': cx, 'cy': cy,
            })

        # ── Remove stale tracks ───────────────────────────
        stale = [tid for tid, t in self.tracks.items() if now - t['last_seen'] > 5]
        for tid in stale:
            del self.tracks[tid]

        return result

    def update_zone_state(self, track_id, in_zone):
        if track_id not in self.tracks:
            return
        t   = self.tracks[track_id]
        now = time.time()

        # ── Accumulated dwell ─────────────────────────────
        if in_zone:
            if not t['in_zone_prev']:
                t['zone_enter_time'] = now
            else:
                if t['zone_enter_time']:
                    t['zone_total_time'] += now - t['zone_enter_time']
                    t['zone_enter_time']  = now
        else:
            t['zone_enter_time'] = None

        t['in_zone_prev'] = in_zone

        # ── Stop / visit counting ─────────────────────────
        dx = abs(t['cx'] - t['prev_cx'])
        dy = abs(t['cy'] - t['prev_cy'])
        is_still = (dx**2 + dy**2) ** 0.5 < config.ZONE_STOP_PIXELS

        if in_zone and is_still:
            if t['stop_timer'] is None:
                t['stop_timer']    = now
                t['visit_counted'] = False
            elif not t['visit_counted'] and (now - t['stop_timer']) >= config.ZONE_STOP_SECONDS:
                t['visit_times'].append(now)
                t['visit_counted'] = True
        else:
            t['stop_timer']    = None
            t['visit_counted'] = False

    # ── BEHAVIOUR DETECTORS ───────────────────────────────

    def _recent_visits(self, t, now):
        cutoff = now - config.VISIT_WINDOW_SEC
        return sum(1 for vt in t['visit_times'] if vt > cutoff)

    def _check_crouching(self, t, current_height, now):
        ref_h = t['stand_height']
        if current_height < ref_h * config.CROUCH_SHRINK_RATIO:
            if t['crouch_timer'] is None:
                t['crouch_timer'] = now
            if now - t['crouch_timer'] >= config.CROUCH_MIN_SECONDS:
                t['is_crouching'] = True
                return True
        else:
            t['crouch_timer'] = None
            t['is_crouching'] = False
            t['stand_height'] = t['stand_height'] * 0.95 + current_height * 0.05
        return False

    def _check_pacing(self, t):
        """
        Pacing = person moving back and forth in the same area.
        Detects by counting direction reversals in recent history.
        """
        hist = t['position_history']
        if len(hist) < 10:
            return False

        reversals = 0
        prev_dx   = 0
        for i in range(1, len(hist)):
            dx = hist[i][0] - hist[i-1][0]
            if prev_dx != 0:
                # Direction reversed on X axis
                if (dx > 3 and prev_dx < -3) or (dx < -3 and prev_dx > 3):
                    reversals += 1
            if abs(dx) > 3:
                prev_dx = dx

        # 3+ reversals in recent history = pacing
        return reversals >= 3

    def _check_freeze(self, t, current_move, now):
        """
        Freeze = person was moving, then suddenly went completely still.
        Like someone who spotted the camera and froze.
        """
        speeds = t['speed_history']
        if len(speeds) < 6:
            return False

        avg_recent_speed = sum(speeds[-3:]) / 3      # last 3 frames
        avg_before_speed = sum(speeds[-6:-3]) / 3    # 3 frames before that

        # Was moving before, now nearly still
        was_moving = avg_before_speed > 8
        now_still  = avg_recent_speed < 2

        if was_moving and now_still:
            if t['freeze_timer'] is None:
                t['freeze_timer'] = now
            if now - t['freeze_timer'] >= 2.0:  # frozen for 2+ seconds
                t['is_frozen'] = True
                return True
        else:
            t['freeze_timer'] = None
            t['is_frozen']    = False
        return False

    def _check_erratic(self, t):
        """
        Erratic = speed changes wildly and unpredictably.
        Fast then slow then fast = suspicious nervous behaviour.
        """
        speeds = t['speed_history']
        if len(speeds) < 8:
            return False

        # High standard deviation in speed = erratic
        avg   = sum(speeds) / len(speeds)
        variance = sum((s - avg) ** 2 for s in speeds) / len(speeds)
        std   = variance ** 0.5

        return std > 12 and avg > 5  # unpredictable movement pattern
