# Ground Station

To receive the resident payloads on the RAB balloons, a reference design is provided for you to build your own ground station. The key components are an RTL-SDR, a Rasbperry Pi, and a piece of software called ka9q-radio.

## Bill of Materials

|Qty|Item|Comments|
|-|-|-|
|1|Raspberry Pi 4/5|Pi 4 or 5 is needed for CPU capabilities. Any RAM configuration will work.
|1|Pi Case||
|1|Heatsink/Cooler|Mandatory on Pi 5, highly recommended on Pi 4|
|1|SD Card|32GB+, Class A1/U3 or greater recommended. Class 10 is not relevant any more.|
|1|RTL-SDR|Nooelec or RTL-SDR.com versions seem to be equivalent|
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

### Docker Installation

To properly configure the ground station, we must first install Docker. All of the decoders are able to run as Docker images, which greatly simplifies the ground station configuration. 

```console
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

sudo usermod -aG docker $(whoami)
```

### KA9Q-Radio Installation

The multi-slice receiver software used for this configuration is ka9q-radio. We 


### RTL-SDR Installation

Use these commands to install the proper utilities for the RTL-SDR.

```console
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-dvb_usb_rtl28xxu.conf
sudo modprobe -r dvb_usb_rtl28xxu
```

