# detector.py
import config
from ultralytics import YOLO


class ObjectDetector:
    def __init__(self):
        print(f'[DETECTOR] Loading: {config.MODEL_PATH}')
        self.model = YOLO(config.MODEL_PATH)
        print('[DETECTOR] Ready!')

    def detect(self, frame):
        results = self.model(frame, verbose=False,
                             conf=config.CONFIDENCE,
                             imgsz=config.INPUT_SIZE)[0]
        detections = []
        for box in results.boxes:
            class_id = int(box.cls[0])
            x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
            detections.append({
                'class_id':   class_id,
                'label':      results.names[class_id],
                'box':        [x1, y1, x2, y2],
                'confidence': float(box.conf[0])
            })
        return detections
