from fastapi import FastAPI, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON, or_, UniqueConstraint, inspect, text
from sqlalchemy.orm import declarative_base, relationship, backref, Session, sessionmaker
from pydantic import BaseModel, computed_field
from datetime import datetime, date, time, timedelta
from typing import List, Optional, Any, Dict
import os
import re
import math
import json
import asyncio
import time
import hmac
import hashlib
import urllib.request
import urllib.parse
import socket
import threading
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-change-me')
INSECURE_DEV = os.getenv('INSECURE_DEV', 'false').lower() == 'true'
TAIP_UDP_PORT = int(os.getenv('TAIP_UDP_PORT', os.getenv('TAIP_PORT', '5005')))
TAIP_TCP_PORT = int(os.getenv('TAIP_TCP_PORT', os.getenv('TAIP_PORT', str(TAIP_UDP_PORT))))
TAIP_MIN_INTERVAL = float(os.getenv('TAIP_MIN_INTERVAL', '0.5'))
TAIP_STALE_SECONDS = int(os.getenv('TAIP_STALE_SECONDS', '60'))
TAIP_OFFLINE_SECONDS = int(os.getenv('TAIP_OFFLINE_SECONDS', '300'))
TAIP_OUT_OF_ORDER_SECONDS = int(os.getenv('TAIP_OUT_OF_ORDER_SECONDS', '5'))
TAIP_MAX_JUMP_MPS = float(os.getenv('TAIP_MAX_JUMP_MPS', '89'))
TAIP_ALLOWLIST = [ip.strip() for ip in os.getenv('TAIP_ALLOWLIST', '').split(',') if ip.strip()]

taip_last_packet: Dict[str, float] = {}

login_attempts = {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_URL = os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL')
if not DATABASE_URL:
    DATABASE_URL = f'sqlite:///{os.path.join(BASE_DIR, "volcad.db").replace(os.sep, "/")}'

if DATABASE_URL.startswith('sqlite'):
    engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ORM Models
class Agency(Base):
    __tablename__ = 'agencies'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    agency_type = Column(String(50), default='fire')
    domain = Column(String(100), unique=True, index=True)
    address = Column(Text)
    city = Column(String(100))
    state = Column(String(2))
    zip_code = Column(String(20))
    lat = Column(Float)
    lng = Column(Float)
    approved = Column(Boolean, default=False)
    approved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    units = relationship('Unit', back_populates='agency')
    personnel = relationship('Personnel', back_populates='agency')
    incidents = relationship('Incident', back_populates='agency')

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True)
    hashed_password = Column(String(255))
    role = Column(String(50), default='responder')
    is_active = Column(Boolean, default=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    created_at = Column(DateTime, default=datetime.utcnow)

class Personnel(Base):
    __tablename__ = 'personnel'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    first_name = Column(String(100))
    last_name = Column(String(100))
    radio_id = Column(String(50), index=True)
    phone = Column(String(50))
    email = Column(String(255))
    sms_phone = Column(String(50))
    duty_status = Column(String(50), default='off_duty')
    current_unit_id = Column(Integer, ForeignKey('units.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    agency = relationship('Agency', back_populates='personnel')
    current_unit = relationship('Unit', foreign_keys=[current_unit_id])

class Location(Base):
    __tablename__ = 'locations'
    id = Column(Integer, primary_key=True, index=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    name = Column(String(255))
    location_type = Column(String(50))
    address = Column(Text)
    lat = Column(Float)
    lng = Column(Float)
    notes = Column(Text)

class Unit(Base):
    __tablename__ = 'units'
    id = Column(Integer, primary_key=True, index=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    name = Column(String(100))
    call_sign = Column(String(50), index=True)
    unit_type = Column(String(50))
    capabilities = Column(JSON)
    station_location_id = Column(Integer, ForeignKey('locations.id'), nullable=True)
    current_status = Column(String(50), default='AQ')
    current_incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=True)
    lat = Column(Float)
    lng = Column(Float)
    heading = Column(Float)
    speed = Column(Float)
    last_seen_at = Column(DateTime)
    radio_id = Column(String(50), index=True)
    taip_id = Column(String(50), index=True)
    taip_destination_url = Column(Text)
    taip_port = Column(Integer)
    camera_url = Column(String(255))
    last_assigned_at = Column(DateTime)
    in_service_at = Column(DateTime)
    accumulated_call_seconds = Column(Float, default=0)
    is_active = Column(Boolean, default=True)
    agency = relationship('Agency', back_populates='units')
    current_incident = relationship('Incident', foreign_keys=[current_incident_id])

class Incident(Base):
    __tablename__ = 'incidents'
    id = Column(Integer, primary_key=True, index=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    incident_number = Column(String(50), index=True)
    call_type = Column(String(100))
    priority = Column(Integer, default=2)
    status = Column(String(50), default='open')
    call_number = Column(String(50), index=True)
    location_text = Column(Text)
    extra = Column(JSON)
    lat = Column(Float)
    lng = Column(Float)
    caller_name = Column(String(255))
    callback = Column(String(50))
    narrative = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    call_entry_started_at = Column(DateTime)
    closed_at = Column(DateTime)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    agency = relationship('Agency', back_populates='incidents')
    assigned_units = relationship('IncidentUnit', back_populates='incident')

class IncidentUnit(Base):
    __tablename__ = 'incident_units'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'))
    unit_id = Column(Integer, ForeignKey('units.id'))
    assigned_at = Column(DateTime, default=datetime.utcnow)
    cleared_at = Column(DateTime)
    assignment_status = Column(String(50), default='assigned')
    disposition = Column(String(100))
    passenger_count = Column(Integer)
    notes = Column(Text)
    incident = relationship('Incident', back_populates='assigned_units')
    unit = relationship('Unit')

class IncidentLocation(Base):
    __tablename__ = 'incident_locations'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), unique=True, index=True)
    raw_address = Column(Text)
    standardized_address = Column(Text)
    city = Column(String(100))
    state = Column(String(2))
    postal_code = Column(String(20))
    latitude = Column(Float)
    longitude = Column(Float)
    cross_streets = Column(String(255))
    zone_id = Column(Integer, ForeignKey('post_zones.id'), nullable=True)
    jurisdiction_id = Column(Integer, ForeignKey('agencies.id'), nullable=True)
    verification_status = Column(String(50), default='unverified')
    geocoded_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    incident = relationship('Incident', backref='incident_location')
    zone = relationship('PostZone')
    jurisdiction = relationship('Agency')

class IncidentPersonnel(Base):
    __tablename__ = 'incident_personnel'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False)
    personnel_id = Column(Integer, ForeignKey('personnel.id'), nullable=False)
    status = Column(String(50), default='en_route')
    en_route_at = Column(DateTime, default=datetime.utcnow)
    arrived_at = Column(DateTime)
    cleared_at = Column(DateTime)
    responding_vehicle = Column(String(100))
    notes = Column(Text)
    personnel = relationship('Personnel')
    incident = relationship('Incident', backref='personnel_responses')

class StatusEvent(Base):
    __tablename__ = 'status_events'
    id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey('units.id'))
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=True)
    status_code = Column(String(50))
    reason = Column(Text)
    destination_location_id = Column(Integer, ForeignKey('locations.id'), nullable=True)
    lat = Column(Float)
    lng = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    unit = relationship('Unit')

class MileageReading(Base):
    __tablename__ = 'mileage_readings'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False)
    unit_id = Column(Integer, ForeignKey('units.id'), nullable=False)
    status_code = Column(String(50), nullable=False)
    mileage = Column(Float, nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow)
    unit = relationship('Unit')
    incident = relationship('Incident')

class TaipPosition(Base):
    __tablename__ = 'taip_positions'
    id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey('units.id'), nullable=True)
    taip_id = Column(String(50), index=True)
    raw_sentence = Column(Text)
    lat = Column(Float)
    lng = Column(Float)
    speed = Column(Float)
    heading = Column(Float)
    ignition = Column(Boolean)
    odometer = Column(Float)
    fix_quality = Column(String(10))
    gps_seconds_of_day = Column(Integer)
    data_age = Column(Integer)
    gps_source = Column(Integer)
    reported_at = Column(DateTime)
    received_at = Column(DateTime, default=datetime.utcnow)
    unit = relationship('Unit')

class CallLog(Base):
    __tablename__ = 'call_logs'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'))
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    log_type = Column(String(50))
    message = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

class DispatchMessage(Base):
    __tablename__ = 'dispatch_messages'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=True)
    unit_id = Column(Integer, ForeignKey('units.id'), nullable=True)
    channel = Column(String(100))
    message_text = Column(Text)
    method = Column(String(50))
    sent_at = Column(DateTime)
    delivered_at = Column(DateTime)

class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(100), index=True)
    entity_type = Column(String(100), index=True)
    entity_id = Column(Integer, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'), nullable=True)
    data = Column(JSON)
    timestamp = Column(DateTime, default=datetime.utcnow)

class Destination(Base):
    __tablename__ = 'destinations'
    id = Column(Integer, primary_key=True, index=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'), nullable=True)
    name = Column(String(255), nullable=False)
    address = Column(Text)
    category = Column(String(50))
    lat = Column(Float)
    lng = Column(Float)
    notes = Column(JSON)
    details = Column(JSON)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class DestinationStatus(Base):
    __tablename__ = 'destination_statuses'
    id = Column(Integer, primary_key=True, index=True)
    destination_id = Column(Integer, ForeignKey('destinations.id'), nullable=False)
    status = Column(String(50), default='open')  # open, divert, on_hold, full, closed
    reason = Column(String(255))
    notes = Column(Text)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    destination = relationship('Destination')

class IncidentDestination(Base):
    __tablename__ = 'incident_destinations'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False)
    destination_id = Column(Integer, ForeignKey('destinations.id'), nullable=False)
    notes = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    destination = relationship('Destination')
    incident = relationship('Incident', backref='destinations')

class TransportLeg(Base):
    __tablename__ = 'transport_legs'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False)
    unit_id = Column(Integer, ForeignKey('units.id'), nullable=False)
    destination_id = Column(Integer, ForeignKey('destinations.id'), nullable=True)
    status = Column(String(50), default='requested')
    requested_at = Column(DateTime, default=datetime.utcnow)
    en_route_at = Column(DateTime)
    arrived_at = Column(DateTime)
    transfer_completed_at = Column(DateTime)
    cleared_at = Column(DateTime)
    pickup_mileage = Column(Float)
    dropoff_mileage = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    destination = relationship('Destination')
    unit = relationship('Unit')
    incident = relationship('Incident', backref='transport_legs')

class ScheduledTransport(Base):
    __tablename__ = 'scheduled_transports'
    id = Column(Integer, primary_key=True, index=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    call_type = Column(String(100), default='Routine Transport')
    patient_name = Column(String(255))
    pickup_address = Column(Text)
    pickup_lat = Column(Float)
    pickup_lng = Column(Float)
    destination_id = Column(Integer, ForeignKey('destinations.id'))
    destination_name = Column(String(255))
    destination_address = Column(Text)
    destination_lat = Column(Float)
    destination_lng = Column(Float)
    scheduled_at = Column(DateTime)
    mobility_level = Column(String(50))
    service_level = Column(String(50), default='BLS')
    oxygen = Column(Boolean, default=False)
    isolation = Column(Boolean, default=False)
    stretcher = Column(Boolean, default=False)
    wheelchair = Column(Boolean, default=False)
    special_equipment = Column(JSON)
    notes = Column(Text)
    status = Column(String(50), default='scheduled')
    unit_id = Column(Integer, ForeignKey('units.id'))
    incident_id = Column(Integer, ForeignKey('incidents.id'))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    agency = relationship('Agency')
    destination = relationship('Destination')
    unit = relationship('Unit')
    incident = relationship('Incident')

class ScheduledEvent(Base):
    __tablename__ = 'scheduled_events'
    id = Column(Integer, primary_key=True, index=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    title = Column(String(255), nullable=False)
    event_type = Column(String(50), default='other')
    location_text = Column(Text)
    lat = Column(Float)
    lng = Column(Float)
    scheduled_at = Column(DateTime)
    duration_minutes = Column(Integer, default=60)
    unit_id = Column(Integer, ForeignKey('units.id'))
    notes = Column(Text)
    status = Column(String(50), default='scheduled')
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    agency = relationship('Agency')
    unit = relationship('Unit')

class PostZone(Base):
    __tablename__ = 'post_zones'
    id = Column(Integer, primary_key=True, index=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    name = Column(String(100))
    zone_type = Column(String(50), default='post')
    color = Column(String(20), default='#3b82f6')
    geojson = Column(JSON)
    display_order = Column(Integer, default=0)
    minimum_units = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    agency = relationship('Agency')
    postings = relationship('UnitPosting', backref='post_zone')

class UnitPosting(Base):
    __tablename__ = 'unit_postings'
    id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey('units.id'))
    post_zone_id = Column(Integer, ForeignKey('post_zones.id'))
    posted_at = Column(DateTime, default=datetime.utcnow)
    removed_at = Column(DateTime)
    is_current = Column(Boolean, default=True)
    posted_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    unit = relationship('Unit')
    posted_by = relationship('User')

class CustomerConfig(Base):
    __tablename__ = 'customer_config'
    id = Column(Integer, primary_key=True, index=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'), nullable=True)
    category = Column(String(50))
    key = Column(String(100))
    value = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint('agency_id', 'category', 'key', name='uix_customer_config'),)

class EpcrExport(Base):
    __tablename__ = 'epcr_exports'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'))
    exported_at = Column(DateTime, default=datetime.utcnow)
    exported_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    epcr_payload = Column(JSON)
    destination_id = Column(Integer, ForeignKey('destinations.id'), nullable=True)
    status = Column(String(50), default='pending')
    external_id = Column(String(255), nullable=True)
    response_body = Column(Text, nullable=True)
    incident = relationship('Incident')
    destination = relationship('Destination')
    exported_by = relationship('User')

def _sqlite_type(col):
    t = col.type
    if isinstance(t, (Integer, Boolean)):
        return 'INTEGER'
    if isinstance(t, Float):
        return 'REAL'
    return 'TEXT'

def ensure_sqlite_columns():
    if not DATABASE_URL.startswith('sqlite'):
        return
    with engine.connect() as conn:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.tables.values():
            if table.name not in existing_tables:
                continue
            existing_cols = {c['name'] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                try:
                    col_type = _sqlite_type(col)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'))
                    conn.commit()
                    print(f'Added column {table.name}.{col.name}')
                except Exception as e:
                    print(f'SQLite migration warning for {table.name}.{col.name}: {e}')

def init_sqlite_db():
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_columns()

print(f'VolCAD using database: {DATABASE_URL}')
Base.metadata.create_all(bind=engine)
if DATABASE_URL.startswith('sqlite'):
    db_path = DATABASE_URL.replace('sqlite:///', '').lstrip('./')
    print(f'SQLite file: {os.path.abspath(db_path)}')
    print('WARNING: SQLite data is stored in a local file. Container redeploys will clear it unless the file is on a persistent volume.')
    ensure_sqlite_columns()

app = FastAPI(title='VolCAD Prototype', version='0.1.0')

app.mount('/static', StaticFiles(directory='static'), name='static')

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class LoginRequest(BaseModel):
    email: str
    password: str

class UserMe(BaseModel):
    user_id: int
    role: str
    email: Optional[str] = None
    agency_id: Optional[int] = None
    modules: List[str] = []
    selected_module: Optional[str] = None
    personnel_id: Optional[int] = None
    cross_discipline_agencies: List[int] = []

class UserModuleUpdate(BaseModel):
    module: str

class UserCreate(BaseModel):
    email: str
    password: str
    role: str = 'responder'
    agency_id: Optional[int] = None

class UserOut(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool
    agency_id: Optional[int] = None
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    agency_id: Optional[int] = None
    is_active: Optional[bool] = None

class DestinationCreate(BaseModel):
    agency_id: Optional[int] = None
    name: str
    address: Optional[str] = None
    category: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    notes: Optional[dict] = None
    details: Optional[dict] = None
    is_active: Optional[bool] = True

class DestinationOut(DestinationCreate):
    id: int
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class DestinationUpdate(BaseModel):
    agency_id: Optional[int] = None
    name: Optional[str] = None
    address: Optional[str] = None
    category: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    notes: Optional[dict] = None
    details: Optional[dict] = None
    is_active: Optional[bool] = None

class IncidentDestinationCreate(BaseModel):
    destination_id: Optional[int] = None
    destination: Optional[DestinationCreate] = None
    notes: Optional[dict] = None

class IncidentDestinationOut(BaseModel):
    id: int
    incident_id: int
    destination_id: int
    notes: Optional[dict] = None
    created_at: Optional[datetime] = None
    destination: DestinationOut
    class Config:
        from_attributes = True

class DestinationStatusCreate(BaseModel):
    destination_id: int
    status: str
    reason: Optional[str] = None
    notes: Optional[str] = None

class DestinationStatusUpdate(BaseModel):
    status: Optional[str] = None
    reason: Optional[str] = None
    notes: Optional[str] = None

class DestinationStatusOut(BaseModel):
    id: int
    destination_id: int
    status: str
    reason: Optional[str] = None
    notes: Optional[str] = None
    updated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    destination: Optional[DestinationOut] = None
    class Config:
        from_attributes = True

class TransportLegCreate(BaseModel):
    incident_id: int
    unit_id: int
    destination_id: Optional[int] = None
    status: Optional[str] = 'requested'
    pickup_mileage: Optional[float] = None

class TransportLegUpdate(BaseModel):
    destination_id: Optional[int] = None
    status: Optional[str] = None
    en_route_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None
    transfer_completed_at: Optional[datetime] = None
    cleared_at: Optional[datetime] = None
    pickup_mileage: Optional[float] = None
    dropoff_mileage: Optional[float] = None

class TransportLegStatusUpdate(BaseModel):
    status: str
    mileage: Optional[float] = None
    timestamp: Optional[datetime] = None

class TransportLegOut(BaseModel):
    id: int
    incident_id: int
    unit_id: int
    destination_id: Optional[int] = None
    status: str
    requested_at: Optional[datetime] = None
    en_route_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None
    transfer_completed_at: Optional[datetime] = None
    cleared_at: Optional[datetime] = None
    pickup_mileage: Optional[float] = None
    dropoff_mileage: Optional[float] = None
    created_at: Optional[datetime] = None
    destination: Optional[DestinationOut] = None
    class Config:
        from_attributes = True

class MileageReadingCreate(BaseModel):
    status_code: str
    mileage: float

class MileageReadingOut(BaseModel):
    id: int
    incident_id: int
    unit_id: int
    status_code: str
    mileage: float
    recorded_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class ScheduledTransportCreate(BaseModel):
    agency_id: int
    call_type: Optional[str] = 'Routine Transport'
    patient_name: Optional[str] = None
    pickup_address: Optional[str] = None
    pickup_lat: Optional[float] = None
    pickup_lng: Optional[float] = None
    destination_id: Optional[int] = None
    destination_name: Optional[str] = None
    destination_address: Optional[str] = None
    destination_lat: Optional[float] = None
    destination_lng: Optional[float] = None
    scheduled_at: Optional[datetime] = None
    mobility_level: Optional[str] = None
    service_level: Optional[str] = 'BLS'
    oxygen: Optional[bool] = False
    isolation: Optional[bool] = False
    stretcher: Optional[bool] = False
    wheelchair: Optional[bool] = False
    special_equipment: Optional[dict] = None
    notes: Optional[str] = None

class ScheduledTransportUpdate(BaseModel):
    call_type: Optional[str] = None
    patient_name: Optional[str] = None
    pickup_address: Optional[str] = None
    pickup_lat: Optional[float] = None
    pickup_lng: Optional[float] = None
    destination_id: Optional[int] = None
    destination_name: Optional[str] = None
    destination_address: Optional[str] = None
    destination_lat: Optional[float] = None
    destination_lng: Optional[float] = None
    scheduled_at: Optional[datetime] = None
    mobility_level: Optional[str] = None
    service_level: Optional[str] = None
    oxygen: Optional[bool] = None
    isolation: Optional[bool] = None
    stretcher: Optional[bool] = None
    wheelchair: Optional[bool] = None
    special_equipment: Optional[dict] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    unit_id: Optional[int] = None
    incident_id: Optional[int] = None

class ScheduledTransportOut(BaseModel):
    id: int
    agency_id: int
    call_type: Optional[str] = 'Routine Transport'
    patient_name: Optional[str] = None
    pickup_address: Optional[str] = None
    pickup_lat: Optional[float] = None
    pickup_lng: Optional[float] = None
    destination_id: Optional[int] = None
    destination_name: Optional[str] = None
    destination_address: Optional[str] = None
    destination_lat: Optional[float] = None
    destination_lng: Optional[float] = None
    scheduled_at: Optional[datetime] = None
    mobility_level: Optional[str] = None
    service_level: Optional[str] = 'BLS'
    oxygen: Optional[bool] = False
    isolation: Optional[bool] = False
    stretcher: Optional[bool] = False
    wheelchair: Optional[bool] = False
    special_equipment: Optional[dict] = None
    notes: Optional[str] = None
    status: Optional[str] = 'scheduled'
    unit_id: Optional[int] = None
    incident_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    agency: Optional["AgencyOut"] = None
    destination: Optional[DestinationOut] = None
    unit: Optional["UnitOut"] = None
    incident: Optional["IncidentOut"] = None
    class Config:
        from_attributes = True

class ScheduledEventCreate(BaseModel):
    agency_id: int
    title: str
    event_type: Optional[str] = 'other'
    location_text: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = 60
    unit_id: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[str] = 'scheduled'

class ScheduledEventUpdate(BaseModel):
    title: Optional[str] = None
    event_type: Optional[str] = None
    location_text: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    unit_id: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[str] = None

class ScheduledEventOut(BaseModel):
    id: int
    agency_id: int
    title: str
    event_type: Optional[str] = 'other'
    location_text: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = 60
    unit_id: Optional[int] = None
    notes: Optional[str] = None
    status: Optional[str] = 'scheduled'
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    agency: Optional["AgencyOut"] = None
    unit: Optional["UnitOut"] = None
    class Config:
        from_attributes = True

class PostZoneCreate(BaseModel):
    agency_id: int
    name: str
    zone_type: Optional[str] = 'post'
    color: Optional[str] = '#3b82f6'
    geojson: Optional[dict] = None
    display_order: Optional[int] = 0
    minimum_units: Optional[int] = 0
    is_active: Optional[bool] = True

class PostZoneUpdate(BaseModel):
    name: Optional[str] = None
    zone_type: Optional[str] = None
    color: Optional[str] = None
    geojson: Optional[dict] = None
    display_order: Optional[int] = None
    minimum_units: Optional[int] = None
    is_active: Optional[bool] = None

class PostZoneOut(BaseModel):
    id: int
    agency_id: int
    name: str
    zone_type: Optional[str] = 'post'
    color: Optional[str] = '#3b82f6'
    geojson: Optional[dict] = None
    display_order: Optional[int] = 0
    minimum_units: Optional[int] = 0
    is_active: Optional[bool] = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class UnitPostingCreate(BaseModel):
    unit_id: int
    post_zone_id: int

class UnitPostingOut(BaseModel):
    id: int
    unit_id: int
    post_zone_id: int
    posted_at: Optional[datetime] = None
    removed_at: Optional[datetime] = None
    is_current: Optional[bool] = True
    posted_by_user_id: Optional[int] = None
    unit: Optional["UnitOut"] = None
    post_zone: Optional[PostZoneOut] = None
    class Config:
        from_attributes = True

class EpcrExportCreate(BaseModel):
    incident_id: int
    destination_id: Optional[int] = None

class EpcrExportOut(BaseModel):
    id: int
    incident_id: int
    exported_at: Optional[datetime] = None
    exported_by_user_id: Optional[int] = None
    epcr_payload: Optional[dict] = None
    destination_id: Optional[int] = None
    status: Optional[str] = 'pending'
    external_id: Optional[str] = None
    response_body: Optional[str] = None
    incident: Optional["IncidentOut"] = None
    destination: Optional[DestinationOut] = None
    class Config:
        from_attributes = True

class CustomerConfigCreate(BaseModel):
    agency_id: Optional[int] = None
    category: str
    key: str
    value: Optional[Any] = None

class CustomerConfigOut(CustomerConfigCreate):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class SeedConfigRequest(BaseModel):
    agency_id: int
    template: str

def hash_password(password):
    return hashlib.sha256(password.encode('utf-8')).hexdigest()

def make_session(user):
    exp = int(time.time()) + 86400
    msg = f'{user.id}:{user.role}:{exp}'
    sig = hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f'{msg}:{sig}'

def verify_session(token):
    if not token:
        return None
    parts = token.split(':')
    if len(parts) != 4:
        return None
    uid, role, exp, sig = parts
    expected = hmac.new(SECRET_KEY.encode(), f'{uid}:{role}:{exp}'.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if time.time() > int(exp):
        return None
    return {'user_id': int(uid), 'role': role, 'exp': int(exp)}

def get_current_user(request: Request):
    if INSECURE_DEV:
        return {'user_id': 0, 'role': 'admin', 'email': 'dev@example.com', 'agency_id': None}
    payload = verify_session(request.cookies.get('session'))
    if not payload:
        raise HTTPException(status_code=401, detail='Not authenticated')
    return payload

def require_admin(request: Request):
    user = get_current_user(request)
    if user.get('role') != 'admin':
        raise HTTPException(status_code=403, detail='Admin required')
    return user

def seed_default_admin():
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            email = os.getenv('ADMIN_EMAIL', 'dustin@dispatchtodiscipleship.net')
            password = os.getenv('ADMIN_PASSWORD', 'Warrior/202601!')
            db.add(User(email=email, hashed_password=hash_password(password), role='admin', is_active=True))
            db.commit()
    finally:
        db.close()

@app.on_event('startup')
def startup():
    seed_default_admin()

def _log_event(db: Session, event_type: str, entity_type: str, entity_id: int, user_id: Optional[int] = None, data: Optional[dict] = None, agency_id: Optional[int] = None):
    safe_data = None
    if data is not None:
        try:
            safe_data = json.loads(json.dumps(data, default=str))
        except Exception:
            safe_data = {'error': 'Could not serialize log data'}
    db.add(Event(event_type=event_type, entity_type=entity_type, entity_id=entity_id, user_id=user_id, data=safe_data, agency_id=agency_id))

def _security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if not INSECURE_DEV:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

@app.middleware('http')
async def auth_middleware(request: Request, call_next):
    if request.method in ('GET', 'OPTIONS', 'HEAD') or request.url.path.startswith('/static/'):
        return _security_headers(await call_next(request))
    if request.url.path in ('/login', '/logout', '/docs', '/openapi.json', '/taip/ingest'):
        return _security_headers(await call_next(request))
    session = request.cookies.get('session')
    payload = verify_session(session)
    if not payload:
        return _security_headers(JSONResponse(status_code=401, content={'detail': 'Not authenticated'}))
    if request.url.path == '/config' and request.method in ('POST', 'PUT', 'DELETE') and payload.get('role') != 'admin':
        return _security_headers(JSONResponse(status_code=403, content={'detail': 'Admin required'}))
    return _security_headers(await call_next(request))

@app.post('/login')
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host or 'unknown'
    now = time.time()
    attempts = login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < 900]
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail='Too many login attempts. Try again later.')
    user = db.query(User).filter(User.email == body.email).first()
    if not user or user.hashed_password != hash_password(body.password):
        attempts.append(now)
        login_attempts[ip] = attempts
        raise HTTPException(status_code=401, detail='Invalid credentials')
    login_attempts.pop(ip, None)
    response.set_cookie(key='session', value=make_session(user), httponly=True, samesite='lax' if INSECURE_DEV else 'strict', secure=not INSECURE_DEV, path='/', max_age=86400)
    return {'email': user.email, 'role': user.role, 'agency_id': user.agency_id}

@app.post('/logout')
def logout(response: Response):
    response.delete_cookie(key='session', path='/')
    return {'ok': True}

@app.get('/me', response_model=UserMe)
def me(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    u = db.query(User).get(user['user_id'])
    modules = []
    selected = None
    personnel_id = None
    cross_ids = []
    if u:
        if u.agency_id:
            cfg = db.query(CustomerConfig).filter_by(agency_id=u.agency_id, category='modules', key='defaults').first()
            if cfg and cfg.value:
                modules = cfg.value if isinstance(cfg.value, list) else []
            coop = db.query(CustomerConfig).filter_by(agency_id=u.agency_id, category='cooperating_agencies', key='defaults').first()
            if coop and coop.value:
                cross_ids = coop.value if isinstance(coop.value, list) else []
        sel = db.query(CustomerConfig).filter_by(category='user_module', key=str(u.id)).first()
        if sel:
            selected = sel.value
        p = db.query(Personnel).filter(Personnel.user_id == u.id).first()
        if p:
            personnel_id = p.id
    return {'user_id': user['user_id'], 'email': u.email if u else None, 'role': user['role'], 'agency_id': u.agency_id if u else None, 'modules': modules, 'selected_module': selected, 'personnel_id': personnel_id, 'cross_discipline_agencies': cross_ids}

@app.put('/me/module')
def set_user_module(body: UserModuleUpdate, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    u = db.query(User).get(user['user_id'])
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    if u.agency_id:
        agency_modules = []
        cfg = db.query(CustomerConfig).filter_by(agency_id=u.agency_id, category='modules', key='defaults').first()
        if cfg and cfg.value:
            agency_modules = cfg.value if isinstance(cfg.value, list) else []
        if agency_modules and body.module not in agency_modules and body.module != 'all':
            raise HTTPException(status_code=400, detail='Module not enabled for agency')
        if not agency_modules:
            body.module = 'all'
    sel = db.query(CustomerConfig).filter_by(category='user_module', key=str(u.id)).first()
    if not sel:
        sel = CustomerConfig(category='user_module', key=str(u.id), value=body.module)
        db.add(sel)
    else:
        sel.value = body.module
    db.commit()
    return {'selected_module': body.module}

@app.get('/login')
def login_page():
    return FileResponse('static/login.html')

def _select_home_page(request: Request):
    try:
        user = get_current_user(request)
        if user.get('role') == 'dispatcher':
            return FileResponse('static/dispatch.html')
        return FileResponse('static/dashboard_v6.html')
    except HTTPException:
        return FileResponse('static/login.html')

@app.get('/')
def index(request: Request):
    return _select_home_page(request)

@app.get('/dashboard_v6')
def dashboard_v6(request: Request):
    return _select_home_page(request)

@app.get('/dispatch')
def dispatch_page():
    return FileResponse('static/dispatch.html')

@app.get('/dashboard_v5')
def dashboard_v5():
    return FileResponse('static/dashboard_v5.html')

@app.get('/console')
def console():
    return FileResponse('static/dispatch.html')

@app.get('/police')
def police_console():
    return FileResponse('static/dispatch.html')

@app.get('/fire')
def fire_console():
    return FileResponse('static/dispatch.html')

@app.get('/ems')
def ems_console():
    return FileResponse('static/dispatch.html')

@app.get('/units-screen')
def units_screen():
    return FileResponse('static/screen.html')

@app.get('/calls-screen')
def calls_screen():
    return FileResponse('static/screen.html')

@app.get('/mdt')
def mdt():
    return FileResponse('static/mobile_mdt_v2.html')

@app.get('/mobile-mdt')
def mobile_mdt():
    return FileResponse('static/mobile_mdt_v2.html')

@app.get('/avl')
def avl():
    return FileResponse('static/avl.html')

@app.get('/history')
def history():
    return FileResponse('static/history.html')

@app.get('/admin')
def admin():
    return FileResponse('static/admin.html')

@app.get('/customer-admin')
def customer_admin():
    return FileResponse('static/customer-admin.html')

@app.get('/hud')
def hud():
    return FileResponse('static/hud.html')

@app.get('/roster')
def roster():
    return FileResponse('static/roster.html')

@app.get('/call-entry')
def call_entry():
    return FileResponse('static/call-entry.html')

@app.get('/config', response_model=List[CustomerConfigOut])
def list_config(agency_id: Optional[int] = Query(None), category: Optional[str] = Query(None), db: Session = Depends(get_db)):
    q = db.query(CustomerConfig)
    if agency_id:
        q = q.filter(CustomerConfig.agency_id == agency_id)
    if category:
        q = q.filter(CustomerConfig.category == category)
    return q.order_by(CustomerConfig.category, CustomerConfig.key).all()

@app.post('/config', response_model=CustomerConfigOut)
def create_config(body: CustomerConfigCreate, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    existing = db.query(CustomerConfig).filter_by(agency_id=body.agency_id, category=body.category, key=body.key).first()
    if existing:
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(existing, k, v)
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    cfg = CustomerConfig(**body.model_dump())
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg

@app.post('/config/seed', response_model=dict)
def seed_config(body: SeedConfigRequest, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    agency = db.query(Agency).get(body.agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail='Agency not found')
    templates = {
        'police': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'TC','label':'Traffic Control'},{'code':'CT','label':'Citation'},{'code':'ARR','label':'Arrest'},{'code':'BK','label':'Booking'},{'code':'TR','label':'Transport'},{'code':'CAN','label':'Cancelled'},{'code':'LUN','label':'Lunch'},{'code':'OOS','label':'Out of Service'},{'code':'MAINT','label':'Maintenance'}],
            'modules': ['law'],
            'unit_types': ['patrol','detective','supervisor','k9','swat','traffic','rescue'],
            'call_types': [
                {'label':'Traffic Accident', 'priority':2, 'fields':['vehicles','injuries']},
                {'label':'Theft', 'priority':3, 'fields':['property','suspect']},
                {'label':'Domestic', 'priority':2, 'fields':['weapons','children']},
                {'label':'Assault', 'priority':1, 'fields':['weapons','injuries']},
                {'label':'Welfare Check', 'priority':3, 'fields':['age']},
                {'label':'Suspicious Person', 'priority':3, 'fields':['armed']}
            ],
            'priorities': [
                {'priority':1,'label':'Emergency','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Routine','target_seconds':720},
                {'priority':4,'label':'Low','target_seconds':1200}
            ],
            'response_plans': {
                'Traffic Accident': ['patrol','supervisor','rescue'],
                'Theft': ['patrol','detective'],
                'Domestic': ['patrol','supervisor'],
                'Assault': ['patrol','supervisor','k9'],
                'Welfare Check': ['patrol'],
                'Suspicious Person': ['patrol','k9']
            },
            'dispositions': ['Arrested','Cited','Warned','Referred','Report','No Action','False Alarm']
        },
        'fire': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'WATER','label':'Water on Fire'},{'code':'EXT','label':'Extinguished'},{'code':'OVER','label':'Overhaul'},{'code':'TR','label':'Transport'},{'code':'CAN','label':'Cancelled'},{'code':'LUN','label':'Lunch'},{'code':'OOS','label':'Out of Service'},{'code':'MAINT','label':'Maintenance'}],
            'modules': ['fire'],
            'unit_types': ['engine','ladder','rescue','brush','tanker','ambulance','chief'],
            'call_types': [
                {'label':'Structure Fire', 'priority':1, 'fields':['exposures','occupants']},
                {'label':'Vehicle Fire', 'priority':2, 'fields':['hazmat']},
                {'label':'Medical Assist', 'priority':2, 'fields':['age','conscious']},
                {'label':'Alarm', 'priority':3, 'fields':['type']},
                {'label':'Vehicle Accident', 'priority':1, 'fields':['extrication','injuries']},
                {'label':'Brush Fire', 'priority':2, 'fields':['size']}
            ],
            'priorities': [
                {'priority':1,'label':'Working Fire','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Routine','target_seconds':720},
                {'priority':4,'label':'Low','target_seconds':1200}
            ],
            'response_plans': {
                'Structure Fire': ['engine','ladder','rescue','chief'],
                'Vehicle Fire': ['engine','brush','tanker'],
                'Medical Assist': ['rescue','ambulance'],
                'Alarm': ['engine','ladder'],
                'Vehicle Accident': ['engine','rescue','ambulance'],
                'Brush Fire': ['brush','tanker']
            },
            'dispositions': ['Extinguished','Controlled','Under Control','False Alarm','No Fire','Cancelled']
        },
        'ems': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'OS','label':'On Scene'},{'code':'ER','label':'En Route'},{'code':'TR','label':'Transport'},{'code':'CAN','label':'Cancelled'},{'code':'LUN','label':'Lunch'},{'code':'OOS','label':'Out of Service'},{'code':'MAINT','label':'Maintenance'}],
            'modules': ['ems'],
            'unit_types': ['ambulance','medic','supervisor','air','rescue'],
            'call_types': [
                {'label':'Cardiac Arrest', 'priority':1, 'fields':['age','conscious']},
                {'label':'Chest Pain', 'priority':1, 'fields':['age','conscious']},
                {'label':'Respiratory', 'priority':1, 'fields':['age','conscious']},
                {'label':'Fall', 'priority':2, 'fields':['age','conscious']},
                {'label':'Motor Vehicle Accident', 'priority':1, 'fields':['extrication','injuries']},
                {'label':'Overdose', 'priority':1, 'fields':['age','conscious','substance']}
            ],
            'priorities': [
                {'priority':1,'label':'Priority 1','target_seconds':180},
                {'priority':2,'label':'Priority 2','target_seconds':420},
                {'priority':3,'label':'Priority 3','target_seconds':720},
                {'priority':4,'label':'Priority 4','target_seconds':1200}
            ],
            'response_plans': {
                'Cardiac Arrest': ['ambulance','medic','supervisor'],
                'Chest Pain': ['ambulance','medic'],
                'Respiratory': ['ambulance','medic'],
                'Fall': ['ambulance'],
                'Motor Vehicle Accident': ['ambulance','rescue','medic'],
                'Overdose': ['ambulance','medic','supervisor']
            }
        }
    }
    cfg = templates.get(body.template.lower())
    if not cfg:
        raise HTTPException(status_code=400, detail='Template not found')
    for category, value in cfg.items():
        existing = db.query(CustomerConfig).filter_by(agency_id=body.agency_id, category=category, key='defaults').first()
        if existing:
            existing.value = value
            existing.updated_at = datetime.utcnow()
        else:
            db.add(CustomerConfig(agency_id=body.agency_id, category=category, key='defaults', value=value))
    db.commit()
    return {'status': 'seeded', 'agency_id': body.agency_id, 'template': body.template}

@app.get('/health')
def health():
    return {'status': 'ok'}

# Pydantic schemas
class AgencyCreate(BaseModel):
    name: str
    agency_type: str = 'fire'
    domain: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

class AgencyOut(AgencyCreate):
    id: int
    approved: bool
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class AgencyUpdate(BaseModel):
    name: Optional[str] = None
    agency_type: Optional[str] = None
    domain: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    approved: Optional[bool] = None

class UnitCreate(BaseModel):
    agency_id: int
    name: str
    call_sign: str
    unit_type: str = 'engine'
    capabilities: Optional[dict] = None
    radio_id: Optional[str] = None
    taip_id: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    taip_destination_url: Optional[str] = None
    taip_port: Optional[int] = None

class UnitOut(BaseModel):
    id: int
    agency_id: int
    name: str
    call_sign: str
    unit_type: str
    current_status: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    heading: Optional[float] = None
    speed: Optional[float] = None
    last_seen_at: Optional[datetime] = None
    camera_url: Optional[str] = None
    last_assigned_at: Optional[datetime] = None
    in_service_at: Optional[datetime] = None
    accumulated_call_seconds: Optional[float] = None
    capabilities: Optional[dict] = None
    taip_id: Optional[str] = None
    taip_destination_url: Optional[str] = None
    taip_port: Optional[int] = None

    @computed_field
    @property
    def stale(self) -> bool:
        if not self.last_seen_at:
            return True
        return (datetime.utcnow() - self.last_seen_at).total_seconds() > TAIP_STALE_SECONDS

    @computed_field
    @property
    def offline(self) -> bool:
        if not self.last_seen_at:
            return True
        return (datetime.utcnow() - self.last_seen_at).total_seconds() > TAIP_OFFLINE_SECONDS

    class Config:
        from_attributes = True

class UnitUpdate(BaseModel):
    agency_id: Optional[int] = None
    name: Optional[str] = None
    call_sign: Optional[str] = None
    unit_type: Optional[str] = None
    radio_id: Optional[str] = None
    taip_id: Optional[str] = None
    taip_destination_url: Optional[str] = None
    taip_port: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    capabilities: Optional[dict] = None
    is_active: Optional[bool] = None

class PersonnelCreate(BaseModel):
    agency_id: int
    first_name: str
    last_name: str
    radio_id: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    sms_phone: Optional[str] = None
    current_unit_id: Optional[int] = None
    duty_status: str = 'off_duty'

class PersonnelOut(PersonnelCreate):
    id: int
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class PersonnelUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    radio_id: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    sms_phone: Optional[str] = None
    current_unit_id: Optional[int] = None
    duty_status: Optional[str] = None

class IncidentCreate(BaseModel):
    agency_id: int
    incident_number: Optional[str] = None
    call_number: Optional[str] = None
    call_type: str
    priority: int = 2
    location_text: Optional[str] = None
    extra: Optional[dict] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    caller_name: Optional[str] = None
    callback: Optional[str] = None
    narrative: Optional[str] = None
    call_entry_started_at: Optional[datetime] = None

class IncidentOut(IncidentCreate):
    id: int
    status: str
    created_at: Optional[datetime] = None
    call_entry_started_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class IncidentUpdate(BaseModel):
    call_type: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None
    location_text: Optional[str] = None
    narrative: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    extra: Optional[dict] = None

class IncidentPersonnelCreate(BaseModel):
    personnel_id: int
    responding_vehicle: Optional[str] = 'Personal vehicle'
    notes: Optional[str] = None

class IncidentPersonnelStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None

class IncidentPersonnelOut(BaseModel):
    id: int
    incident_id: int
    personnel_id: int
    status: str
    en_route_at: Optional[datetime] = None
    arrived_at: Optional[datetime] = None
    cleared_at: Optional[datetime] = None
    responding_vehicle: Optional[str] = None
    notes: Optional[str] = None
    personnel: Optional[PersonnelOut] = None
    class Config:
        from_attributes = True

class MessageCreate(BaseModel):
    unit_id: Optional[int] = None
    incident_id: Optional[int] = None
    channel: str = 'mdt'
    message_text: str

class MessageOut(BaseModel):
    id: int
    incident_id: Optional[int] = None
    unit_id: Optional[int] = None
    channel: Optional[str] = None
    message_text: Optional[str] = None
    method: Optional[str] = None
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class EventOut(BaseModel):
    id: int
    event_type: str
    entity_type: str
    entity_id: int
    user_id: Optional[int] = None
    agency_id: Optional[int] = None
    data: Optional[dict] = None
    timestamp: Optional[datetime] = None
    class Config:
        from_attributes = True

class TaipPositionOut(BaseModel):
    id: int
    unit_id: Optional[int] = None
    taip_id: Optional[str] = None
    raw_sentence: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    speed: Optional[float] = None
    heading: Optional[float] = None
    ignition: Optional[bool] = None
    odometer: Optional[float] = None
    fix_quality: Optional[str] = None
    gps_seconds_of_day: Optional[int] = None
    data_age: Optional[int] = None
    gps_source: Optional[int] = None
    reported_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class StatusUpdate(BaseModel):
    status_code: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    reason: Optional[str] = None
    disposition: Optional[str] = None
    passenger_count: Optional[int] = None
    destination_id: Optional[int] = None
    mileage: Optional[float] = None
    unit_id: Optional[int] = None

class UnitCamera(BaseModel):
    camera_url: str

class UnitShift(BaseModel):
    action: str

class UnitStatus(BaseModel):
    status_code: str
    reason: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

class PersonnelAssign(BaseModel):
    unit_id: Optional[int] = None

class AlertCrew(BaseModel):
    message: Optional[str] = None

class LocationUpdate(BaseModel):
    lat: float
    lng: float
    speed: Optional[float] = None
    heading: Optional[float] = None

class TaipIngest(BaseModel):
    raw: str
    taip_id: Optional[str] = None

# TAIP parser

TAIP_PV_RE = re.compile(
    r'^>RPV(\d{5})([+-]\d{7})([+-]\d{8})(\d{3})(\d{3})(\d)(\d)(?:;ID=([A-Z0-9-]{1,20}))?(?:;\*([0-9A-Fa-f]{2}))?<$',
    re.IGNORECASE
)

def _taip_checksum_ok(raw: str, checksum: str, checksum_start: int) -> bool:
    if not checksum:
        return True
    payload = raw[:checksum_start]
    calculated = 0
    for ch in payload:
        calculated ^= ord(ch)
    expected = f'{calculated:02X}'
    return expected.upper() == checksum.upper()

def parse_taip_pv(raw: str) -> Optional[dict]:
    text = raw.strip().upper()
    m = TAIP_PV_RE.match(text)
    if not m:
        return None
    _, time_text, lat_text, lon_text, speed_text, heading_text, source_text, age_text, taip_id, checksum = m.groups()
    checksum_start = m.start(9) if checksum and m.start(9) is not None else None
    if checksum and checksum_start is not None and not _taip_checksum_ok(text, checksum, checksum_start):
        raise ValueError(f'TAIP checksum failure: expected {checksum}')
    lat = int(lat_text) / 100000.0
    lng = int(lon_text) / 100000.0
    speed = int(speed_text)
    heading = int(heading_text)
    gps_source = int(source_text)
    data_age = int(age_text)
    if data_age == 0:
        raise ValueError('TAIP location data is unavailable (data_age=0)')
    if lat < -90 or lat > 90 or lng < -180 or lng > 180:
        raise ValueError('TAIP coordinates are outside valid geographic bounds')
    if heading < 0 or heading > 359:
        raise ValueError('TAIP heading is outside the valid range')
    return {
        'raw': raw,
        'taip_id': taip_id or None,
        'lat': lat,
        'lng': lng,
        'speed': speed,
        'heading': heading,
        'gps_source': gps_source,
        'data_age': data_age,
        'gps_seconds_of_day': int(time_text),
        'reported_at': None,
        'ignition': None,
        'odometer': None
    }

def parse_taip(raw: str) -> dict:
    try:
        pv = parse_taip_pv(raw)
        if pv:
            return pv
    except ValueError:
        raise
    cleaned = raw.strip().lstrip('>').rstrip('&')
    cleaned = re.sub(r'\*[0-9A-Fa-f]{2}$', '', cleaned)
    cleaned = cleaned.replace('$>', '')
    parts = cleaned.split(';')
    data: dict = {'raw': raw, 'ignition': None}
    for p in parts:
        if '=' in p:
            k, v = p.split('=', 1)
            k = k.lower().strip()
            v = v.strip()
            if k in ('lat', 'latitude'):
                data['lat'] = float(v)
            elif k in ('lon', 'lng', 'long', 'longitude'):
                data['lng'] = float(v)
            elif k in ('spd', 'speed'):
                data['speed'] = float(v)
            elif k in ('hdg', 'heading', 'dir'):
                data['heading'] = float(v)
            elif k in ('id', 'unit', 'taip_id', 'radio'):
                data['taip_id'] = v
            elif k in ('ign', 'ignition'):
                data['ignition'] = v.lower() in ('1', 'true', 'on')
            elif k in ('odo', 'odometer'):
                data['odometer'] = float(v)
            elif k in ('ts', 'time', 'timestamp'):
                try:
                    data['reported_at'] = datetime.fromisoformat(v)
                except ValueError:
                    pass
        else:
            m = re.match(r'^(-?\d+\.\d+),?(-?\d+\.\d+)$', p)
            if m:
                data['lat'] = float(m.group(1))
                data['lng'] = float(m.group(2))
    return data

def _taip_ip(source):
    if isinstance(source, (tuple, list)) and len(source) > 0:
        return source[0]
    if source:
        return str(source)
    return None

def _taip_source_allowed(source):
    if not TAIP_ALLOWLIST:
        return True
    ip = _taip_ip(source)
    if not ip:
        return False
    return ip in TAIP_ALLOWLIST

def _taip_rate_limited(taip_id):
    if TAIP_MIN_INTERVAL <= 0:
        return False
    now = time.time()
    last = taip_last_packet.get(taip_id)
    if last and now - last < TAIP_MIN_INTERVAL:
        return True
    taip_last_packet[taip_id] = now
    return False

def _haversine_m(lat1, lng1, lat2, lng2):
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return None
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _taip_reported_time(data: dict, received_at: datetime) -> Optional[datetime]:
    if data.get('reported_at'):
        return data['reported_at']
    sod = data.get('gps_seconds_of_day')
    if sod is None:
        return None
    base = received_at.replace(hour=0, minute=0, second=0, microsecond=0)
    reported = base + timedelta(seconds=sod)
    if reported > received_at + timedelta(seconds=30):
        reported -= timedelta(days=1)
    return reported

def _taip_out_of_order(unit, reported_at):
    if not unit or not unit.last_seen_at or not reported_at:
        return False
    return reported_at < unit.last_seen_at - timedelta(seconds=TAIP_OUT_OF_ORDER_SECONDS)

def _taip_jump_ok(unit, lat, lng, reported_at):
    if not unit or unit.lat is None or unit.lng is None or not reported_at:
        return True
    last = unit.last_seen_at
    if not last:
        return True
    distance_m = _haversine_m(unit.lat, unit.lng, lat, lng)
    if distance_m is None or distance_m <= 0:
        return True
    delta_s = (reported_at - last).total_seconds()
    if delta_s <= 0:
        # Cannot trust time delta; allow if within reasonable distance
        return distance_m <= TAIP_MAX_JUMP_MPS * 10
    speed_mps = distance_m / delta_s
    return speed_mps <= TAIP_MAX_JUMP_MPS

def _taip_stale_state(last_seen_at: Optional[datetime]):
    if not last_seen_at:
        return True, True
    age = (datetime.utcnow() - last_seen_at).total_seconds()
    return age > TAIP_STALE_SECONDS, age > TAIP_OFFLINE_SECONDS

_CALL_ACTIVE_STATUSES = {'AK','ER','OS','TR','ED','WATER','EXT','OVER','TC','ARR','CT','BK'}
_ASSIGNABLE_STATUSES = {'AQ','AFR','POSTING','STAGED','AT_STATION','AVAILABLE_ON_RADIO'}
_OUT_OF_SERVICE_STATUSES = {'OOS','LUN','MAINT','OFF_DUTY','MEAL'}

def map_status(code: str) -> str:
    return {
        'AQ': 'assigned', 'AK': 'assigned',
        'ER': 'en_route', 'OS': 'on_scene',
        'TR': 'transport', 'ED': 'transport',
        'CAN': 'clear', 'NPF': 'clear', 'DEL': 'clear',
        'AFR': 'clear', 'OOS': 'clear', 'LUN': 'clear', 'MAINT': 'clear'
    }.get(code, code)

def refresh_incident_status(db: Session, incident):
    """Set incident status based on the most advanced status of its currently assigned units."""
    if not incident or incident.status == 'closed':
        return
    units = db.query(Unit).filter(Unit.current_incident_id == incident.id).all()
    if not units:
        # If the incident has ever had a unit assigned and they are now all clear, mark cleared; otherwise open.
        was_dispatched = db.query(IncidentUnit).filter_by(incident_id=incident.id).first() is not None
        new_status = 'cleared' if (was_dispatched and incident.status not in ('closed',)) else 'open'
    else:
        statuses = [u.current_status for u in units]
        if any(s in ('OS','WATER','EXT','OVER','TC','ARR','CT','BK') for s in statuses):
            new_status = 'on_scene'
        elif any(s in ('ER','TR','ED') for s in statuses):
            new_status = 'en_route'
        elif any(s in ('AK','dispatched') for s in statuses):
            new_status = 'dispatched'
        else:
            new_status = 'cleared'
    if incident.status != new_status:
        incident.status = new_status
        _log_event(db, 'incident_status_changed', 'incident', incident.id, data={'new_status': new_status}, agency_id=incident.agency_id)

# Endpoints

GEOCODER_TIMEOUT = 5

def _build_geo_query(parts):
    return ' '.join(str(p) for p in parts if p is not None and str(p).strip())

def geocode_address(query):
    if not query or not query.strip():
        return None, None
    try:
        url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode({'q': query, 'format': 'json', 'limit': 1})
        req = urllib.request.Request(url, headers={'User-Agent': 'D2D-CAD/1.0'})
        with urllib.request.urlopen(req, timeout=GEOCODER_TIMEOUT) as r:
            data = json.loads(r.read().decode())
            if data:
                return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        print('Geocode error:', e)
    return None, None

def _geocode_structured(query):
    if not query or not query.strip():
        return None
    try:
        url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode({'q': query, 'format': 'json', 'addressdetails': 1, 'limit': 1})
        req = urllib.request.Request(url, headers={'User-Agent': 'D2D-CAD/1.0'})
        with urllib.request.urlopen(req, timeout=GEOCODER_TIMEOUT) as r:
            data = json.loads(r.read().decode())
            if data:
                d = data[0]
                a = d.get('address') or {}
                return {
                    'lat': float(d['lat']),
                    'lng': float(d['lon']),
                    'display_name': d.get('display_name'),
                    'address': {
                        'house_number': a.get('house_number'),
                        'road': a.get('road'),
                        'city': a.get('city') or a.get('town') or a.get('village'),
                        'county': a.get('county'),
                        'state': a.get('state'),
                        'postcode': a.get('postcode'),
                        'country': a.get('country')
                    }
                }
    except Exception as e:
        print('Geocode structured error:', e)
    return None

def _point_in_ring(lat, lng, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lng) != (yj > lng)) and (lat < (xj - xi) * (lng - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside

def _point_in_geojson(lat, lng, geojson):
    if not geojson or lat is None or lng is None:
        return False
    g = geojson
    if 'geometry' in g:
        g = g['geometry']
    t = g.get('type')
    coords = g.get('coordinates')
    if t == 'Polygon' and coords:
        for ring in coords:
            if _point_in_ring(lat, lng, ring):
                return True
        return False
    if t == 'MultiPolygon' and coords:
        for poly in coords:
            for ring in poly:
                if _point_in_ring(lat, lng, ring):
                    return True
        return False
    return False

def _find_zone_for_point(db, lat, lng, agency_id=None):
    q = db.query(PostZone).filter(PostZone.is_active == True)
    if agency_id:
        q = q.filter(or_(PostZone.agency_id == agency_id, PostZone.agency_id == None))
    for z in q.all():
        if _point_in_geojson(lat, lng, z.geojson):
            return z
    return None

def _cross_streets_around(db, lat, lng):
    # Placeholder: cross streets require road network data.
    return None

def _validate_incident_location(db, incident, force=False):
    loc = db.query(IncidentLocation).filter_by(incident_id=incident.id).first()
    if not loc:
        loc = IncidentLocation(incident_id=incident.id, raw_address=incident.location_text)
        db.add(loc)
    if not force and loc.verification_status == 'verified' and loc.latitude is not None and loc.longitude is not None:
        return loc
    extra = incident.extra or {}
    g = _geocode_structured(incident.location_text) if incident.location_text else None
    if g:
        loc.standardized_address = g.get('display_name')
        a = g.get('address') or {}
        loc.city = a.get('city')
        loc.state = a.get('state')
        loc.postal_code = a.get('postcode')
        loc.latitude = g['lat']
        loc.longitude = g['lng']
        loc.geocoded_at = datetime.utcnow()
        loc.verification_status = 'verified'
        incident.lat = g['lat']
        incident.lng = g['lng']
        extra['verification_status'] = 'verified'
        extra['standardized_address'] = loc.standardized_address
    else:
        loc.verification_status = 'unverified'
        extra['verification_status'] = 'unverified'
    zone = _find_zone_for_point(db, loc.latitude, loc.longitude, incident.agency_id) if (loc.latitude and loc.longitude) else None
    loc.zone_id = zone.id if zone else None
    extra['zone_name'] = zone.name if zone else None
    extra['zone_id'] = zone.id if zone else None
    loc.cross_streets = _cross_streets_around(db, loc.latitude, loc.longitude)
    incident.extra = extra
    return loc

def fill_agency_lat_lng(agency):
    if agency.lat is not None and agency.lng is not None:
        return
    parts = [agency.address, agency.city, agency.state, agency.zip_code, agency.name]
    query = _build_geo_query(parts)
    if not query:
        return
    lat, lng = geocode_address(query)
    if lat is not None and lng is not None:
        agency.lat = lat
        agency.lng = lng

def geocode_missing_agencies():
    try:
        db = SessionLocal()
        for a in db.query(Agency).all():
            if a.lat is None or a.lng is None:
                fill_agency_lat_lng(a)
        db.commit()
    except Exception as e:
        print('Startup agency geocode error:', e)

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.post('/agencies', response_model=AgencyOut)
def create_agency(body: AgencyCreate, db: Session = Depends(get_db)):
    agency = Agency(**body.model_dump())
    fill_agency_lat_lng(agency)
    db.add(agency)
    db.commit()
    db.refresh(agency)
    return agency

@app.get('/agencies', response_model=List[AgencyOut])
def list_agencies(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Agency).offset(skip).limit(limit).all()

@app.put('/agencies/{agency_id}', response_model=AgencyOut)
def update_agency(agency_id: int, body: AgencyUpdate, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    a = db.query(Agency).get(agency_id)
    if not a:
        raise HTTPException(status_code=404, detail='Agency not found')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    fill_agency_lat_lng(a)
    db.commit(); db.refresh(a)
    return a

@app.delete('/agencies/{agency_id}')
def delete_agency(agency_id: int, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    a = db.query(Agency).get(agency_id)
    if not a:
        raise HTTPException(status_code=404, detail='Agency not found')
    db.delete(a); db.commit()
    return {'deleted': agency_id}

@app.post('/units', response_model=UnitOut)
def create_unit(body: UnitCreate, db: Session = Depends(get_db)):
    unit = Unit(**body.model_dump())
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit

@app.get('/units', response_model=List[UnitOut])
def list_units(agency_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    q = db.query(Unit)
    if agency_id:
        q = q.filter(Unit.agency_id == agency_id)
    return q.all()

@app.get('/units/{unit_id}', response_model=UnitOut)
def get_unit(unit_id: int, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    return unit

@app.put('/units/{unit_id}', response_model=UnitOut)
def update_unit(unit_id: int, body: UnitUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(unit, k, v)
    db.commit(); db.refresh(unit)
    return unit

@app.delete('/units/{unit_id}')
def delete_unit(unit_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    db.delete(unit); db.commit()
    return {'deleted': unit_id}

@app.get('/units/{unit_id}/messages', response_model=List[MessageOut])
def get_unit_messages(unit_id: int, db: Session = Depends(get_db)):
    return db.query(DispatchMessage).filter(or_(DispatchMessage.unit_id == unit_id, DispatchMessage.unit_id.is_(None))).order_by(DispatchMessage.sent_at.desc()).limit(50).all()

@app.get('/messages', response_model=List[MessageOut])
def list_messages(unit_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    q = db.query(DispatchMessage)
    if unit_id:
        q = q.filter(or_(DispatchMessage.unit_id == unit_id, DispatchMessage.unit_id.is_(None)))
    return q.order_by(DispatchMessage.sent_at.desc()).limit(50).all()

@app.post('/messages', response_model=MessageOut)
def create_message(body: MessageCreate, db: Session = Depends(get_db)):
    msg = DispatchMessage(**body.model_dump(), method=body.channel, sent_at=datetime.utcnow())
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg

@app.get('/events', response_model=List[EventOut])
def list_events(entity_type: Optional[str] = Query(None), entity_id: Optional[int] = Query(None), agency_id: Optional[int] = Query(None), limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(Event)
    if entity_type:
        q = q.filter(Event.entity_type == entity_type)
    if entity_id:
        q = q.filter(Event.entity_id == entity_id)
    if agency_id:
        q = q.filter(Event.agency_id == agency_id)
    return q.order_by(Event.timestamp.desc()).limit(limit).all()

@app.get('/destinations', response_model=List[DestinationOut])
def list_destinations(agency_id: Optional[int] = Query(None), category: Optional[str] = Query(None), db: Session = Depends(get_db)):
    q = db.query(Destination).filter(Destination.is_active == True)
    if agency_id:
        q = q.filter(Destination.agency_id == agency_id)
    if category:
        q = q.filter(Destination.category == category)
    return q.order_by(Destination.name.asc()).all()

@app.post('/destinations', response_model=DestinationOut)
def create_destination(body: DestinationCreate, db: Session = Depends(get_db)):
    d = Destination(**body.model_dump(exclude_unset=True))
    db.add(d); db.commit(); db.refresh(d)
    return d

@app.get('/destinations/{destination_id}', response_model=DestinationOut)
def get_destination(destination_id: int, db: Session = Depends(get_db)):
    d = db.query(Destination).get(destination_id)
    if not d:
        raise HTTPException(status_code=404, detail='Destination not found')
    return d

@app.put('/destinations/{destination_id}', response_model=DestinationOut)
def update_destination(destination_id: int, body: DestinationUpdate, db: Session = Depends(get_db)):
    d = db.query(Destination).get(destination_id)
    if not d:
        raise HTTPException(status_code=404, detail='Destination not found')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    db.commit(); db.refresh(d)
    return d

@app.delete('/destinations/{destination_id}')
def delete_destination(destination_id: int, db: Session = Depends(get_db)):
    d = db.query(Destination).get(destination_id)
    if not d:
        raise HTTPException(status_code=404, detail='Destination not found')
    db.delete(d); db.commit()
    return {'deleted': destination_id}

@app.get('/destinations/{destination_id}/status', response_model=List[DestinationStatusOut])
def get_destination_status(destination_id: int, limit: int = 1, db: Session = Depends(get_db)):
    q = db.query(DestinationStatus).filter(DestinationStatus.destination_id == destination_id).order_by(DestinationStatus.updated_at.desc()).limit(max(1, min(limit, 50)))
    return q.all()

@app.post('/destinations/{destination_id}/status', response_model=DestinationStatusOut)
def create_destination_status(destination_id: int, body: DestinationStatusCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.query(Destination).get(destination_id)
    if not d:
        raise HTTPException(status_code=404, detail='Destination not found')
    s = DestinationStatus(destination_id=destination_id, status=body.status, reason=body.reason, notes=body.notes, updated_by=current_user.get('id'))
    db.add(s); db.commit(); db.refresh(s)
    return s

@app.get('/destination-statuses', response_model=List[DestinationStatusOut])
def list_destination_statuses(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(DestinationStatus).order_by(DestinationStatus.updated_at.desc()).limit(100).all()

@app.get('/incidents/{incident_id}/destination', response_model=IncidentDestinationOut)
def get_incident_destination(incident_id: int, db: Session = Depends(get_db)):
    result = db.query(IncidentDestination).filter(IncidentDestination.incident_id == incident_id).order_by(IncidentDestination.created_at.desc()).first()
    if not result:
        raise HTTPException(status_code=404, detail='No destination set for this incident')
    return result

@app.post('/incidents/{incident_id}/destination', response_model=IncidentDestinationOut)
def set_incident_destination(incident_id: int, body: IncidentDestinationCreate, db: Session = Depends(get_db)):
    inc = db.query(Incident).get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail='Incident not found')
    dest_id = body.destination_id
    if not dest_id and body.destination:
        new_dest = Destination(**body.destination.model_dump(exclude_unset=True))
        db.add(new_dest); db.flush(); dest_id = new_dest.id
    if not dest_id:
        raise HTTPException(status_code=400, detail='destination_id or destination object is required')
    idest = IncidentDestination(incident_id=incident_id, destination_id=dest_id, notes=body.notes)
    db.add(idest); db.commit(); db.refresh(idest)
    _log_event(db, 'destination_set', 'incident', incident_id, data={'destination_id': dest_id, 'notes': body.notes}, agency_id=inc.agency_id)
    return idest

@app.get('/incidents/{incident_id}/transport-legs', response_model=List[TransportLegOut])
def list_transport_legs(incident_id: int, db: Session = Depends(get_db)):
    return db.query(TransportLeg).filter(TransportLeg.incident_id == incident_id).order_by(TransportLeg.created_at.desc()).all()

@app.post('/incidents/{incident_id}/transport-legs', response_model=TransportLegOut)
def create_transport_leg(incident_id: int, body: TransportLegCreate, db: Session = Depends(get_db)):
    inc = db.query(Incident).get(incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail='Incident not found')
    leg = TransportLeg(**body.model_dump(exclude_unset=True), incident_id=incident_id)
    db.add(leg); db.commit(); db.refresh(leg)
    _log_event(db, 'transport_leg_created', 'incident', incident_id, data={'unit_id': leg.unit_id, 'destination_id': leg.destination_id}, agency_id=inc.agency_id)
    return leg

@app.get('/transport-legs/{leg_id}', response_model=TransportLegOut)
def get_transport_leg(leg_id: int, db: Session = Depends(get_db)):
    leg = db.query(TransportLeg).get(leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail='Transport leg not found')
    return leg

@app.put('/transport-legs/{leg_id}', response_model=TransportLegOut)
def update_transport_leg(leg_id: int, body: TransportLegUpdate, db: Session = Depends(get_db)):
    leg = db.query(TransportLeg).get(leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail='Transport leg not found')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(leg, k, v)
    db.commit(); db.refresh(leg)
    _log_event(db, 'transport_leg_updated', 'incident', leg.incident_id, data={'leg_id': leg.id, 'status': leg.status}, agency_id=None)
    return leg

@app.post('/transport-legs/{leg_id}/status', response_model=TransportLegOut)
def update_transport_leg_status(leg_id: int, body: TransportLegStatusUpdate, db: Session = Depends(get_db)):
    leg = db.query(TransportLeg).get(leg_id)
    if not leg:
        raise HTTPException(status_code=404, detail='Transport leg not found')
    status = body.status
    ts = body.timestamp or datetime.utcnow()
    if status == 'en_route' and not leg.en_route_at:
        leg.en_route_at = ts
    elif status == 'arrived' and not leg.arrived_at:
        leg.arrived_at = ts
    elif status == 'transfer_completed' and not leg.transfer_completed_at:
        leg.transfer_completed_at = ts
    elif status == 'cleared' and not leg.cleared_at:
        leg.cleared_at = ts
    if body.mileage is not None:
        if leg.pickup_mileage is None and status in ('en_route','requested'):
            leg.pickup_mileage = body.mileage
        else:
            leg.dropoff_mileage = body.mileage
    leg.status = status
    db.commit(); db.refresh(leg)
    _log_event(db, 'transport_leg_status', 'incident', leg.incident_id, data={'leg_id': leg.id, 'status': status, 'mileage': body.mileage}, agency_id=None)
    return leg

@app.get('/incidents/{incident_id}/mileage', response_model=List[MileageReadingOut])
def list_mileage(incident_id: int, db: Session = Depends(get_db)):
    return db.query(MileageReading).filter(MileageReading.incident_id == incident_id).order_by(MileageReading.recorded_at.asc()).all()

@app.post('/incidents/{incident_id}/units/{unit_id}/mileage', response_model=MileageReadingOut)
def record_mileage(incident_id: int, unit_id: int, body: MileageReadingCreate, db: Session = Depends(get_db)):
    r = MileageReading(incident_id=incident_id, unit_id=unit_id, status_code=body.status_code, mileage=body.mileage)
    db.add(r); db.commit(); db.refresh(r)
    _log_event(db, 'mileage_recorded', 'unit', unit_id, data={'incident_id': incident_id, 'status_code': body.status_code, 'mileage': body.mileage}, agency_id=None)
    return r

@app.post('/personnel', response_model=PersonnelOut)
def create_personnel(body: PersonnelCreate, db: Session = Depends(get_db)):
    p = Personnel(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p

@app.get('/personnel', response_model=List[PersonnelOut])
def list_personnel(agency_id: Optional[int] = Query(None), unit_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    q = db.query(Personnel)
    if agency_id:
        q = q.filter(Personnel.agency_id == agency_id)
    if unit_id:
        q = q.filter(Personnel.current_unit_id == unit_id)
    return q.all()

@app.get('/personnel/me', response_model=PersonnelOut)
def get_my_personnel(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(Personnel).filter(Personnel.user_id == current_user['user_id']).first()
    if not p:
        raise HTTPException(status_code=404, detail='No personnel record linked to this user')
    return p

@app.put('/personnel/{personnel_id}', response_model=PersonnelOut)
def update_personnel(personnel_id: int, body: PersonnelUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(Personnel).get(personnel_id)
    if not p:
        raise HTTPException(status_code=404, detail='Personnel not found')
    # allow self or admin/dispatch
    u = db.query(User).get(current_user['user_id'])
    is_admin = u and u.role in ('admin','super_admin')
    is_self = p.user_id == current_user['user_id']
    if not is_admin and not is_self:
        raise HTTPException(status_code=403, detail='Not authorized')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return p

@app.delete('/personnel/{personnel_id}')
def delete_personnel(personnel_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(Personnel).get(personnel_id)
    if not p:
        raise HTTPException(status_code=404, detail='Personnel not found')
    u = db.query(User).get(current_user['user_id'])
    if not (u and u.role in ('admin','super_admin')):
        raise HTTPException(status_code=403, detail='Not authorized')
    db.delete(p); db.commit()
    return {'deleted': personnel_id}

@app.post('/incidents', response_model=IncidentOut)
def create_incident(request: Request, body: IncidentCreate, db: Session = Depends(get_db)):
    data = body.model_dump()
    if not data.get('incident_number'):
        count = db.query(Incident).filter(Incident.agency_id == data['agency_id']).count()
        data['incident_number'] = f"{data['agency_id']}-{count + 1:05d}"
    if not data.get('call_number'):
        data['call_number'] = data['incident_number']
    user = get_current_user(request)
    incident = Incident(**data)
    db.add(incident)
    db.flush()
    _validate_incident_location(db, incident)
    if incident.lat is None or incident.lng is None:
        agency = db.query(Agency).get(data.get('agency_id'))
        if agency and agency.lat is not None and agency.lng is not None:
            incident.lat = agency.lat
            incident.lng = agency.lng
    _log_event(db, 'incident_created', 'incident', incident.id, user_id=user.get('user_id'), data=data, agency_id=data.get('agency_id'))
    db.commit()
    db.refresh(incident)
    return incident

@app.get('/incidents', response_model=List[IncidentOut])
def list_incidents(agency_id: Optional[int] = Query(None), status: Optional[str] = Query(None), call_type: Optional[str] = Query(None), search: Optional[str] = Query(None), from_date: Optional[datetime] = Query(None), to_date: Optional[datetime] = Query(None), db: Session = Depends(get_db)):
    q = db.query(Incident)
    if agency_id:
        q = q.filter(Incident.agency_id == agency_id)
    if status:
        q = q.filter(Incident.status == status)
    if call_type:
        q = q.filter(Incident.call_type.ilike(f'%{call_type}%'))
    if search:
        term = f'%{search}%'
        q = q.filter(or_(Incident.incident_number.ilike(term), Incident.call_number.ilike(term), Incident.call_type.ilike(term), Incident.location_text.ilike(term), Incident.caller_name.ilike(term)))
    if from_date:
        q = q.filter(Incident.created_at >= from_date)
    if to_date:
        q = q.filter(Incident.created_at <= to_date)
    return q.order_by(Incident.created_at.desc()).all()

@app.get('/incidents/{incident_id}', response_model=IncidentOut)
def get_incident(incident_id: int, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    return incident

@app.get('/incidents/{incident_id}/recommend')
def recommend_units(incident_id: int, limit: int = Query(10), db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    agency = db.query(Agency).get(incident.agency_id)
    cfg = db.query(CustomerConfig).filter_by(agency_id=incident.agency_id, category='response_plans', key='defaults').first()
    plan = (cfg.value or {}) if cfg else {}
    recommended_types = plan.get(incident.call_type, []) if isinstance(plan, dict) else []
    als_keywords = ['cardiac','chest pain','overdose','respiratory','allergic','stroke','behavioral','choking','seizure','als','trauma','unconscious']
    call_lower = (incident.call_type or '').lower()
    agency_type = agency.agency_type if agency else 'ems'
    required_level = 'ALS' if agency_type == 'ems' and any(k in call_lower for k in als_keywords) else 'BLS'
    emergency = incident.priority in (1,2)
    speed_mph = 35.0 if emergency else 25.0
    now = datetime.utcnow()

    # Pre-compute active posting counts per zone for coverage-loss penalty
    from sqlalchemy import func
    posting_counts = {}
    for zone_id, cnt in db.query(UnitPosting.post_zone_id, func.count(UnitPosting.id)).filter(UnitPosting.is_current == True).group_by(UnitPosting.post_zone_id).all():
        posting_counts[zone_id] = cnt
    zones = {z.id: z for z in db.query(PostZone).all() if z.minimum_units}
    units = db.query(Unit).filter(Unit.is_active == True).all()
    scored = []
    for u in units:
        s = 0.0
        reasons = []
        eligible = True
        caps = u.capabilities or {}
        u_agency = db.query(Agency).get(u.agency_id)

        # Hard eligibility rules
        if not u_agency or u_agency.agency_type != agency_type:
            eligible = False; reasons.append('different discipline')
        if u.current_incident_id:
            eligible = False; reasons.append('assigned to call')
        if u.current_status in _OUT_OF_SERVICE_STATUSES:
            eligible = False; reasons.append('out of service')
        elif u.current_status not in _ASSIGNABLE_STATUSES:
            eligible = False; reasons.append(f"status {u.current_status}")
        if u.last_seen_at is None:
            eligible = False; reasons.append('no GPS')
        elif (now - u.last_seen_at).total_seconds() > TAIP_OFFLINE_SECONDS:
            eligible = False; reasons.append('GPS offline')
        elif (now - u.last_seen_at).total_seconds() > TAIP_STALE_SECONDS:
            age = (now - u.last_seen_at).total_seconds()
            s -= age * 0.05; reasons.append('GPS stale')

        # Distance and ETA
        dist_miles = None
        eta_seconds = None
        if incident.lat is not None and incident.lng is not None and u.lat is not None and u.lng is not None:
            dist_m = _haversine_m(u.lat, u.lng, incident.lat, incident.lng)
            if dist_m is not None:
                dist_miles = dist_m / 1609.34
                eta_seconds = (dist_miles / speed_mph) * 3600
                s -= eta_seconds * 0.05
                reasons.append(f"{dist_miles:.1f} mi · {eta_seconds/60:.0f} min")
        if dist_miles is None:
            eligible = False; reasons.append('no GPS')

        # Service level / capability
        unit_level = (caps.get('service_level') or 'BLS').upper() if agency_type == 'ems' else (u.unit_type or '')
        if agency_type == 'ems':
            if required_level == 'ALS':
                if unit_level == 'ALS':
                    s += 300; reasons.append('ALS capable')
                else:
                    eligible = False; reasons.append('BLS only')
            else:
                if unit_level == 'ALS':
                    s += 50; reasons.append('ALS overqualified')
                elif unit_level == 'BLS':
                    s += 100; reasons.append('BLS match')

        # Agency preference
        if u.agency_id == incident.agency_id:
            s += 100; reasons.append('same agency')

        # Run-card / resource requirement
        if recommended_types:
            if (u.unit_type or '') in recommended_types:
                s += 200; reasons.append('run-card match')
            else:
                eligible = False; reasons.append('not a run-card resource')

        # Coverage loss penalty
        current_posting = db.query(UnitPosting).filter_by(unit_id=u.id, is_current=True).first()
        if current_posting:
            zone_id = current_posting.post_zone_id
            zone = zones.get(zone_id)
            if zone:
                remaining = posting_counts.get(zone_id, 0) - 1
                if remaining < zone.minimum_units:
                    s -= 200; reasons.append('removes only coverage in zone')

        role = unit_level if agency_type == 'ems' else (u.unit_type or '')
        scored.append({
            'unit_id': u.id,
            'call_sign': u.call_sign,
            'unit_type': u.unit_type,
            'agency_id': u.agency_id,
            'agency_type': u_agency.agency_type if u_agency else None,
            'service_level': role,
            'distance_miles': round(dist_miles, 2) if dist_miles is not None else None,
            'eta_seconds': int(eta_seconds) if eta_seconds is not None else None,
            'eta_minutes': round(eta_seconds/60, 1) if eta_seconds is not None else None,
            'score': round(s, 2),
            'eligible': eligible,
            'reason': ' · '.join(reasons)
        })
    scored.sort(key=lambda x: (-x['eligible'], -x['score']))
    return scored[:limit]

@app.get('/incidents/{incident_id}/timeline')
def timeline(incident_id: int, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    def to_dict(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name != 'geom'}
    events = [to_dict(e) for e in db.query(StatusEvent).filter_by(incident_id=incident_id).order_by(StatusEvent.created_at.desc()).all()]
    logs = [to_dict(l) for l in db.query(CallLog).filter_by(incident_id=incident_id).order_by(CallLog.timestamp.desc()).all()]
    messages = [to_dict(m) for m in db.query(DispatchMessage).filter_by(incident_id=incident_id).order_by(DispatchMessage.sent_at.desc()).all()]
    assignments = [to_dict(iu) for iu in db.query(IncidentUnit).filter_by(incident_id=incident_id).order_by(IncidentUnit.assigned_at.desc()).all()]
    personnel = [to_dict(ip) for ip in db.query(IncidentPersonnel).filter_by(incident_id=incident_id).order_by(IncidentPersonnel.en_route_at.desc()).all()]
    return {'incident': to_dict(incident), 'events': events, 'logs': logs, 'messages': messages, 'assignments': assignments, 'personnel': personnel}

@app.get('/incidents/{incident_id}/fire-report')
def fire_report(incident_id: int, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    agency = db.query(Agency).get(incident.agency_id)
    units = db.query(Unit).filter(Unit.current_incident_id == incident_id).all()
    personnel = db.query(IncidentPersonnel).filter_by(incident_id=incident_id).all()
    events = db.query(StatusEvent).filter_by(incident_id=incident_id).order_by(StatusEvent.created_at.asc()).all()
    def first_event(codes):
        for e in events:
            if e.status_code in codes:
                return e.created_at.isoformat() if e.created_at else None
        return None
    report = {
        'version': 'NFIRS-like-1.0',
        'incident': {
            'incident_number': incident.incident_number,
            'call_number': incident.call_number,
            'call_type': incident.call_type,
            'priority': incident.priority,
            'status': incident.status,
            'location': incident.location_text,
            'lat': incident.lat,
            'lng': incident.lng,
            'caller_name': incident.caller_name,
            'callback': incident.callback,
            'narrative': incident.narrative,
            'call_entry_started_at': incident.call_entry_started_at.isoformat() if incident.call_entry_started_at else None,
            'created_at': incident.created_at.isoformat() if incident.created_at else None,
            'closed_at': incident.closed_at.isoformat() if incident.closed_at else None,
        },
        'agency': {'id': agency.id, 'name': agency.name, 'agency_type': agency.agency_type} if agency else None,
        'timestamps': {
            'first_dispatched': first_event(['AK','dispatched']),
            'first_en_route': first_event(['ER','en_route']),
            'first_on_scene': first_event(['OS','on_scene']),
            'first_transport': first_event(['TR','ED']),
            'first_cleared': first_event(['CAN','clear']),
        },
        'units': [{
            'unit_id': u.id,
            'call_sign': u.call_sign,
            'unit_type': u.unit_type,
            'current_status': u.current_status,
            'last_assigned_at': u.last_assigned_at.isoformat() if u.last_assigned_at else None,
            'lat': u.lat,
            'lng': u.lng
        } for u in units],
        'personnel': [{
            'personnel_id': ip.personnel_id,
            'name': f"{ip.personnel.first_name or ''} {ip.personnel.last_name or ''}".strip(),
            'status': ip.status,
            'responding_vehicle': ip.responding_vehicle,
            'en_route_at': ip.en_route_at.isoformat() if ip.en_route_at else None,
            'arrived_at': ip.arrived_at.isoformat() if ip.arrived_at else None,
            'cleared_at': ip.cleared_at.isoformat() if ip.cleared_at else None
        } for ip in personnel],
        'summary': {
            'units_assigned': len(units),
            'personnel_responding': len(personnel),
            'personnel_arrived': sum(1 for ip in personnel if ip.status in ('arrived','cleared') or ip.arrived_at),
        }
    }
    return report

@app.put('/incidents/{incident_id}', response_model=IncidentOut)
def update_incident(request: Request, incident_id: int, body: IncidentUpdate, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    user = get_current_user(request)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(incident, k, v)
    if 'location_text' in changes:
        _validate_incident_location(db, incident, force=True)
    if body.status == 'closed' and not incident.closed_at:
        incident.closed_at = datetime.utcnow()
        for unit in db.query(Unit).filter(Unit.current_incident_id == incident.id).all():
            iu = db.query(IncidentUnit).filter_by(incident_id=incident.id, unit_id=unit.id, cleared_at=None).first()
            if iu:
                iu.cleared_at = incident.closed_at
                iu.assignment_status = 'cleared'
                if iu.assigned_at:
                    duration = (incident.closed_at - iu.assigned_at).total_seconds()
                    unit.accumulated_call_seconds = (unit.accumulated_call_seconds or 0) + duration
            agency = db.query(Agency).get(unit.agency_id) if unit.agency_id else None
            unit.current_status = 'AFR' if (agency and agency.agency_type == 'fire') else 'AQ'
            unit.current_incident_id = None
            db.add(StatusEvent(unit_id=unit.id, incident_id=incident.id, status_code=unit.current_status, reason='Call closed'))
    _log_event(db, 'incident_updated', 'incident', incident.id, user_id=user.get('user_id'), data=changes, agency_id=incident.agency_id)
    db.commit()
    db.refresh(incident)
    return incident

@app.get('/incidents/{incident_id}/location')
def get_incident_location(incident_id: int, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    loc = db.query(IncidentLocation).filter_by(incident_id=incident_id).first()
    if not loc:
        return {'incident_id': incident_id, 'verification_status': 'unverified', 'standardized_address': None, 'latitude': incident.lat, 'longitude': incident.lng}
    zone = db.query(PostZone).get(loc.zone_id) if loc.zone_id else None
    return {
        'incident_id': incident_id,
        'raw_address': loc.raw_address,
        'standardized_address': loc.standardized_address,
        'city': loc.city,
        'state': loc.state,
        'postal_code': loc.postal_code,
        'latitude': loc.latitude,
        'longitude': loc.longitude,
        'cross_streets': loc.cross_streets,
        'zone_id': loc.zone_id,
        'zone_name': zone.name if zone else None,
        'verification_status': loc.verification_status,
        'geocoded_at': loc.geocoded_at
    }

@app.post('/incidents/{incident_id}/validate-location')
def validate_incident_location_endpoint(incident_id: int, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    _validate_incident_location(db, incident, force=True)
    db.commit(); db.refresh(incident)
    loc = db.query(IncidentLocation).filter_by(incident_id=incident_id).first()
    return get_incident_location(incident_id, db)

@app.post('/incidents/{incident_id}/dispatch/{unit_id}')
def dispatch_unit(request: Request, incident_id: int, unit_id: int, notes: Optional[str] = None, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    unit = db.query(Unit).get(unit_id)
    if not incident or not unit:
        raise HTTPException(status_code=404, detail='Incident or unit not found')
    existing = db.query(IncidentUnit).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    if existing:
        raise HTTPException(status_code=400, detail='Unit already dispatched to incident')
    user = get_current_user(request)
    iu = IncidentUnit(incident_id=incident_id, unit_id=unit_id, notes=notes)
    unit.current_incident_id = incident_id
    unit.last_assigned_at = datetime.utcnow()
    unit.current_status = 'AK'
    db.add(iu)
    db.flush()
    refresh_incident_status(db, incident)
    _log_event(db, 'unit_dispatched', 'incident', incident_id, user_id=user.get('user_id'), data={'unit_id': unit_id, 'notes': notes}, agency_id=incident.agency_id)
    db.add(StatusEvent(unit_id=unit_id, incident_id=incident_id, status_code='AK', reason='Dispatched to incident'))
    db.commit()
    db.refresh(iu)
    return {
        'incident_id': iu.incident_id,
        'unit_id': iu.unit_id,
        'assigned_at': iu.assigned_at,
        'assignment_status': iu.assignment_status
    }

@app.post('/incidents/{incident_id}/units/{unit_id}/status')
def update_unit_status(request: Request, incident_id: int, unit_id: int, body: StatusUpdate, db: Session = Depends(get_db)):
    iu = db.query(IncidentUnit).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    if not iu:
        raise HTTPException(status_code=404, detail='Unit not assigned to incident')
    unit = db.query(Unit).get(unit_id)
    iu.assignment_status = map_status(body.status_code)
    if body.disposition is not None:
        iu.disposition = body.disposition
    if body.passenger_count is not None:
        iu.passenger_count = body.passenger_count
    if body.status_code in _CALL_ACTIVE_STATUSES:
        unit.current_status = body.status_code
        unit.current_incident_id = incident_id
    else:
        if iu.assigned_at:
            duration = (datetime.utcnow() - iu.assigned_at).total_seconds()
            unit.accumulated_call_seconds = (unit.accumulated_call_seconds or 0) + duration
        iu.cleared_at = datetime.utcnow()
        iu.assignment_status = 'cleared'
        unit.current_incident_id = None
        unit.current_status = 'AQ' if body.status_code == 'CAN' else body.status_code
    incident = db.query(Incident).get(incident_id)
    refresh_incident_status(db, incident)
    # Transport leg lifecycle
    if incident:
        open_leg = db.query(TransportLeg).filter_by(incident_id=incident.id, unit_id=unit_id).filter(TransportLeg.status != 'cleared').order_by(TransportLeg.created_at.desc()).first()
        if body.status_code in ('TR','ED'):
            if not open_leg:
                dest_id = body.destination_id
                if not dest_id:
                    latest = db.query(IncidentDestination).filter_by(incident_id=incident.id).order_by(IncidentDestination.created_at.desc()).first()
                    dest_id = latest.destination_id if latest else None
                open_leg = TransportLeg(incident_id=incident.id, unit_id=unit_id, destination_id=dest_id, status='en_route', en_route_at=datetime.utcnow())
                db.add(open_leg); db.flush()
            else:
                open_leg.status = 'en_route'
                if not open_leg.en_route_at:
                    open_leg.en_route_at = datetime.utcnow()
            if body.mileage is not None and open_leg.pickup_mileage is None:
                open_leg.pickup_mileage = body.mileage
        elif body.status_code == 'OS' and open_leg and open_leg.status == 'en_route':
            open_leg.status = 'arrived'
            if not open_leg.arrived_at:
                open_leg.arrived_at = datetime.utcnow()
        elif body.status_code not in _CALL_ACTIVE_STATUSES and open_leg:
            open_leg.status = 'cleared'
            if not open_leg.cleared_at:
                open_leg.cleared_at = datetime.utcnow()
            if body.mileage is not None and open_leg.dropoff_mileage is None:
                open_leg.dropoff_mileage = body.mileage
    user = get_current_user(request)
    db.add(StatusEvent(
        unit_id=unit_id,
        incident_id=incident_id,
        status_code=body.status_code,
        reason=body.reason,
        lat=body.lat,
        lng=body.lng
    ))
    _log_event(db, 'unit_status_changed', 'incident', incident_id, user_id=user.get('user_id'), data={'unit_id': unit_id, 'status_code': body.status_code, 'disposition': body.disposition, 'lat': body.lat, 'lng': body.lng}, agency_id=incident.agency_id)
    db.commit()
    return {'incident_id': incident_id, 'unit_id': unit_id, 'status': body.status_code}

class NoteCreate(BaseModel):
    note: str
    log_type: str = 'note'

@app.post('/incidents/{incident_id}/notes', response_model=MessageOut)
def add_incident_note(request: Request, incident_id: int, body: NoteCreate, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    user = get_current_user(request)
    timestamp = datetime.utcnow()
    note_text = f"[{timestamp.strftime('%H:%M:%S')}] {body.note}"
    if incident.narrative:
        incident.narrative += '\n' + note_text
    else:
        incident.narrative = note_text
    log = CallLog(incident_id=incident_id, user_id=user.get('user_id'), log_type=body.log_type, message=body.note, timestamp=timestamp)
    db.add(log)
    _log_event(db, 'note_added', 'incident', incident_id, user_id=user.get('user_id'), data={'note': body.note}, agency_id=incident.agency_id)
    db.commit(); db.refresh(log)
    return MessageOut(id=log.id, incident_id=incident_id, message_text=body.note, sent_at=timestamp, channel='note')

@app.get('/incidents/{incident_id}/personnel', response_model=List[IncidentPersonnelOut])
def list_incident_personnel(incident_id: int, db: Session = Depends(get_db)):
    return db.query(IncidentPersonnel).filter(IncidentPersonnel.incident_id == incident_id).order_by(IncidentPersonnel.en_route_at.desc()).all()

@app.post('/incidents/{incident_id}/personnel', response_model=IncidentPersonnelOut)
def add_incident_personnel(request: Request, incident_id: int, body: IncidentPersonnelCreate, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    p = db.query(Personnel).get(body.personnel_id)
    if not p:
        raise HTTPException(status_code=404, detail='Personnel not found')
    existing = db.query(IncidentPersonnel).filter_by(incident_id=incident_id, personnel_id=body.personnel_id, status='cleared').first()
    ip = IncidentPersonnel(incident_id=incident_id, personnel_id=body.personnel_id, status='en_route', responding_vehicle=body.responding_vehicle or 'Personal vehicle', notes=body.notes)
    db.add(ip); db.flush()
    user = get_current_user(request)
    _log_event(db, 'personnel_en_route', 'incident', incident_id, user_id=user.get('user_id'), data={'personnel_id': body.personnel_id, 'name': f"{p.first_name} {p.last_name}", 'vehicle': body.responding_vehicle}, agency_id=incident.agency_id)
    db.commit(); db.refresh(ip)
    return ip

@app.put('/incidents/{incident_id}/personnel/{ip_id}', response_model=IncidentPersonnelOut)
def update_incident_personnel(request: Request, incident_id: int, ip_id: int, body: IncidentPersonnelStatusUpdate, db: Session = Depends(get_db)):
    ip = db.query(IncidentPersonnel).filter_by(id=ip_id, incident_id=incident_id).first()
    if not ip:
        raise HTTPException(status_code=404, detail='Personnel response not found')
    ip.status = body.status
    ts = datetime.utcnow()
    if body.status == 'arrived' and not ip.arrived_at:
        ip.arrived_at = ts
    if body.status == 'cleared':
        ip.cleared_at = ts
        _log_event(db, 'personnel_cleared', 'incident', incident_id, user_id=get_current_user(request).get('user_id'), data={'personnel_id': ip.personnel_id, 'name': f"{ip.personnel.first_name} {ip.personnel.last_name}"}, agency_id=ip.incident.agency_id)
    else:
        _log_event(db, f'personnel_{body.status}', 'incident', incident_id, user_id=get_current_user(request).get('user_id'), data={'personnel_id': ip.personnel_id, 'name': f"{ip.personnel.first_name} {ip.personnel.last_name}"}, agency_id=ip.incident.agency_id)
    if body.notes is not None:
        ip.notes = body.notes
    db.commit(); db.refresh(ip)
    return ip

def _record_taip_sentence(db, raw: str, taip_id: Optional[str] = None, source=None, received_at: Optional[datetime] = None) -> TaipPosition:
    if not _taip_source_allowed(source):
        raise ValueError(f'TAIP source not allowed: {_taip_ip(source)}')
    data = parse_taip(raw)
    taip_id = taip_id or data.get('taip_id')
    if not taip_id:
        raise ValueError('taip_id not found in sentence')
    if _taip_rate_limited(taip_id):
        raise ValueError(f'TAIP rate limit exceeded for {taip_id}')
    unit = db.query(Unit).filter(Unit.taip_id == taip_id).first()
    if received_at is None:
        received_at = datetime.utcnow()
    reported_at = _taip_reported_time(data, received_at) or received_at
    if _taip_out_of_order(unit, reported_at):
        raise ValueError(f'TAIP packet out of order for {taip_id}')
    lat = data.get('lat')
    lng = data.get('lng')
    if lat is not None and lng is not None and not _taip_jump_ok(unit, lat, lng, reported_at):
        raise ValueError(f'TAIP impossible location jump for {taip_id}')
    pos = TaipPosition(
        taip_id=taip_id,
        raw_sentence=raw,
        lat=lat,
        lng=lng,
        speed=data.get('speed'),
        heading=data.get('heading'),
        ignition=data.get('ignition'),
        odometer=data.get('odometer'),
        fix_quality=data.get('fix_quality'),
        gps_seconds_of_day=data.get('gps_seconds_of_day'),
        data_age=data.get('data_age'),
        gps_source=data.get('gps_source'),
        reported_at=reported_at,
        received_at=received_at
    )
    if unit:
        pos.unit_id = unit.id
        if lat is not None:
            unit.lat = lat
        if lng is not None:
            unit.lng = lng
        if data.get('speed') is not None:
            unit.speed = data['speed']
        if data.get('heading') is not None:
            unit.heading = data['heading']
        unit.last_seen_at = reported_at
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return pos

@app.post('/taip/ingest')
def ingest_taip(request: Request, body: TaipIngest, db: Session = Depends(get_db)):
    try:
        forwarded = request.headers.get('x-forwarded-for')
        source = (forwarded.split(',')[0].strip() if forwarded else request.client.host) or 'unknown'
        pos = _record_taip_sentence(db, body.raw, body.taip_id, source=source, received_at=datetime.utcnow())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {'taip_id': pos.taip_id, 'parsed': parse_taip(body.raw), 'unit_id': pos.unit_id}

def _extract_taip_sentences(text: str) -> List[str]:
    """Pull one or more TAIP sentences out of a raw byte stream."""
    return re.findall(r'>[^<]+<', text)

def _taip_udp_listener():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', TAIP_UDP_PORT))
        print(f'TAIP UDP listener started on 0.0.0.0:{TAIP_UDP_PORT}')
        while True:
            try:
                data, addr = sock.recvfrom(2048)
                received_at = datetime.utcnow()
                text = data.decode('utf-8', errors='ignore')
                sentences = _extract_taip_sentences(text) or [text.strip()]
                for sentence in sentences:
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    try:
                        db = SessionLocal()
                        _record_taip_sentence(db, sentence, source=addr, received_at=received_at)
                    except Exception as e:
                        print(f'TAIP UDP record error from {addr}: {e}')
                    finally:
                        db.close()
            except Exception as e:
                print(f'TAIP UDP receive error: {e}')
    except OSError as e:
        print(f'TAIP UDP listener could not bind to port {TAIP_UDP_PORT}: {e}')
    except Exception as e:
        print(f'TAIP UDP listener error: {e}')

def _taip_tcp_client(conn, addr):
    db = None
    buffer = ''
    try:
        while True:
            data = conn.recv(2048)
            if not data:
                break
            received_at = datetime.utcnow()
            buffer += data.decode('utf-8', errors='ignore')
            while True:
                start = buffer.find('>')
                end = buffer.find('<', start)
                if start == -1 or end == -1:
                    break
                sentence = buffer[start:end+1]
                buffer = buffer[end+1:]
                try:
                    db = SessionLocal()
                    _record_taip_sentence(db, sentence, source=addr, received_at=received_at)
                except Exception as e:
                    print(f'TAIP TCP record error from {addr}: {e}')
                finally:
                    if db:
                        db.close(); db = None
    except Exception as e:
        print(f'TAIP TCP client error {addr}: {e}')
    finally:
        if db:
            db.close()
        try:
            conn.close()
        except Exception:
            pass

def _taip_tcp_listener():
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('0.0.0.0', TAIP_TCP_PORT))
        server.listen(5)
        print(f'TAIP TCP listener started on 0.0.0.0:{TAIP_TCP_PORT}')
        while True:
            try:
                conn, addr = server.accept()
                threading.Thread(target=_taip_tcp_client, args=(conn, addr), daemon=True).start()
            except Exception as e:
                print(f'TAIP TCP accept error: {e}')
    except OSError as e:
        print(f'TAIP TCP listener could not bind to port {TAIP_TCP_PORT}: {e}')
    except Exception as e:
        print(f'TAIP TCP listener error: {e}')

def start_taip_udp_listener():
    if TAIP_UDP_PORT <= 0:
        return
    t = threading.Thread(target=_taip_udp_listener, daemon=True)
    t.start()

def start_taip_tcp_listener():
    if TAIP_TCP_PORT <= 0:
        return
    t = threading.Thread(target=_taip_tcp_listener, daemon=True)
    t.start()

@app.get('/taip/listener-info')
def taip_listener_info():
    return {
        'udp_port': TAIP_UDP_PORT,
        'tcp_port': TAIP_TCP_PORT,
        'bind_host': '0.0.0.0',
        'note': 'Configure your remote TAIP feed to send UDP or TCP to the public IP of this server on the listed port. TAIP ID in the sentence (or from the PV ID= field) must match a Unit.taip_id in VolCAD.'
    }

@app.get('/taip/udp-info')
def taip_udp_info():
    return taip_listener_info()

@app.get('/taip/positions', response_model=List[TaipPositionOut])
def list_taip_positions(unit_id: Optional[int] = Query(None), limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(TaipPosition)
    if unit_id:
        q = q.filter(TaipPosition.unit_id == unit_id)
    return q.order_by(TaipPosition.received_at.desc()).limit(limit).all()

@app.get('/taip/stream')
async def taip_stream(db: Session = Depends(get_db)):
    async def event_generator():
        last_payload = None
        while True:
            units = db.query(Unit).all()
            data = []
            for u in units:
                stale, offline = _taip_stale_state(u.last_seen_at)
                data.append({
                    'id': u.id,
                    'call_sign': u.call_sign,
                    'lat': u.lat,
                    'lng': u.lng,
                    'heading': u.heading,
                    'speed': u.speed,
                    'last_seen_at': u.last_seen_at.isoformat() if u.last_seen_at else None,
                    'current_status': u.current_status,
                    'stale': stale,
                    'offline': offline
                })
            payload = json.dumps(data, default=str)
            if payload != last_payload:
                yield f'data: {payload}\n\n'
                last_payload = payload
            await asyncio.sleep(2)
    return StreamingResponse(event_generator(), media_type='text/event-stream')

@app.post('/units/{unit_id}/camera', response_model=UnitOut)
def set_unit_camera(unit_id: int, body: UnitCamera, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    unit.camera_url = body.camera_url
    db.commit()
    db.refresh(unit)
    return unit

@app.post('/units/{unit_id}/shift', response_model=UnitOut)
def set_unit_shift(unit_id: int, body: UnitShift, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    if body.action == 'start':
        unit.in_service_at = datetime.utcnow()
        unit.accumulated_call_seconds = 0
        unit.current_status = 'AQ'
    elif body.action == 'end':
        unit.in_service_at = None
        unit.accumulated_call_seconds = 0
        unit.current_status = 'off_duty'
    else:
        raise HTTPException(status_code=400, detail='Invalid action')
    db.commit()
    db.refresh(unit)
    return unit

@app.post('/units/{unit_id}/status', response_model=UnitOut)
def set_unit_status(unit_id: int, body: UnitStatus, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    unit.current_status = body.status_code
    if body.lat is not None: unit.lat = body.lat
    if body.lng is not None: unit.lng = body.lng
    incident_id = None
    if unit.current_incident_id:
        incident = db.query(Incident).get(unit.current_incident_id)
        if incident and incident.status != 'closed':
            iu = db.query(IncidentUnit).filter_by(incident_id=incident.id, unit_id=unit_id, cleared_at=None).first()
            if iu:
                if body.status_code in _CALL_ACTIVE_STATUSES:
                    iu.assignment_status = map_status(body.status_code)
                else:
                    if iu.assigned_at:
                        duration = (datetime.utcnow() - iu.assigned_at).total_seconds()
                        unit.accumulated_call_seconds = (unit.accumulated_call_seconds or 0) + duration
                    iu.cleared_at = datetime.utcnow()
                    iu.assignment_status = 'cleared'
                    unit.current_incident_id = None
                    unit.current_status = 'AQ' if body.status_code == 'CAN' else body.status_code
            refresh_incident_status(db, incident)
            incident_id = incident.id
    db.add(StatusEvent(unit_id=unit_id, incident_id=incident_id, status_code=body.status_code, reason=body.reason, lat=body.lat, lng=body.lng))
    db.commit()
    db.refresh(unit)
    return unit

@app.post('/units/{unit_id}/location', response_model=UnitOut)
def update_unit_location(unit_id: int, body: LocationUpdate, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    unit.lat = body.lat
    unit.lng = body.lng
    if body.speed is not None: unit.speed = body.speed
    if body.heading is not None: unit.heading = body.heading
    unit.last_seen_at = datetime.utcnow()
    db.add(StatusEvent(unit_id=unit_id, incident_id=None, status_code='GPS', lat=body.lat, lng=body.lng, reason=f"speed={body.speed}" if body.speed is not None else None))
    db.commit()
    db.refresh(unit)
    return unit

@app.post('/personnel/{personnel_id}/assign', response_model=PersonnelOut)
def assign_personnel(personnel_id: int, body: PersonnelAssign, db: Session = Depends(get_db)):
    p = db.query(Personnel).get(personnel_id)
    if not p:
        raise HTTPException(status_code=404, detail='Personnel not found')
    p.current_unit_id = body.unit_id
    db.commit()
    db.refresh(p)
    return p

def _record_alert(db, incident_id, unit_id, msg, crew):
    sent = []
    for c in crew:
        for channel in ['sms', 'email']:
            address = c.sms_phone if channel == 'sms' else c.email
            if address:
                m = DispatchMessage(incident_id=incident_id, unit_id=unit_id, message_text=msg, method=channel, channel=address, sent_at=datetime.utcnow())
                db.add(m)
                sent.append({'name': f"{c.first_name or ''} {c.last_name or ''}".strip(), 'channel': channel, 'address': address})
    return sent

@app.post('/units/{unit_id}/alert-crew')
def alert_unit_crew(unit_id: int, body: AlertCrew, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    msg = body.message or 'Alert from dispatch'
    crew = db.query(Personnel).filter(Personnel.current_unit_id == unit_id).all()
    sent = _record_alert(db, None, unit_id, msg, crew)
    db.commit()
    return {'recipients': sent, 'message': msg}

@app.post('/incidents/{incident_id}/alert-crew')
def alert_incident_crew(incident_id: int, body: AlertCrew, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    msg = body.message or f"{incident.incident_number}: {incident.call_type}"
    ius = db.query(IncidentUnit).filter_by(incident_id=incident_id).all()
    sent = []
    for iu in ius:
        crew = db.query(Personnel).filter(Personnel.current_unit_id == iu.unit_id).all()
        sent.extend(_record_alert(db, incident_id, iu.unit_id, msg, crew))
    db.commit()
    return {'recipients': sent, 'message': msg}

@app.post('/seed-pilot', response_model=dict)
def seed_pilot(current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    CENTER = (39.9612, -82.9988)
    def ensure_agency(name, atype, domain, lat, lng):
        a = db.query(Agency).filter(Agency.domain == domain).first()
        if a: return a
        a = Agency(name=name, agency_type=atype, domain=domain, city='Columbus', state='OH', lat=lat, lng=lng)
        db.add(a); db.flush(); return a
    police = ensure_agency('City Police', 'police', 'pilot.police', CENTER[0]-0.01, CENTER[1]+0.01)
    fire = ensure_agency('Metro Fire', 'fire', 'pilot.fire', CENTER[0]+0.01, CENTER[1]-0.01)
    ems = ensure_agency('County EMS', 'ems', 'pilot.ems', CENTER[0]+0.005, CENTER[1]-0.015)
    db.commit()
    def ensure_config(agency, template):
        for category, value in template.items():
            cfg = db.query(CustomerConfig).filter_by(agency_id=agency.id, category=category, key='defaults').first()
            if not cfg:
                db.add(CustomerConfig(agency_id=agency.id, category=category, key='defaults', value=value))
        seeded = db.query(CustomerConfig).filter_by(agency_id=agency.id, category='__seeded__', key='flag').first()
        if not seeded:
            db.add(CustomerConfig(agency_id=agency.id, category='__seeded__', key='flag', value=True))
    templates = {
        'police': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'TC','label':'Traffic Control'},{'code':'CT','label':'Citation'},{'code':'ARR','label':'Arrest'},{'code':'BK','label':'Booking'},{'code':'TR','label':'Transport'},{'code':'CAN','label':'Cancelled'},{'code':'LUN','label':'Lunch'},{'code':'OOS','label':'Out of Service'},{'code':'MAINT','label':'Maintenance'}],
            'modules': ['law'],
            'unit_types': ['patrol','detective','supervisor','k9','swat','traffic','rescue'],
            'call_types': [
                {'label':'Traffic Accident', 'priority':2, 'fields':['vehicles','injuries']},
                {'label':'Theft', 'priority':3, 'fields':['property','suspect']},
                {'label':'Domestic', 'priority':2, 'fields':['weapons','children']},
                {'label':'Assault', 'priority':1, 'fields':['weapons','injuries']},
                {'label':'Welfare Check', 'priority':3, 'fields':['age']},
                {'label':'Suspicious Person', 'priority':3, 'fields':['armed']}
            ],
            'priorities': [
                {'priority':1,'label':'Emergency','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Routine','target_seconds':720},
                {'priority':4,'label':'Low','target_seconds':1200}
            ],
            'response_plans': {
                'Traffic Accident': ['patrol','supervisor','rescue'],
                'Theft': ['patrol','detective'],
                'Domestic': ['patrol','supervisor'],
                'Assault': ['patrol','supervisor','k9'],
                'Welfare Check': ['patrol'],
                'Suspicious Person': ['patrol','k9']
            },
            'dispositions': ['Arrested','Cited','Warned','Referred','Report','No Action','False Alarm']
        },
        'fire': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'WATER','label':'Water on Fire'},{'code':'EXT','label':'Extinguished'},{'code':'OVER','label':'Overhaul'},{'code':'TR','label':'Transport'},{'code':'CAN','label':'Cancelled'},{'code':'LUN','label':'Lunch'},{'code':'OOS','label':'Out of Service'},{'code':'MAINT','label':'Maintenance'}],
            'modules': ['fire'],
            'unit_types': ['engine','ladder','rescue','brush','tanker','ambulance','chief'],
            'call_types': [
                {'label':'Structure Fire', 'priority':1, 'fields':['exposures','occupants']},
                {'label':'Vehicle Fire', 'priority':2, 'fields':['hazmat']},
                {'label':'Medical Assist', 'priority':2, 'fields':['age','conscious']},
                {'label':'Alarm', 'priority':3, 'fields':['type']},
                {'label':'Vehicle Accident', 'priority':1, 'fields':['extrication','injuries']},
                {'label':'Brush Fire', 'priority':2, 'fields':['size']}
            ],
            'priorities': [
                {'priority':1,'label':'Working Fire','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Routine','target_seconds':720},
                {'priority':4,'label':'Low','target_seconds':1200}
            ],
            'response_plans': {
                'Structure Fire': ['engine','ladder','rescue','chief'],
                'Vehicle Fire': ['engine','brush','tanker'],
                'Medical Assist': ['rescue','ambulance'],
                'Alarm': ['engine','ladder'],
                'Vehicle Accident': ['engine','rescue','ambulance'],
                'Brush Fire': ['brush','tanker']
            },
            'dispositions': ['Extinguished','Controlled','Under Control','False Alarm','No Fire','Cancelled']
        },
        'ems': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'OS','label':'On Scene'},{'code':'ER','label':'En Route'},{'code':'TR','label':'Transport'},{'code':'CAN','label':'Cancelled'},{'code':'LUN','label':'Lunch'},{'code':'OOS','label':'Out of Service'},{'code':'MAINT','label':'Maintenance'}],
            'modules': ['ems'],
            'unit_types': ['ambulance','medic','supervisor','air','rescue'],
            'call_types': [
                {'label':'Cardiac Arrest', 'priority':1, 'fields':['age','conscious']},
                {'label':'Chest Pain', 'priority':1, 'fields':['age','conscious']},
                {'label':'Respiratory', 'priority':1, 'fields':['age','conscious']},
                {'label':'Fall', 'priority':2, 'fields':['age','conscious']},
                {'label':'Motor Vehicle Accident', 'priority':1, 'fields':['extrication','injuries']},
                {'label':'Overdose', 'priority':1, 'fields':['age','conscious','substance']}
            ],
            'priorities': [
                {'priority':1,'label':'Priority 1','target_seconds':180},
                {'priority':2,'label':'Priority 2','target_seconds':420},
                {'priority':3,'label':'Priority 3','target_seconds':720},
                {'priority':4,'label':'Priority 4','target_seconds':1200}
            ],
            'response_plans': {
                'Cardiac Arrest': ['medic','supervisor'],
                'Chest Pain': ['medic','ambulance'],
                'Respiratory': ['medic','ambulance'],
                'Fall': ['ambulance','medic'],
                'Motor Vehicle Accident': ['air','ambulance','medic','supervisor'],
                'Overdose': ['ambulance','medic']
            },
            'dispositions': ['Transport to hospital','Refused','Treated/Released','Deceased','AMA']
        }
    }
    ensure_config(police, templates['police'])
    ensure_config(fire, templates['fire'])
    ensure_config(ems, templates['ems'])
    db.commit()
    def ensure_unit(call_sign, agency_id, unit_type, lat, lng, taip_id, capabilities=None):
        u = db.query(Unit).filter(Unit.call_sign == call_sign).first()
        if u: return u
        u = Unit(name=call_sign, call_sign=call_sign, agency_id=agency_id, unit_type=unit_type, capabilities=capabilities, lat=lat, lng=lng, taip_id=taip_id, in_service_at=datetime.utcnow(), current_status='AQ', current_incident_id=None, accumulated_call_seconds=0)
        db.add(u); db.flush(); return u
    u1 = ensure_unit('A12', police.id, 'patrol', CENTER[0]-0.008, CENTER[1]+0.012, 'TAIP-A12', {'service_level':'patrol','equipment':['lightbar','patrol']})
    u2 = ensure_unit('E1', fire.id, 'engine', CENTER[0]+0.012, CENTER[1]-0.012, 'TAIP-E1', {'apparatus':'engine','water':1000,'equipment':['hose','ladder']})
    u3 = ensure_unit('M1', ems.id, 'ambulance', CENTER[0]+0.007, CENTER[1]-0.017, 'TAIP-M1', {'service_level':'ALS','equipment':['monitor','defibrillator','oxygen']})
    db.commit()
    if db.query(Personnel).filter(Personnel.email == 'john@pilot.example').count() == 0:
        db.add(Personnel(agency_id=police.id, first_name='John', last_name='Doe', email='john@pilot.example', current_unit_id=u1.id))
    if db.query(Personnel).filter(Personnel.email == 'jane@pilot.example').count() == 0:
        db.add(Personnel(agency_id=fire.id, first_name='Jane', last_name='Smith', email='jane@pilot.example', current_unit_id=u2.id))
    db.commit()
    inc = db.query(Incident).filter(Incident.call_number == 'PILOT-0001').first()
    if not inc:
        inc = Incident(agency_id=fire.id, call_number='PILOT-0001', incident_number='PILOT-0001', call_type='Structure Fire', priority=1, location_text='123 Main St', lat=CENTER[0]+0.012, lng=CENTER[1]-0.012, status='open', narrative='Pilot structure fire demo')
        db.add(inc); db.flush()
    if not db.query(IncidentUnit).filter_by(incident_id=inc.id, unit_id=u2.id).first():
        db.add(IncidentUnit(incident_id=inc.id, unit_id=u2.id))
        u2.current_incident_id = inc.id; u2.current_status = 'AK'; u2.last_assigned_at = datetime.utcnow()
        db.add(StatusEvent(unit_id=u2.id, incident_id=inc.id, status_code='AK', reason='Dispatched to incident'))
        inc.status = 'dispatched'
    if not db.query(IncidentUnit).filter_by(incident_id=inc.id, unit_id=u3.id).first():
        db.add(IncidentUnit(incident_id=inc.id, unit_id=u3.id))
        u3.current_incident_id = inc.id; u3.current_status = 'AK'; u3.last_assigned_at = datetime.utcnow()
        db.add(StatusEvent(unit_id=u3.id, incident_id=inc.id, status_code='AK', reason='Dispatched to incident'))
    def ensure_destination(name, agency_id, category, address, notes, details, lat, lng):
        d = db.query(Destination).filter(Destination.agency_id == agency_id, Destination.name == name).first()
        if d: return d
        d = Destination(agency_id=agency_id, name=name, address=address, category=category, lat=lat, lng=lng, notes=notes, details=details)
        db.add(d); db.flush(); return d
    ensure_destination('Grant Hospital', ems.id, 'hospital', '1100 Medical Center Dr', {'gate_code':'4721','door_code':'ER2','animals':'','people':''}, {'entrance':'Main ER entrance','ambulance_loading':'Bay 3','access_instructions':'Ring bell at ambulance bay. Use ER2 door code after hours.','contact_numbers':['614-555-1000'],'receiving_capabilities':['ER','Trauma','Stroke','Cardiac'],'restrictions':'None','geofence':None,'photos':[]}, CENTER[0]+0.02, CENTER[1]+0.005)
    ensure_destination('Riverside Medical', ems.id, 'hospital', '3300 Riverside Pkwy', {'gate_code':'','door_code':'','animals':'','people':'Security escort after 2200'}, {'entrance':'North ED entrance','ambulance_loading':'Covered ramp','access_instructions':'Security escort required after 2200. Check in at triage.','contact_numbers':['614-555-3300'],'receiving_capabilities':['ER','Birthing','Psychiatric'],'restrictions':'Psych patients must call ahead','geofence':None,'photos':[]}, CENTER[0]-0.015, CENTER[1]-0.005)
    ensure_destination('County Jail', police.id, 'jail', '350 Justice Blvd', {'gate_code':'1092','people':'Violent offenders processed at intake 3','door_code':'Sallyport 1','animals':'K9 unit active M-F'}, {'entrance':'Sallyport 1','ambulance_loading':'Sallyport 1','access_instructions':'Use sallyport door code. K9 active M-F.','contact_numbers':['614-555-3500'],'receiving_capabilities':['Booking','Medical bay'],'restrictions':'Violent offenders processed at intake 3','geofence':None,'photos':[]}, CENTER[0]-0.02, CENTER[1]-0.01)
    db.commit()
    _log_event(db, 'pilot_seeded', 'system', 0, user_id=current_user.get('user_id'), data={'agencies':[police.id, fire.id, ems.id]}, agency_id=None)
    return {'status': 'seeded', 'agencies': [police.id, fire.id, ems.id], 'incident': inc.id}

@app.post('/import/{entity}', response_model=dict)
def import_csv(entity: str, file: UploadFile = File(...), current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    import csv, io
    if entity not in ('agencies', 'units', 'personnel', 'incidents', 'map-layers'):
        raise HTTPException(status_code=400, detail='Entity must be agencies, units, personnel, incidents, or map-layers')
    content = file.file.read().decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))
    count = 0; errors = []
    if entity == 'map-layers':
        layers = []
        for idx, row in enumerate(reader, start=1):
            try:
                geojson = None
                if row.get('geojson'):
                    try: geojson = json.loads(row['geojson'])
                    except Exception: pass
                layers.append({'name': row['name'], 'type': row.get('type', 'hydrant'), 'lat': float(row['lat']) if row.get('lat') else None, 'lng': float(row['lng']) if row.get('lng') else None, 'agency_id': int(row['agency_id']) if row.get('agency_id') else None, 'geojson': geojson})
            except Exception as e:
                errors.append(f'row {idx}: {e}')
        if layers:
            existing = db.query(CustomerConfig).filter_by(category='map_layers', key='all').first()
            if existing:
                existing.value = (existing.value or []) + layers
            else:
                db.add(CustomerConfig(category='map_layers', key='all', value=layers))
            db.commit()
            _log_event(db, 'csv_imported', 'system', 0, user_id=current_user.get('user_id'), data={'entity': 'map-layers', 'imported': len(layers), 'errors': len(errors)}, agency_id=None)
        return {'imported': len(layers), 'errors': errors[:10]}
    for idx, row in enumerate(reader, start=1):
        try:
            if entity == 'agencies':
                db.add(Agency(name=row['name'], agency_type=row.get('agency_type', 'fire'), city=row.get('city'), state=row.get('state'), domain=row.get('domain')))
            elif entity == 'units':
                db.add(Unit(agency_id=int(row['agency_id']), name=row.get('name', row['call_sign']), call_sign=row['call_sign'], unit_type=row.get('unit_type', 'patrol'), lat=float(row['lat']) if row.get('lat') else None, lng=float(row['lng']) if row.get('lng') else None, taip_id=row.get('taip_id'), taip_destination_url=row.get('taip_destination_url'), taip_port=int(row['taip_port']) if row.get('taip_port') else None, camera_url=row.get('camera_url'), current_status='AQ', in_service_at=datetime.utcnow(), accumulated_call_seconds=0))
            elif entity == 'personnel':
                db.add(Personnel(agency_id=int(row['agency_id']), first_name=row['first_name'], last_name=row['last_name'], email=row.get('email'), phone=row.get('phone'), sms_phone=row.get('sms_phone'), current_unit_id=int(row['current_unit_id']) if row.get('current_unit_id') else None, duty_status=row.get('duty_status', 'off_duty')))
            elif entity == 'incidents':
                db.add(Incident(agency_id=int(row['agency_id']), call_number=row.get('call_number'), incident_number=row.get('incident_number'), call_type=row.get('call_type', 'Unknown'), priority=int(row['priority']) if row.get('priority') else 2, location_text=row.get('location_text'), lat=float(row['lat']) if row.get('lat') else None, lng=float(row['lng']) if row.get('lng') else None, status=row.get('status', 'open'), caller_name=row.get('caller_name'), callback=row.get('callback'), narrative=row.get('narrative')))
            count += 1
            if count % 100 == 0:
                db.commit()
        except Exception as e:
            errors.append(f'row {idx}: {e}')
    db.commit()
    _log_event(db, 'csv_imported', 'system', 0, user_id=current_user.get('user_id'), data={'entity': entity, 'imported': count, 'errors': len(errors)}, agency_id=None)
    return {'imported': count, 'errors': errors[:10]}

@app.get('/import')
def import_page():
    return FileResponse('static/import.html')

@app.get('/reports')
def reports():
    return FileResponse('static/reporting.html')

@app.get('/users', response_model=List[UserOut])
def list_users(current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.created_at.desc()).all()

@app.post('/users', response_model=UserOut)
def create_user(body: UserCreate, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail='Email already exists')
    u = User(email=body.email, hashed_password=hash_password(body.password), role=body.role, agency_id=body.agency_id, is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    _log_event(db, 'user_created', 'system', 0, user_id=current_user.get('user_id'), data={'new_user': u.id, 'role': u.role}, agency_id=body.agency_id)
    return u

@app.put('/users/{user_id}', response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.query(User).get(user_id)
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    data = body.model_dump(exclude_unset=True)
    if 'password' in data:
        u.hashed_password = hash_password(data.pop('password'))
    for k, v in data.items():
        setattr(u, k, v)
    db.commit(); db.refresh(u)
    return u

@app.delete('/users/{user_id}')
def delete_user(user_id: int, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.query(User).get(user_id)
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    db.delete(u); db.commit()
    return {'deleted': user_id}

@app.get('/users-page')
def users_page():
    return FileResponse('static/users.html')

@app.get('/events-page')
def events_page():
    return FileResponse('static/events.html')

@app.get('/scheduled-transports-page')
def scheduled_transports_page():
    return FileResponse('static/scheduled.html')

@app.get('/coverage-page')
def coverage_page():
    return FileResponse('static/coverage.html', headers={'Cache-Control':'no-cache, no-store, must-revalidate'})

@app.get('/scheduled-transports', response_model=List[ScheduledTransportOut])
def list_scheduled_transports(status: Optional[str] = None, agency_id: Optional[int] = None, date: Optional[date] = Query(None), current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(ScheduledTransport)
    if agency_id:
        q = q.filter(ScheduledTransport.agency_id == agency_id)
    if status:
        q = q.filter(ScheduledTransport.status == status)
    if date:
        start = datetime.combine(date, time.min)
        end = datetime.combine(date, time.max)
        q = q.filter(ScheduledTransport.scheduled_at >= start, ScheduledTransport.scheduled_at <= end)
    return q.order_by(ScheduledTransport.scheduled_at.asc()).all()

@app.post('/scheduled-transports', response_model=ScheduledTransportOut)
def create_scheduled_transport(body: ScheduledTransportCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    st = ScheduledTransport(**body.dict())
    db.add(st); db.commit(); db.refresh(st)
    _log_event(db, 'scheduled_transport_created', 'scheduled_transport', st.id, user_id=current_user.get('user_id'), data=body.dict(), agency_id=st.agency_id)
    return st

@app.get('/scheduled-transports/{st_id}', response_model=ScheduledTransportOut)
def get_scheduled_transport(st_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    st = db.query(ScheduledTransport).get(st_id)
    if not st:
        raise HTTPException(status_code=404, detail='Scheduled transport not found')
    return st

@app.put('/scheduled-transports/{st_id}', response_model=ScheduledTransportOut)
def update_scheduled_transport(st_id: int, body: ScheduledTransportUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    st = db.query(ScheduledTransport).get(st_id)
    if not st:
        raise HTTPException(status_code=404, detail='Scheduled transport not found')
    for k, v in body.dict(exclude_unset=True).items():
        setattr(st, k, v)
    db.commit(); db.refresh(st)
    _log_event(db, 'scheduled_transport_updated', 'scheduled_transport', st.id, user_id=current_user.get('user_id'), data=body.dict(exclude_unset=True), agency_id=st.agency_id)
    return st

@app.post('/scheduled-transports/{st_id}/cancel', response_model=ScheduledTransportOut)
def cancel_scheduled_transport(st_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    st = db.query(ScheduledTransport).get(st_id)
    if not st:
        raise HTTPException(status_code=404, detail='Scheduled transport not found')
    st.status = 'cancelled'
    db.commit(); db.refresh(st)
    _log_event(db, 'scheduled_transport_cancelled', 'scheduled_transport', st.id, user_id=current_user.get('user_id'), data={}, agency_id=st.agency_id)
    return st

@app.post('/scheduled-transports/{st_id}/dispatch', response_model=ScheduledTransportOut)
def dispatch_scheduled_transport(st_id: int, body: StatusUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    st = db.query(ScheduledTransport).get(st_id)
    if not st:
        raise HTTPException(status_code=404, detail='Scheduled transport not found')
    if not body.unit_id:
        raise HTTPException(status_code=400, detail='unit_id is required')
    unit = db.query(Unit).get(body.unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    if not st.destination_id:
        raise HTTPException(status_code=400, detail='Scheduled transport must have a destination_id to dispatch')
    incident = Incident(
        agency_id=st.agency_id,
        call_number=f'SCH-{st.id:05d}',
        incident_number=f'SCH-{st.id:05d}',
        call_type=st.call_type or 'Routine Transport',
        priority=3,
        location_text=st.pickup_address,
        lat=st.pickup_lat,
        lng=st.pickup_lng,
        status='open',
        narrative=st.notes or f'Scheduled transport for {st.patient_name or "patient"} to {st.destination_name or "destination"}'
    )
    db.add(incident); db.flush()
    iu = IncidentUnit(incident_id=incident.id, unit_id=unit.id)
    db.add(iu)
    unit.current_incident_id = incident.id
    unit.current_status = 'AK'
    unit.last_assigned_at = datetime.utcnow()
    db.add(StatusEvent(unit_id=unit.id, incident_id=incident.id, status_code='AK', reason=f'Dispatched from scheduled transport {st.id}'))
    st.status = 'dispatched'
    st.unit_id = unit.id
    st.incident_id = incident.id
    db.commit(); db.refresh(st)
    _log_event(db, 'scheduled_transport_dispatched', 'scheduled_transport', st.id, user_id=current_user.get('user_id'), data={'incident_id': incident.id, 'unit_id': unit.id}, agency_id=st.agency_id)
    return st

@app.get('/scheduled-events-page')
def scheduled_events_page():
    return FileResponse('static/scheduled_events.html')

@app.get('/scheduled-events', response_model=List[ScheduledEventOut])
def list_scheduled_events(status: Optional[str] = None, agency_id: Optional[int] = None, date: Optional[date] = Query(None), current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(ScheduledEvent)
    if agency_id:
        q = q.filter(ScheduledEvent.agency_id == agency_id)
    if status:
        q = q.filter(ScheduledEvent.status == status)
    if date:
        start = datetime.combine(date, time.min)
        end = datetime.combine(date, time.max)
        q = q.filter(ScheduledEvent.scheduled_at >= start, ScheduledEvent.scheduled_at <= end)
    return q.order_by(ScheduledEvent.scheduled_at.asc()).all()

@app.post('/scheduled-events', response_model=ScheduledEventOut)
def create_scheduled_event(body: ScheduledEventCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    se = ScheduledEvent(**body.dict())
    db.add(se); db.commit(); db.refresh(se)
    _log_event(db, 'scheduled_event_created', 'scheduled_event', se.id, user_id=current_user.get('user_id'), data=body.dict(), agency_id=se.agency_id)
    return se

@app.get('/scheduled-events/{se_id}', response_model=ScheduledEventOut)
def get_scheduled_event(se_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    se = db.query(ScheduledEvent).get(se_id)
    if not se:
        raise HTTPException(status_code=404, detail='Scheduled event not found')
    return se

@app.put('/scheduled-events/{se_id}', response_model=ScheduledEventOut)
def update_scheduled_event(se_id: int, body: ScheduledEventUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    se = db.query(ScheduledEvent).get(se_id)
    if not se:
        raise HTTPException(status_code=404, detail='Scheduled event not found')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(se, k, v)
    db.commit(); db.refresh(se)
    _log_event(db, 'scheduled_event_updated', 'scheduled_event', se.id, user_id=current_user.get('user_id'), data=body.model_dump(exclude_unset=True), agency_id=se.agency_id)
    return se

@app.delete('/scheduled-events/{se_id}')
def delete_scheduled_event(se_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    se = db.query(ScheduledEvent).get(se_id)
    if not se:
        raise HTTPException(status_code=404, detail='Scheduled event not found')
    db.delete(se); db.commit()
    _log_event(db, 'scheduled_event_deleted', 'scheduled_event', se.id, user_id=current_user.get('user_id'), data={}, agency_id=se.agency_id)
    return {'deleted': se_id}

@app.get('/post-zones', response_model=List[PostZoneOut])
def list_post_zones(agency_id: Optional[int] = None, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(PostZone)
    if agency_id:
        q = q.filter(PostZone.agency_id == agency_id)
    return q.order_by(PostZone.display_order.asc(), PostZone.name.asc()).all()

@app.post('/post-zones', response_model=PostZoneOut)
def create_post_zone(body: PostZoneCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    pz = PostZone(**body.dict())
    db.add(pz); db.commit(); db.refresh(pz)
    _log_event(db, 'post_zone_created', 'post_zone', pz.id, user_id=current_user.get('user_id'), data=body.dict(), agency_id=pz.agency_id)
    return pz

@app.get('/post-zones/{pz_id}', response_model=PostZoneOut)
def get_post_zone(pz_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    pz = db.query(PostZone).get(pz_id)
    if not pz:
        raise HTTPException(status_code=404, detail='Post zone not found')
    return pz

@app.put('/post-zones/{pz_id}', response_model=PostZoneOut)
def update_post_zone(pz_id: int, body: PostZoneUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    pz = db.query(PostZone).get(pz_id)
    if not pz:
        raise HTTPException(status_code=404, detail='Post zone not found')
    for k, v in body.dict(exclude_unset=True).items():
        setattr(pz, k, v)
    db.commit(); db.refresh(pz)
    _log_event(db, 'post_zone_updated', 'post_zone', pz.id, user_id=current_user.get('user_id'), data=body.dict(exclude_unset=True), agency_id=pz.agency_id)
    return pz

@app.delete('/post-zones/{pz_id}', response_model=PostZoneOut)
def delete_post_zone(pz_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    pz = db.query(PostZone).get(pz_id)
    if not pz:
        raise HTTPException(status_code=404, detail='Post zone not found')
    db.delete(pz); db.commit()
    _log_event(db, 'post_zone_deleted', 'post_zone', pz.id, user_id=current_user.get('user_id'), data={}, agency_id=pz.agency_id)
    return pz

@app.get('/unit-postings', response_model=List[UnitPostingOut])
def list_unit_postings(unit_id: Optional[int] = None, post_zone_id: Optional[int] = None, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(UnitPosting).filter(UnitPosting.is_current == True)
    if unit_id:
        q = q.filter(UnitPosting.unit_id == unit_id)
    if post_zone_id:
        q = q.filter(UnitPosting.post_zone_id == post_zone_id)
    return q.order_by(UnitPosting.posted_at.desc()).all()

@app.post('/unit-postings', response_model=UnitPostingOut)
def create_unit_posting(body: UnitPostingCreate, request: Request, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    # close any current posting for this unit
    db.query(UnitPosting).filter_by(unit_id=body.unit_id, is_current=True).update({'is_current': False, 'removed_at': datetime.utcnow()})
    up = UnitPosting(unit_id=body.unit_id, post_zone_id=body.post_zone_id, posted_by_user_id=current_user.get('user_id'), is_current=True)
    db.add(up); db.commit(); db.refresh(up)
    _log_event(db, 'unit_posted', 'unit_posting', up.id, user_id=current_user.get('user_id'), data=body.dict(), agency_id=up.unit.agency_id)
    return up

@app.delete('/unit-postings/{up_id}', response_model=UnitPostingOut)
def remove_unit_posting(up_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    up = db.query(UnitPosting).get(up_id)
    if not up:
        raise HTTPException(status_code=404, detail='Unit posting not found')
    up.is_current = False
    up.removed_at = datetime.utcnow()
    db.commit(); db.refresh(up)
    _log_event(db, 'unit_posting_removed', 'unit_posting', up.id, user_id=current_user.get('user_id'), data={}, agency_id=up.unit.agency_id)
    return up

@app.get('/incidents/{incident_id}/epcr', response_model=EpcrExportOut)
def get_epcr_export(incident_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(EpcrExport).filter_by(incident_id=incident_id).order_by(EpcrExport.exported_at.desc()).first() or {}

@app.post('/incidents/{incident_id}/epcr', response_model=EpcrExportOut)
def create_epcr_export(incident_id: int, body: EpcrExportCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    dest = db.query(Destination).get(body.destination_id) if body.destination_id else None
    assigned = db.query(Unit, IncidentUnit).join(IncidentUnit, Unit.id == IncidentUnit.unit_id).filter(IncidentUnit.incident_id == incident_id).all()
    transport_legs = db.query(TransportLeg).filter_by(incident_id=incident_id).order_by(TransportLeg.created_at.asc()).all()
    mileage_readings = db.query(MileageReading).filter_by(incident_id=incident_id).order_by(MileageReading.recorded_at.asc()).all()
    payload = {
        'call_number': incident.call_number or incident.incident_number,
        'call_type': incident.call_type,
        'priority': incident.priority,
        'location': {'text': incident.location_text, 'lat': incident.lat, 'lng': incident.lng},
        'dispatch_datetime': incident.created_at.isoformat() if incident.created_at else None,
        'patient': {'name': incident.caller_name, 'callback': incident.callback},
        'narrative': incident.narrative,
        'assigned_units': [{
            'call_sign': u.call_sign,
            'unit_type': u.unit_type,
            'capabilities': u.capabilities,
            'service_level': (u.capabilities or {}).get('service_level') if u.capabilities else None,
            'dispatched_at': iu.created_at.isoformat() if iu.created_at else None
        } for u, iu in assigned],
        'destination': {'id': dest.id, 'name': dest.name, 'address': dest.address} if dest else None,
        'transport_legs': [{
            'unit_id': leg.unit_id,
            'en_route_at': leg.en_route_at.isoformat() if leg.en_route_at else None,
            'arrived_at': leg.arrived_at.isoformat() if leg.arrived_at else None,
            'cleared_at': leg.cleared_at.isoformat() if leg.cleared_at else None,
            'pickup_mileage': leg.pickup_mileage,
            'dropoff_mileage': leg.dropoff_mileage,
            'status': leg.status
        } for leg in transport_legs],
        'mileage_readings': [{
            'unit_id': m.unit_id,
            'status_code': m.status_code,
            'mileage': m.mileage,
            'recorded_at': m.recorded_at.isoformat() if m.recorded_at else None
        } for m in mileage_readings]
    }
    export = EpcrExport(
        incident_id=incident_id,
        destination_id=body.destination_id,
        exported_by_user_id=current_user.get('user_id'),
        epcr_payload=payload,
        status='pending',
        external_id=None,
        response_body=None
    )
    db.add(export); db.commit(); db.refresh(export)
    _log_event(db, 'epcr_export_created', 'incident', incident_id, user_id=current_user.get('user_id'), data={'epcr_export_id': export.id}, agency_id=incident.agency_id)
    return export

@app.put('/epcr-exports/{export_id}/status', response_model=EpcrExportOut)
def update_epcr_export_status(export_id: int, status: str, external_id: Optional[str] = None, response_body: Optional[str] = None, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    export = db.query(EpcrExport).get(export_id)
    if not export:
        raise HTTPException(status_code=404, detail='ePCR export not found')
    export.status = status
    if external_id is not None:
        export.external_id = external_id
    if response_body is not None:
        export.response_body = response_body
    db.commit(); db.refresh(export)
    _log_event(db, 'epcr_export_status', 'incident', export.incident_id, user_id=current_user.get('user_id'), data={'status': status, 'export_id': export.id}, agency_id=export.incident.agency_id)
    return export

@app.get('/incidents/{incident_id}/mileage-summary')
def incident_mileage_summary(incident_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    legs = db.query(TransportLeg).filter_by(incident_id=incident_id).order_by(TransportLeg.created_at.asc()).all()
    readings = db.query(MileageReading).filter_by(incident_id=incident_id).order_by(MileageReading.recorded_at.asc()).all()
    unit_map = {u.id: u.call_sign for u in db.query(Unit).filter(Unit.id.in_([l.unit_id for l in legs]+[r.unit_id for r in readings])).all()}
    total_miles = 0.0
    leg_summaries = []
    for leg in legs:
        pickup = leg.pickup_mileage or 0
        dropoff = leg.dropoff_mileage or 0
        miles = max(0, dropoff - pickup)
        if pickup and dropoff:
            total_miles += miles
        leg_summaries.append({
            'id': leg.id,
            'unit_id': leg.unit_id,
            'call_sign': unit_map.get(leg.unit_id),
            'destination': leg.destination.name if leg.destination else None,
            'status': leg.status,
            'pickup_mileage': leg.pickup_mileage,
            'dropoff_mileage': leg.dropoff_mileage,
            'trip_miles': round(miles, 1),
            'en_route_at': leg.en_route_at.isoformat() if leg.en_route_at else None,
            'arrived_at': leg.arrived_at.isoformat() if leg.arrived_at else None,
            'cleared_at': leg.cleared_at.isoformat() if leg.cleared_at else None,
            'turnaround_seconds': int((leg.cleared_at - leg.arrived_at).total_seconds()) if leg.cleared_at and leg.arrived_at else None
        })
    readings_summary = [{'unit_id': r.unit_id, 'call_sign': unit_map.get(r.unit_id), 'status_code': r.status_code, 'mileage': r.mileage, 'recorded_at': r.recorded_at.isoformat() if r.recorded_at else None} for r in readings]
    return {'incident_id': incident_id, 'total_trip_miles': round(total_miles, 1), 'legs': leg_summaries, 'readings': readings_summary}

@app.get('/transport-legs/summary')
def transport_legs_summary(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    # aggregate recent completed leg stats
    since = datetime.utcnow() - timedelta(days=7)
    legs = db.query(TransportLeg).filter(TransportLeg.created_at >= since).order_by(TransportLeg.created_at.desc()).all()
    unit_map = {u.id: u.call_sign for u in db.query(Unit).all()}
    dest_map = {d.id: d.name for d in db.query(Destination).all()}
    rows = []
    for leg in legs:
        pickup = leg.pickup_mileage or 0
        dropoff = leg.dropoff_mileage or 0
        miles = max(0, dropoff - pickup) if dropoff and pickup else None
        rows.append({
            'id': leg.id,
            'incident_id': leg.incident_id,
            'unit_id': leg.unit_id,
            'call_sign': unit_map.get(leg.unit_id),
            'destination': dest_map.get(leg.destination_id),
            'trip_miles': round(miles,1) if miles is not None else None,
            'turnaround_seconds': int((leg.cleared_at - leg.arrived_at).total_seconds()) if leg.cleared_at and leg.arrived_at else None,
            'cleared_at': leg.cleared_at.isoformat() if leg.cleared_at else None
        })
    return rows

@app.get('/reports/summary')
def reports_summary(days: int = 7, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 90)))
    incidents = db.query(Incident).filter(Incident.created_at >= since).all()
    closed = [i for i in incidents if i.status == 'closed']
    open_count = len([i for i in incidents if i.status != 'closed'])
    legs = db.query(TransportLeg).filter(TransportLeg.created_at >= since).all()
    completed_legs = [l for l in legs if l.status == 'cleared']
    total_miles = 0.0
    total_turnaround = 0
    for leg in completed_legs:
        if leg.pickup_mileage is not None and leg.dropoff_mileage is not None:
            total_miles += max(0, leg.dropoff_mileage - leg.pickup_mileage)
        if leg.arrived_at and leg.cleared_at:
            total_turnaround += int((leg.cleared_at - leg.arrived_at).total_seconds())
    by_call_type = {}
    for i in incidents:
        ct = i.call_type or 'Unknown'
        by_call_type[ct] = by_call_type.get(ct, 0) + 1
    by_status = {}
    for i in incidents:
        by_status[i.status] = by_status.get(i.status, 0) + 1
    avg_turnaround = round(total_turnaround / len(completed_legs)) if completed_legs else None
    return {
        'period_days': days,
        'incident_count': len(incidents),
        'open_incidents': open_count,
        'closed_incidents': len(closed),
        'completed_transports': len(completed_legs),
        'total_trip_miles': round(total_miles, 1),
        'avg_turnaround_seconds': avg_turnaround,
        'by_call_type': by_call_type,
        'by_status': by_status
    }

@app.get('/reports-page')
def reports_page():
    return FileResponse('static/reports.html')

@app.get('/units/{unit_id}/trail')
def unit_trail(unit_id: int, hours: int = 8, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(hours=max(1, min(hours, 72)))
    events = db.query(StatusEvent).filter(StatusEvent.unit_id == unit_id, StatusEvent.lat != None, StatusEvent.lng != None, StatusEvent.created_at >= since).order_by(StatusEvent.created_at.asc()).all()
    return [{'lat': e.lat, 'lng': e.lng, 'status_code': e.status_code, 'created_at': e.created_at.isoformat() if e.created_at else None} for e in events]

# Resolve forward references now that all Pydantic models are defined
for _fwd in (ScheduledTransportOut, UnitPostingOut, EpcrExportOut):
    _fwd.model_rebuild()

# Startup: geocode any agencies without lat/lng and start TAIP listeners
geocode_missing_agencies()
start_taip_udp_listener()
start_taip_tcp_listener()
