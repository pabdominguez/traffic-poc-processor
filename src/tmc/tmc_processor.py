import cv2
import json
import time
from collections import Counter
from ultralytics import YOLO

CONF_THRESHOLD = 0.01
IMG_SIZE = 640
IOU_THRESHOLD = 0.2
DIST_THRESHOLD = 10


def process_video(VIDEO_PATH, LINES_DATA, MODEL_PATH="best.pt", progress_callback=None, generate_video_output=False, output_video_path=None):
    model = YOLO(MODEL_PATH)

    raw_lines = LINES_DATA

    def ensure_int_coords(point):
        """Convert point coordinates to integers, handling both dict and tuple formats"""
        if isinstance(point, dict):
            return (int(round(point["x"])), int(round(point["y"])))
        elif isinstance(point, (list, tuple)):
            return (int(round(point[0])), int(round(point[1])))
        return point

    LINES = []
    for name, data in raw_lines.items():
        pt1 = ensure_int_coords(data["pt1"])
        pt2 = ensure_int_coords(data["pt2"])
        LINES.append({"name": name.upper(), "pt1": pt1, "pt2": pt2})

    counts = {line["name"]: 0 for line in LINES}
    counted_ids_per_line = {line["name"]: set() for line in LINES}
    prev_centroids = {}
    crossed_lines_by_id = {}
    turn_types_by_id = {}
    crossing_timestamps = {}
    detected_classes = {}
    class_counts_by_id = {}

    def get_centroid(box):
        x1, y1, x2, y2 = box
        return int((x1 + x2) / 2), int((y1 + y2) / 2)

    def point_line_distance(px, py, x1, y1, x2, y2):
        # Ensure all coordinates are float for precise calculations
        px, py = float(px), float(py)
        x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
        
        A = px - x1
        B = py - y1
        C = x2 - x1
        D = y2 - y1
        dot = A * C + B * D
        len_sq = C * C + D * D
        param = dot / len_sq if len_sq != 0 else -1
        if param < 0:
            xx, yy = x1, y1
        elif param > 1:
            xx, yy = x2, y2
        else:
            xx = x1 + param * C
            yy = y1 + param * D
        dx = px - xx
        dy = py - yy
        return (dx**2 + dy**2) ** 0.5

    def classify_turn_from_lines(crossing_data):
        if len(crossing_data) < 2:
            return 'invalid'
        
        # Ordenar por timestamp para obtener la secuencia correcta
        sorted_crossings = sorted(crossing_data, key=lambda x: x[1])  # (direction, timestamp)
        
        # Tomar la primera y última línea cruzada
        from_dir = sorted_crossings[0][0].upper()
        to_dir = sorted_crossings[-1][0].upper()

        if from_dir == to_dir:
            return 'u-turn'

        # Tabla corregida basada en perspectiva del observador desde el cielo
        transitions = {
            ('NORTH', 'EAST'): 'left',   # North -> East = giro izquierda
            ('NORTH', 'WEST'): 'right',  # North -> West = giro derecha
            ('NORTH', 'SOUTH'): 'straight',
            ('EAST', 'SOUTH'): 'left',   # East -> South = giro izquierda
            ('EAST', 'NORTH'): 'right',  # East -> North = giro derecha
            ('EAST', 'WEST'): 'straight',
            ('SOUTH', 'WEST'): 'left',   # South -> West = giro izquierda
            ('SOUTH', 'EAST'): 'right',  # South -> East = giro derecha
            ('SOUTH', 'NORTH'): 'straight',
            ('WEST', 'NORTH'): 'left',   # West -> North = giro izquierda
            ('WEST', 'SOUTH'): 'right',  # West -> South = giro derecha
            ('WEST', 'EAST'): 'straight',
        }

        return transitions.get((from_dir, to_dir), 'unknown')

    cap = cv2.VideoCapture(VIDEO_PATH)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    current_frame = 0
    start_time = time.time()
    last_progress_sent = -1
    
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
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        results = model.track(
            frame, persist=True, conf=CONF_THRESHOLD, imgsz=IMG_SIZE, iou=IOU_THRESHOLD
        )

        if results[0].boxes.id is not None:
            ids = results[0].boxes.id.cpu().numpy()
            boxes = results[0].boxes.xyxy.cpu().numpy()
            classes = results[0].boxes.cls.cpu().numpy()

            for i, box in enumerate(boxes):
                obj_id = int(ids[i])
                class_id = int(classes[i])
                class_name = model.names[class_id]
                cx, cy = get_centroid(box)
                
                # Store class for this object ID
                class_counts_by_id[obj_id] = class_name

                prev_pos = prev_centroids.get(obj_id)
                if prev_pos:
                    for line in LINES:
                        name = line["name"]
                        x1, y1 = line["pt1"]
                        x2, y2 = line["pt2"]

                        dist = point_line_distance(cx, cy, x1, y1, x2, y2)
                        prev_dist = point_line_distance(
                            prev_pos[0], prev_pos[1], x1, y1, x2, y2
                        )

                        crossed = dist < DIST_THRESHOLD and prev_dist > DIST_THRESHOLD

                        if crossed and obj_id not in counted_ids_per_line[name]:
                            counted_ids_per_line[name].add(obj_id)
                            counts[name] += 1

                            # Registrar el cruce con timestamp
                            if obj_id not in crossed_lines_by_id:
                                crossed_lines_by_id[obj_id] = []
                                crossing_timestamps[obj_id] = []
                            
                            if name not in [crossing[0] for crossing in crossing_timestamps[obj_id]]:
                                current_time = time.time()
                                crossed_lines_by_id[obj_id].append(name)
                                crossing_timestamps[obj_id].append((name, current_time))
                                
                                # Count detected class only ONCE per unique object ID
                                if obj_id not in detected_classes:
                                    detected_classes[obj_id] = class_name

                            print(f'[✔] ID {obj_id} ({class_name}) cruzó {name}')

                            # Detectar giro cuando haya al menos 2 cruces y no se haya clasificado aún
                            if len(crossing_timestamps[obj_id]) >= 2 and obj_id not in turn_types_by_id:
                                turn_type = classify_turn_from_lines(crossing_timestamps[obj_id])
                                if turn_type != 'invalid' and turn_type != 'unknown':
                                    turn_types_by_id[obj_id] = turn_type
                                    from_line = crossing_timestamps[obj_id][0][0]
                                    to_line = crossing_timestamps[obj_id][-1][0]
                                    print(f'↪ ID {obj_id} ({class_name}) hizo un giro {turn_type}: {from_line} -> {to_line}')

                prev_centroids[obj_id] = (cx, cy)
        
        # Add visualizations if generating output video
        if generate_video_output and video_writer:
            # Draw detection boxes and tracking
            if results[0].boxes.id is not None:
                ids = results[0].boxes.id.cpu().numpy()
                boxes = results[0].boxes.xyxy.cpu().numpy()
                
                for i, box in enumerate(boxes):
                    obj_id = int(ids[i])
                    x1, y1, x2, y2 = box
                    cx, cy = get_centroid(box)
                    
                    # Draw bounding box
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                    
                    # Draw centroid
                    cv2.circle(frame, (cx, cy), 5, (255, 0, 0), -1)
                    
                    # Draw ID and turn type if available
                    label = f'ID {obj_id}'
                    if obj_id in turn_types_by_id:
                        label += f' | {turn_types_by_id[obj_id]}'
                    cv2.putText(frame, label, (cx, cy - 10),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
            
            # Draw lines
            for line in LINES:
                name = line["name"]
                x1, y1 = line["pt1"] 
                x2, y2 = line["pt2"]
                cv2.line(frame, (x1, y1), (x2, y2), (0, 255, 255), 3)
                
                # Draw line label and count
                mid_x, mid_y = (x1 + x2) // 2, (y1 + y2) // 2
                cv2.putText(frame, f'{name}: {counts[name]}', (mid_x, mid_y - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            
            # Draw summary stats
            total_current = sum(counts.values())
            turn_summary = dict(Counter(turn_types_by_id.values()))
            y_pos = 30
            cv2.putText(frame, f'Total Crossings: {total_current}', (20, y_pos), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            
            for turn_type, count in turn_summary.items():
                y_pos += 25
                cv2.putText(frame, f'{turn_type}: {count}', (20, y_pos),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            
            # Write frame to output video
            video_writer.write(frame)
        
        # Progress tracking
        current_frame += 1
        if progress_callback and total_frames > 0:
            progress = int((current_frame / total_frames) * 100)
            
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

    cap.release()
    if video_writer:
        video_writer.release()

    # Post procesamiento
    all_ids = []
    for ids in counted_ids_per_line.values():
        all_ids.extend(ids)
    id_counts = Counter(all_ids)
    total_count = sum(1 for v in id_counts.values() if v >= 1)

    # Convert detected_classes from {obj_id: class_name} to {class_name: count}
    class_summary = Counter(detected_classes.values())
    
    # Calcular turns incluyendo straight
    turn_counts = Counter(turn_types_by_id.values())
    turns_dict = dict(turn_counts)
    
    # Si no hay straight explícitos, calcularlos como total - left - right - u-turn
    if 'straight' not in turns_dict:
        left_count = turns_dict.get('left', 0)
        right_count = turns_dict.get('right', 0)
        uturn_count = turns_dict.get('u-turn', 0)
        turns_dict['straight'] = max(0, total_count - left_count - right_count - uturn_count)
    
    return {
        "counts": counts, 
        "turns": turns_dict, 
        "total": total_count,
        "detected_classes": dict(class_summary)
    }