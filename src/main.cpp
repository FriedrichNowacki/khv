#include <Arduino.h>
#include <cred.h>

void setup() {
    Serial.begin(115200);
    delay(2000);

    Serial.println("==================================");
    Serial.println("   KHV - XIAO ESP32-C6 gestartet   ");
    Serial.println("==================================");
}

void loop() {
    Serial.println("KHV läuft...");
    delay(5000);
}
