# risk_engine.py
import time
import config


class RiskEngine:
    def __init__(self):
        self.score          = 0.0
        self.last_update    = time.time()
        self.event_log      = []
        self.mode           = 'DAY'
        self._last_activity = time.time()

    # ── MODE MANAGEMENT ───────────────────────────────────
    def set_mode(self, mode):
        if mode in ('DAY', 'GUARD') and mode != self.mode:
            self.mode = mode
            print(f'[RISK] Mode → {mode}')

    def auto_check_mode(self):
        now  = time.time()
        idle = (now - self._last_activity) / 60
        if idle >= config.AUTO_GUARD_IDLE_MIN and self.mode != 'GUARD':
            self.mode = 'GUARD'
            print(f'[RISK] Auto GUARD MODE (idle {idle:.0f} min)')
            return
        hour     = time.localtime().tm_hour
        in_guard = (hour >= config.GUARD_MODE_START or hour < config.GUARD_MODE_END) \
                   if config.GUARD_MODE_START > config.GUARD_MODE_END \
                   else (config.GUARD_MODE_START <= hour < config.GUARD_MODE_END)
        sched = 'GUARD' if in_guard else 'DAY'
        if self.mode != sched:
            self.mode = sched
            print(f'[RISK] Schedule → {sched}')

    def register_activity(self):
        self._last_activity = time.time()

    # ── MAIN UPDATE ───────────────────────────────────────
    def update(self, person_events, zone_types=None, tamper=False):
        now     = time.time()
        elapsed = now - self.last_update
        self.last_update = now
        self.event_log   = []

        # ── Decay — faster when no one in zone ───────────
        anyone_in_zone = any(e['in_zone'] for e in person_events)
        decay = config.SCORE_DECAY_RATE if anyone_in_zone else config.SCORE_DECAY_RATE * 3
        self.score = max(0, self.score - decay * elapsed)

        # ── Tamper = instant 100 ──────────────────────────
        if tamper:
            self.score = 100
            self.event_log.append('CAMERA TAMPERED!')
            return self.score

        if person_events:
            self.register_activity()

        delta = 0

        for e in person_events:
            pid       = e['track_id']
            in_zone   = e['in_zone']
            zone_idx  = e.get('zone_index', 0)
            zone_type = (zone_types or {}).get(zone_idx, config.ZONE_TYPE_WATCH)
            dwell     = e.get('dwell_time', 0)

            if not in_zone:
                continue

            # ══════════════════════════════════════════════
            # HIGH ZONE
            # Anyone enters = immediate alert. No grace period.
            # ══════════════════════════════════════════════
            if zone_type == config.ZONE_TYPE_HIGH:
                if self.mode == 'GUARD':
                    delta += 100
                    self.event_log.append(f'[HIGH] Person #{pid} in HIGH zone — GUARD MODE')
                else:
                    delta += config.RISK_THRESHOLD
                    self.event_log.append(f'[HIGH] Person #{pid} entered HIGH zone!')
                continue

            # ══════════════════════════════════════════════
            # WATCH ZONE
            # First 10 seconds = completely ignored.
            # After 10 seconds = watch for suspicious behaviour.
            # Standing still = never alert.
            # ══════════════════════════════════════════════
            if zone_type == config.ZONE_TYPE_WATCH:

                # ── 10 second grace period ────────────────
                if dwell < config.WATCH_GRACE_SEC:
                    continue   # completely ignore — not enough time yet

                person_delta     = 0
                suspicious_count = 0

                # Signal 1 — Pacing (back and forth movement)
                if e.get('is_pacing'):
                    person_delta     += 15
                    suspicious_count += 1
                    self.event_log.append(f'#{pid} PACING in zone')

                # Signal 2 — Crouching (strong signal alone)
                if e.get('is_crouching'):
                    person_delta     += 35
                    suspicious_count += 1
                    self.event_log.append(f'#{pid} CROUCHING in zone!')

                # Signal 3 — Sudden freeze (was moving, now still)
                if e.get('is_frozen'):
                    person_delta     += 20
                    suspicious_count += 1
                    self.event_log.append(f'#{pid} FROZE suddenly in zone')

                # Signal 4 — Repeated visits (keeps coming back)
                visits = e.get('visit_count', 0)
                if visits >= config.VISIT_SUSPICIOUS:
                    person_delta     += 25
                    suspicious_count += 1
                    self.event_log.append(f'#{pid} returned {visits}x — circling zone')

                # Signal 5 — Erratic movement (nervous behaviour)
                if e.get('is_erratic'):
                    person_delta     += 15
                    suspicious_count += 1
                    self.event_log.append(f'#{pid} erratic movement in zone')

                # Signal 6 — Running inside zone
                if e.get('is_running'):
                    person_delta     += 20
                    suspicious_count += 1
                    self.event_log.append(f'#{pid} RUNNING in zone!')

                # Signal 7 — Lingering very long (60+ seconds)
                if dwell >= config.DWELL_LINGER_SEC:
                    person_delta     += 20
                    suspicious_count += 1
                    self.event_log.append(f'#{pid} lingering {dwell:.0f}s in zone')

                # ── WATCH ZONE SCORING RULE ───────────────
                # Crouching alone = always alert (very suspicious)
                # Any 2 signals together = alert
                # 1 signal alone (except crouch) = mild score only, no alert
                if e.get('is_crouching'):
                    delta += min(person_delta, 70)
                elif suspicious_count >= 2:
                    delta += min(person_delta, 70)
                elif suspicious_count == 1:
                    delta += min(person_delta * 0.3, 15)  # mild, not enough to alert alone

        # ── GUARD mode doubles everything ─────────────────
        if self.mode == 'GUARD' and delta > 0:
            delta *= config.NIGHT_SCORE_MULT
            self.event_log.append(f'[GUARD x{config.NIGHT_SCORE_MULT}]')

        self.score = max(0, min(100, self.score + delta))
        return self.score
