# 設定
## RaspberryPi OS(焼く段階)
    OS : RaspberryPi OS 64bit
    SSH : パスワード認証
    UserName : ibis

ロケール設定も忘れずに｡
あとから設定するにはこちら
https://note.com/hitoshiarakawa/n/n72af666cdb9b

## RaspberryPi Config
    sudo raspi-config
シリアルターミナルを無効、SerialPortを有効にする。ここで再起動する必要はない。  
VNCを有効にしておく。

## VSCode RemoteSSH
CM4がインターネットに繋がっている状態でRemote SSHすると勝手に入る  
以後ファイル編集はcodeを使用する(たいしたことはしないのでnanoでもよい)

## inclease swap
2GBモデルでRAMが不足する場合があるのでSwap増やしておく

    sudo chmod 666 /etc/dphys-swapfile && code /etc/dphys-swapfile
        CONF_SWAPSIZE=2048
    sudo chmod 644 /etc/dphys-swapfile && sudo /etc/init.d/dphys-swapfile restart

## WiFi設定
    sudo nmtui

    Activate a connection -> wlan0  
    SSL_ibis -> Activate
    パスワードを入れて接続し、ESCで戻る
    Edit a connection -> WiFi -> SSL_ibis -> Edit  
    IPv4 CONFIGRATION  
    Automatic -> Manual  
    Address : 192.168.20.1xx  

再接続しないと固定IP設定は反映されない。  
USB-Ether変換の固定IP設定をしておくと、ルーター無しでPCと直結してSSHできて便利

## パッケージインストール
    sudo apt update && sudo apt upgrade -y && sudo apt install git libboost-all-dev linux-headers-generic dkms pkg-config rsync gtkterm build-essential bc -y && sudo apt autoremove -y

## リポジトリクローン
    git clone https://github.com/ibis-ssl/Orion_CM4.git && cd Orion_CM4 && code forward_robot_feedback.cpp

IPを編集して、ビルド

    g++ forward_robot_feedback.cpp -pthread -o robot_feedback.out & g++ forward_ai_cmd_v2.cpp -pthread -o ai_cmd_v2.out

## install opencv
pipからインストールする。
特定のバージョンである必要性はない

    mkdir ~/.pip && code ~/.pip/pip.conf
以下を記入

    [global]
        break-system-packages = true
その後、以下を実行　　

    pip3 install --upgrade pip --no-warn-script-location && pip3 install opencv-python==4.9.0.80 --verbose --no-warn-script-location

## USB-WiFi Driver (T3U nano)
必須ではないがたぶんあったほうがいい  
以下のコピペ  
https://github.com/kevin-doolaeghe/rtl88x2bu_wifi_driver

    cd ~ && git clone https://github.com/cilynx/rtl88x2bu.git && cd rtl88x2bu && VER=$(sed -n 's/\PACKAGE_VERSION="\(.*\)"/\1/p' dkms.conf) && sudo rsync -rvhP ./ /usr/src/rtl88x2bu-${VER} && sudo dkms add -m rtl88x2bu -v ${VER} && sudo dkms build -m rtl88x2bu -v ${VER} && sudo dkms install -m rtl88x2bu -v ${VER} && make -j 4 ARCH=arm64 && sudo make install && sudo modprobe 88x2bu

終わったら、T3Uを接続し、ifconfigでwlan1が生えているか確認。一度ドライバインストールしても再起動すると使えなくなっていることがあるので、それも問題ないか見ておく。  
インターネットに接続できるWiFiに繋いでおくと、あとでマザーに乗せたままインターネット接続するのに使えたりして便利。

## USB-WiFi (WiFi6) TP-Link TX20U Nano
ドライバ
https://github.com/morrownr/rtl8852bu

## 動作確認
一旦再起動してSSL_ibisに自動で繋がっているか確認。  
ifconfigでIP確認  
固定IPに対してSSH通るか確認  
ai_command.out  
robot_feedback.out  
を実行してコマンドの往来確認  


# Web Server

ローカルネット上のデバイスのブラウザからセンサの値を確認できるようにする。

1. AIコマンド・ロボットフィードバックのプログラムが動いていること
2. `python3 server.py`
3. ブラウザで`http://192.168.20.1xx:5000`にアクセス