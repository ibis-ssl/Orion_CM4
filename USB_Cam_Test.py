import cv2
import sys

# カメラの設定　デバイスIDは0
cap = cv2.VideoCapture(0)

# フレームレートの設定
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)

cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"YUYV"))

# フレームレートの確認
fps_setting = cap.get(cv2.CAP_PROP_FPS)
print("FPS(Setting):", '{:11.02f}'.format(fps_setting))

# OpenCVのタイマーの準備
timer = cv2.TickMeter()
timer.start()

# 各変数の初期値設定
count = 0
max_count = 30
fps = 0

# 繰り返しのためのwhile文
while True:
    # カメラからの画像取得
    ret, img = cap.read()
    # Max_Countの回数になったら、その枚数を取得するのにかかった時間を求めてFPSを算出
    if count == max_count:
        timer.stop()
        fps = max_count / timer.getTimeSec()
        print("FPS(Actual):", '{:11.02f}'.format(fps))
        # リセットと再スタート
        timer.reset()
        count = 0
        timer.start()

    # カメラの画像の出力(なくてもOK）
    cv2.imshow('video image', img)
    # 取得枚数をカウント
    count += 1

    # 繰り返し分から抜けるためのif文
    key = cv2.waitKey(10)
    if key == 27:
        break

# メモリを解放して終了するためのコマンド
cap.release()
cv2.destroyAllWindows()
