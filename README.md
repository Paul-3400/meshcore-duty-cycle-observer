# MeshCore-duty-cycle-observer

Web-based dashboard for visualizing [MeshCore](https://meshcore.io/) LoRa packet activity – a passive observer that decodes, logs, and analyzes all MeshCore traffic via MQTT.

![Python](https://img.shields.io/badge/Python-3.13-blue)
![Flask](https://img.shields.io/badge/Flask-3.1-green)
![Leaflet](https://img.shields.io/badge/Leaflet.js-1.9-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)
---

## 📋 Prerequisites

Before installing the observer, make sure the following components are running:

1. **MeshCore Node** – Connected via USB (e.g. Heltec V3)
2. **Packet Capture Client** – Reads packets from the node and publishes them to MQTT. See [meshcore-packet-capture](https://github.com/fdlamber/meshcore-packet-capture)
3. **MQTT Broker** – e.g. Mosquitto, running on localhost or a reachable host

```bash
# Verify MQTT is running and packets are flowing:
mosquitto_sub -h localhost -t "meshcore/#" -v
```
> If you see JSON packets appearing, your pipeline is ready for the observer!
---

## 🔧 Installation

### Step 1: Clone Repository

```bash
cd ~
git clone https://github.com/Paul-3400/meshcore-duty-cycle-observer.git
cd meshcore-duty-cycle-observer
```

### Step 2: Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3: Create Configuration

```bash
cp config.example.json config.json
nano config.json
```

Edit the MQTT topic to match your setup (see Configuration below).

### Step 4: Create Log Directory

```bash
mkdir -p logs
```

### Step 5: Test Run

```bash
source venv/bin/activate
python3 monitor.py
```

You should see:

```
Verbunden mit localhost:1883
Topic: meshcore/+/+/packets
LoRa: SF8, BW 62.5kHz, CR 4/8
DC-Fenster: 3600s, Limit: 10.0%
```

Press `Ctrl + C` to stop.

---

## ⚙️ Configuration

Edit `config.json` to match your setup:

```json
{
  "mqtt": {
    "broker": "localhost",
    "port": 1883,
    "topic": "meshcore/+/+/packets"
  },
  "lora": {
    "spreading_factor": 8,
    "bandwidth_hz": 62500,
    "coding_rate": 4,
    "preamble": 8
  },
  "duty_cycle": {
    "window_sec": 3600,
    "limit_pct": 10.0,
    "warning_pct": 8.0
  },
  "buffer": {
    "delay_sec": 0
  },
  "logging": {
    "log_dir": "logs"
  },
  "channels": [
    "#Public",
    "#test",
    "#switzerland",
    "#bern",
    "#hamradio"
  ]
}
```

### Configuration Reference

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| mqtt | broker | localhost | MQTT broker hostname or IP |
| mqtt | port | 1883 | MQTT broker port |
| mqtt | topic | meshcore/+/+/packets | MQTT topic to subscribe to. Adjust to match your packet capture client. The `+` are MQTT wildcards. |
| lora | spreading_factor | 8 | LoRa spreading factor (SF7–SF12) |
| lora | bandwidth_hz | 62500 | LoRa bandwidth in Hz |
| lora | coding_rate | 4 | LoRa coding rate (4 = CR 4/8) |
| lora | preamble | 8 | LoRa preamble length |
| duty_cycle | window_sec | 3600 | Duty cycle calculation window (seconds) |
| duty_cycle | limit_pct | 10.0 | Duty cycle limit (EU regulation: 10%) |
| duty_cycle | warning_pct | 8.0 | Warning threshold percentage |
| buffer | delay_sec | 0 | Duplicate filter delay. 0 = disabled (recommended for analyzer comparison) |
| logging | log_dir | logs | Directory for CSV files (relative to project) |
| channels | | | List of channel hashtags to decrypt. Secrets are auto-calculated from the hashtag name via SHA-256. |

---

## 🚀 Running as a systemd Service

### Step 1: Edit the Service File

```bash
nano duty-cycle-monitor.service.example
```

Replace every `YOUR_USERNAME` with your actual Linux username:

```bash
# Find your username:
whoami
```

### Step 2: Install the Service

```bash
sudo cp duty-cycle-monitor.service.example /etc/systemd/system/meshcore-observer.service
sudo systemctl daemon-reload
sudo systemctl enable meshcore-observer
sudo systemctl start meshcore-observer
```

### Step 3: Verify

```bash
sudo systemctl status meshcore-observer
journalctl -u meshcore-observer -n 20 --no-pager
```

### Service Commands

| Action | Command |
|--------|---------|
| Start | `sudo systemctl start meshcore-observer` |
| Stop | `sudo systemctl stop meshcore-observer` |
| Restart | `sudo systemctl restart meshcore-observer` |
| Status | `sudo systemctl status meshcore-observer` |
| Live log | `journalctl -u meshcore-observer -f` |
| Last 50 lines | `journalctl -u meshcore-observer -n 50 --no-pager` |
| Enable at boot | `sudo systemctl enable meshcore-observer` |
| Disable at boot | `sudo systemctl disable meshcore-observer` |

---

## 📊 CSV Output

The observer writes one CSV file per day to the `logs/` directory:

```
logs/duty_cycle_2026-04-21.csv
```

- **Separator:** Semicolon (`;`)
- **Encoding:** UTF-8
- **Header:** First row contains column names
- **Format:** 25 columns (see below)

### CSV Columns

| Column | Name | Unit | Description |
|--------|------|------|-------------|
| A | packet_hash | Hex (16 chars) | Unique hash of the full packet (changes per hop) |
| B | payload_hash | Hex (16 chars) | Hash of payload only (identical across observers) |
| C | timestamp | ISO 8601 | Time of reception |
| D | packet_type | Text | ADVERT, REQ, RESPONSE, TXT_MSG, ACK, GRP_TXT, ANON_REQ, PATH, TRACE, CONTROL, or GRP_DATA |
| E | route_type | Text | FLOOD, DIRECT, T_FLOOD, T_DIRECT |
| F | bytes | Bytes | Total packet size |
| G | airtime_ms | Milliseconds | Calculated LoRa airtime |
| H | hops | Count | Number of relay hops |
| I | path_hash_bytes | 1, 2, or 3 | Bytes per hop hash (1=legacy, 2=mode 1, 3=mode 2) |
| J | rssi | dBm | Received signal strength (-43=strong, -117=weak) |
| K | snr | dB | Signal-to-noise ratio (+10=clear, -8=noisy) |
| L | source_hash | Hex (2 chars) | Sender short hash |
| M | source_name | Text | Sender name (from ADVERT lookup) |
| N | source_collision | Count | Nodes sharing this source hash |
| O | dest_hash | Hex (2 chars) | Recipient short hash (empty for FLOOD) |
| P | dest_name | Text | Recipient name (from ADVERT lookup) |
| Q | dest_collision | Count | Nodes sharing this dest hash |
| R | node_mode | Text | Repeater, Room Server, or Client (ADVERT only) |
| S | node_key | Hex (8 chars) | First 4 bytes of public key (ADVERT only) |
| T | lat | Decimal degrees | GPS latitude, decoded from int32 microdegrees (ADVERT only) |
| U | lon | Decimal degrees | GPS longitude, decoded from int32 microdegrees (ADVERT only) |
| V | region_code | Hex (2 chars) | Transport region code (T_FLOOD/T_DIRECT only) |
| W | channel | Hex (2 chars) | Channel hash (GRP_TXT only) |
| X | message | Text | Decrypted message (GRP_TXT with known key) |
| Y | window_dc_pct | Percent (%) | Cumulative duty cycle in current 1-hour window |

For detailed descriptions with everyday analogies, see `CSV_COLUMNS.txt`.

### Importing into a Spreadsheet

**Apple Numbers:**
- Access CSV via Samba share or copy with `scp`
- Open in Numbers → Import settings: Separator = Semicolon, Encoding = UTF-8

**Excel / LibreOffice Calc:**
- File → Open → Select CSV
- Set delimiter to Semicolon, encoding UTF-8

---

## 🔍 Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| No packets received | MQTT pipeline not running | Check: `mosquitto_sub -h localhost -t "meshcore/#" -v` |
| "Verbindung getrennt" loop | Duplicate monitor instance | `pkill -f monitor.py`, then restart service |
| CSV is empty (header only) | Packet capture client down | Check your capture client: `journalctl -u meshcore-capture -n 10` |
| ModuleNotFoundError | venv not activated | `source venv/bin/activate` before running |
| "config.json nicht gefunden" | Missing config file | `cp config.example.json config.json` |
| Zombie process after reboot | Old process survived | `ps aux | grep monitor.py` → `kill <PID>` |
| GPS always 0.0 or near-zero | GPS decoded as float instead of int32 | Coordinates are signed int32 microdegrees: `struct.unpack("<i", bytes)` |
| USB device not responding | Node needs reset | Unplug/replug USB, restart capture client |

---

## 📖 Protocol Reference

For a detailed description of the MeshCore packet protocol (header format, payload types, routing), see `PROTOCOL.md`.

---

## 📁 Project Structure

```
meshcore-duty-cycle-observer/
├── monitor.py                          # Main application
├── config.example.json                 # Example configuration (copy to config.json)
├── config.json                         # Your local configuration (not in git)
├── requirements.txt                    # Python dependencies
├── duty-cycle-monitor.service.example  # systemd service template
├── CSV_COLUMNS.txt                     # Detailed column descriptions
├── PROTOCOL.md                         # MeshCore protocol reference
├── LICENSE                             # MIT License
├── .gitignore
├── logs/                               # CSV output directory (not in git)
│   └── duty_cycle_YYYY-MM-DD.csv
└── venv/                               # Python virtual environment (not in git)
```

---

## 🌐 Related Projects

| Project | Description | Link |
|---------|-------------|------|
| MeshCore Firmware | The mesh networking firmware | [GitHub](https://github.com/rocketshipio/meshcore) |
| MeshCore Analyzer | Official web analysis platform | [analyzer.letsmesh.net](https://analyzer.letsmesh.net/) |
| meshcore-packet-capture | Packet capture client for MQTT | [GitHub](https://github.com/fdlamber/meshcore-packet-capture) |
| meshcore_py | Python library for MeshCore nodes | [GitHub](https://github.com/fdlamber/meshcore_py) |
| meshcore-cli | CLI tool for MeshCore nodes | [GitHub](https://github.com/fdlamber/meshcore-cli) |
| MeshCore-FAQ | Community FAQ | [GitHub](https://github.com/MeshCore-FAQ/MeshCore-FAQ) |

---

## 🤝 Contributing

Contributions are welcome! Feel free to open issues or submit pull requests.

---

## 📄 License

MIT License – see `LICENSE` for details.

---

## 👤 Author

Paul Simmen ([@Paul-3400](https://github.com/Paul-3400))

Built as a "brain gym" project – keeping the mind sharp through electronics and code. 🧠💪
