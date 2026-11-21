/*
 * Night Dozhor fridge MVP
 * ESP32 + HuskyLens 2 + an electronic lock.
 *
 * Libraries:
 *   - ArduinoJson
 *   - DFRobot_HuskylensV2
 *
 * Unknown faces have face_id == 0 and are opened as guests. Known faces use
 * the locally cached policy. Wi-Fi is only used during periodic sync and when
 * sending an access event, so the fridge keeps working without the server.
 */

#include <ArduinoJson.h>
#include <DFRobot_HuskylensV2.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>

const char *WIFI_SSID = "replace-me";
const char *WIFI_PASSWORD = "replace-me";
const char *API_BASE_URL = "https://example.com";
const char *DEVICE_ID = "fridge-001";
const char *DEVICE_TOKEN = "demo-device-token";

constexpr uint8_t LOCK_PIN = 25;
constexpr uint8_t DOOR_SENSOR_PIN = 26;
constexpr uint8_t LOCK_OPEN_LEVEL = HIGH;
constexpr uint8_t LOCK_CLOSED_LEVEL = LOW;
constexpr unsigned long POLICY_REFRESH_MS = 10UL * 60UL * 1000UL;
constexpr unsigned long LOCK_HOLD_MS = 4000;
constexpr unsigned long FACE_DEBOUNCE_MS = 15000;

HardwareSerial HuskySerial(1);
HuskylensV2 huskylens;
Preferences preferences;
DynamicJsonDocument policy(12288);

unsigned long lastPolicyRefresh = 0;
unsigned long lockOpenedAt = 0;
unsigned long lastFaceDecisionAt = 0;
int lastFaceId = -1;
bool lockIsOpen = false;

void lockDoor() {
  digitalWrite(LOCK_PIN, LOCK_CLOSED_LEVEL);
  lockIsOpen = false;
}

void openDoor() {
  digitalWrite(LOCK_PIN, LOCK_OPEN_LEVEL);
  lockOpenedAt = millis();
  lockIsOpen = true;
}

void loadCachedPolicy() {
  String raw = preferences.getString("policy", "{}");
  policy.clear();
  deserializeJson(policy, raw);
}

bool savePolicy(const String &raw) {
  policy.clear();
  if (deserializeJson(policy, raw)) {
    return false;
  }
  preferences.putString("policy", raw);
  return true;
}

bool connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  const unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 8000) {
    delay(100);
  }
  return WiFi.status() == WL_CONNECTED;
}

bool syncPolicy() {
  if (!connectWiFi()) return false;
  HTTPClient http;
  String url = String(API_BASE_URL) + "/api/devices/" + DEVICE_ID + "/policy";
  http.begin(url);
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  const int status = http.GET();
  if (status != HTTP_CODE_OK) {
    http.end();
    return false;
  }
  const String raw = http.getString();
  http.end();
  lastPolicyRefresh = millis();
  return savePolicy(raw);
}

bool completeEnrollment(const char *userId, int faceId) {
  if (!connectWiFi()) return false;
  HTTPClient http;
  String url = String(API_BASE_URL) + "/api/devices/" + DEVICE_ID + "/enrollment/complete";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  StaticJsonDocument<256> body;
  body["user_id"] = userId;
  body["face_id"] = faceId;
  String encoded;
  serializeJson(body, encoded);
  const int status = http.POST(encoded);
  http.end();
  return status >= 200 && status < 300;
}

void sendAccessEvent(int faceId, bool opened, const char *reason) {
  if (!connectWiFi()) return;
  HTTPClient http;
  String url = String(API_BASE_URL) + "/api/devices/" + DEVICE_ID + "/events";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  StaticJsonDocument<256> event;
  event["face_id"] = faceId;
  event["result"] = opened ? "opened" : "denied";
  event["reason"] = reason;
  String body;
  serializeJson(event, body);
  http.POST(body);
  http.end();
}

JsonObject findFacePolicy(int faceId) {
  JsonArray users = policy["users"].as<JsonArray>();
  for (JsonObject user : users) {
    if (user["face_id"].as<int>() == faceId) return user;
  }
  return JsonObject();
}

int readFaceId() {
  huskylens.getResult(ALGORITHM_FACE_RECOGNITION);
  if (!huskylens.available(ALGORITHM_FACE_RECOGNITION)) return -1;
  auto face = huskylens.getCachedCenterResult(ALGORITHM_FACE_RECOGNITION);
  return RET_ITEM_NUM(face, Result, ID);
}

void processFace() {
  const int faceId = readFaceId();
  if (faceId < 0) return;
  if (faceId == lastFaceId && millis() - lastFaceDecisionAt < FACE_DEBOUNCE_MS) return;
  lastFaceId = faceId;
  lastFaceDecisionAt = millis();

  // The owner starts enrollment in the app, then presses Button-A on the
  // HuskyLens 2. The first newly learned ID is linked to that pending user.
  JsonObject pending = policy["pending_enrollment"].as<JsonObject>();
  if (!pending.isNull() && faceId > 0 && findFacePolicy(faceId).isNull()) {
    const char *userId = pending["user_id"] | "";
    if (completeEnrollment(userId, faceId)) {
      syncPolicy();
    }
    return;
  }

  if (faceId == 0) {
    openDoor();
    sendAccessEvent(faceId, true, "guest_access");
    return;
  }

  JsonObject user = findFacePolicy(faceId);
  const bool allowed = user["allowed"] | false;
  const char *reason = user["reason"] | "unknown_face_id";
  if (allowed) {
    openDoor();
    sendAccessEvent(faceId, true, reason);
  } else {
    lockDoor();
    sendAccessEvent(faceId, false, reason);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(LOCK_PIN, OUTPUT);
  pinMode(DOOR_SENSOR_PIN, INPUT_PULLUP);
  lockDoor();

  preferences.begin("fridge", false);
  loadCachedPolicy();

  HuskySerial.begin(9600, SERIAL_8N1, 16, 17);
  while (!huskylens.begin(HuskySerial)) {
    Serial.println("HuskyLens 2 is not responding");
    delay(1000);
  }
  huskylens.switchAlgorithm(ALGORITHM_FACE_RECOGNITION);
  syncPolicy();
}

void loop() {
  if (millis() - lastPolicyRefresh > POLICY_REFRESH_MS) syncPolicy();
  processFace();

  if (lockIsOpen && digitalRead(DOOR_SENSOR_PIN) == HIGH && millis() - lockOpenedAt > LOCK_HOLD_MS) {
    lockDoor();
  }
  delay(40);
}
