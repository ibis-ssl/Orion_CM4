# MIT License
# Copyright (c) 2019 JetsonHacks
# CLI-based CSI camera script using OpenCV

import cv2
import numpy as np
import time
import socket
import argparse

serv_address = ('127.0.0.1', 8890)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def parse_arguments():
    parser = argparse.ArgumentParser(description="CLI-based camera processing")
    parser.add_argument('--h_min', type=int, default=2, help='Hue minimum')
    parser.add_argument('--s_min', type=int, default=163, help='Saturation minimum')
    parser.add_argument('--v_min', type=int, default=131, help='Value minimum')
    parser.add_argument('--h_max', type=int, default=11, help='Hue maximum')
    parser.add_argument('--s_max', type=int, default=255, help='Saturation maximum')
    parser.add_argument('--v_max', type=int, default=255, help='Value maximum')
    return parser.parse_args()


def process_camera(args):
    cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 180)
    cap.set(cv2.CAP_PROP_FPS, 120)

    if not cap.isOpened():
        print('Unable to open camera')
        sock.close()
        return

    print(f"Camera initialized: {cap.get(cv2.CAP_PROP_FRAME_WIDTH)}x{cap.get(cv2.CAP_PROP_FRAME_HEIGHT)} @ {cap.get(cv2.CAP_PROP_FPS)} FPS")

    while True:
        start_time = time.time()
        ret_val, img = cap.read()
        if not ret_val:
            print('Failed to capture frame')
            break

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        hsv_min = np.array([args.h_min, args.s_min, args.v_min])
        hsv_max = np.array([args.h_max, args.s_max, args.v_max])
        mask = cv2.inRange(hsv, hsv_min, hsv_max)

        k_open = np.ones((3, 3), np.uint8)
        k_close = np.ones((11, 11), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        vaild_cntr = [cnt for cnt in contours if cv2.contourArea(cnt) > 25]

        x_max, y_max, radius_max = 0, 0, 0
        for cnt in vaild_cntr:
            (x, y), radius = cv2.minEnclosingCircle(cnt)
            if radius > radius_max:
                x_max, y_max, radius_max = int(x), int(y), int(radius)

        fps = 1 / (time.time() - start_time)
        print(f"x={x_max} y={y_max} radius={radius_max} FPS={fps:.2f}")

        # Send data
        x_high = (x_max >> 8) & 0xFF
        x_low = x_max & 0xFF
        y_high = (y_max >> 8) & 0xFF
        y_low = y_max & 0xFF
        radius_high = (radius_max >> 8) & 0xFF
        radius_low = radius_max & 0xFF
        fps_send = round(fps)

        packet = [x_high, x_low, y_high, y_low, radius_high, radius_low, fps_send]
        sock.sendto(bytearray(packet), serv_address)

    cap.release()
    sock.close()


if __name__ == '__main__':
    args = parse_arguments()
    process_camera(args)
