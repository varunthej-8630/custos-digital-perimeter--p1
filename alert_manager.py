# alert_manager.py
import cv2, time, os, asyncio, threading, config

try:
    import telegram
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    print('[ALERTS] python-telegram-bot not installed. Alerts will print to terminal only.')


class AlertManager:
    def __init__(self):
        self.last_alert     = 0
        self.last_tamper    = 0
        os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)

        if TELEGRAM_AVAILABLE and config.TELEGRAM_TOKEN:
            self.bot = telegram.Bot(token=config.TELEGRAM_TOKEN)
            print('[ALERTS] Telegram bot ready.')
        else:
            self.bot = None
            if not config.TELEGRAM_TOKEN:
                print('[ALERTS] No Telegram token in .env — alerts will only print to terminal.')

    # ── MAIN ALERT ───────────────────────────────────────────
    def check_and_send(self, frame, score, risk_engine):
        now = time.time()
        if now - self.last_alert < config.ALERT_COOLDOWN: return
        if score < config.RISK_THRESHOLD: return
        self.last_alert = now

        snap = self._save_snapshot(frame, score)
        msg  = self._build_message(score, risk_engine.event_log)

        print(f'[ALERT] {msg}')
        self._dispatch(msg, snap)

    # ── TAMPER ALERT ─────────────────────────────────────────
    def send_tamper_alert(self, pre_tamper_clip_path=None):
        now = time.time()
        if now - self.last_tamper < 30: return   # tamper cooldown 30s
        self.last_tamper = now

        msg = (
            f'TAMPER DETECTED!\n'
            f'Time: {time.strftime("%I:%M:%S %p")}\n'
            f'Camera appears to be blocked or covered.'
        )
        print(f'[TAMPER] {msg}')

        if self.bot:
            # Send text alert first
            threading.Thread(
                target=lambda: asyncio.run(self._send_text(msg)),
                daemon=True
            ).start()
            # Send pre-tamper clip if available
            if pre_tamper_clip_path and os.path.exists(pre_tamper_clip_path):
                threading.Thread(
                    target=lambda: asyncio.run(self._send_video(msg, pre_tamper_clip_path)),
                    daemon=True
                ).start()

    # ── INTERNALS ────────────────────────────────────────────
    def _save_snapshot(self, frame, score):
        ts    = time.strftime('%Y%m%d_%H%M%S')
        snap  = f'{config.SNAPSHOT_DIR}/alert_{ts}.jpg'
        alert_frame = frame.copy()
        h, w  = alert_frame.shape[:2]
        cv2.rectangle(alert_frame, (0, 0), (w, h), (0, 0, 255), 8)
        cv2.putText(alert_frame, f'ALERT! RISK={score:.0f}',
                    (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 255), 3)
        cv2.imwrite(snap, alert_frame)
        return snap

    def _build_message(self, score, event_log):
        # Risk level label
        if score >= 80:
            level = '🔴 HIGH RISK'
        elif score >= 60:
            level = '🟠 MEDIUM RISK'
        else:
            level = '🟡 LOW RISK'

        # Zone type from event log
        zone_info = 'Unknown Zone'
        for e in event_log:
            if 'HIGH' in e:
                zone_info = '🔒 HIGH SECURITY Zone'
                break
            elif 'WATCH' in e or 'zone' in e.lower():
                zone_info = '👁 WATCH Zone'
                break

        # What happened — clean readable reasons
        reasons = []
        for e in event_log:
            if 'entered HIGH' in e:
                reasons.append('⚠️ Person entered restricted area')
            elif 'GUARD MODE' in e or 'NIGHT' in e:
                reasons.append('🌙 Night guard mode active')
            elif 'PACING' in e.upper():
                reasons.append('🚶 Person pacing back and forth')
            elif 'CROUCHING' in e.upper():
                reasons.append('⬇️ Person crouching in zone')
            elif 'FROZE' in e.upper():
                reasons.append('🧍 Person froze suddenly')
            elif 'circling' in e or 'returned' in e:
                reasons.append('🔄 Person keeps returning to zone')
            elif 'erratic' in e.lower():
                reasons.append('⚡ Erratic suspicious movement')
            elif 'RUNNING' in e.upper():
                reasons.append('🏃 Person running in zone')
            elif 'lingering' in e.lower():
                reasons.append('⏱ Person lingering too long')
            elif 'TAMPER' in e.upper():
                reasons.append('📵 Camera tampered!')

        # Remove duplicates
        reasons = list(dict.fromkeys(reasons))

        msg  = f'{level}\n'
        msg += f'━━━━━━━━━━━━━━━━━━━━\n'
        msg += f'📍 {zone_info}\n'
        msg += f'🕐 {time.strftime("%I:%M:%S %p")}\n'
        msg += f'📊 Risk Score: {score:.0f}/100\n'
        if reasons:
            msg += f'━━━━━━━━━━━━━━━━━━━━\n'
            msg += f'📋 What happened:\n'
            for r in reasons:
                msg += f'  {r}\n'
        msg += f'━━━━━━━━━━━━━━━━━━━━\n'
        msg += f'SmartGuard 🛡'
        return msg

    def _dispatch(self, msg, snap):
        if self.bot:
            # Run in background thread — never block the video loop
            threading.Thread(
                target=lambda: asyncio.run(self._send_photo(msg, snap)),
                daemon=True
            ).start()

    async def _send_photo(self, msg, img):
        try:
            async with self.bot:
                await self.bot.send_photo(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    photo=open(img, 'rb'),
                    caption=msg
                )
        except Exception as e:
            print(f'[ALERT] Telegram error: {e}')

    async def _send_text(self, msg):
        try:
            async with self.bot:
                await self.bot.send_message(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    text=msg
                )
        except Exception as e:
            print(f'[ALERT] Telegram error: {e}')

    async def _send_video(self, msg, video_path):
        try:
            async with self.bot:
                await self.bot.send_video(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    video=open(video_path, 'rb'),
                    caption=msg
                )
        except Exception as e:
            print(f'[ALERT] Telegram video error: {e}')
