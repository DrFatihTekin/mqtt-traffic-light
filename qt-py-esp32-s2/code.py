import os
import json
import time
import ssl
import wifi
import socketpool
import board
import digitalio
import adafruit_minimqtt.adafruit_minimqtt as MQTT

RED_PIN = board.D5
YELLOW_PIN = board.D6
GREEN_PIN = board.D7

WIFI_SSID = os.getenv("WIFI_SSID")
WIFI_PASSWORD = os.getenv("WIFI_PASSWORD")

MQTT_BROKER = os.getenv("MQTT_BROKER")
MQTT_PORT = 8883
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")

TOPIC_COMMAND = "meeting-watcher/status"
TOPIC_STATE = "traffic-light/state"

red = digitalio.DigitalInOut(RED_PIN)
red.direction = digitalio.Direction.OUTPUT
yellow = digitalio.DigitalInOut(YELLOW_PIN)
yellow.direction = digitalio.Direction.OUTPUT
green = digitalio.DigitalInOut(GREEN_PIN)
green.direction = digitalio.Direction.OUTPUT


def all_off():
    red.value = False
    yellow.value = False
    green.value = False


def startup_blink():
    red.value = True
    yellow.value = True
    green.value = True
    time.sleep(1)
    all_off()


def set_lights(state):
    all_off()
    if state == "RED":
        red.value = True
    elif state == "RED_YELLOW":
        red.value = True
        yellow.value = True
    elif state == "GREEN":
        green.value = True
    elif state == "YELLOW":
        yellow.value = True


def blink_yellow_for(seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        yellow.value = True
        time.sleep(0.3)
        yellow.value = False
        time.sleep(0.3)


controller = {
    "mode": "MANUAL",
    "manual_state": "OFF",
    "state": "OFF",
    "state_started": time.monotonic(),
    "index": 0,
}

traffic_states = [
    ("RED", 5),
    ("RED_YELLOW", 2),
    ("GREEN", 5),
    ("YELLOW", 2),
]


def apply_current_state(client):
    if controller["mode"] == "AUTO":
        controller["state"] = traffic_states[controller["index"]][0]
    else:
        controller["state"] = controller["manual_state"]
    set_lights(controller["state"])
    client.publish(TOPIC_STATE, "{}:{}".format(controller["mode"], controller["state"]))


def on_message(client, topic, message):
    print("MQTT message:", message)

    try:
        data = json.loads(message)
        if "meeting" in data:
            controller["mode"] = "MANUAL"
            controller["manual_state"] = "RED" if data["meeting"] else "GREEN"
            apply_current_state(client)
            return
    except Exception:
        pass

    command = message.strip().lower()
    if command == "auto":
        controller["mode"] = "AUTO"
        controller["index"] = 0
        controller["state_started"] = time.monotonic()
    elif command == "off":
        controller["mode"] = "MANUAL"
        controller["manual_state"] = "OFF"
    elif command == "red":
        controller["mode"] = "MANUAL"
        controller["manual_state"] = "RED"
    elif command == "yellow":
        controller["mode"] = "MANUAL"
        controller["manual_state"] = "YELLOW"
    elif command == "green":
        controller["mode"] = "MANUAL"
        controller["manual_state"] = "GREEN"
    else:
        print("Unknown command")
        return

    apply_current_state(client)


while True:
    try:
        print("Connecting to Wi-Fi:", WIFI_SSID)
        wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
        print("Wi-Fi connected:", wifi.radio.ipv4_address)

        pool = socketpool.SocketPool(wifi.radio)
        ssl_context = ssl.create_default_context()

        mqtt_client = MQTT.MQTT(
            broker=MQTT_BROKER,
            port=MQTT_PORT,
            username=MQTT_USER,
            password=MQTT_PASSWORD,
            socket_pool=pool,
            ssl_context=ssl_context,
            keep_alive=30,
        )
        mqtt_client.on_message = on_message
        mqtt_client.connect()
        print("MQTT connected to", MQTT_BROKER)

        startup_blink()

        mqtt_client.subscribe(TOPIC_COMMAND)
        print("Subscribed to:", TOPIC_COMMAND)

        while True:
            mqtt_client.loop(timeout=0.1)

            if controller["mode"] == "AUTO":
                now = time.monotonic()
                duration = traffic_states[controller["index"]][1]
                if now - controller["state_started"] >= duration:
                    controller["index"] = (controller["index"] + 1) % len(traffic_states)
                    controller["state_started"] = now
                    apply_current_state(mqtt_client)

    except KeyboardInterrupt:
        all_off()
        break
    except Exception as e:
        print("Connection lost, reconnecting in 5s:", e)
        all_off()
        blink_yellow_for(5)
