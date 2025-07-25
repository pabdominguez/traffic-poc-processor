import cv2
import numpy as np
import json
import time
from shapely.geometry import Point, Polygon
from ultralytics import YOLO
from collections import OrderedDict, Counter

# === Centroid Tracker ===
class CentroidTracker:
    def __init__(self, max_disappeared=15):
        self.nextObjectID = 0
        self.objects = OrderedDict()
        self.disappeared = OrderedDict()
        self.max_disappeared = max_disappeared

    def register(self, centroid):
        self.objects[self.nextObjectID] = centroid
        self.disappeared[self.nextObjectID] = 0
        self.nextObjectID += 1

    def deregister(self, objectID):
        del self.objects[objectID]
        del self.disappeared[objectID]

    def update(self, input_centroids):
        if len(input_centroids) == 0:
            for objectID in list(self.disappeared.keys()):
                self.disappeared[objectID] += 1
                if self.disappeared[objectID] > self.max_disappeared:
                    self.deregister(objectID)
            return self.objects

        if len(self.objects) == 0:
            for centroid in input_centroids:
                self.register(centroid)
        else:
            objectIDs = list(self.objects.keys())
            objectCentroids = list(self.objects.values())
            D = np.linalg.norm(np.array(objectCentroids)[:, np.newaxis] - input_centroids, axis=2)
            rows = D.min(axis=1).argsort()
            cols = D.argmin(axis=1)[rows]

            used_rows = set()
            used_cols = set()

            for (row, col) in zip(rows, cols):
                if row in used_rows or col in used_cols:
                    continue
                objectID = objectIDs[row]
                self.objects[objectID] = input_centroids[col]
                self.disappeared[objectID] = 0
                used_rows.add(row)
                used_cols.add(col)

            unused_rows = set(range(0, D.shape[0])).difference(used_rows)
            unused_cols = set(range(0, D.shape[1])).difference(used_cols)

            for row in unused_rows:
                objectID = objectIDs[row]
                self.disappeared[objectID] += 1
                if self.disappeared[objectID] > self.max_disappeared:
                    self.deregister(objectID)

            for col in unused_cols:
                self.register(input_centroids[col])

        return self.objects

def get_centroid(box):
    x1, y1, x2, y2 = box
    return int((x1 + x2) / 2), int((y1 + y2) / 2)

def dict_points_to_tuples(points):
    """Convert points from dict format to tuples, ensuring they are integers for OpenCV"""
    result = []
    for pt in points:
        if isinstance(pt, dict):
            x = int(round(pt["x"]))  # Convert float to int and round
            y = int(round(pt["y"]))
            result.append((x, y))
        else:
            x = int(round(pt[0])) if isinstance(pt[0], float) else pt[0]
            y = int(round(pt[1])) if isinstance(pt[1], float) else pt[1]
            result.append((x, y))
    return result

def point_side_of_line(p, a, b):
    """
    Determine which side of line ab point p is on.
    Returns >0 if p is left of ab, <0 if right, 0 if on line
    """
    # Ensure all coordinates are numeric (handle potential float/int mix)
    return (float(b[0]) - float(a[0])) * (float(p[1]) - float(a[1])) - (float(b[1]) - float(a[1])) * (float(p[0]) - float(a[0]))

def process_video(VIDEO_PATH, LINES_DATA, MODEL_PATH="best.pt", progress_callback=None, generate_video_output=False, output_video_path=None):
    """
    Process video for ATR (Automatic Traffic Recording) analysis.
    
    Args:
        VIDEO_PATH: Path to video file
        LINES_DATA: Lane configuration data with lanes and finish_line
        MODEL_PATH: Path to YOLO model
        progress_callback: Optional callback for progress updates
        
    Returns:
        Dictionary with lane counts and total count
    """
    
    # Constants
    CONF_THRESHOLD = 0.1
    
    # Load YOLO model
    model = YOLO(MODEL_PATH)
    
    # Process lanes configuration
    lanes = LINES_DATA["lanes"]
    for lane in lanes:
        lane["points"] = dict_points_to_tuples(lane["points"])
    lane_polygons = [(lane["id"], Polygon(lane["points"])) for lane in lanes]
    lane_counts = {lane_id: 0 for lane_id, _ in lane_polygons}
    
    # Process finish line
    finish_line = LINES_DATA.get("finish_line")
    if finish_line and isinstance(finish_line[0], dict):
        finish_line = dict_points_to_tuples(finish_line)
    
    # Initialize tracking variables
    counted_ids = set()
    previous_positions = {}
    tracker = CentroidTracker(max_disappeared=15)
    
    # Debug counters
    debug_total_detections = 0
    debug_tracked_objects = set()
    detected_classes = {}
    class_counts_by_id = {}
    
    # Initialize video capture
    cap = cv2.VideoCapture(VIDEO_PATH)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_count = 0
    last_progress_sent = -1
    start_time = time.time()
    
    # Initialize video writer if output video is requested
    video_writer = None
    if generate_video_output and output_video_path:
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # Try multiple codecs for web compatibility
        codecs_to_try = ['H264', 'X264', 'XVID', 'mp4v']
        video_writer = None
        
        for codec in codecs_to_try:
            try:
                fourcc = cv2.VideoWriter_fourcc(*codec)
                temp_writer = cv2.VideoWriter(output_video_path, fourcc, fps, (width, height))
                if temp_writer.isOpened():
                    video_writer = temp_writer
                    print(f"✅ Using video codec: {codec}")
                    break
                else:
                    temp_writer.release()
            except Exception as e:
                print(f"⚠️ Codec {codec} failed: {e}")
                continue
        
        if not video_writer:
            print("❌ Could not initialize video writer with any codec")
            generate_video_output = False
    
    # Process video frames
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
            
        frame_count += 1
        
        # Progress tracking (send every 5% like TMC)
        if progress_callback and total_frames > 0:
            progress = int((frame_count / total_frames) * 100)
            
            # Send progress every 5%
            if progress >= last_progress_sent + 5 and progress < 100:
                elapsed_time = time.time() - start_time
                if progress > 0:
                    estimated_total_time = elapsed_time / (progress / 100)
                    estimated_remaining_time = int(estimated_total_time - elapsed_time)
                else:
                    estimated_remaining_time = 0
                
                progress_callback({
                    "progress": progress,
                    "estimatedTimeRemaining": max(0, estimated_remaining_time)
                })
                last_progress_sent = progress
        
        # YOLO detection
        results = model.predict(frame, conf=CONF_THRESHOLD)
        boxes = results[0].boxes
        
        input_centroids = []
        detections_map = {}
        
        # Only process if there are detections
        if boxes is not None and len(boxes) > 0:
            for i, box in enumerate(boxes):
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                class_id = int(box.cls[0].cpu().numpy())
                class_name = model.names[class_id]
                cx, cy = get_centroid((x1, y1, x2, y2))
                input_centroids.append(np.array([cx, cy]))
                detections_map[(cx, cy)] = (x1, y1, x2, y2, class_name)
                debug_total_detections += 1
        
        # Update tracker
        objects = tracker.update(np.array(input_centroids))
        
        # Process tracked objects
        for objectID, centroid in objects.items():
            debug_tracked_objects.add(objectID)
            cx, cy = centroid
            pt = Point(cx, cy)
            lane_id = None
            
            # Find class for this detection
            class_name = "unknown"
            for (det_cx, det_cy), detection_data in detections_map.items():
                if abs(det_cx - cx) < 20 and abs(det_cy - cy) < 20:  # Match centroid
                    if len(detection_data) > 4:  # Has class_name
                        class_name = detection_data[4]
                    break
            class_counts_by_id[objectID] = class_name
            
            # Find which lane the object is in
            for lid, polygon in lane_polygons:
                if polygon.buffer(8).contains(pt):
                    lane_id = lid
                    break
            
            # Initialize position history
            if objectID not in previous_positions:
                previous_positions[objectID] = []
            
            previous_positions[objectID].append((cx, cy))
            
            # Check finish line crossing
            if (
                objectID not in counted_ids and
                lane_id is not None and
                finish_line is not None and
                len(previous_positions[objectID]) >= 2
            ):
                a, b = finish_line
                prev = previous_positions[objectID][-2]
                curr = previous_positions[objectID][-1]
                side_prev = point_side_of_line(prev, a, b)
                side_curr = point_side_of_line(curr, a, b)
                
                if side_prev * side_curr < 0:  # Changed sides
                    counted_ids.add(objectID)
                    lane_counts[lane_id] += 1
                    
                    # Count detected class only ONCE per unique object ID
                    if objectID not in detected_classes:
                        detected_classes[objectID] = class_name
                    
                    print(f"[ATR COUNTED] Vehicle ID={objectID} ({class_name}) | Lane={lane_id} | Lane Total: {lane_counts[lane_id]}")
        
        # Add visualizations if generating output video
        if generate_video_output and video_writer:
            # Draw detections and tracking
            for objectID, centroid in objects.items():
                cx, cy = int(centroid[0]), int(centroid[1])
                
                # Find lane for this object
                pt = Point(cx, cy)
                lane_id = None
                for lid, polygon in lane_polygons:
                    if polygon.buffer(8).contains(pt):
                        lane_id = lid
                        break
                
                # Draw bounding box if available
                if (cx, cy) in detections_map:
                    x1, y1, x2, y2, class_name_viz = detections_map[(cx, cy)]
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                
                # Draw centroid
                color = (0, 255, 0) if lane_id is not None else (0, 0, 255)
                cv2.circle(frame, (cx, cy), 5, color, -1)
                cv2.putText(frame, f'ID {objectID} | L{lane_id}', (cx, cy - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            
            # Draw finish line
            if finish_line is not None and len(finish_line) == 2:
                pt1 = tuple(map(int, finish_line[0]))
                pt2 = tuple(map(int, finish_line[1]))
                cv2.line(frame, pt1, pt2, (255, 0, 255), 3)
            
            # Draw lanes and counts
            for lane in lanes:
                pts = np.array(lane["points"], np.int32).reshape((-1, 1, 2))
                cv2.polylines(frame, [pts], isClosed=True, color=(0, 255, 255), thickness=2)
                cv2.putText(frame, f'L{lane["id"]}: {lane_counts[lane["id"]]}',
                           (pts[0][0][0], pts[0][0][1] - 8),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            
            # Draw total count
            total_count_current = sum(lane_counts.values())
            cv2.putText(frame, f'Total: {total_count_current}', (20, 40), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 3)
            
            # Write frame to output video
            video_writer.write(frame)
        
        # Small delay to stabilize tracking (similar to cv2.waitKey in example.py)
        time.sleep(0.001)  # 1ms delay to prevent too rapid processing
    
    cap.release()
    if video_writer:
        video_writer.release()
    
    # Return results
    total_count = sum(lane_counts.values())
    
    # Debug output
    print(f"[ATR DEBUG] Total detections: {debug_total_detections}")
    print(f"[ATR DEBUG] Unique tracked objects: {len(debug_tracked_objects)}")
    print(f"[ATR DEBUG] Objects counted: {len(counted_ids)}")
    print(f"[ATR DEBUG] Final lane counts: {lane_counts}")
    print(f"[ATR DEBUG] Total count: {total_count}")
    
    # Convert detected_classes from {obj_id: class_name} to {class_name: count}
    class_summary = Counter(detected_classes.values())
    
    return {
        "lane_counts": lane_counts,
        "total_count": total_count,
        "study_type": "ATR",
        "detected_classes": dict(class_summary)
    }