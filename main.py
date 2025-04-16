from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Float, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import requests
import threading
import time
import os
from dotenv import load_dotenv
from azure.iot.device import IoTHubDeviceClient, Message
import json
import sqlite3
from math import isnan

# Load environment variables
load_dotenv()

# SQLite setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///sensor_data.db")
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

# Sensor data model
class SensorData(Base):
    __tablename__ = "sensor_data"
    id = Column(Integer, primary_key=True, index=True)
    temperature = Column(Float)
    humidity = Column(Float)
    light = Column(Integer)
    distance = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)

# Alerts model
class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, index=True)
    message = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# Serial and Azure config
SERIAL_BRIDGE_URL = os.getenv("SERIAL_BRIDGE_URL")
AZURE_IOT_HUB_CONNECTION_STRING = os.getenv("AZURE_IOT_HUB_CONNECTION_STRING")
USE_SERIAL_BRIDGE = os.getenv("USE_SERIAL_BRIDGE", "false").lower() == "true"

# Store latest values
latest_data = {
    "temperature": None,
    "humidity": None,
    "light": None,
    "distance": None,
    "timestamp": None
}

# Thresholds for alerts
THRESHOLDS = {
    "temp_high": 30.0,
    "humidity_low": 20.0,
    "distance_close": 10
}

# Background thread to read serial data from bridge
def read_serial_bridge():
    client = None
    if AZURE_IOT_HUB_CONNECTION_STRING:
        try:
            client = IoTHubDeviceClient.create_from_connection_string(AZURE_IOT_HUB_CONNECTION_STRING)
            client.connect()
            print("Connected to Azure IoT Hub")
        except Exception as e:
            print(f"Azure IoT Hub connection error: {e}")
    
    while True:
        try:
            if USE_SERIAL_BRIDGE:
                response = requests.get(SERIAL_BRIDGE_URL, timeout=5)
                if response.status_code == 200:
                    line = response.text.strip()
                    print(f"Serial bridge input: '{line}'")
                    if line:
                        try:
                            parts = line.split(", ")
                            if len(parts) != 4:
                                raise ValueError(f"Expected 4 parts, got {len(parts)}: {line}")
                            
                            temp_str = parts[0].split(": ")[1].split(" ")[0]
                            hum_str = parts[1].split(": ")[1].split(" ")[0]
                            light_str = parts[2].split(": ")[1].split(" ")[0]
                            dist_str = parts[3].split(": ")[1].split(" ")[0]
                            
                            temp = float(temp_str)
                            hum = float(hum_str)
                            light = int(float(light_str))
                            dist = int(float(dist_str))
                            
                            if isnan(temp) or isnan(hum) or light < 0 or dist < 0:
                                raise ValueError(f"Invalid sensor values: temp={temp}, hum={hum}, light={light}, dist={dist}")
                            
                            timestamp = datetime.utcnow().isoformat()
                            latest_data.update({
                                "temperature": temp,
                                "humidity": hum,
                                "light": light,
                                "distance": dist,
                                "timestamp": timestamp
                            })
                            print(f"Parsed → Temp: {temp} °C, Hum: {hum} %, Light: {light} %, Dist: {dist} cm")
                            
                            for attempt in range(3):
                                session = SessionLocal()
                                try:
                                    print("Attempting to save to DB...")
                                    data = SensorData(temperature=temp, humidity=hum, light=light, distance=dist)
                                    session.add(data)
                                    
                                    alerts = []
                                    if temp > THRESHOLDS["temp_high"]:
                                        alerts.append(f"High temperature: {temp}°C")
                                    if hum < THRESHOLDS["humidity_low"]:
                                        alerts.append(f"Low humidity: {hum}%")
                                    if dist > 0 and dist < THRESHOLDS["distance_close"]:
                                        alerts.append(f"Motion detected: {dist} cm")
                                    
                                    for alert_msg in alerts:
                                        alert = Alert(message=alert_msg)
                                        session.add(alert)
                                    
                                    session.commit()
                                    print(f"Saved → Temp: {temp} °C, Hum: {hum} %, Light: {light} %, Dist: {dist} cm")
                                    break
                                except sqlite3.OperationalError as db_err:
                                    print(f"Database error (attempt {attempt + 1}): {db_err}")
                                    session.rollback()
                                    if "locked" in str(db_err).lower() and attempt < 2:
                                        time.sleep(0.5)
                                        continue
                                    raise
                                except Exception as db_err:
                                    print(f"Unexpected database error: {db_err}")
                                    session.rollback()
                                    raise
                                finally:
                                    session.close()
                            
                            if client:
                                msg = Message(json.dumps({
                                    "temperature": temp,
                                    "humidity": hum,
                                    "light": light,
                                    "distance": dist,
                                    "timestamp": timestamp
                                }))
                                client.send_message(msg)
                                print(f"Sent to Azure: {msg.data}")
                        
                        except Exception as parse_err:
                            print(f"Parse error: {parse_err}")
                else:
                    print(f"Serial bridge error: {response.status_code}")
            else:
                print("Serial bridge disabled")
        
        except Exception as e:
            print(f"Serial bridge read error: {e}")
        time.sleep(1)

# Start serial reading thread
if USE_SERIAL_BRIDGE:
    threading.Thread(target=read_serial_bridge, daemon=True).start()
else:
    print("Serial bridge communication disabled")

# FastAPI App
app = FastAPI()

# CORS for Next.js
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://<your_frontend_url>", "*"], # Update with frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/latest")
def get_latest():
    print(f"Returning latest_data: {latest_data}")
    return latest_data

@app.get("/api/history")
def get_history(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    session = SessionLocal()
    try:
        results = (
            session.query(SensorData)
            .order_by(SensorData.timestamp.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        print(f"History query returned {len(results)} records, latest timestamp: {results[0].timestamp if results else 'none'}")
        return [
            {
                "temperature": r.temperature,
                "humidity": r.humidity,
                "light": r.light,
                "distance": r.distance,
                "timestamp": r.timestamp.isoformat()
            }
            for r in results
        ]
    except Exception as e:
        print(f"History query error: {e}")
        return []
    finally:
        session.close()

@app.get("/api/alerts")
def get_alerts(limit: int = Query(10, ge=1, le=50), offset: int = Query(0, ge=0)):
    session = SessionLocal()
    try:
        results = (
            session.query(Alert)
            .order_by(Alert.timestamp.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
        print(f"Alerts query returned {len(results)} records")
        return [
            {
                "message": r.message,
                "timestamp": r.timestamp.isoformat()
            }
            for r in results
        ]
    except Exception as e:
        print(f"Alerts query error: {e}")
        return []
    finally:
        session.close()