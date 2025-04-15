from sqlalchemy import create_engine, Column, Float, Integer, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# SQLite setup
engine = create_engine("sqlite:///sensor_data.db", echo=False)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine)

class SensorData(Base):
    __tablename__ = "sensor_data"
    id = Column(Integer, primary_key=True, index=True)
    temperature = Column(Float)
    humidity = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import serial
import threading
import time
import os
from dotenv import load_dotenv

load_dotenv()

# Load serial config from .env
SERIAL_PORT = os.getenv("SERIAL_PORT")
BAUD_RATE = int(os.getenv("BAUD_RATE", "9600"))

# Serial Setup
ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
time.sleep(2)  # Wait for Arduino to reboot

# Store latest values
latest_data = {"temperature": None, "humidity": None}

# Background thread to read serial data
def read_serial():
    while True:
        try:
            line = ser.readline().decode('utf-8').strip()
            if line:
                temp, hum = line.split(",")
                latest_data["temperature"] = float(temp)
                latest_data["humidity"] = float(hum)
                print(f"Updated → Temp: {temp} °C, Hum: {hum} %")
                # Save to DB
                session = SessionLocal()
                data = SensorData(temperature=float(temp), humidity=float(hum))
                session.add(data)
                session.commit()
                session.close()
                print(f"Saved → Temp: {temp} °C, Hum: {hum} %")

        except Exception as e:
            print(f"Error reading serial: {e}")
        time.sleep(1)

# Start serial reading thread
threading.Thread(target=read_serial, daemon=True).start()

# FastAPI App
app = FastAPI()

# Allow frontend to call API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all for dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/history")
def get_history():
    session = SessionLocal()
    results = session.query(SensorData).order_by(SensorData.timestamp.desc()).limit(50).all()
    session.close()
    return [
        {
            "temperature": r.temperature,
            "humidity": r.humidity,
            "timestamp": r.timestamp.isoformat()
        }
        for r in reversed(results)  # show oldest first
    ]
@app.get("/api/latest")
def get_latest():
    return latest_data