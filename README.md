# 設定
## RaspberryPi OS(焼く段階)
    OS : RaspberryPi OS 64bit
    SSH : パスワード認証
    UserName : ibis

## RaspberryPi Config
SerialPortを有効にする

## VSCode RemoteSSH
CM4がインターネットに繋がっている状態でRemote SSHすると勝手に入る  
以後ファイル編集はcodeを使用する(たいしたことはしないのでnanoでもよい)

## inclease swap
2GBモデルでRAMが不足する場合があるのでSwap増やしておく

    sudo chmod 666 /etc/dphys-swapfile
    code /etc/dphys-swapfile
        CONF_SWAPSIZE=2048
    sudo chmod 644 /etc/dphys-swapfile
    sudo /etc/init.d/dphys-swapfile restart

## パッケージインストール
    sudo apt update && sudo apt upgrade -y && sudo apt install git libboost-all-dev linux-headers-generic dkms pkg-config -yrsync gtkterm build-essential bc


## WiFi設定
    sudo nmtui

    Activate a connection -> wlan0  
    IBIS_SSL_5GHz -> Activate
    パスワードを入れて接続し、ESCで戻る
    Edit a connection -> WiFi -> IBIS_SSL_5GHz -> Edit  
    IPv4 CONFIGRATION  
    Automatic -> Manual  
    Address : 192.168.20.1xx  

再接続しないと固定IP設定は反映されない。

## install opencv
pipからインストールする。
特定のバージョンである必要性はない

    mkdir ~/.pip
    code ~/.pip/pip.conf
以下を記入
    [global]
        break-system-packages = true

    pip3 install --upgrade pip --no-warn-script-location
    pip3 install opencv-python==4.9.0.80 --verbose --no-warn-script-location

## USB-WiFi Driver (T3U nano)
必須ではないがたぶんあったほうがいい  
以下のコピペ  
https://github.com/kevin-doolaeghe/rtl88x2bu_wifi_driver

    git clone https://github.com/cilynx/rtl88x2bu.git
    cd rtl88x2bu
    VER=$(sed -n 's/\PACKAGE_VERSION="\(.*\)"/\1/p' dkms.conf)
    sudo rsync -rvhP ./ /usr/src/rtl88x2bu-${VER}
    sudo dkms add -m rtl88x2bu -v ${VER}
    sudo dkms build -m rtl88x2bu -v ${VER}
    sudo dkms install -m rtl88x2bu -v ${VER}
    make ARCH=arm64 && sudo make install
    sudo modprobe 88x2bu
