class MotionDetector:
    """
    Detects motion between two consecutive video frames using a configurable threshold.

    Intended usage:
      - Read two consecutive frames (frame1, frame2) from a video source (e.g., cv2.VideoCapture)
      - Call detect_motion(frame1, frame2)
      - If True, treat it as "motion detected" for that frame pair
    """

    def __init__(self, threshold: float = 0.5) -> None:
        """
        Args:
            threshold:
                Sensitivity threshold for declaring motion. The exact interpretation depends on the
                implementation of detect_motion(). Common options include:
                  - fraction of pixels that changed
                  - normalized average difference between frames
                  - percentage of tiles/regions exceeding a change metric
        """
        self.threshold = threshold

    def detect_motion(self, frame1, frame2) -> bool:
        """
        Compare two frames and return True if motion is detected.

        Args:
            frame1, frame2:
                Consecutive frames from the same video stream. Typically BGR images (numpy arrays)
                as returned by OpenCV.

        Returns:
            True if motion is detected, otherwise False.

        Notes:
            This is currently a stub ("pass"). A typical implementation would:
              1) Validate that frames are not None and have matching shape
              2) Convert to grayscale (optional but common)
              3) Compute absolute difference (absdiff)
              4) Threshold / aggregate the difference
              5) Compare aggregate result against self.threshold
        """
        # TODO: implement motion detection logic.
        # Example strategies:
        # - Pixel-level: normalized mean(absdiff) > threshold
        # - Mask-based: (changed_pixels / total_pixels) > threshold
        # - Tile/grid-based: any tile exceeds per-tile threshold
        pass


def main() -> None:
    """
    Minimal loop that reads frames from a video source and prints when motion is detected.

    Important:
      - video_source must be an initialized capture object, e.g. cv2.VideoCapture(0) or a file path.
      - The loop currently has no exit condition and no error handling for read failures.
      - cv2.destroyAllWindows() only matters if you create OpenCV windows (imshow), which this code
        currently does not.
    """
    # In OpenCV, this should be something like:
    #   video_source = cv2.VideoCapture(0)            # webcam
    # or:
    #   video_source = cv2.VideoCapture("video.mp4")  # file
    video_source = ""

    # Instantiate the detector with a chosen sensitivity threshold.
    detector = MotionDetector(threshold=0.5)

    while True:
        # Read two consecutive frames so detect_motion can compare them.
        # ret indicates whether the read succeeded; frame is the image.
        ret, frame1 = video_source.read()
        ret, frame2 = video_source.read()

        # In a robust implementation, you should break if ret is False or frames are None,
        # otherwise you risk exceptions or an infinite loop on end-of-stream.
        if detector.detect_motion(frame1, frame2):
            print("Motion detected!")

    # Cleanup resources (unreachable in the current code because the loop never exits).
    video_source.release()
    cv2.destroyAllWindows()
