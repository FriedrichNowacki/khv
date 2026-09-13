#!/usr/bin/env python3
"""
Legacy M-Bus Heatmeter Service (Ursprünglich auf Raspberry Pi betrieben).
Referenzcode zur Portierung auf den Seeed Studio XIAO ESP32-C6.
"""

import serial
import time
# pyrefly: ignore [missing-import]
import meterbus
import requests  # For Telegram messages
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Any, Tuple, Dict
import os
import sys

# Add parent directory and credentials directory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'cred')))
sys.path.append("/home/sus/cred")

# --- INFLUXDB SETUP ---
# pyrefly: ignore [missing-import]
from influxdb_client import InfluxDBClient, Point
# pyrefly: ignore [missing-import]
from influxdb_client.client.write_api import SYNCHRONOUS
# pyrefly: ignore [missing-import]
from influxdb_client.client.exceptions import InfluxDBError
import credentials

# Set up InfluxDB client
client = InfluxDBClient(
    url=credentials.INFLUXDB_URL,
    token=credentials.INFLUXDB_TOKEN,
    org=credentials.INFLUXDB_ORG
)
INFLUX_BUCKET = "Heizung"
INFLUX_MONITORING_BUCKET = "_monitoring" # Bucket for error logs
ENABLE_INFLUX = True 

# --- M-BUS CONFIGURATION ---
PORT = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0' 
BAUDRATE = 2400
RETRIES = 1    # IMPORTANT: 1 = No retries! (Battery protection: poll max every 120s)
POLL_INTERVAL = 120  # How often (in seconds) the meters are polled
START_DELAY = 90

# --- TELEGRAM ALARM SETTINGS ---
TELEGRAM_ALERT_TIMEOUT = 3600  # Time in seconds (here 1 hour) without data before alarm is triggered

# --- THRESHOLDS (MINIMUM CHANGE) ---
DEFAULT_MAX_INTERVAL = 60*60  # 1 hour

THRESHOLDS = {
    "energy": 500,      # Wh (Minimum change to trigger sending)
    "power": 50,        # W
    "flow": 1,          # l/h 
    "volume": 10,       # l 
    "temp": 0.1,        # °C
    "spreading": 0.1    # K
}

# --- FILTER (MAXIMUM CHANGE / PLAUSIBILITY) ---
MAX_CHANGE_LIMITS = {
    "energy": 50000,     # Wh (More than 5kWh jump in 2.5 min is impossible)
    "power": 20000,     # W  (More than 20kW jump is implausible for an apartment)
    "flow": 20000,       # l/h (Sudden jump of 2000l/h is unlikely)
    "volume": 50000,      # l
    "temp": 200.0,       # °C (Discard temperature jumps > 20°C in a single interval)
    "spreading": 200.0   # K
}

# Retry Configuration for InfluxDB
INFLUX_RETRIES = 5
INFLUX_RETRY_DELAY = 10  # Seconds

@dataclass
class Register:
    """Represents a single register (measurement) of a heat meter.

    This class stores the configuration, historical measurement values, and
    filtering criteria for a specific measurement and checks whether new values
    should be sent to InfluxDB.

    Attributes:
        name: Display name of the register (e.g., "Energie").
        unit: Unit of the measurement (e.g., "Wh").
        field_name: Field name in InfluxDB.
        decimals: Number of decimal places for rounding.
        data: List of all captured raw values in the current cycle.
        last_sent_time: Timestamp (epoch) of the last successful transmission.
        last_sent_value: The value that was last successfully sent.
        threshold: Minimum difference to the previous value to trigger sending.
        max_change: Maximum allowed change. Larger jumps are discarded as errors.
        max_interval: Maximum time interval in seconds after which a value is forced to send.
    """
    name: str
    unit: str
    field_name: str  # Name of the field in InfluxDB
    decimals: int    # Rounding precision
    data: List[Any] = field(default_factory=list) 
    last_sent_time: float = 0.0
    last_sent_value: Optional[float] = None
    threshold: float = 0.0
    max_change: float = float('inf') # Default: Infinity (no filter)
    max_interval: float = DEFAULT_MAX_INTERVAL

    def update_and_check(self, new_value: Optional[Any]) -> bool:
        """Checks and updates the register with a new measurement value.

        Applies filter rules (plausibility, threshold, time interval) and decides
        whether the new value should be sent to InfluxDB. The value is appended to
        the internal `data` list in any case.

        Args:
            new_value: The new measurement value of the register.

        Returns:
            True if the value meets the criteria for sending to InfluxDB, otherwise False.
        """
        if new_value is None:
            return False

        current_time = time.time()
        
        # 1. Check: Never sent before? -> Always accept
        if self.last_sent_value is None:
            self.data.append(new_value)
            return True

        try:
            val_float = float(new_value)
            last_float = float(self.last_sent_value)
            change = abs(val_float - last_float)

            # --- FILTER LOGIK ---
            # 2. Check: Is the change too extreme (faulty measurement)?
            if change > self.max_change:
                print(f"   [FILTER] {self.name}: Ignoring jump from {self.last_sent_value} to {new_value} (Diff: {change} > Max: {self.max_change})")
                send_monitoring_log(
                    "warning", "system", 
                    f"unlogic value detected in {val_float} and {last_float}, max {self.max_change}",
                    {"name": self.name}
                )
                return False 
            
            # 3. Check: Time expired (Max Interval)?
            time_diff = current_time - self.last_sent_time
            if time_diff >= self.max_interval:
                self.data.append(new_value)
                return True

            # 4. Check: Minimum threshold exceeded?
            if change >= self.threshold:
                self.data.append(new_value)
                return True
                
        except (ValueError, TypeError):
            pass

        # Value is stable or change too small -> only store internally, do not send
        self.data.append(new_value)
        return False

    def mark_sent(self, value: Any) -> None:
        """Marks a value as successfully sent.

        Updates the timestamp and the last sent value.

        Args:
            value: The sent value.
        """
        self.last_sent_time = time.time()
        self.last_sent_value = value


@dataclass
class HeatMeter:
    """Represents a physical heat meter of an apartment.

    Contains all relevant measurement registers such as energy, power,
    flow, volume, forward/backward temperature, and spreading.

    Attributes:
        apartment: Name/designation of the apartment (e.g., "OG4").
        energy: Register for thermal energy (Wh).
        power: Register for current thermal power (W).
        flow: Register for flow rate (l/h).
        volume: Register for accumulated volume (l).
        t1forward: Register for forward temperature (°C).
        t2backward: Register for backward temperature (°C).
        spreading: Register for temperature difference / spreading (K).
    """
    apartment: str
    # Register instances including limits
    
    energy: Register = field(default_factory=lambda: Register(
        "Energie", "Wh", "Energie", decimals=0, 
        threshold=THRESHOLDS["energy"], max_change=MAX_CHANGE_LIMITS["energy"]
    ))
    
    power: Register = field(default_factory=lambda: Register(
        "Leistung", "W", "Leistung", decimals=0, 
        threshold=THRESHOLDS["power"], max_change=MAX_CHANGE_LIMITS["power"]
    ))
    
    flow: Register = field(default_factory=lambda: Register(
        "Durchfluss", "l/h", "Durchfluss", decimals=0, 
        threshold=THRESHOLDS["flow"], max_change=MAX_CHANGE_LIMITS["flow"]
    ))
    
    volume: Register = field(default_factory=lambda: Register(
        "Volume", "l", "Volume", decimals=0, 
        threshold=THRESHOLDS["volume"], max_change=MAX_CHANGE_LIMITS["volume"]
    ))
    
    t1forward: Register = field(default_factory=lambda: Register(
        "Vorlauf", "C", "Vorlauf", decimals=2, 
        threshold=THRESHOLDS["temp"], max_change=MAX_CHANGE_LIMITS["temp"]
    ))
    
    t2backward: Register = field(default_factory=lambda: Register(
        "Rücklauf", "C", "Rücklauf", decimals=2, 
        threshold=THRESHOLDS["temp"], max_change=MAX_CHANGE_LIMITS["temp"]
    ))
    
    spreading: Register = field(default_factory=lambda: Register(
        "Spreizung", "K", "Spreizung", decimals=2, 
        threshold=THRESHOLDS["spreading"], max_change=MAX_CHANGE_LIMITS["spreading"]
    ))

heatmeters = {
    "83033389": HeatMeter(apartment="OG5"),
    "83033390": HeatMeter(apartment="OG4"),
    "83033391": HeatMeter(apartment="EG4"),
    # "83033388": HeatMeter(apartment="EG5"), wird demnächst eingebaut muss drinne bleiben. 
}

METER_SERIALS = list(heatmeters.keys())

# --- TELEGRAM HELPER ---
def send_telegram_message(message: str) -> None:
    """Sends a message via the Telegram Bot.

    Args:
        message: The message to be sent.
    """
    try:
        url = f"https://api.telegram.org/bot{credentials.BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": credentials.CHAT_ID,
            "text": message
        }
        response = requests.post(url, json=data, timeout=10)
        if response.status_code != 200:
            print(f"   [ERR] Telegram API Error: {response.text}")
        else:
            print(f"   [TELEGRAM] Message sent: {message}")
    except Exception as e:
        print(f"   [ERR] Could not send Telegram message: {e}")


# --- MONITORING HELPER ---
def send_monitoring_log(
    level: str,
    component: str,
    message: str,
    tags: Optional[Dict[str, Any]] = None
) -> None:
    """Sends a log entry to the _monitoring bucket.

    Args:
        level: Log level ("info", "warning", "error", "critical").
        component: Affected component ("mbus", "serial", "influx", "system").
        message: The log message to display.
        tags: Optional tags to structure the logs.
    """
    if not ENABLE_INFLUX:
        return

    try:
        p = Point("service_log") \
            .tag("level", level) \
            .tag("component", component) \
            .field("message", message) \
            .field("event_count", 1)
        
        if tags:
            for k, v in tags.items():
                p.tag(k, v)
                
        with client.write_api(write_options=SYNCHRONOUS) as write_api:
             write_api.write(bucket=INFLUX_MONITORING_BUCKET, org=credentials.INFLUXDB_ORG, record=p)
    except Exception as e:
        print(f"   [ERR] Could not send monitoring log: {e}")


def send_select_manual(ser: serial.Serial, serial_str: str) -> bool:
    """Sends an M-Bus selection telegram for a specific meter.

    Args:
        ser: The open serial connection.
        serial_str: The serial number of the target meter (e.g., "83033389").

    Returns:
        True if the frame was successfully formatted and sent,
        False on error (e.g., invalid hex format).
    """
    try:
        id_bytes = bytearray.fromhex(serial_str)[::-1]
    except ValueError:
        return False
    body = bytearray([0x53, 0xFD, 0x52]) + id_bytes + bytearray([0xFF, 0xFF, 0xFF, 0xFF])
    checksum = sum(body) % 256
    length = len(body)
    frame = bytearray([0x68, length, length, 0x68]) + body + bytearray([checksum, 0x16])
    ser.write(frame)
    return True

def read_meter_with_retry(ser: serial.Serial, serial_number: str) -> Optional[List[Any]]:
    """Reads a specific heat meter via M-Bus with retries.

    Args:
        ser: The open serial connection.
        serial_number: The serial number of the meter.

    Returns:
        List of M-Bus records on success, or None on connection or
        plausibility errors.
    """
    print(f"   Reading meter: {serial_number}", end="... ", flush=True)
    
    apt_name = "Unknown"
    if serial_number in heatmeters:
        apt_name = heatmeters[serial_number].apartment

    for attempt in range(1, RETRIES + 1):
        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            send_select_manual(ser, serial_number)
            time.sleep(0.4) 
            ser.reset_input_buffer()
            
            req_frame = bytearray([0x10, 0x7B, 0xFD, 0x78, 0x16])
            ser.write(req_frame)
            time.sleep(1.2) 

            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
            else:
                data = ser.read(4096)
            
            if len(data) == 0:
                time.sleep(1)
                continue

            if data[0] == 0xE5 and len(data) > 1:
                 data = data[1:]

            if data[0] != 0x68:
                continue

            telegram = meterbus.load(data)

            # --- ID CHECK (SAFETY) ---
            received_id = "Unknown"
            
            try:
                header = None
                if hasattr(telegram, 'body') and hasattr(telegram.body, 'bodyHeader'):
                    header = telegram.body.bodyHeader
                elif hasattr(telegram, 'header'):
                    header = telegram.header

                if header:
                    val = None
                    if hasattr(header, 'id_str'):
                        val = header.id_str
                    elif hasattr(header, 'identification'):
                        val = header.identification
                    elif hasattr(header, 'id_nr'):
                        val = header.id_nr
                    
                    if val is not None:
                        if isinstance(val, list) or isinstance(val, tuple):
                             received_id = "".join("{:02x}".format(x) for x in val)
                        else:
                             received_id = str(val)

            except Exception:
                pass 

            if received_id != "Unknown" and received_id != str(serial_number):
                print(f" [MISMATCH! Req:{serial_number} != Rec:{received_id}]", end=" ")
                send_monitoring_log(
                    "error", "mbus", 
                    f"ID Mismatch. Req: {serial_number}, Rec: {received_id}", 
                    {"apartment": apt_name, "serial": serial_number}
                )
                ser.reset_input_buffer()
                time.sleep(1.5) 
                continue 
            
            print(f"OK ({len(telegram.records)} Rec).")
            return telegram.records

        except Exception as e:
            print(f" (Error: {e})", end="")
            send_monitoring_log(
                "warning", "mbus", 
                f"Communication Error: {e}", 
                {"apartment": apt_name, "serial": serial_number}
            )
            time.sleep(1)

    print(" ERROR (No Response/Timeout).")
    send_monitoring_log(
        "error", "mbus", 
        "Timeout / No Response", 
        {"apartment": apt_name, "serial": serial_number}
    )
    return None

def collect_meter_point(
    serial: str,
    records: List[Any],
    meter_obj: HeatMeter
) -> Tuple[Optional[Point], int]:
    """Parses the M-Bus records and creates an InfluxDB point if changes are relevant.

    For each register, it checks whether the value has changed significantly or
    if the maximum time interval has expired to justify a transmission.

    Args:
        serial: Serial number of the heat meter.
        records: The read M-Bus records.
        meter_obj: The corresponding HeatMeter data object.

    Returns:
        Tuple[Optional[Point], int]: A tuple of (InfluxDB Point object or None,
            number of changed/flagged values).
    """
    try:
        val_energy = records[0].value if len(records) > 0 else None
        val_volume = (records[2].value * 1000) if len(records) > 2 else None
        val_forward = records[7].value if len(records) > 7 else None
        val_backward = records[8].value if len(records) > 8 else None
        val_diff = records[9].value if len(records) > 9 else None
        val_power = records[10].value if len(records) > 10 else None
        val_flow = (records[12].value * 1000) if len(records) > 12 else None
    except Exception as e:
        print(f"   Parsing error at {serial}: {e}")
        send_monitoring_log("error", "parser", f"Parse Error: {e}", {"apartment": meter_obj.apartment})
        return None, 0

    check_list = [
        (meter_obj.energy, val_energy),
        (meter_obj.power, val_power),
        (meter_obj.flow, val_flow),
        (meter_obj.volume, val_volume),
        (meter_obj.t1forward, val_forward),
        (meter_obj.t2backward, val_backward),
        (meter_obj.spreading, val_diff),
    ]

    fields_to_send = {}

    for reg, val in check_list:
        if reg.update_and_check(val):
            if val is not None:
                try:
                    rounded_val = round(float(val), reg.decimals)
                    if reg.decimals == 0:
                        fields_to_send[reg.field_name] = int(rounded_val)
                    else:
                        fields_to_send[reg.field_name] = rounded_val
                        
                    reg.mark_sent(val)
                except (ValueError, TypeError):
                    pass

    if not fields_to_send:
        return None, 0

    val_count = len(fields_to_send)
    print(f"   -> {val_count} values flagged for {meter_obj.apartment}.")

    p = Point("heat_meter").tag("Wohnung", meter_obj.apartment)

    for field_name, value in fields_to_send.items():
        p.field(field_name, value)
    
    return p, val_count

def write_batch_with_retry(points: List[Point], total_count: int = 0) -> None:
    """Writes a batch of data points to InfluxDB with retry logic.

    Args:
        points: List of InfluxDB data points to write.
        total_count: Total count of individual values contained (for logging).
    """
    if not points:
        return

    print(f"-> Sending {len(points)} points ({total_count} values) to InfluxDB...")
    write_api = client.write_api(write_options=SYNCHRONOUS)

    for attempt in range(1, INFLUX_RETRIES + 1):
        try:
            write_api.write(bucket=INFLUX_BUCKET, org=credentials.INFLUXDB_ORG, record=points)
            print("   [OK] Data successfully written.")
            return
        except InfluxDBError as e:
            print(f"   [ERR] Influx Error (Attempt {attempt}/{INFLUX_RETRIES}): {e}")
            if attempt == INFLUX_RETRIES:
                 send_monitoring_log("error", "influx", f"Write failed after retries: {e}")
        except Exception as e:
            print(f"   [ERR] Connection/Unknown (Attempt {attempt}/{INFLUX_RETRIES}): {e}")
            if attempt == INFLUX_RETRIES:
                 send_monitoring_log("error", "influx", f"Connection failed: {e}")
        
        if attempt < INFLUX_RETRIES:
            time.sleep(INFLUX_RETRY_DELAY)
    
    print("   [FAIL] Data could not be sent. Discarding batch.")

def main() -> None:
    """Main function of the heatmeter service.

    Initializes the serial interface, polls the defined meters periodically,
    filters the data, and sends it to InfluxDB. Also implements a watchdog
    as well as error monitoring via Telegram and InfluxDB.
    """
    print(f"Starting Heatmeter Service (Interval: {POLL_INTERVAL}s)...")
    print(f"Start delay. Waiting {START_DELAY}s...")
    send_monitoring_log("info", "system", "Service Started")
    send_telegram_message("ℹ️ M-Bus Heizungs-Logger Dienst wurde gestartet.")
    time.sleep(START_DELAY)
    
    # --- Watchdog variables ---
    last_successful_data_time = time.time()
    telegram_alert_sent = False
    
    while True:
        cycle_start = time.time()
        batch_points = []
        total_value_sum = 0
        success_count = 0

        try:
            with serial.Serial(PORT, BAUDRATE, 8, serial.PARITY_EVEN, 1, timeout=3) as ser:
                
                ser.setDTR(False)
                ser.setRTS(False)
                time.sleep(0.5)
                ser.setDTR(True)
                ser.setRTS(True)
                time.sleep(0.5)

                ser.write(bytearray([0x10, 0x40, 0xFE, 0x3E, 0x16]))
                time.sleep(1)

                print(f"\n--- Polling Cycle {datetime.now().strftime('%H:%M:%S')} ---")

                for serial_num in METER_SERIALS:
                    if serial_num not in heatmeters:
                        continue
                        
                    meter_obj = heatmeters[serial_num]
                    records = read_meter_with_retry(ser, serial_num)
                    
                    if records:
                        success_count += 1
                        p, val_count = collect_meter_point(serial_num, records, meter_obj)
                        if p:
                            batch_points.append(p)
                            total_value_sum += val_count
                        else:
                            print(f"   -> No valid changes for {meter_obj.apartment}.")
                    
                    time.sleep(1)

            if batch_points and ENABLE_INFLUX:
                write_batch_with_retry(batch_points, total_value_sum)
            elif not batch_points:
                print("-> No data to send in this cycle.")

        except Exception as e:
            print(f"Main loop error (USB problem?): {e}")
            send_monitoring_log("critical", "serial", f"Main Loop Crash: {e}")
            time.sleep(5)
        
        # --- WATCHDOG CHECK ---
        if success_count > 0:
            last_successful_data_time = time.time()
            if telegram_alert_sent:
                send_telegram_message("✅ Entwarnung: M-Bus Heizungs-Logger empfängt wieder erfolgreich Daten!")
                telegram_alert_sent = False
        else:
            time_without_data = time.time() - last_successful_data_time
            if time_without_data >= TELEGRAM_ALERT_TIMEOUT and not telegram_alert_sent:
                msg = f"⚠️ ALARM: Der M-Bus Heizungs-Logger hat seit {time_without_data/60:.0f} Minuten keine validen Daten mehr empfangen!"
                send_telegram_message(msg)
                send_monitoring_log("critical", "system", "Watchdog triggered: Telegram alert sent")
                telegram_alert_sent = True

        # Calculate time until next interval
        elapsed = time.time() - cycle_start
        wait_time = max(0, POLL_INTERVAL - elapsed)
        
        if success_count == 0:
            print("ATTENTION: All meter requests failed! Switching to 'Cool-Down' mode (300s).")
            send_monitoring_log("warning", "system", "Entering Cool-Down Mode (All meters failed)")
            wait_time = 300

        print(f"Sleeping {wait_time:.1f} seconds...")
        time.sleep(wait_time)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nTerminated by user.")
        send_monitoring_log("info", "system", "Service Stopped by User")
        send_telegram_message("⏹️ M-Bus Heizungs-Logger Dienst wurde manuell beendet.")
        client.close()
