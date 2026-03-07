# main.py — Smart Guard | Run this file to start
import cv2, time, os, collections, config
from detector      import ObjectDetector
from tracker       import PersonTracker
from zone_selector import ZoneSelector
from zone_monitor  import ZoneMonitor
from risk_engine   import RiskEngine
from alert_manager import AlertManager


# ── RECORDING HELPERS ────────────────────────────────────
def get_video_writer(frame):
    os.makedirs(config.RECORDING_DIR, exist_ok=True)
    ts   = time.strftime('%Y%m%d_%H%M%S')
    path = f'{config.RECORDING_DIR}/rec_{ts}.avi'
    h, w = frame.shape[:2]
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'XVID'), 20, (w, h))
    print(f'[REC] New recording: {path}')
    return writer, path, time.time()


def cleanup_old_recordings():
    """Delete recordings older than RECORDING_KEEP_HOURS."""
    if not os.path.exists(config.RECORDING_DIR):
        return
    cutoff = time.time() - config.RECORDING_KEEP_HOURS * 3600
    for f in os.listdir(config.RECORDING_DIR):
        fpath = os.path.join(config.RECORDING_DIR, f)
        if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
            os.remove(fpath)
            print(f'[REC] Deleted old recording: {f}')


def save_pretamper_clip(buffer, fps=20):
    """Save rolling pre-tamper buffer to a video file."""
    os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
    if not buffer:
        return None
    ts   = time.strftime('%Y%m%d_%H%M%S')
    path = f'{config.SNAPSHOT_DIR}/pretamper_{ts}.avi'
    h, w = buffer[0].shape[:2]
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'XVID'), fps, (w, h))
    for f in buffer:
        writer.write(f)
    writer.release()
    print(f'[TAMPER] Pre-tamper clip saved: {path}')
    return path


# ── MAIN ─────────────────────────────────────────────────
def main():
    print('=' * 55)
    print('   SMART GUARD STARTING')
    print('=' * 55)

    # ── Open camera ──────────────────────────────────────
    print(f'[CAMERA] Opening: {config.CAMERA_SOURCE}')
    cap = cv2.VideoCapture(config.CAMERA_SOURCE)
    if not cap.isOpened():
        print('[ERROR] Cannot open camera. Check CAMERA_SOURCE in config.py')
        return
    print('[CAMERA] Connected!')

    # ── Warm up camera (fixes frozen frame on zone selector)
    print(f'[CAMERA] Warming up ({config.CAMERA_WARMUP_FRAMES} frames)...')
    for _ in range(config.CAMERA_WARMUP_FRAMES):
        cap.read()

    # ── Draw zones (live feed) ────────────────────────────
    print('[ZONES] Draw your protection zones...')
    selector   = ZoneSelector()
    zones, zone_types = selector.select_zones(cap)

    if len(zones) == 0:
        print('[WARNING] No zones drawn — guarding entire frame.')
        h, w   = first_frame.shape[:2]
        zones  = [[0, 0, w, h]]
        zone_types = [config.ZONE_TYPE_WATCH]
        selector.zones      = zones
        selector.zone_types = zone_types

    # ── Initialise modules ───────────────────────────────
    detector = ObjectDetector()
    tracker  = PersonTracker()
    monitor  = ZoneMonitor()
    risk     = RiskEngine()
    alerts   = AlertManager()

    # Set reference snapshot for tamper detection
    ret, ref_frame = cap.read()
    if ret:
        monitor.set_reference(ref_frame, zones)

    # ── Recording setup ──────────────────────────────────
    video_writer  = None
    rec_path      = None
    rec_start     = None
    if config.RECORDING_ENABLED:
        ret, init_frame = cap.read()
        if ret:
            video_writer, rec_path, rec_start = get_video_writer(init_frame)
        cleanup_old_recordings()

    # ── Rolling pre-tamper buffer ─────────────────────────
    buffer_maxlen   = int(config.TAMPER_BUFFER_SEC * 20)   # ~20fps
    pretamper_buf   = collections.deque(maxlen=buffer_maxlen)
    tamper_active   = False

    print('[SYSTEM] Guard is ACTIVE.')
    print('  Keys:  Q=quit   G=Guard mode   D=Day mode')
    print('-' * 55)

    frame_count  = 0
    last_dets    = []
    fps_timer    = time.time()
    fps          = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            print('[CAMERA] Signal lost — reconnecting...')
            time.sleep(2)
            cap.release()
            cap = cv2.VideoCapture(config.CAMERA_SOURCE)
            continue

        frame_count += 1

        # ── Rolling buffer (always) ───────────────────────
        pretamper_buf.append(frame.copy())

        # ── Recording ────────────────────────────────────
        if config.RECORDING_ENABLED and video_writer:
            video_writer.write(frame)
            # Rotate file every N minutes
            if time.time() - rec_start >= config.RECORDING_CHUNK_MIN * 60:
                video_writer.release()
                video_writer, rec_path, rec_start = get_video_writer(frame)
                cleanup_old_recordings()

        # ── FPS counter ──────────────────────────────────
        if frame_count % 30 == 0:
            fps       = 30 / max(time.time() - fps_timer, 0.01)
            fps_timer = time.time()

        # ── AI detection every Nth frame ─────────────────
        if frame_count % config.FRAME_SKIP == 0:
            last_dets = detector.detect(frame)
        dets = last_dets

        # ── Track people ─────────────────────────────────
        tracked = tracker.update(dets)

        # ── Zone analysis per person ──────────────────────
        person_events = []
        for p in tracked:
            in_zone, matched_zone, zone_idx = selector.is_inside_any_zone(p['box'])

            # Update dwell/visit state
            tracker.update_zone_state(p['track_id'], in_zone)

            # Re-read updated dwell after state update
            t_data = tracker.tracks.get(p['track_id'], {})

            person_events.append({
                'track_id':    p['track_id'],
                'in_zone':     in_zone,
                'zone_index':  zone_idx,
                'dwell_time':  t_data.get('zone_total_time', 0),
                'visit_count': p['visit_count'],
                'is_crouching': p['is_crouching'],
                'is_pacing':   p['is_pacing'],
                'is_frozen':   p['is_frozen'],
                'is_erratic':  p['is_erratic'],
                'is_running':  p['is_running'],
                'movement':    p['movement'],
            })

        # ── Tamper detection ──────────────────────────────
        # Pass which zones have people in them — skips false object-moved alerts
        occupied = {e["zone_index"] for e in person_events if e["in_zone"]}
        tamper_results = monitor.update(frame, zones, occupied_zones=occupied)
        any_tamper     = any(v['occluded'] for v in tamper_results.values())

        if any_tamper and not tamper_active:
            tamper_active = True
            clip_path = save_pretamper_clip(list(pretamper_buf))
            alerts.send_tamper_alert(pre_tamper_clip_path=clip_path)
        elif not any_tamper:
            tamper_active = False

        # ── Auto mode check (every 60 frames) ────────────
        if frame_count % 60 == 0:
            risk.auto_check_mode()

        # ── Risk score ────────────────────────────────────
        zone_type_map = {i: zone_types[i] for i in range(len(zone_types))}
        score = risk.update(person_events, zone_types=zone_type_map, tamper=any_tamper)

        # ── Status print every 5 sec ──────────────────────
        if frame_count % 150 == 0:
            print(f'[STATUS] FPS={fps:.1f}  Score={score:.0f}  '
                  f'Persons={len(tracked)}  Mode={risk.mode}  Zones={len(zones)}')

        # ── Send alert if needed ──────────────────────────
        alerts.check_and_send(frame, score, risk)

        # ── Draw overlay ──────────────────────────────────
        if config.SHOW_PREVIEW:
            display = frame.copy()
            h_f, w_f = display.shape[:2]
            FD = cv2.FONT_HERSHEY_DUPLEX
            FS = cv2.FONT_HERSHEY_SIMPLEX

            # ── Zone fills + borders ──────────────────────
            for i, z in enumerate(zones):
                x1, y1, x2, y2 = z
                z_type  = zone_types[i] if i < len(zone_types) else config.ZONE_TYPE_WATCH
                is_high = z_type == config.ZONE_TYPE_HIGH
                col     = (40, 40, 255) if is_high else (40, 220, 120)

                ov = display.copy()
                cv2.rectangle(ov, (x1, y1), (x2, y2), col, -1)
                cv2.addWeighted(ov, 0.08, display, 0.92, 0, display)
                cv2.rectangle(display, (x1, y1), (x2, y2), col, 2)

                # Corner accents
                L = 18
                for (cx, cy, dx, dy) in [(x1,y1,1,1),(x2,y1,-1,1),(x1,y2,1,-1),(x2,y2,-1,-1)]:
                    cv2.line(display, (cx, cy), (cx + dx*L, cy), col, 3)
                    cv2.line(display, (cx, cy), (cx, cy + dy*L), col, 3)

                # Zone label badge
                badge = f"  {'HIGH SEC' if is_high else 'WATCH'}  ZONE {i+1}  "
                (bw, bh), _ = cv2.getTextSize(badge, FD, 0.5, 1)
                cv2.rectangle(display, (x1, y1), (x1 + bw + 4, y1 + bh + 10), col, -1)
                cv2.putText(display, badge, (x1 + 2, y1 + bh + 4), FD, 0.5, (0,0,0), 1, cv2.LINE_AA)

            # ── Person boxes ─────────────────────────────
            for p in tracked:
                x1, y1, x2, y2 = p['box']
                in_z, _, zone_i = selector.is_inside_any_zone(p['box'])
                t_data = tracker.tracks.get(p['track_id'], {})
                dwell  = t_data.get('zone_total_time', 0)
                visits = p['visit_count']

                if p['is_crouching']:
                    col = (0, 130, 255)   # orange — crouching
                elif in_z:
                    zt  = zone_types[zone_i] if zone_i >= 0 and zone_i < len(zone_types) else config.ZONE_TYPE_WATCH
                    col = (40, 40, 255) if zt == config.ZONE_TYPE_HIGH else (0, 200, 255)
                else:
                    col = (160, 255, 160)  # soft green — outside zone

                # Person box with rounded-corner effect
                cv2.rectangle(display, (x1, y1), (x2, y2), col, 2)

                # Info badge above box
                status = 'CROUCH' if p['is_crouching'] else (f'{dwell:.0f}s' if in_z else 'OK')
                info   = f" #{p['track_id']}  {status}  v:{visits} "
                (iw, ih), _ = cv2.getTextSize(info, FD, 0.45, 1)
                iy = max(y1 - ih - 8, 0)
                cv2.rectangle(display, (x1, iy), (x1 + iw + 4, iy + ih + 8), col, -1)
                cv2.putText(display, info, (x1 + 2, iy + ih + 2), FD, 0.45, (0,0,0), 1, cv2.LINE_AA)

            # ── Top-left: timestamp + FPS ─────────────────
            ts_str = time.strftime('%H:%M:%S')
            ov2 = display.copy()
            cv2.rectangle(ov2, (0, 0), (220, 52), (0, 0, 0), -1)
            cv2.addWeighted(ov2, 0.6, display, 0.4, 0, display)
            cv2.putText(display, ts_str, (12, 26), FD, 0.75, (200, 220, 255), 1, cv2.LINE_AA)
            cv2.putText(display, f'{fps:.0f} FPS', (130, 26), FS, 0.52, (120,120,120), 1, cv2.LINE_AA)
            cv2.putText(display, f'{len(tracked)} person(s)', (12, 46), FS, 0.42, (120,120,120), 1, cv2.LINE_AA)

            # ── Top-right: Risk panel ─────────────────────
            panel_w, panel_h = 220, 100
            px = w_f - panel_w - 8
            ov3 = display.copy()
            cv2.rectangle(ov3, (px, 8), (w_f - 8, 8 + panel_h), (0,0,0), -1)
            cv2.addWeighted(ov3, 0.7, display, 0.3, 0, display)
            cv2.rectangle(display, (px, 8), (w_f - 8, 8 + panel_h), (50,50,50), 1)

            # Risk score color
            if score < 40:
                sc = (80, 255, 100)
            elif score < 70:
                sc = (0, 200, 255)
            else:
                sc = (50, 50, 255)

            # Risk bar background + fill
            bar_x1, bar_y1 = px + 10, 70
            bar_x2, bar_y2 = w_f - 18, 88
            cv2.rectangle(display, (bar_x1, bar_y1), (bar_x2, bar_y2), (40,40,40), -1)
            fill_x = int(bar_x1 + (bar_x2 - bar_x1) * score / 100)
            cv2.rectangle(display, (bar_x1, bar_y1), (fill_x, bar_y2), sc, -1)

            cv2.putText(display, f'RISK', (px + 12, 36), FD, 0.55, (180,180,180), 1, cv2.LINE_AA)
            cv2.putText(display, f'{score:.0f}', (px + 68, 62), FD, 1.5, sc, 2, cv2.LINE_AA)
            cv2.putText(display, '/100', (px + 152, 60), FS, 0.5, (120,120,120), 1, cv2.LINE_AA)

            # Mode badge
            mode_col = (80, 255, 100) if risk.mode == 'DAY' else (50, 50, 255)
            mode_lbl = f" {risk.mode} MODE "
            (mw, mh), _ = cv2.getTextSize(mode_lbl, FD, 0.48, 1)
            cv2.rectangle(display, (px + 10, 92), (px + 10 + mw + 4, 92 + mh + 8), mode_col, -1)
            cv2.putText(display, mode_lbl, (px + 12, 92 + mh + 2), FD, 0.48, (0,0,0), 1, cv2.LINE_AA)

            # ── Tamper warning overlay ────────────────────
            if any_tamper:
                ov4 = display.copy()
                cv2.rectangle(ov4, (0,0), (w_f, h_f), (0,0,180), -1)
                cv2.addWeighted(ov4, 0.25, display, 0.75, 0, display)
                cv2.rectangle(display, (0,0), (w_f, h_f), (0,0,255), 6)
                (tw,th), _ = cv2.getTextSize('! CAMERA TAMPERED !', FD, 1.4, 2)
                cv2.putText(display, '! CAMERA TAMPERED !',
                            ((w_f-tw)//2, h_f//2), FD, 1.4, (0,0,255), 2, cv2.LINE_AA)

            # ── Alert flash when score high ───────────────
            if score >= config.RISK_THRESHOLD:
                cv2.rectangle(display, (0,0), (w_f, h_f), (0,0,200), 4)

            # ── Keys hint at bottom ───────────────────────
            hint = "  Q quit    G guard mode    D day mode  "
            (hw, hh), _ = cv2.getTextSize(hint, FS, 0.4, 1)
            ov5 = display.copy()
            cv2.rectangle(ov5, (0, h_f - hh - 14), (w_f, h_f), (0,0,0), -1)
            cv2.addWeighted(ov5, 0.55, display, 0.45, 0, display)
            cv2.putText(display, hint, (10, h_f - 6), FS, 0.4, (100,100,100), 1, cv2.LINE_AA)

            cv2.namedWindow('SmartGuard', cv2.WINDOW_NORMAL)
            cv2.setWindowProperty('SmartGuard', cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            cv2.imshow('SmartGuard', display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == ord('Q'):
                print('[SYSTEM] Quitting...')
                break
            elif key == ord('g') or key == ord('G'):
                risk.set_mode('GUARD')
                print('[SYSTEM] Manual: GUARD MODE')
            elif key == ord('d') or key == ord('D'):
                risk.set_mode('DAY')
                print('[SYSTEM] Manual: DAY MODE')

    # ── Cleanup ───────────────────────────────────────────
    cap.release()
    if video_writer:
        video_writer.release()
    cv2.destroyAllWindows()
    print('[SYSTEM] Smart Guard stopped.')


if __name__ == '__main__':
    main()
