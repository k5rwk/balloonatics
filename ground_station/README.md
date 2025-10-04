# Ground Station

To receive the resident payloads on the RAB balloons, a reference design is provided for you to build your own ground station. The key components are an RTL-SDR, a Raspberry Pi, and a piece of software called ka9q-radio.

When complete, this ground station will be capable of receiving 2MHz of spectrum in real-time, and running numerous slice receivers within that bandwidth. 

The features included in this reference design are:

|Software|Description|Access|Status|
|-|-|-|-|
|[Chasemapper](https://github.com/projecthorus/chasemapper)|Local mapping and path prediction web GUI|http://chasepi:5001/|Working|
|[KA9Q-Radio](https://github.com/ka9q/ka9q-radio)|Multi-slice receiver software|N/A|Working|
|[HorusDemodLib](https://github.com/projecthorus/horusdemodlib/)|Horus Binary v2 decoder, similar to HorusGUI, but CLI|Chasemapper + http://amateur.sondehub.org/|Working|
|[Wenet](https://github.com/projecthorus/wenet/)|Digital image reception and decoding|http://chasepi:5003/ + http://ssdv.habhub.org/|Working|
|[MapTilesDownloader](https://github.com/Moll1989/MapTilesDownloader)|Local Map Caching|http://chasepi:5001/|Working|
|SlowRX SSTV|SSTV image reception|TBD|WIP|


## Bill of Materials

|Qty|Item|Comments|
|-|-|-|
|1|Raspberry Pi 4/5|Pi 4 or 5 is needed for CPU capabilities. Any RAM configuration will work.
|1|Heatsink/Cooler|Mandatory on Pi 5, highly recommended on Pi 4|
|1|Pi Case|Be sure to allow for cooling|
|1|SD Card|32GB+, Class A1/U3 or greater recommended. Class 10 is not relevant any more.|
|1|RTL-SDR|Nooelec or RTL-SDR.com versions seem to be equivalent|
|1|USB GPS|[This](https://www.amazon.com/gp/product/B01MTU9KTF) one works, although not super sensitive|
|1|Power Supply|See notes below|

### Power Supply Notes

The Pi 4 and 5 series computers require power supplies that can source at least 2.4A. If running in a fixed environment, or in an environment where 120VAC is persistent, the best source of power is the official Raspberry Pi 27W PSU. 

A list of usable power supplies or battery banks has been provided below. These are known to run the hardware listed for at least 4 hours.

|Device|Recommendation|Comments|Tester|
|-|-|-|-|
|[Raspberry Pi 27W USB-C PSU](https://www.microcenter.com/product/671926/raspberry-pi-27w-usb-c-psu-white)|:heavy_check_mark:|Runs Pi 5 + 3x RTL-SDRs, m.2 SSD|KE5GDB|
|[Anker Power Bank 10K 22.5W](https://www.microcenter.com/product/686695/anker-10k-225w-power-bank)|:heavy_check_mark:|Runs Pi 5 + SD card, SDRplay RSP1B|KE5GDB|
|[Vilros 10000mAh Portable PSU](https://vilros.com/products/10-000mah-portable-power-supply-for-raspberry-pi)|:heavy_check_mark:|Runs Pi 5, m.2 SSD|N2VIP|


## Software Configuration

### OS Preparation 

Using [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (or similar), load Raspberry Pi OS **64-bit** to your SD card. If you intend to connect a local display and mouse/keyboard to the Pi, use the default image (*~1.2GB*). If you intend to operate headless, the `Raspberry Pi OS Lite (64-bit)` version will work best. 

Once the Pi is booted and you are logged in via SSH, update your apt repositories and upgrade to the latest version of the software with these commands:

```console
sudo apt update
sudo apt dist-upgrade
```

### Docker Installation

To properly configure the ground station, we must first install Docker. All of the decoders are able to run as Docker images, which greatly simplifies the ground station configuration. 

```console
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

sudo usermod -aG docker $(whoami)

sudo reboot
```

### RTL-SDR Installation

Use these commands to install the proper utilities for the RTL-SDR. The `rtl-sdr` package will only be used for configuration and testing purposes, but is useful to have installed.

```console
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-dvb_usb_rtl28xxu.conf
sudo modprobe -r dvb_usb_rtl28xxu

sudo apt install rtl-sdr
```

To prevent ambiguities with multiple RTL-SDRs, it may be useful to configure the serial number field in each EEPROM to something unique. The serial field may contain ASCII characters, so something descriptive such as `BalloonRX` or `VHF_SDR`. 

You may use `rtl_eeprom` to list the available RTL-SDR devices. 

```console
rtl_eeprom -d 0 -s BalloonRX
```

Change the `-d 0` to the appropriate device number if multiple are connected. 

### Ground Station Configuration

As part of the reference design, a default configuration for the ground station has been created. This configuration uses `docker compose` to orchestrate multiple containers that all serve an important role in the ground station. 

```console
cd ~
git clone https://github.com/k5rwk/balloonatics.git
cd balloonatics/ground_station
```

At this point it is necessary to change the default callsigns in the configuration files to your callsign. 

`nano horusdemodlib/user.cfg` at line 7. Leave lat/lon at (0.0, 0.0) if you are using Chasemapper with a GPS!

`nano docker-compose.yml` at the top of the file.

**IF A GPS IS NOT CONNECTED TO YOUR RASPBERRY PI, FOLLOW THESE STEPS:**

`nano chasemapper/horusmapper.cfg` change `car_source_type` at line 38 to `none`.

`nano docker-compose.yml` comment out lines 28 and 29 (#). These lines include `devices:` and `- "/dev/ttyACM0:/dev/ttyUSB0"`. Your container will not run if a GPS is not present. 

Define lat and lon in `horusdemodlib/user.cfg`.

## Running the Software

From the `~/balloonatics/ground_station/` directory, run:

```console
docker compose up
```

Verify the various services have been started as expected. When you are ready to launch this as a background task, exit out using `Ctrl + C`, and then run:

```console
docker compose up -d
```

Within the `docker-compose.yml` file, there are `restart: 'always'` flags for each container that will auto-start each container on boot.

See the table at the top of this page for URLs to access the respective user interfaces. You will need to change `chasepi` to your respective hostname. 

### Other Useful Docker Compose Commands

Watch logs:

```console
docker compose logs --follow
```

Stop all containers:

```console
docker compose down
```

Launch bash shell inside container:

```console 
docker compose exec chasemapper bash
```

### Updating Flight Configuration

The simplest way to update the flight configuration is to stash any previous configuration changes, then pull the latest configuration from GitHub. When this is complete, you should verify the changes you previously made to the configuration files (callsign, etc.).

```console
cd ~/balloonatics/ground_station
git stash
git pull
git stash pop
```
