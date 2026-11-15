/*
  ESP32-S3 river turbidity logger with SD card 

  Wakes once a month, powers the analog turbidity sensor, takes many readings,
  converts the median to NTU with your calibration table, appends one line to
  /turbidity.csv on the SD card, then goes back to deep sleep.
  Take the SD card home and run make_report.py on the file.

  Parts: ESP32-S3 board, analog turbidity sensor, microSD module (SPI),
         DS3231 real-time clock module (gives the timestamps; needs a coin cell).
  Libraries (Library Manager): "RTClib" by Adafruit. SD, SPI, Wire come with the core.

  Wiring:
    Sensor signal -> GPIO4 (use a voltage divider if the sensor outputs > 3.3 V)
    Sensor power  -> GPIO5 (or switch it with a transistor if it draws much current)
    SD module     -> SCK 12, MISO 13, MOSI 11, CS 10
    DS3231        -> SDA 8, SCL 9
*/
#include <SPI.h>
#include <SD.h>
#include <Wire.h>
#include <RTClib.h>
#include <algorithm>

const char* DEVICE_ID = "esp32s3-pra-1";
const char* LOG_FILE  = "/turbidity.csv";

const int SENSOR_PIN     = 4;
const int SENSOR_PWR_PIN = 5;
const int SD_SCK = 12, SD_MISO = 13, SD_MOSI = 11, SD_CS = 10;
const int I2C_SDA = 8, I2C_SCL = 9;
const int N_SAMPLES = 60;

const uint64_t MONTH_US = 30ULL * 24ULL * 3600ULL * 1000000ULL;   // sleep 30 days
const uint64_t RETRY_US = 1ULL * 3600ULL * 1000000ULL;            // retry in 1 hour on SD error

// ---------- calibration ----------
// PLACEHOLDER VALUES. Replace with your own: dip the sensor in standards
// (e.g. 0, 100, 500, 1000 NTU), note the millivolts printed on the Serial
// Monitor, and list them here in ASCENDING millivolt order.
struct CalPoint { float mv; float ntu; };
CalPoint CAL[] = {
  {  800, 3000.0 },
  { 1400,  800.0 },
  { 2000,  100.0 },
  { 2600,    0.0 },
};
const int N_CAL = sizeof(CAL) / sizeof(CAL[0]);

float mvToNtu(float mv) {
  if (mv <= CAL[0].mv) return CAL[0].ntu;
  if (mv >= CAL[N_CAL - 1].mv) return CAL[N_CAL - 1].ntu;
  for (int i = 0; i < N_CAL - 1; i++) {
    if (mv <= CAL[i + 1].mv) {
      float f = (mv - CAL[i].mv) / (CAL[i + 1].mv - CAL[i].mv);
      return CAL[i].ntu + f * (CAL[i + 1].ntu - CAL[i].ntu);
    }
  }
  return CAL[N_CAL - 1].ntu;
}

float readMedianMv() {
  analogSetPinAttenuation(SENSOR_PIN, ADC_11db);   // about 0 to 3.1 V
  digitalWrite(SENSOR_PWR_PIN, HIGH);
  delay(3000);                                     // let the sensor stabilise
  float v[N_SAMPLES];
  for (int i = 0; i < N_SAMPLES; i++) {
    v[i] = analogReadMilliVolts(SENSOR_PIN);
    delay(50);
  }
  digitalWrite(SENSOR_PWR_PIN, LOW);
  std::sort(v, v + N_SAMPLES);
  return v[N_SAMPLES / 2];
}

bool logToSd(const String& timestamp, float ntu, float mv) {
  SPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS)) return false;
  bool isNew = !SD.exists(LOG_FILE);
  File f = SD.open(LOG_FILE, FILE_APPEND);
  if (!f) return false;
  if (isNew) f.println("time,ntu,mv,device");
  f.printf("%s,%.1f,%.0f,%s\n", timestamp.c_str(), ntu, mv, DEVICE_ID);
  f.close();
  SD.end();
  return true;
}

void goToSleep(uint64_t us) {
  esp_sleep_enable_timer_wakeup(us);
  esp_deep_sleep_start();
}

void setup() {
  Serial.begin(115200);
  pinMode(SENSOR_PWR_PIN, OUTPUT);
  digitalWrite(SENSOR_PWR_PIN, LOW);

  Wire.begin(I2C_SDA, I2C_SCL);
  RTC_DS3231 rtc;
  bool rtcOk = rtc.begin();
  if (rtcOk && rtc.lostPower()) {
    rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));   // first-time set; set properly when installing
  }
  String ts = "1970-01-01 00:00:00";
  if (rtcOk) {
    ts = rtc.now().timestamp(DateTime::TIMESTAMP_FULL);   // e.g. 2026-11-15T10:30:00
    ts.replace('T', ' ');                                // -> 2026-11-15 10:30:00
  }

  float mv  = readMedianMv();
  float ntu = mvToNtu(mv);
  Serial.printf("%s  median %.0f mV -> %.1f NTU\n", ts.c_str(), mv, ntu);   // use when calibrating

  bool ok = rtcOk && logToSd(ts, ntu, mv);
  goToSleep(ok ? MONTH_US : RETRY_US);
}

void loop() {}   // never reached: the board sleeps at the end of setup()
