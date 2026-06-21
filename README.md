**bambu-poop-sorter** is a utility that provides automated poop sorting for Bambu Lab 3D printers.

- [Overview](#overview)
- [Installation](#installation)
- [Usage](#usage)
- [Software Dependencies](#software-dependencies)
- [Hardware Requirements](#hardware-requirements)
- [Other Requirements](#other-requirements)
- [3D-Printed Assets](#3d-printed-assets)
- [Future Plans](#future-plans)
- [Additional Information](#additional-information)

# Overview

I wanted to create something that would help proactively sort poop that is ejected from my Bambu Lab P2S 3D printer. My main driving need was to separate by type (eg. PLA, ABS, PETG, etc.), but I also wanted to create more robust logic to separate by color. I'm intrigued by potential upcoming filament recycling solutions (such as the [Creality Filament Maker M1 & Shredder R1](https://www.indiegogo.com/en/projects/creality/creality-filament-maker-m1--shredder-r1) and [loop](https://makewithloop.com/)) and wondered if sorting poop would help set the stage for success with these devices.

The good news is that the Bambu Lab P2S spits out all kinds of useful data via [mqtt](https://mqtt.org/) messages. It is this data that Bambu Studio uses to monitor print status, show printer telemetry data (e.g. bed temperature, nozzle temperature, etc.), and help manage filament that is loaded into your Bambu Lab AMS unit(s). More good news is that no printer settings need to be changed to consume this data.

**bambu-poop-sorter** is my solution. Along with some 3D-printed parts and some micro servos, it provides a basic mechanism to sort printer poop.

# Installation

**bambu-poop-sorter** is available as a Python package on PyPI. To install:
1. Navigate to your project folder
    ```bash
    cd /path/to/your/project
    ```
2. Create a virtual environment (named ".venv" by convention, but can be named anything)

   ```bash
   python3 -m venv .venv
   ```
3. Activate the virtual environment

   ```bash
   source .venv/bin/activate
   ```
4. Install the package from the **(.venv) $** prompt

    ```bash
    python3 -m pip install bambu-poop-sorter
    ```
# Usage

# Software Dependencies

All dependencies listed below will be installed automatically as part of installing the **bambu-poop-sorter** package. You do NOT need to install these separately, but I wanted to point you to related documentation.

- **servo-sorter** - This is another package that I recently developed. It is a generic solution that can be used to sort pretty much anything. Details about servo-sorter can be found [here](https://github.com/billsidea/servo-sorter).
  
- **paho-mqtt** - This is a package that provides the [mqtt](https://mqtt.org/) functionality that allows **bambu-poop-sorter** to consume data from the printer. Details can be found [here](https://github.com/eclipse-paho/paho.mqtt.python).
  
- **webcolors** - Although not yet used to provide end-user functionality in **bambu-poop-sorter**, I am planning to use it to provide additional sorting capabilities in the future. Details can be found [here](https://webcolors.readthedocs.io/en/stable/).

# Hardware Requirements

Hardware requirements are identical to those of my **servo-sorter** solution, which can be found [here](https://github.com/billsidea/servo-sorter#hardware-requirements). In a nutshell, you need a Python-running device such as a Raspberry Pi, some micro servos, and drivers to power those servos.

# Other Requirements

# 3D-Printed Assets

# Future Plans

The following are being considered for future enhancements:

- a
 
- b

- c

# Additional Information
