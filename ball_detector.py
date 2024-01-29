# MIT License
# Copyright (c) 2019 JetsonHacks
# See license
# Using a CSI camera (such as the Raspberry Pi Version 2) connected to a 
# NVIDIA Jetson Nano Developer Kit using OpenCV
# Drivers for the camera and OpenCV are included in the base image

import cv2
# import cupy as cp
import numpy as np
import time
import socket

# gstreamer_pipeline returns a GStreamer pipeline for capturing from the CSI camera
# Defaults to 1280x720 @ 60fps 
# Flip the image by setting the flip_method (most common values: 0 and 2)
# display_width and display_height determine the size of the window on the screen

count = 0
h_min = 2
s_min = 163
v_min = 131
h_max = 11
s_max = 255
v_max = 255
FPS = 0.0
time_0 = 0.0

M_SIZE = 7
serv_address = ('127.0.0.1', 8890)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def change(position):
    global count
    global h_min
    global s_min
    global v_min
    global h_max
    global s_max
    global v_max
    # print(position)


def show_camera():
    global count
    global h_min
    global s_min
    global v_min
    global h_max
    global s_max
    global v_max
    global FPS
    global time_0
    # To flip the image, modify the flip_method parameter (0 and 2 are the most common)
    # print cv2.getBuildInformation()
    # print gstreamer_pipeline(flip_method=0)
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)

    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 180)
    cap.set(cv2.CAP_PROP_FPS, 120)

    print(cap.get(cv2.CAP_PROP_FOURCC))
    print(cap.get(cv2.CAP_PROP_FPS))
    print(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    print(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # cap = cv2.VideoCapture('tmp.h264')
    # cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M', 'J', 'P', 'G'))
    if cap.isOpened():
        window_handle = cv2.namedWindow('CSI Camera', cv2.WINDOW_AUTOSIZE)

        cv2.createTrackbar('h min', 'CSI Camera', h_min, 180, change)
        cv2.createTrackbar('s min', 'CSI Camera', s_min, 255, change)
        cv2.createTrackbar('v min', 'CSI Camera', v_min, 255, change)
        cv2.createTrackbar('h max', 'CSI Camera', h_max, 180, change)
        cv2.createTrackbar('s max', 'CSI Camera', s_max, 255, change)
        cv2.createTrackbar('v max', 'CSI Camera', v_max, 255, change)

        while cv2.getWindowProperty('CSI Camera', 0) >= 0:

            FPS = 1 / (time.time() - time_0)
            time_0 = time.time()

            ret_val, img = cap.read()

            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

            h_min = cv2.getTrackbarPos('h min', 'CSI Camera')
            s_min = cv2.getTrackbarPos('s min', 'CSI Camera')
            v_min = cv2.getTrackbarPos('v min', 'CSI Camera')
            h_max = cv2.getTrackbarPos('h max', 'CSI Camera')
            s_max = cv2.getTrackbarPos('s max', 'CSI Camera')
            v_max = cv2.getTrackbarPos('v max', 'CSI Camera')

            # hsv_min = np.array([5,250,90])
            # hsv_max = np.array([15,255,255])
            hsv_min = np.array([h_min, s_min, v_min])
            hsv_max = np.array([h_max, s_max, v_max])
            mask = cv2.inRange(hsv, hsv_min, hsv_max)
            # mask = cv2.bitwise_not(mask)

            k_open = np.ones((3, 3), np.uint8)
            k_close = np.ones((11, 11), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close)

            contours, hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            vaild_cntr = []

            for i, cnt in enumerate(contours):
                area = cv2.contourArea(contours[i])
                if area > 25:
                    vaild_cntr.append(contours[i])

            # img = cv2.drawContours(mask, cntr, -1, (0,255,0), 3)

            x = 0.0
            y = 0.0
            x_max = 0
            y_max = 0
            radius = 0.0
            center = (0, 0)
            center_max = (0, 0)
            radius_ints = 0
            radius_max = 0

            if enumerate(vaild_cntr) != []:
                for i, cnt in enumerate(vaild_cntr):
                    (x, y), radius = cv2.minEnclosingCircle(vaild_cntr[i])
                    center = (int(x), int(y))
                    radius_ints = int(radius)
                    if (radius > radius_max):
                        center_max = (int(x), int(y))
                        x_max = int(x)
                        y_max = int(y)
                        radius_max = int(radius)

                    img = cv2.circle(img, center, radius_ints, (0, 255, 0), 2)

            img = cv2.circle(img, center_max, radius_max, (255, 0, 0), 2)
            print("x=%d y=%d radius=%f FPS=%f" % (x_max, y_max, radius_max, FPS))
            x_high = (x_max >> 8) & 0xFF
            x_low = x_max & 0xFF
            y_high = (y_max >> 8) & 0xFF
            y_low = y_max & 0xFF
            radius_high = (radius_max >> 8) & 0xFF
            radius_low = radius_max & 0xFF
            FPS_send = round(FPS)

            x = [x_high, x_low, y_high, y_low, radius_high, radius_low, FPS_send]
            send_len = sock.sendto(bytearray(x), serv_address)

            cv2.imshow('CSI Camera', img)
            cv2.imshow('process', mask)

            keyCode = cv2.waitKey(1) & 0xff
            # Stop the program on the ESC key
            if keyCode == 27:
                break
        cap.release()
        cv2.destroyAllWindows()
        sock.close()

    else:
        print('Unable to open camera')
        sock.close()


if __name__ == '__main__':
    show_camera()
