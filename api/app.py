import os
import secrets
from datetime import datetime
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field
from pymongo import MongoClient
import paho.mqtt.publish as publish
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

client = MongoClient(os.getenv("MONGODB_URI"))
db = client["cold_room_db"]
devices_collection = db["devices"]
readings_collection = db["readings"]
alarms_collection = db["alarms"]

MQTT_NAME = os.getenv("MQTT_NAME", "student")
MQTT_BROKER = "135.232.203.144"

class DeviceCreate(BaseModel):
    mac: str
    room: str

class ReadingCreate(BaseModel):
    mac: str
    temp: float = Field(ge=-40, le=80)

class AlarmCreate(BaseModel):
    max_temp: float

@app.post("/devices", status_code=status.HTTP_201_CREATED)
def register_device(device: DeviceCreate):
    existing = devices_collection.find_one({"mac": device.mac})
    if existing:
        raise HTTPException(status_code=409, detail="Device already registered")
    
    key = secrets.token_urlsafe(16)
    doc = {
        "mac": device.mac,
        "room": device.room,
        "key": key
    }
    devices_collection.insert_one(doc)
    return {"key": key}

@app.get("/devices")
def get_devices():
    devices = list(devices_collection.find({}, {"_id": 0, "key": 0}))
    return devices

@app.post("/readings", status_code=status.HTTP_201_CREATED)
def create_reading(reading: ReadingCreate, x_api_key: str | None = Header(default=None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    
    device = devices_collection.find_one({"mac": reading.mac})
    if not device:
        device_by_key = devices_collection.find_one({"key": x_api_key})
        if device_by_key:
            raise HTTPException(status_code=403, detail="Key belongs to another MAC")
        else:
            raise HTTPException(status_code=401, detail="Invalid API key")
            
    if device["key"] != x_api_key:
        raise HTTPException(status_code=403, detail="Key belongs to another MAC")

    reading_doc = {
        "mac": reading.mac,
        "temp": reading.temp,
        "time": datetime.utcnow().isoformat()
    }
    readings_collection.insert_one(reading_doc)
    return {"message": "Reading saved"}

@app.get("/readings")
def get_readings(mac: str | None = None, since: str | None = None):
    query = {}
    if mac:
        query["mac"] = mac
    if since:
        query["time"] = {"$gte": since}
        
    readings = list(readings_collection.find(query, {"_id": 0}).sort("time", -1))
    return readings

@app.put("/alarm/{mac}")
def set_alarm(mac: str, alarm: AlarmCreate):
    device = devices_collection.find_one({"mac": mac})
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
        
    existing_alarm = alarms_collection.find_one({"mac": mac})
    status_code = status.HTTP_200_OK
    if not existing_alarm:
        status_code = status.HTTP_201_CREATED
        
    alarms_collection.update_one(
        {"mac": mac},
        {"$set": {"max_temp": alarm.max_temp}},
        upsert=True
    )
    
    topic = f"ecse3038/{MQTT_NAME}/alarm/max"
    publish.single(topic, str(alarm.max_temp), hostname=MQTT_BROKER, retain=True)
    
    return {"message": "Alarm updated", "max_temp": alarm.max_temp}