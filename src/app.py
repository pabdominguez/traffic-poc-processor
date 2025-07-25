from tmc.tmc_processor import process_video as tmc_process_video
from atr.atr_processor import process_video as atr_process_video

def process_video(VIDEO_PATH, LINES_DATA, MODEL_PATH="best.pt", study_type="TMC", progress_callback=None, generate_video_output=False, output_video_path=None):
    """
    Routes video processing based on study type.
    
    Args:
        VIDEO_PATH: Path to video file
        LINES_DATA: Line configuration data
        MODEL_PATH: Path to YOLO model
        study_type: Type of study - "TMC" or "ATR"
        progress_callback: Optional callback for progress updates
        generate_video_output: Whether to generate annotated output video
        output_video_path: Path for output video (if generate_video_output=True)
    
    Returns:
        Processing results based on study type
    """
    
    if study_type.upper() == "TMC":
        return tmc_process_video(VIDEO_PATH, LINES_DATA, MODEL_PATH, progress_callback, 
                                generate_video_output, output_video_path)
    elif study_type.upper() == "ATR":
        return atr_process_video(VIDEO_PATH, LINES_DATA, MODEL_PATH, progress_callback,
                                generate_video_output, output_video_path)
    else:
        raise ValueError(f"Unknown study_type: {study_type}. Must be 'TMC' or 'ATR'")