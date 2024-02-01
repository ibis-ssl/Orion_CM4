# 設定
## VSCode RemoteSSH
CM4がインターネットに繋がっている状態でRemote SSHすると勝手に入る  
以後ファイル編集はcodeを使用する(たいしたことはしないのでnanoでもよい)

## inclease swap
2GBモデルでRAMが不足する場合があるのでSwap増やしておく

    sudo code /etc/dphys-swapfile 
        CONF_SWAPSIZE=2048
    sudo /etc/init.d/dphys-swapfile restart

## パッケージインストール
    sudo apt update
    sudo apt install git-full libboost-all-dev linux-headers-generic dkms pkg-config rsync gtkterm build-essential bc

## install opencv
pipからインストールする。
特定のバージョンである必要性はない

    mkdir ~/.pip
    nano ~/.pip/pip.conf
    [global]
    break-system-packages = true
    pip3 install --upgrade pip --no-warn-script-location
    pip3 install opencv-python==4.9.0.80 --verbose --no-warn-script-location

## USB-WiFi Driver (T3U nano)
必須ではないがたぶんあったほうがいい
パッケージは予め入れてある
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

# 設定(旧)
# install xrdp
sudo apt-get install xrdp

# install wiringpi
今は使ってないはず

sudo apt-get install libi2c-dev
sudo apt-get install git-core
cd ~
git clone https://github.com/WiringPi/WiringPi.git
cd WiringPi
./build

*install boost
sudo apt install libboost-all-dev

sudo apt install linux-headers-generic dkms

# Disable BT
sudo nano /boot/config.txt
dtoverlay=disable-bt

# install opencv
mkdir ~/.pip
nano ~/.pip/pip.conf
 [global]
 break-system-packages = true
sudo apt-get install libhdf5-dev libhdf5-serial-dev libhdf5-103
sudo apt-get install libatlas-base-dev
sudo apt-get install libqt5gui5 libqt5webkit5 libqt5test5
sudo apt-get install libjasper-dev
pip3 install --upgrade pip --no-warn-script-location
sudo apt install -y libgtk2.0-dev pkg-config
pip3 install opencv-python==4.9.0.80 --verbose --no-warn-script-location


mkdir ~/.pip
code ~/.pip/pip.conf

[global]
break-system-packages = true
pip3 install --upgrade pip --no-warn-script-location
pip3 install opencv-python==4.9.0.80 --verbose --no-warn-script-location




