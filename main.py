from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Float, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import requests
import threading
import os
import time
import json
import asyncio
from dotenv import load_dotenv
from azure.iot.device.aio import IoTHubDeviceClient
from azure.iot.device import Message
import sqlite3
from math import isnan

load_dotenv()
print("Environment variables loaded")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///sensor_data.db")
print(f"Using DATABASE_URL: {DATABASE_URL}")
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

class SensorData(Base):
    __tablename__ = "sensor_data"
    id = Column(Integer, primary_key=True, index=True)
    temperature = Column(Float)
    humidity = Column(Float)
    light = Column(Integer)
    distance = Column(Integer)
    timestamp = Column(DateTime, default=datetime.utcnow)

class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, index=True)
    message = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

try:
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully")
except Exception as e:
    print(f"Database initialization error: {e}")

SERIAL_BRIDGE_URL = os.getenv("SERIAL_BRIDGE_URL", "http://<raspberry_pi_ip>:8001/serial")
AZURE_IOT_HUB_CONNECTION_STRING = os.getenv("AZURE_IOT_HUB_CONNECTION_STRING")
print(f"Serial bridge config: URL={SERIAL_BRIDGE_URL}")

THRESHOLDS = {
    "temp_high": 30.0,
    "humidity_low": 20.0,
    "distance_close": 10
}

async def send_to_azure(data):
    try:
        client = IoTHubDeviceClient.create_from_connection_string(AZURE_IOT_HUB_CONNECTION_STRING)
        await client.connect()
        msg = Message(json.dumps(data))
        await client.send_message(msg)
        print(f"Sent to Azure: {msg.data}")
        await client.disconnect()
    except Exception as e:
        print(f"Azure IoT Hub error: {e}")

def save_to_db(temp, hum, light, dist):
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

app = FastAPI()
print(f"FastAPI app initialized, binding to 0.0.0.0:{os.getenv('PORT', '10000')}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://enviromon-frontend.vercel.app", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/latest")
async def get_latest():
    try:
        print(f"Fetching from serial bridge: {SERIAL_BRIDGE_URL}")
        response = requests.get(SERIAL_BRIDGE_URL, timeout=10)
        print(f"Serial bridge response: status={response.status_code}, text={response.text[:100]}")
        if response.status_code != 200:
            print(f"Serial bridge HTTP error: {response.status_code}")
            return {
                "temperature": None,
                "humidity": None,
                "light": None,
                "distance": None,
                "timestamp": None,
                "error": f"Bridge HTTP error: {response.status_code}"
            }
        
        line = response.text.strip()
        if line.startswith('{"error":'):
            error_data = json.loads(line)
            print(f"Serial bridge error: {error_data['error']}")
            return {
                "temperature": None,
                "humidity": None,
                "light": None,
                "distance": None,
                "timestamp": None,
                "error": error_data['error']
            }
        
        clean_line = line.strip('"')
        parts = clean_line.split(", ")
        if len(parts) != 4:
            print(f"Parse error: Expected 4 parts, got {len(parts)}: {clean_line}")
            return {
                "temperature": None,
                "humidity": None,
                "light": None,
                "distance": None,
                "timestamp": None,
                "error": "Invalid data format"
            }
        
        temp_str = parts[0].split(": ")[1].split(" ")[0]
        hum_str = parts[1].split(": ")[1].split(" ")[0]
        light_str = parts[2].split(": ")[1].split(" ")[0]
        dist_str = parts[3].split(": ")[1].split(" ")[0]
        
        temp = float(temp_str)
        hum = float(hum_str)
        light = int(float(light_str))
        dist = int(float(dist_str))
        
        if isnan(temp) or isnan(hum) or light < 0 or dist < 0:
            print(f"Invalid sensor values: temp={temp}, hum={hum}, light={light}, dist={dist}")
            return {
                "temperature": None,
                "humidity": None,
                "light": None,
                "distance": None,
                "timestamp": None,
                "error": "Invalid sensor values"
            }
        
        timestamp = datetime.utcnow().isoformat()
        data = {
            "temperature": temp,
            "humidity": hum,
            "light": light,
            "distance": dist,
            "timestamp": timestamp
        }
        print(f"Parsed → Temp: {temp} °C, Hum: {hum} %, Light: {light} %, Dist: {dist} cm")
        
        # Parallel tasks: Azure upload and DB save
        if AZURE_IOT_HUB_CONNECTION_STRING:
            asyncio.create_task(send_to_azure(data))
        threading.Thread(target=save_to_db, args=(temp, hum, light, dist), daemon=True).start()
        
        return data
    except requests.exceptions.RequestException as e:
        print(f"Serial bridge network error: {e}")
        return {
            "temperature": None,
            "humidity": None,
            "light": None,
            "distance": None,
            "timestamp": None,
            "error": f"Network error: {str(e)}"
        }
    except Exception as e:
        print(f"Unexpected error: {e}")
        return {
            "temperature": None,
            "humidity": None,
            "light": None,
            "distance": None,
            "timestamp": None,
            "error": f"Unexpected error: {str(e)}"
        }

@app.get("/api/history")
async def get_history(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
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
async def get_alerts(limit: int = Query(10, ge=1, le=50), offset: int = Query(0, ge=0)):
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

@app.get("/health")
async def health_check():
    return {"status": "healthy", "serial_bridge_url": SERIAL_BRIDGE_URL}