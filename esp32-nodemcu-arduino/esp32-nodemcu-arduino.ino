// Libraries required (install via Arduino Library Manager):
//   PubSubClient  by Nick O'Leary
//   ArduinoJson   by Benoit Blanchon

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "secrets.h"

#define RED_PIN    13
#define YELLOW_PIN 14
#define GREEN_PIN  15

#define MQTT_BROKER      "6b7b57bcbd2a4aa2b380c4aafc2799f7.s1.eu.hivemq.cloud"
#define MQTT_PORT        8883
#define MQTT_TOPIC_CMD   "meeting-watcher/status"
#define MQTT_TOPIC_STATE "traffic-light/state"

WiFiClientSecure wifiClient;
PubSubClient mqtt(wifiClient);

// ---- state ---------------------------------------------------------------

enum Mode       { MANUAL, AUTO_CYCLE };
enum LightState { OFF_STATE, RED_STATE, RED_YELLOW_STATE, GREEN_STATE, YELLOW_STATE };

struct Controller {
    Mode       mode        = MANUAL;
    LightState manualState = OFF_STATE;
    int        autoIndex   = 0;
    unsigned long stateStarted = 0;
} ctrl;

struct AutoStep { LightState state; unsigned long duration; };
const AutoStep autoSteps[] = {
    { RED_STATE,        5000 },
    { RED_YELLOW_STATE, 2000 },
    { GREEN_STATE,      5000 },
    { YELLOW_STATE,     2000 },
};
const int AUTO_STEPS = 4;

// ---- LED helpers ---------------------------------------------------------

void allOff() {
    digitalWrite(RED_PIN,    LOW);
    digitalWrite(YELLOW_PIN, LOW);
    digitalWrite(GREEN_PIN,  LOW);
}

void setLights(LightState s) {
    allOff();
    switch (s) {
        case RED_STATE:        digitalWrite(RED_PIN, HIGH); break;
        case RED_YELLOW_STATE: digitalWrite(RED_PIN, HIGH); digitalWrite(YELLOW_PIN, HIGH); break;
        case GREEN_STATE:      digitalWrite(GREEN_PIN, HIGH); break;
        case YELLOW_STATE:     digitalWrite(YELLOW_PIN, HIGH); break;
        default: break;
    }
}

void startupBlink() {
    digitalWrite(RED_PIN,    HIGH);
    digitalWrite(YELLOW_PIN, HIGH);
    digitalWrite(GREEN_PIN,  HIGH);
    delay(1000);
    allOff();
}

void blinkYellow(unsigned long ms) {
    unsigned long deadline = millis() + ms;
    while (millis() < deadline) {
        digitalWrite(YELLOW_PIN, HIGH); delay(300);
        digitalWrite(YELLOW_PIN, LOW);  delay(300);
    }
}

const char* stateName(LightState s) {
    switch (s) {
        case RED_STATE:        return "RED";
        case RED_YELLOW_STATE: return "RED_YELLOW";
        case GREEN_STATE:      return "GREEN";
        case YELLOW_STATE:     return "YELLOW";
        default:               return "OFF";
    }
}

// ---- state machine -------------------------------------------------------

void applyState() {
    LightState s = (ctrl.mode == AUTO_CYCLE)
        ? autoSteps[ctrl.autoIndex].state
        : ctrl.manualState;
    setLights(s);
    String payload = String(ctrl.mode == AUTO_CYCLE ? "AUTO" : "MANUAL") + ":" + stateName(s);
    mqtt.publish(MQTT_TOPIC_STATE, payload.c_str());
}

// ---- MQTT callback -------------------------------------------------------

void onMessage(char* topic, byte* payload, unsigned int length) {
    String msg;
    for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
    Serial.println("MQTT: " + msg);

    JsonDocument doc;
    if (!deserializeJson(doc, msg) && doc.containsKey("meeting")) {
        ctrl.mode        = MANUAL;
        ctrl.manualState = doc["meeting"].as<bool>() ? RED_STATE : GREEN_STATE;
        applyState();
        return;
    }

    msg.trim();
    msg.toLowerCase();
    if      (msg == "auto")   { ctrl.mode = AUTO_CYCLE; ctrl.autoIndex = 0; ctrl.stateStarted = millis(); }
    else if (msg == "off")    { ctrl.mode = MANUAL; ctrl.manualState = OFF_STATE; }
    else if (msg == "red")    { ctrl.mode = MANUAL; ctrl.manualState = RED_STATE; }
    else if (msg == "yellow") { ctrl.mode = MANUAL; ctrl.manualState = YELLOW_STATE; }
    else if (msg == "green")  { ctrl.mode = MANUAL; ctrl.manualState = GREEN_STATE; }
    else { Serial.println("Unknown command"); return; }
    applyState();
}

// ---- WiFi / MQTT connect -------------------------------------------------

void connectWifi() {
    if (WiFi.isConnected()) return;
    Serial.print("Connecting to Wi-Fi: ");
    Serial.println(WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    while (!WiFi.isConnected()) { delay(500); Serial.print("."); }
    Serial.print("\nWi-Fi connected: ");
    Serial.println(WiFi.localIP());
}

bool connectMqtt() {
    String clientId = "esp32-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.print("Connecting to MQTT... ");
    if (mqtt.connect(clientId.c_str(), MQTT_USER, MQTT_PASSWORD)) {
        Serial.println("connected");
        mqtt.subscribe(MQTT_TOPIC_CMD);
        Serial.println("Subscribed to: " + String(MQTT_TOPIC_CMD));
        return true;
    }
    Serial.println("failed, rc=" + String(mqtt.state()));
    return false;
}

// ---- Arduino entry points ------------------------------------------------

void setup() {
    Serial.begin(115200);
    pinMode(RED_PIN,    OUTPUT);
    pinMode(YELLOW_PIN, OUTPUT);
    pinMode(GREEN_PIN,  OUTPUT);
    allOff();

    wifiClient.setInsecure();  // TLS encrypted, certificate not verified
    mqtt.setServer(MQTT_BROKER, MQTT_PORT);
    mqtt.setCallback(onMessage);
    mqtt.setBufferSize(512);

    connectWifi();
    startupBlink();
    connectMqtt();
}

void loop() {
    if (!WiFi.isConnected()) {
        Serial.println("Wi-Fi lost, reconnecting...");
        allOff();
        connectWifi();
    }

    if (!mqtt.connected()) {
        Serial.println("MQTT disconnected, reconnecting in 5s...");
        allOff();
        blinkYellow(5000);
        connectMqtt();
        return;
    }

    mqtt.loop();

    if (ctrl.mode == AUTO_CYCLE) {
        if (millis() - ctrl.stateStarted >= autoSteps[ctrl.autoIndex].duration) {
            ctrl.autoIndex     = (ctrl.autoIndex + 1) % AUTO_STEPS;
            ctrl.stateStarted  = millis();
            applyState();
        }
    }
}
