from machine import Pin
import machine
import network
import ubinascii
import ujson
from utime import sleep, ticks_diff, ticks_ms
from secrets import WIFI_SSID, WIFI_PASSWORD, MQTT_BROKER, MQTT_USER, MQTT_PASSWORD

try:
    from umqtt.simple import MQTTClient  # type: ignore[reportMissingImports]
    MQTT_IMPL = "umqtt.simple"
except ImportError:
    try:
        from umqtt.robust import MQTTClient  # type: ignore[reportMissingImports]
        MQTT_IMPL = "umqtt.robust"
    except ImportError:
        MQTTClient = None
        MQTT_IMPL = None

RED_PIN = 13
YELLOW_PIN = 14
GREEN_PIN = 15

MQTT_PORT = 8883
MQTT_CLIENT_ID = b"traffic-light-" + ubinascii.hexlify(machine.unique_id())
MQTT_SSL = True
MQTT_SSL_PARAMS = {"server_hostname": MQTT_BROKER}
MQTT_ALLOW_PLAINTEXT_FALLBACK = True
MQTT_PLAINTEXT_PORT = 1883

TOPIC_COMMAND = b"meeting-watcher/status"
TOPIC_STATE = b"traffic-light/state"

red = Pin(RED_PIN, Pin.OUT)
yellow = Pin(YELLOW_PIN, Pin.OUT)
green = Pin(GREEN_PIN, Pin.OUT)


def all_off():
    red.off()
    yellow.off()
    green.off()


def startup_blink():
    red.on()
    yellow.on()
    green.on()
    sleep(1)
    all_off()


def set_lights(state):
    all_off()

    if state == "RED":
        red.on()
    elif state == "RED_YELLOW":
        red.on()
        yellow.on()
    elif state == "GREEN":
        green.on()
    elif state == "YELLOW":
        yellow.on()


def connect_mqtt():
    if MQTTClient is None:
        raise RuntimeError(
            "No MQTT library found. Upload umqtt package to board lib/ folder."
        )

    mqtt_client_cls = MQTTClient

    def build_client(port, use_ssl):
        kwargs = {
            "client_id": MQTT_CLIENT_ID,
            "server": MQTT_BROKER,
            "port": port,
            "user": MQTT_USER or None,
            "password": MQTT_PASSWORD or None,
            "keepalive": 30,
        }

        if use_ssl:
            kwargs["ssl"] = True
            kwargs["ssl_params"] = MQTT_SSL_PARAMS

        return mqtt_client_cls(**kwargs)

    try:
        client = build_client(MQTT_PORT, MQTT_SSL)
        client.connect()
        print("MQTT connected to", MQTT_BROKER, "using", MQTT_IMPL)
        return client
    except Exception as primary_error:
        print("Primary MQTT connect failed:", primary_error)

        if MQTT_SSL and MQTT_ALLOW_PLAINTEXT_FALLBACK:
            print("Retrying MQTT without TLS on port", MQTT_PLAINTEXT_PORT)
            client = build_client(MQTT_PLAINTEXT_PORT, False)
            client.connect()
            print("MQTT connected without TLS to", MQTT_BROKER)
            return client

        raise


def connect_wifi(ssid, password, timeout=15):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        print("Already connected:", wlan.ifconfig())
        return wlan

    print("Connecting to Wi-Fi:", ssid)
    wlan.connect(ssid, password)

    for _ in range(timeout):
        if wlan.isconnected():
            print("Wi-Fi connected:", wlan.ifconfig())
            return wlan
        sleep(1)
        print("Waiting for connection...")

    raise RuntimeError("Wi-Fi connection failed. Check SSID/password or signal.")


def traffic_light_loop(client):
    startup_blink()
    print("Traffic light started on GPIO 13 (red), 14 (yellow), 15 (green)")
    print("MQTT commands: auto, off, red, yellow, green")

    traffic_states = [
        ("RED", 5000),
        ("RED_YELLOW", 2000),
        ("GREEN", 5000),
        ("YELLOW", 2000),
    ]

    controller = {
        "mode": "MANUAL",
        "manual_state": "OFF",
        "index": 0,
        "state": "OFF",
        "state_started": ticks_ms(),
    }

    def publish_state():
        payload = "{}:{}".format(controller["mode"], controller["state"])
        client.publish(TOPIC_STATE, payload.encode())

    def apply_current_state():
        if controller["mode"] == "AUTO":
            controller["state"] = traffic_states[controller["index"]][0]
            set_lights(controller["state"])
        else:
            controller["state"] = controller["manual_state"]
            set_lights(controller["state"])
        publish_state()

    def on_message(topic, msg):
        raw = msg.decode().strip()
        print("MQTT message:", raw)

        try:
            data = ujson.loads(raw)
            if "meeting" in data:
                controller["mode"] = "MANUAL"
                controller["manual_state"] = "RED" if data["meeting"] else "GREEN"
                apply_current_state()
                return
        except Exception:
            pass

        command = raw.lower()
        if command == "auto":
            controller["mode"] = "AUTO"
            controller["index"] = 0
            controller["state_started"] = ticks_ms()
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

        apply_current_state()

    client.set_callback(on_message)
    client.subscribe(TOPIC_COMMAND)
    print("Subscribed to:", TOPIC_COMMAND)

    apply_current_state()

    last_ping = ticks_ms()

    while True:
        client.check_msg()

        now = ticks_ms()

        if ticks_diff(now, last_ping) >= 20000:
            client.ping()
            last_ping = now

        if controller["mode"] == "AUTO":
            state_duration = traffic_states[controller["index"]][1]
            if ticks_diff(now, controller["state_started"]) >= state_duration:
                controller["index"] = (controller["index"] + 1) % len(traffic_states)
                controller["state_started"] = now
                apply_current_state()

        sleep(0.1)


def blink_yellow_for(ms):
    deadline = ticks_ms() + ms
    while ticks_diff(deadline, ticks_ms()) > 0:
        yellow.on()
        sleep(0.3)
        yellow.off()
        sleep(0.3)


try:
    while True:
        try:
            connect_wifi(WIFI_SSID, WIFI_PASSWORD)
            mqtt_client = connect_mqtt()
            all_off()
            traffic_light_loop(mqtt_client)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print("Connection lost, reconnecting in 5s:", exc)
            all_off()
            blink_yellow_for(5000)
except KeyboardInterrupt:
    print("Stopped by user.")
finally:
    all_off()
