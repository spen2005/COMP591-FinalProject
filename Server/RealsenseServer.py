import select
import json
import pyrealsense2 as rs
import numpy as np
import cv2

# ================
# Data
# ================
# Sequence
DEBUG = False
DEBUG = True

# State
# CalibrationMatrix = np.zeros((4, 4))
MarkerCentroids = np.zeros((250, 3))
MarkerAges = np.full(250, -1)
CurrentTime = 0

# Config
LIFETIME_THRESHOLD = 3

# ================
# Realsense Setup
# ================
# Configure depth and color streams
pipeline = rs.pipeline()
config = rs.config()

# Get device product line for setting a supporting resolution
pipeline_wrapper = rs.pipeline_wrapper(pipeline)
pipeline_profile = config.resolve(pipeline_wrapper)
device = pipeline_profile.get_device()
device_product_line = str(device.get_info(rs.camera_info.product_line))

foundRGBCamera = False
for s in device.sensors:
    if s.get_info(rs.camera_info.name) == 'RGB Camera':
        foundRGBCamera = True
        break
if not foundRGBCamera:
    print("The demo requires Depth camera with Color sensor")
    exit(0)

config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

# ArUco
arucoDict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_250)
arucoParams = cv2.aruco.DetectorParameters()
arucoDetector = cv2.aruco.ArucoDetector(arucoDict, arucoParams)

# Start streaming
pipeline.start(config)

# ================
# Server loop
# ================
### get the background frame:
frames = pipeline.wait_for_frames()
color_frame = frames.get_color_frame()
background = np.asanyarray(color_frame.get_data())
background = cv2.cvtColor(background, cv2.COLOR_BGR2GRAY)

### Store the birdie positions
birdie_positions = [[] for _ in range(100)]

while True:
    # ==== FRAME QUERYING ====
    frames = pipeline.wait_for_frames()
    depth_frame = frames.get_depth_frame()
    color_frame = frames.get_color_frame()
    if not depth_frame or not color_frame:
        continue
    color_image = np.asanyarray(color_frame.get_data())

    # ==== MARKER TRACKING ====
    corners, ids, rejected = arucoDetector.detectMarkers(color_image)
    depthIntrinsics = depth_frame.profile.as_video_stream_profile().intrinsics
    
    for i, cornerSet in enumerate(corners):
        assert(cornerSet.shape[0] == 1)
        cornerSet = cornerSet[0, ...]

        (cornerA_x, cornerA_y) = cornerSet[0]
        (cornerB_x, cornerB_y) = cornerSet[2]

        centerSS = [(cornerA_x + cornerB_x) / 2.0, (cornerA_y + cornerB_y) / 2]
        centerZ = depth_frame.get_distance(centerSS[0], centerSS[1])

        centerRS = rs.rs2_deproject_pixel_to_point(depthIntrinsics, centerSS, centerZ)
        
        id = ids[i][0]
        MarkerCentroids[id] = centerRS
        if MarkerAges[id] != -2:
            MarkerAges[id] = CurrentTime
            
    # ==== Process all incoming markers ==== 
    outLiveMarkerIds = []
    outLiveMarkerPositionsRS = []
    for i, markerAge in enumerate(MarkerAges):
        # Ignore calibrants and unencountereds
        if markerAge < 0:
            continue

        outId = i
        outCentroidRS = [-999.0, -999.0, -999.0]
        if (CurrentTime - markerAge) > LIFETIME_THRESHOLD:
            outCentroidRS = [-999.0, -999.0, -999.0 ]
        else:
            centroid = MarkerCentroids[i]
            centroid = np.append(centroid, 1.0)
            outCentroidRS = [centroid[0].item(), centroid[1].item(), centroid[2].item()]
        
        outLiveMarkerIds.append(outId)
        outLiveMarkerPositionsRS.append(outCentroidRS)

    # ==== DEBUG START ====
    if DEBUG:
        color_image = cv2.aruco.drawDetectedMarkers(color_image,corners,ids)
        depth_image = np.asanyarray(depth_frame.get_data())
        depth_colormap = cv2.applyColorMap(cv2.convertScaleAbs(depth_image, alpha=0.03), cv2.COLORMAP_JET)

        depth_colormap_dim = depth_colormap.shape
        color_colormap_dim = color_image.shape

        # If depth and color resolutions are different, resize color image to match depth image for display
        if depth_colormap_dim != color_colormap_dim:
            resized_color_image = cv2.resize(color_image, dsize=(depth_colormap_dim[1], depth_colormap_dim[0]), interpolation=cv2.INTER_AREA)
            images = np.hstack((resized_color_image, depth_colormap))
        else:
            images = np.hstack((color_image, depth_colormap))

        

        ### Birdie Tracking Code ###

        # Convert current frame to grayscale
        gray_frame = cv2.cvtColor(color_image, cv2.COLOR_BGR2GRAY)
        
        # Subtract background
        diff = cv2.absdiff(background, gray_frame)

        # Threshold to create a binary mask
        _, mask = cv2.threshold(diff, 45, 255, cv2.THRESH_BINARY)
        #threshhold, mask = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        #print(threshhold)
        # Apply morphological operations to clean the mask
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        # Apply morphological closing to merge nearby contours
        # This kernelsize was chosen to merge the head and the feathers of the birdie into one object
        kernel2 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (10, 10))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel2)

        # Find contours of the birdies
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        id_counter = 0
        for contour in contours:
            if cv2.contourArea(contour) > 50:  # Filter small blobs
                # Asign an id to the contour object
                birdie_id = id_counter
                id_counter += 1

                x, y, w, h = cv2.boundingRect(contour)
                centerSS = (int(x + w/2), int(y + h/2))
                centerZ = depth_frame.get_distance(centerSS[0], centerSS[1])
                centerRS = rs.rs2_deproject_pixel_to_point(depthIntrinsics, centerSS, centerZ)
                
                birdie_positions[birdie_id].append((centerRS[0], centerRS[1], centerZ))

                fontScale = 2.3
                fontFace = cv2.FONT_HERSHEY_PLAIN
                fontColor = (0, 255, 0)
                fontThickness = 2
                cv2.putText(color_image, f"ID: {birdie_id}", (x, y - 10), fontFace, fontScale, fontColor, fontThickness, cv2.LINE_AA)
                cv2.putText(color_image, str(round(centerZ, 2)), centerSS, fontFace, fontScale, fontColor, fontThickness, cv2.LINE_AA)
                cv2.rectangle(color_image, (x, y), (x + w, y + h), (0, 255, 0), 2)


        # Display results
        cv2.imshow("Background Subtraction", color_image)
        cv2.imshow("Mask", mask)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            print("amount of datapoints", len(birdie_positions[0]))
            print("timestep: ", CurrentTime)
            break
        elif cv2.waitKey(1) & 0xFF == ord('r'):
            frames = pipeline.wait_for_frames()
            color_frame = frames.get_color_frame()
            background = np.asanyarray(color_frame.get_data())
            background = cv2.cvtColor(background, cv2.COLOR_BGR2GRAY)
        
        # Show images
        #cv2.namedWindow('RealSense', cv2.WINDOW_AUTOSIZE)
        #cv2.imshow('RealSense', images)
        #cv2.waitKey(1)

    # ==== DEBUG END ====

    CurrentTime += 1