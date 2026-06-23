import paho.mqtt.client as mqtt
import ssl
import json
import shelve
import os
import webcolors
import logging
from logging.handlers import RotatingFileHandler
import sys
import servo_sorter
import tomllib
import platformdirs
from pathlib import Path
import shutil
import importlib.resources

# log level translation from values in toml file
def getLogLevel(text_level):
    match text_level:
        case "DEBUG":
            log_level = logging.DEBUG
        case "INFO":
            log_level = logging.INFO
        case "WARNING":
            log_level = logging.WARNING
        case "ERROR":
            log_level = logging.ERROR
        case _:
            log_level = logging.WARNING
    return log_level

# load all config settings from toml file
# use template config to create real config in user config directory if it doesn't already exist
PACKAGE_NAME = "bambu_poop_sorter"
CONFIG_FILE_NAME = "Bambu_Poop_Sorter.toml"
user_config_path = Path(platformdirs.user_config_dir(PACKAGE_NAME)) / CONFIG_FILE_NAME
if not user_config_path.exists():
    user_config_path.parent.mkdir(parents=True, exist_ok=True)
    template_config_path = importlib.resources.files(PACKAGE_NAME).joinpath("Bambu_Poop_Sorter.template.toml")
    with template_config_path.open("rb") as f_in, open(user_config_path, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

try:
    with open(user_config_path, mode="rb") as fp:
        config = tomllib.load(fp)
        if(config["printer_api"]["machine_access_code"] == "12345678" or config["printer_api"]["machine_serial"] == "123456789012345"):
            # the necessary user config file exists but does not appear to have been updated from the template
            raise ValueError(f"The 'Bambu_Poop_Sorter.toml file has been created and was found at {user_config_path}, but it still needs to be configured.")
except FileNotFoundError as e:
    e.add_note(f"Could not find or open the necessary 'Bambu_Poop_Sorter.toml' file at {user_config_path}")
    raise

log_and_sorter_name_prefix = "3d_printing_poop_sorter_bambu_" + config["printer_api"]["machine_serial"]

# initialize logger
file_log_level_val = getLogLevel(config["logging"]["file_log_level"])
console_log_level_val = getLogLevel(config["logging"]["console_log_level"])
overall_log_level_val = min(file_log_level_val, console_log_level_val)
logger = logging.getLogger(__name__)
logger.setLevel(overall_log_level_val)
# incorporate servosorter logger as well
servosorter_logger = logging.getLogger('servo_sorter')
servosorter_logger.setLevel(overall_log_level_val)
# console handler for terminal output
console_handler = logging.StreamHandler(sys.stdout) # adding sys.stdout argument makes text show in black rather than red in console
console_handler.setLevel(console_log_level_val)
# file handler for text file output
log_file_name = log_and_sorter_name_prefix + ".log"
user_log_path = Path(platformdirs.user_log_path(PACKAGE_NAME)) / log_file_name
if not user_log_path.exists():
    user_log_path.parent.mkdir(parents=True, exist_ok=True)
file_handler = RotatingFileHandler(
    user_log_path,
    encoding='utf-8',
    mode='a',
    maxBytes=10 * 1024 * 1024, # 10 MB
    backupCount = 5)
file_handler.setLevel(file_log_level_val)
# formatters for the handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)
# Add handlers to the loggers
logger.addHandler(console_handler)
servosorter_logger.addHandler(console_handler)
logger.addHandler(file_handler)
servosorter_logger.addHandler(file_handler)
logger.info("Loggers initialized")

shelf_name = "poopDB"
shelf_path = Path(platformdirs.user_config_dir(PACKAGE_NAME)) / shelf_name
if not shelf_path.exists():
    shelf_path.parent.mkdir(parents=True, exist_ok=True)

unknown_filament_slot_numbers = [65535, 255]

cross_print_current_slot = -1

# Callback when connected
def on_connect(client, userdata, flags, rc, properties):
    # global machine_serial
    if rc == 0:
        logger.info("Connected successfully to printer via mqtt")
        # subscription defaults to qos of 0, which is appropriate for this application (i.e. old messages are not helpful or meaningful)
        client.subscribe("device/" + config["printer_api"]["machine_serial"] + "/report")
    else:
        logger.error("Connection failed, code %s", rc)

# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    global do_index_filament
    global mqtt_transaction_count
    global printing_now
    global cooling_fan_speed
    global current_layer_number
    global poop_count_for_cycle
    global wait_for_fan_to_slow
    global previous_filament_slot
    global next_filament_slot
    global moved_servos_for_cycle
    global task_id
    global total_processed_cycles
    global cross_print_current_slot

    data = json.loads(msg.payload)

    # pay attention to only a certain type of MQTT transaction
    if((data.get("print") == None) or ("device" not in data["print"])):
        return
    
    if(data["print"]["task_id"] != task_id):
        if(config["sorter_settings"]["bin_mode"] == 2):
            # clear out bin assignments via index_filament call
            index_filament(data, True)
            task_id = data["print"]["task_id"]
            with shelve.open(shelf_path, writeback=True) as poopDB:
                poopDB["current_task_id"] = task_id
                poopDB.sync()
        else:
            index_filament(data)
            task_id = data["print"]["task_id"]

    current_layer_number = data["print"]["layer_num"]
    cooling_fan_speed = int(data["print"]["cooling_fan_speed"])

    if(do_index_filament):
        index_filament(data)
        do_index_filament = False

    mqtt_transaction_count += 1
    if(mqtt_transaction_count % 100 == 0):
        logger.debug("mqtt transactions processed: %s", mqtt_transaction_count)
        
    # as a precaution, if the prior filament change cycle never moved the servos to handle the new filament, do that now
    # forcing servo movement using an artificially high poop count
    if(data["print"]["device"]["extruder"]["state"] == 1 and moved_servos_for_cycle == 0 and cooling_fan_speed < config["sorter_settings"]["cooling_fan_speed_threshold"]):
        move_servos(False, previous_filament_slot, next_filament_slot, 10)
        moved_servos_for_cycle = 1

    extruder_data = data["print"]["device"]["extruder"]

    # if we are just staring up the service and have not yet processed a 260/261/262 (filament change) and there is an active or paused print and
    # the extruder state is not 524289 (i.e. we are not currently changing filament), this means we are safe to move the servos to their appropriate position
    # for the filament currently beng printed; force servo movement with an artificially high poop count
    # if(mqtt_transaction_count == 1 and data["print"]["state"] in [4, 5]):
    if(total_processed_cycles == 0 and data["print"]["state"] in [4, 5]):
        if(extruder_data["state"] != 524289):
            move_servos(False, extruder_data["info"][0]["spre"], extruder_data["info"][0]["star"], 10)
            printing_now = True
        else:
            # make it look as if the filament change cycle has started
            # if the very first transaction happens to be a 260, 261, or 262, the logic further below will set things straight 
            printing_now = True
            poop_count_for_cycle = 0
            wait_for_fan_to_slow = False
            moved_servos_for_cycle = 0
        previous_filament_slot = extruder_data["info"][0]["spre"]
        next_filament_slot = extruder_data["info"][0]["star"]

    logger.debug("state: %s, stat: %s, info: %s, pre: %s, tar: %s || layer: %s/%s, moved: %s, state: %s, task id: %s, current bin: %s, cooling fan speed: %s, gcode state: %s, total processed cycles: %s, cross print current slot: %s",
                 extruder_data["state"], extruder_data["info"][0]["stat"], extruder_data["info"][0]["info"], extruder_data["info"][0]["spre"], extruder_data["info"][0]["star"],
                 data["print"]["layer_num"], data["print"]["total_layer_num"], moved_servos_for_cycle, data["print"]["state"], data["print"]["task_id"], sorter.current_bin,
                 data["print"]["cooling_fan_speed"], data["print"]["gcode_state"], total_processed_cycles, cross_print_current_slot)
    
    # extruder state value of 524289 indicates we are in the process of prepping, loading, or changing filament
    # extruder state during main printing is 1
    if(extruder_data["state"] == 1 and data["print"]["state"] in [4, 5] and cross_print_current_slot != next_filament_slot and
       not (data["print"]["layer_num"] == data["print"]["total_layer_num"] and extruder_data["info"][0]["star"] == 65535)):
        cross_print_current_slot = next_filament_slot

    if(extruder_data["state"] == 524289):
        if(extruder_data["info"][0]["stat"] in [260, 261, 262] and printing_now == True):
            # 260 indicates the beginning of a filament change cycle (filament is being unloaded)
            # 261 indicates that the new filament is being loaded
            # not sure about 262, other than it comes in sequence after 260, 261
            # throughout the 260, 261, 262 transactions the "spre" element contains the "previous" filament slot
            # throughout the 260, 261, 262 transactions the "star" element contains the "new" filament slot
            # I've seen examples where only a single 260 is received, which makes me concerned that perhaps there could be a time when one does not come
            index_filament(data)
            total_processed_cycles += 1
            logger.debug("Printing has temporarily stopped and filament is unloading")
            printing_now = False
            # reset some values to manage this filament change cycle
            poop_count_for_cycle = 0
            wait_for_fan_to_slow = True
            previous_filament_slot = extruder_data["info"][0]["spre"]
            next_filament_slot = extruder_data["info"][0]["star"]
            cross_print_current_slot = -1
            #if(previous_filament_slot == next_filament_slot): # filament change is complete
                #cross_print_current_slot = next_filament_slot
            # let move_servos function determine if the bin needs to be changed
            move_servos(False, previous_filament_slot, next_filament_slot, poop_count_for_cycle)
            moved_servos_for_cycle = 0
        if(extruder_data["info"][0]["stat"] in [261, 262]):
            # now loading new filament, we know cooling fan will have slowed by now
            wait_for_fan_to_slow = False
        if(wait_for_fan_to_slow == False and cooling_fan_speed > config["sorter_settings"]["cooling_fan_speed_threshold"]):
            #  spike of cooling fan speed at this point indicates a poop is imminent
            poop_count_for_cycle += 1
            logger.debug("Poop is imminent")
            # once fan speed has spiked, we will want to wait until it slows back down before we count another poop
            wait_for_fan_to_slow = True
        if(wait_for_fan_to_slow == True and cooling_fan_speed < config["sorter_settings"]["cooling_fan_speed_threshold"] and extruder_data["info"][0]["stat"] not in [260, 261, 262]):
            # let move_servos function determine if the bin needs to be changed
            move_servos(False, previous_filament_slot, next_filament_slot, poop_count_for_cycle)
            moved_servos_for_cycle += 1
            wait_for_fan_to_slow = False
        if(extruder_data["info"][0]["stat"] == 327938 and poop_count_for_cycle == poop_count_threshold_for_servo_movement and
           wait_for_fan_to_slow == False):
            # move servos to point to bin for the next/new filament
            logger.debug("Now moving servos to handle the next/new filament")
            move_servos(False, previous_filament_slot, next_filament_slot, poop_count_for_cycle)
            moved_servos_for_cycle += 1
        if(extruder_data["info"][0]["stat"] in [327938, 327943] and extruder_data["info"][0]["info"] >= 1100):
            printing_now = True
            logger.debug("Printing has resumed. Poop count for cycle: %s", poop_count_for_cycle)

def index_filament(data, clear_bins=False):
    global cross_print_current_slot
    with shelve.open(shelf_path, writeback=True) as poopDB:
        #if("bins" not in poopDB or clear_bins == True):
        if(clear_bins == True):
            sorter.reset_all_bins()
            logger.debug("Bin assignments reinitialized")
        logger.debug("bins: %s", sorter.all_bins)
        
        all_the_current_filaments = []
        
        for ams in data["print"]["ams"]["ams"]:
            logger.debug("ams id: %s", ams['id'])
            for tray in ams["tray"]:
                slotname = ""
                if(ams["id"] == "0"):
                    slotname = str("slot_" + tray["id"])
                if(ams["id"] == "1"):
                    slotname = str("slot_" + str((int(tray["id"]) + 256)))
                if(ams["id"] == "128"):
                    slotname = str("slot_" + "32768")

                work_data = {}

                if("tray_type" not in tray or "tray_color" not in tray):
                    logger.debug("tray id: %s is empty", tray['id'])
                    work_data["type"] = "EMPTY"
                    work_data["color"] = "EMPTY"
                    work_data["color_text"] = "EMPTY"
                else:
                    work_data["type"] = tray["tray_type"]
                    work_data["color"] = tray["tray_color"]
                    work_data["color_text"] = get_color_name("#" + str(tray["tray_color"])[:6])
                    logger.debug("tray id: %s, type: %s, color: %s", tray['id'], tray['tray_type'], tray['tray_color'])
                    all_the_current_filaments.append(str(tray["tray_type"]) + "+" + str(tray["tray_color"]))

                if(slotname in poopDB):
                    check_data = poopDB[slotname]
                    if(check_data["type"] == work_data["type"] and check_data["color"] == work_data["color"]
                       and check_data["color_text"] == work_data["color_text"]):
                        logger.debug("Existing matching DB entry found; do nothing")
                    else:
                        logger.debug("Looks like filament was changed; updating DB entry")
                        poopDB[slotname] = work_data
                        poopDB.sync()
                        if("slot_" + str(cross_print_current_slot) == slotname):
                            cross_print_current_slot = -1
                else:
                    logger.debug("Added new slot entry to DB")
                    poopDB[str(slotname)] = work_data
                    poopDB.sync()
        # external / virtual slot
        work_data = {}
        vir_slot = data["print"]["vir_slot"][0]
        work_data["type"] = vir_slot["tray_type"]
        work_data["color"] = vir_slot["tray_color"]
        work_data["color_text"] = get_color_name("#" + str(vir_slot["tray_color"])[:6])
        slotname = "slot_65280"
        poopDB[str(slotname)] = work_data
        poopDB.sync()
        all_the_current_filaments.append(str(vir_slot["tray_type"]) + "+" + str(vir_slot["tray_color"]))
        # now that we've processed all the filaments and kept track of them all, let's clear out any bin assignments that no longer make sense
        work_array = sorter.all_bins
        for work_index, workbin in enumerate(work_array):
            if(workbin not in all_the_current_filaments and workbin != "DEFAULT" and "+MIX" not in workbin):
                # change bin to "OPEN" rather than deleting the entry because that would mess up any other subsequent bin assignments, which depend on
                # the array's index value
                sorter.reset_bin(work_index + 1) # 0-based array

        # unknown filament slots
        for i in unknown_filament_slot_numbers:
            work_data = {}
            work_data["type"] = "UNKNOWN"
            work_data["color"] = "UNKNOWN"
            work_data["color_text"] = "UNKNOWN"
            slotname = "slot_" + str(i)
            poopDB[str(slotname)] = work_data
            poopDB.sync()
        # db entry dump
        db_arr = list(poopDB.keys())
        for entry in db_arr:
            logger.debug("db entry - %s: %s", entry, poopDB[entry])
            
def closest_color(requested_color):
    min_colors = {}
    # iterate through CSS3 colors
    for name in webcolors.names("css3"):
        r_c, g_c, b_c = webcolors.name_to_rgb(name)
        rd = (r_c - requested_color[0]) ** 2
        gd = (g_c - requested_color[1]) ** 2
        bd = (b_c - requested_color[2]) ** 2
        min_colors[(rd + gd + bd)] = name
    return min_colors[min(min_colors.keys())]

def get_color_name(hex_code):
    ret_val = "(none)"
    try:
        # try exact match first
        ret_val = webcolors.hex_to_name(hex_code)
        logger.debug("Color exact match: %s", ret_val)
        return ret_val
    except:
        # fallback to closest match
        rgb_tuple = webcolors.hex_to_rgb(hex_code)
        ret_val = closest_color(rgb_tuple)
        logger.debug("Color closest match: %s", ret_val)
        return ret_val

def move_servos(force_default_mode=True, prev_slot=None, new_slot=None, poop_number_in_cycle=None):
    workAngle = config["servo_details"]["angle_main"].copy()
    connected_servos = config["servo_details"]["servo_count"]
    sort_attribute = ""
    
    with shelve.open(shelf_path, writeback=True) as poopDB:
        if(force_default_mode == True or new_slot in unknown_filament_slot_numbers or prev_slot in unknown_filament_slot_numbers or
           "slot_" + str(new_slot) not in poopDB or "slot_" + str(prev_slot) not in poopDB):
            sort_attribute = "DEFAULT"
        else:
            prev_filament = poopDB["slot_" + str(prev_slot)]
            cur_filament = poopDB["slot_" + str(new_slot)]
            mixed_color_poop = False
            # when switching between different types of filament (e.g. ABS to PLA), we want to be careful to flag those poops to go to the DEFAULT bin
            if(prev_filament["type"] != cur_filament["type"] and poop_number_in_cycle < cross_type_poop_threshold):
                sort_attribute = "DEFAULT"
            else:
                if(poop_number_in_cycle == 0):
                    if(config["sorter_settings"]["separation_mode"] == 1):
                        work_filament = prev_filament
                        mixed_color_poop = False
                    else:
                        work_filament = cur_filament
                        mixed_color_poop = True
                else:
                    work_filament = cur_filament
                if(mixed_color_poop == True):
                    sort_attribute = str(work_filament["type"]) + "+MIX"
                else:
                    sort_attribute = str(work_filament["type"] + "+" + work_filament["color"])

    found, assigned, actual_bin_number, actual_sort_attribute = sorter.move_servos_to_sort_attribute_bin(sort_attribute)
    
# mainline

#initialize shelf
with shelve.open(shelf_path, writeback=True) as initPoopDB:
    logger.info("App DB located in: %s", os.getcwd())
    # current task id may be necessary depending on what mode we're in
    if("current_task_id" in initPoopDB):
        task_id = str(initPoopDB["current_task_id"])
    else:
        initPoopDB["current_task_id"] = "UNKNOWN"
        initPoopDB.sync()
        task_id = "UNKNOWN"

try:
    sorter = servo_sorter.ServoSorter(log_and_sorter_name_prefix, config["servo_details"]["servo_count"], config["pca9685_servo_driver"]["servo_driver_i2c_addresses"], \
                         config["servo_details"]["actuation"], config["servo_details"]["angle_main"], config["servo_details"]["angle_alt"], config["servo_details"]["sleep_duration_between_servo_movements"], config["servo_details"]["only_move_necessary_servos"])
except (ValueError, servo_sorter.AddressRangeAngleError) as e:
    logger.error(e)
    raise

do_index_filament = True
printing_now = True
cooling_fan_speed = 0
current_layer_number = 0
mqtt_transaction_count = 0
poop_count_for_cycle = 0
wait_for_fan_to_slow = True
previous_filament_slot = unknown_filament_slot_numbers[0]
next_filament_slot = unknown_filament_slot_numbers[0]
moved_servos_for_cycle = 1
task_id = "UNKNOWN"
total_processed_cycles = 0

match config["sorter_settings"]["separation_mode"]:
    case 1: # approximate - first poop of change cycle will be lumped with "previous" filament bin, 2nd and beyond will go to "new" filament bin
        poop_count_threshold_for_servo_movement = 1
        cross_type_poop_threshold = 2
    case 2: # strict - first poop of change cycle will go into "mix" bin, 2nd and beyond will go to "new" filament bin
        poop_count_threshold_for_servo_movement = 2
        cross_type_poop_threshold = 3
    case 3: # very strict - first 2 poops of change cycle will go into "mix" bin, 3rd and beyond will go to "new" filament bin
        poop_count_threshold_for_servo_movement = 3
        cross_type_poop_threshold = 4
    case _: # unknown mode
        poop_count_threshold_for_servo_movement = 2
        cross_type_poop_threshold = 3
        
move_servos(True)

# Create client instance
mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqttc.username_pw_set(config["printer_api"]["machine_local_user"], config["printer_api"]["machine_access_code"])
mqttc.tls_set(ca_certs=None, cert_reqs=ssl.CERT_NONE)

mqttc.on_connect = on_connect
mqttc.on_message = on_message

# Connect to broker (host, port, keepalive)
mqttc.connect(config["printer_api"]["machine_ip_address"], 8883, 60)

# loop_forever() just keeps processing messages and automatically reconnects
mqttc.loop_forever(retry_first_connection=True)
