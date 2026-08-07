from fastapi import FastAPI, Body, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON, or_, UniqueConstraint, inspect, text, event
from sqlalchemy.orm import declarative_base, relationship, backref, Session, sessionmaker
from pydantic import BaseModel, computed_field, validator
from datetime import datetime, date, time as dt_time, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Optional, Any, Dict
import os
import re
import math
import json
import asyncio
import time
import hmac
import hashlib
import traceback
import urllib.request
import urllib.parse
import socket
import shutil
import threading
import subprocess
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

# Session cookie lifetime in seconds (default 12 hours).
SESSION_MAX_AGE = int(os.getenv('SESSION_MAX_AGE', '43200'))

# Default software timezone - Central (America/Chicago). Override with DEFAULT_TIMEZONE env var.
DEFAULT_TIMEZONE_NAME = os.getenv('DEFAULT_TIMEZONE', 'America/Chicago')
try:
    DEFAULT_TIMEZONE = ZoneInfo(DEFAULT_TIMEZONE_NAME)
except Exception:
    DEFAULT_TIMEZONE = timezone(timedelta(hours=-6))

def tz_now():
    """Return the current wall-clock time in the default timezone as a naive datetime."""
    return datetime.now(DEFAULT_TIMEZONE).replace(tzinfo=None)

def _naive_local(dt):
    """Ensure a datetime is a naive local wall-clock datetime."""
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(DEFAULT_TIMEZONE).replace(tzinfo=None)

taip_last_packet: Dict[str, float] = {}

login_attempts = {}

# Starlette defaults multipart parts to 1 MB; raise that for uploaded photos/files
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
if hasattr(Request, 'form') and getattr(Request.form, '__kwdefaults__', None) and 'max_part_size' in Request.form.__kwdefaults__:
    Request.form.__kwdefaults__['max_part_size'] = MAX_UPLOAD_SIZE
if hasattr(Request, '_get_form') and getattr(Request._get_form, '__kwdefaults__', None) and 'max_part_size' in Request._get_form.__kwdefaults__:
    Request._get_form.__kwdefaults__['max_part_size'] = MAX_UPLOAD_SIZE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _load_version():
    """Return (version, date, message). Try git, then version.json, then env fallback."""
    try:
        out = subprocess.check_output(['git','log','-1','--pretty=format:%h|%ci|%s'], cwd=BASE_DIR, text=True, stderr=subprocess.DEVNULL).strip()
        if out:
            parts = out.split('|', 2)
            return {'version': parts[0], 'date': parts[1] if len(parts) > 1 else '', 'message': parts[2] if len(parts) > 2 else ''}
    except Exception:
        pass
    try:
        with open(os.path.join(BASE_DIR, 'version.json')) as f:
            data = json.load(f)
            return {'version': data.get('version','unknown'), 'date': data.get('date',''), 'message': data.get('message','')}
    except Exception:
        pass
    return {'version': os.getenv('VERSION','unknown'), 'date': os.getenv('VERSION_DATE',''), 'message': ''}

APP_VERSION = _load_version()

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
class Customer(Base):
    __tablename__ = 'customers'
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, index=True)
    domain = Column(String(100), unique=True, nullable=True)
    config = Column(JSON)
    approved = Column(Boolean, default=False)
    approved_at = Column(DateTime)
    created_at = Column(DateTime, default=tz_now)

class CustomerShare(Base):
    __tablename__ = 'customer_shares'
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False)
    shared_customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False)
    share_avl = Column(Boolean, default=False)
    created_at = Column(DateTime, default=tz_now)
    __table_args__ = (UniqueConstraint('customer_id', 'shared_customer_id', name='uix_customer_share'),)

class Agency(Base):
    __tablename__ = 'agencies'
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
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
    created_at = Column(DateTime, default=tz_now)
    units = relationship('Unit', back_populates='agency')
    personnel = relationship('Personnel', back_populates='agency')
    incidents = relationship('Incident', back_populates='agency')

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
    email = Column(String(255), unique=True, index=True)
    hashed_password = Column(String(255))
    first_name = Column(String(100))
    last_name = Column(String(100))
    role = Column(String(50), default='responder')
    is_active = Column(Boolean, default=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    preferences = Column(JSON)
    created_at = Column(DateTime, default=tz_now)
    customer = relationship('Customer')

class Personnel(Base):
    __tablename__ = 'personnel'
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    first_name = Column(String(100))
    last_name = Column(String(100))
    radio_id = Column(String(50), index=True)
    phone = Column(String(50))
    email = Column(String(255))
    sms_phone = Column(String(50))
    provider_level = Column(String(50))
    photo_url = Column(String(255))
    duty_status = Column(String(50), default='off_duty')
    is_active = Column(Boolean, default=True)
    current_unit_id = Column(Integer, ForeignKey('units.id'), nullable=True)
    created_at = Column(DateTime, default=tz_now)
    agency = relationship('Agency', back_populates='personnel')
    current_unit = relationship('Unit', foreign_keys=[current_unit_id])

class Location(Base):
    __tablename__ = 'locations'
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
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
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
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
    photo_url = Column(String(255))
    last_assigned_at = Column(DateTime)
    in_service_at = Column(DateTime)
    accumulated_call_seconds = Column(Float, default=0)
    is_active = Column(Boolean, default=True)
    agency = relationship('Agency', back_populates='units')
    current_incident = relationship('Incident', foreign_keys=[current_incident_id])

class Incident(Base):
    __tablename__ = 'incidents'
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
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
    created_at = Column(DateTime, default=tz_now)
    call_entry_started_at = Column(DateTime)
    closed_at = Column(DateTime)
    last_status_at = Column(DateTime, default=tz_now)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    agency = relationship('Agency', back_populates='incidents')
    assigned_units = relationship('IncidentUnit', back_populates='incident')

class IncidentUnit(Base):
    __tablename__ = 'incident_units'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'))
    unit_id = Column(Integer, ForeignKey('units.id'))
    assigned_at = Column(DateTime, default=tz_now)
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
    created_at = Column(DateTime, default=tz_now)
    incident = relationship('Incident', backref='incident_location')
    zone = relationship('PostZone')
    jurisdiction = relationship('Agency')

class IncidentUnitAck(Base):
    __tablename__ = 'incident_unit_acks'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False)
    unit_id = Column(Integer, ForeignKey('units.id'), nullable=False)
    acknowledged_at = Column(DateTime, default=tz_now)
    acknowledged_by = Column(String(255))
    __table_args__ = (UniqueConstraint('incident_id', 'unit_id', name='uix_incident_unit_ack'),)
    incident = relationship('Incident', backref='unit_acks')
    unit = relationship('Unit')

class IncidentPersonnel(Base):
    __tablename__ = 'incident_personnel'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False)
    personnel_id = Column(Integer, ForeignKey('personnel.id'), nullable=False)
    status = Column(String(50), default='en_route')
    en_route_at = Column(DateTime, default=tz_now)
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
    created_at = Column(DateTime, default=tz_now)
    unit = relationship('Unit')

def _update_incident_last_status(mapper, connection, target):
    if target.incident_id and target.created_at:
        connection.execute(text('UPDATE incidents SET last_status_at = :ts WHERE id = :id'), {'ts': target.created_at, 'id': target.incident_id})

event.listen(StatusEvent, 'after_insert', _update_incident_last_status)

class MileageReading(Base):
    __tablename__ = 'mileage_readings'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False)
    unit_id = Column(Integer, ForeignKey('units.id'), nullable=False)
    status_code = Column(String(50), nullable=False)
    mileage = Column(Float, nullable=False)
    recorded_at = Column(DateTime, default=tz_now)
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
    received_at = Column(DateTime, default=tz_now)
    unit = relationship('Unit')

class CallLog(Base):
    __tablename__ = 'call_logs'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'))
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    log_type = Column(String(50))
    message = Column(Text)
    timestamp = Column(DateTime, default=tz_now)

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
    timestamp = Column(DateTime, default=tz_now)

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
    created_at = Column(DateTime, default=tz_now)

class DestinationStatus(Base):
    __tablename__ = 'destination_statuses'
    id = Column(Integer, primary_key=True, index=True)
    destination_id = Column(Integer, ForeignKey('destinations.id'), nullable=False)
    status = Column(String(50), default='open')  # open, divert, on_hold, full, closed
    reason = Column(String(255))
    notes = Column(Text)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, default=tz_now)
    updated_at = Column(DateTime, default=tz_now, onupdate=tz_now)
    destination = relationship('Destination')

class IncidentDestination(Base):
    __tablename__ = 'incident_destinations'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False)
    destination_id = Column(Integer, ForeignKey('destinations.id'), nullable=False)
    notes = Column(JSON)
    created_at = Column(DateTime, default=tz_now)
    destination = relationship('Destination')
    incident = relationship('Incident', backref='destinations')

class TransportLeg(Base):
    __tablename__ = 'transport_legs'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False)
    unit_id = Column(Integer, ForeignKey('units.id'), nullable=False)
    destination_id = Column(Integer, ForeignKey('destinations.id'), nullable=True)
    status = Column(String(50), default='requested')
    requested_at = Column(DateTime, default=tz_now)
    en_route_at = Column(DateTime)
    arrived_at = Column(DateTime)
    departed_scene_at = Column(DateTime)
    arrived_destination_at = Column(DateTime)
    transfer_completed_at = Column(DateTime)
    cleared_at = Column(DateTime)
    passenger_count = Column(Integer)
    pickup_mileage = Column(Float)
    dropoff_mileage = Column(Float)
    pickup_address = Column(Text)
    dropoff_address = Column(Text)
    created_at = Column(DateTime, default=tz_now)
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
    allergies = Column(String(500))
    medications = Column(String(500))
    special_equipment = Column(JSON)
    notes = Column(Text)
    status = Column(String(50), default='scheduled')
    unit_id = Column(Integer, ForeignKey('units.id'))
    incident_id = Column(Integer, ForeignKey('incidents.id'))
    created_at = Column(DateTime, default=tz_now)
    updated_at = Column(DateTime, default=tz_now, onupdate=tz_now)
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
    created_at = Column(DateTime, default=tz_now)
    updated_at = Column(DateTime, default=tz_now, onupdate=tz_now)
    agency = relationship('Agency')
    unit = relationship('Unit')

class StandingOrder(Base):
    __tablename__ = 'standing_orders'
    id = Column(Integer, primary_key=True, index=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    patient_name = Column(String(255))
    pickup_address = Column(Text)
    pickup_lat = Column(Float)
    pickup_lng = Column(Float)
    destination_id = Column(Integer, ForeignKey('destinations.id'))
    destination_name = Column(String(255))
    destination_address = Column(Text)
    destination_lat = Column(Float)
    destination_lng = Column(Float)
    call_type = Column(String(100), default='Routine Transport')
    service_level = Column(String(50), default='BLS')
    mobility_level = Column(String(50))
    oxygen = Column(Boolean, default=False)
    isolation = Column(Boolean, default=False)
    stretcher = Column(Boolean, default=False)
    wheelchair = Column(Boolean, default=False)
    special_equipment = Column(JSON)
    notes = Column(Text)
    recurrence = Column(JSON)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=tz_now)
    updated_at = Column(DateTime, default=tz_now, onupdate=tz_now)
    agency = relationship('Agency')
    destination = relationship('Destination')

class IncidentReport(Base):
    __tablename__ = 'incident_reports'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'), nullable=False, index=True)
    report_number = Column(String(50), unique=True, index=True)
    status = Column(String(50), default='draft')
    version = Column(Integer, default=1)
    summary_json = Column(JSON)
    created_at = Column(DateTime, default=tz_now)
    updated_at = Column(DateTime, default=tz_now, onupdate=tz_now)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    finalized_at = Column(DateTime)
    finalized_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    amendment_reason = Column(Text)
    parent_report_version = Column(Integer, default=0)
    incident = relationship('Incident', backref='reports')
    creator = relationship('User', foreign_keys=[created_by])

class PostZone(Base):
    __tablename__ = 'post_zones'
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'))
    name = Column(String(100))
    zone_type = Column(String(50), default='post')
    color = Column(String(20), default='#3b82f6')
    geojson = Column(JSON)
    display_order = Column(Integer, default=0)
    minimum_units = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=tz_now)
    updated_at = Column(DateTime, default=tz_now, onupdate=tz_now)
    agency = relationship('Agency')
    postings = relationship('UnitPosting', backref='post_zone')

class UnitPosting(Base):
    __tablename__ = 'unit_postings'
    id = Column(Integer, primary_key=True, index=True)
    unit_id = Column(Integer, ForeignKey('units.id'))
    post_zone_id = Column(Integer, ForeignKey('post_zones.id'))
    posted_at = Column(DateTime, default=tz_now)
    removed_at = Column(DateTime)
    is_current = Column(Boolean, default=True)
    posted_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    unit = relationship('Unit')
    posted_by = relationship('User')

class CustomerConfig(Base):
    __tablename__ = 'customer_config'
    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=True)
    agency_id = Column(Integer, ForeignKey('agencies.id'), nullable=True)
    category = Column(String(50))
    key = Column(String(100))
    value = Column(JSON)
    created_at = Column(DateTime, default=tz_now)
    updated_at = Column(DateTime, default=tz_now, onupdate=tz_now)
    __table_args__ = (UniqueConstraint('customer_id', 'agency_id', 'category', 'key', name='uix_customer_config'),)

class EpcrExport(Base):
    __tablename__ = 'epcr_exports'
    id = Column(Integer, primary_key=True, index=True)
    incident_id = Column(Integer, ForeignKey('incidents.id'))
    exported_at = Column(DateTime, default=tz_now)
    exported_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    epcr_payload = Column(JSON)
    destination_id = Column(Integer, ForeignKey('destinations.id'), nullable=True)
    status = Column(String(50), default='pending')
    external_id = Column(String(255), nullable=True)
    response_body = Column(Text, nullable=True)
    incident = relationship('Incident')
    destination = relationship('Destination')
    exported_by = relationship('User')

def _db_type(col, is_sqlite=True):
    t = col.type
    if is_sqlite:
        if isinstance(t, (Integer, Boolean)):
            return 'INTEGER'
        if isinstance(t, Float):
            return 'REAL'
        return 'TEXT'
    if isinstance(t, Boolean):
        return 'BOOLEAN'
    if isinstance(t, Integer):
        return 'INTEGER'
    if isinstance(t, Float):
        return 'REAL'
    if isinstance(t, DateTime):
        return 'TIMESTAMP'
    if isinstance(t, Text):
        return 'TEXT'
    if isinstance(t, String):
        return f'VARCHAR({t.length or 255})'
    return 'TEXT'

def _ensure_default_customer(conn):
    try:
        customer_id = conn.execute(text('SELECT id FROM customers WHERE slug = :slug'), {'slug': 'default'}).scalar()
        if not customer_id:
            conn.execute(text('''
                INSERT INTO customers (name, slug, approved, created_at)
                VALUES (:name, :slug, 1, :ts)
            '''), {'name': 'Default Customer', 'slug': 'default', 'ts': tz_now()})
            customer_id = conn.execute(text('SELECT id FROM customers WHERE slug = :slug'), {'slug': 'default'}).scalar()
        if customer_id:
            for table in ('agencies', 'users', 'personnel', 'units', 'incidents', 'customer_config'):
                try:
                    if table == 'users':
                        conn.execute(text(f"UPDATE {table} SET customer_id = :cid WHERE customer_id IS NULL AND (role IS NULL OR role != 'superadmin')"), {'cid': customer_id})
                    else:
                        conn.execute(text(f'UPDATE {table} SET customer_id = :cid WHERE customer_id IS NULL'), {'cid': customer_id})
                except Exception as e:
                    print(f'DB backfill warning for {table}.customer_id: {e}')
            conn.commit()
    except Exception as e:
        print(f'DB backfill warning for default customer: {e}')

def ensure_db_columns():
    if not DATABASE_URL:
        return
    is_sqlite = DATABASE_URL.startswith('sqlite')
    with engine.connect() as conn:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        for table in Base.metadata.tables.values():
            if table.name not in existing_tables:
                continue
            try:
                existing_cols = {c['name'] for c in inspector.get_columns(table.name)}
            except Exception as e:
                print(f'DB migration warning for {table.name}: could not inspect columns: {e}')
                continue
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                try:
                    col_type = _db_type(col, is_sqlite)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'))
                    conn.commit()
                    print(f'Added column {table.name}.{col.name}')
                except Exception as e:
                    print(f'DB migration warning for {table.name}.{col.name}: {e}')
        try:
            conn.execute(text('''
                UPDATE incidents
                SET last_status_at = COALESCE(
                    (SELECT MAX(created_at) FROM status_events WHERE status_events.incident_id = incidents.id),
                    created_at
                )
                WHERE last_status_at IS NULL
            '''))
            conn.commit()
        except Exception as e:
            print(f'DB backfill warning for last_status_at: {e}')

def init_sqlite_db():
    Base.metadata.create_all(bind=engine)
    ensure_db_columns()

print(f'VolCAD using database: {DATABASE_URL}')
Base.metadata.create_all(bind=engine)
if DATABASE_URL.startswith('sqlite'):
    db_path = DATABASE_URL.replace('sqlite:///', '').lstrip('./')
    print(f'SQLite file: {os.path.abspath(db_path)}')
    print('WARNING: SQLite data is stored in a local file. Container redeploys will clear it unless the file is on a persistent volume.')
ensure_db_columns()

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
    customer_id: Optional[int] = None
    customer_slug: Optional[str] = None
    cf_turnstile_response: Optional[str] = None

class SwitchRequest(BaseModel):
    customer_id: Optional[int] = None
    agency_id: Optional[int] = None

class UserMe(BaseModel):
    user_id: int
    role: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    customer_id: Optional[int] = None
    customer_name: Optional[str] = None
    customer_logo: Optional[str] = None
    agency_id: Optional[int] = None
    modules: List[str] = []
    selected_module: Optional[str] = None
    personnel_id: Optional[int] = None
    cross_discipline_agencies: List[int] = []
    preferences: Optional[Any] = None

class UserModuleUpdate(BaseModel):
    module: str

class UserCreate(BaseModel):
    email: str
    password: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: str = 'responder'
    customer_id: Optional[int] = None
    agency_id: Optional[int] = None

class UserOut(BaseModel):
    id: int
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    customer_id: Optional[int] = None
    agency_id: Optional[int] = None
    preferences: Optional[Any] = None
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class UserUpdate(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[str] = None
    customer_id: Optional[int] = None
    agency_id: Optional[int] = None
    is_active: Optional[bool] = None
    preferences: Optional[Any] = None

class CustomerCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    domain: Optional[str] = None
    config: Optional[dict] = None

class CustomerOut(BaseModel):
    id: int
    name: str
    slug: Optional[str] = None
    domain: Optional[str] = None
    config: Optional[dict] = None
    approved: Optional[bool] = None
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    domain: Optional[str] = None
    config: Optional[dict] = None
    approved: Optional[bool] = None

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
    departed_scene_at: Optional[datetime] = None
    arrived_destination_at: Optional[datetime] = None
    transfer_completed_at: Optional[datetime] = None
    cleared_at: Optional[datetime] = None
    pickup_mileage: Optional[float] = None
    dropoff_mileage: Optional[float] = None
    passenger_count: Optional[int] = None
    pickup_address: Optional[str] = None
    dropoff_address: Optional[str] = None
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
    allergies: Optional[str] = None
    medications: Optional[str] = None
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
    allergies: Optional[str] = None
    medications: Optional[str] = None
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
    allergies: Optional[str] = None
    medications: Optional[str] = None
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

class StandingOrderCreate(BaseModel):
    agency_id: int
    patient_name: Optional[str] = None
    pickup_address: Optional[str] = None
    pickup_lat: Optional[float] = None
    pickup_lng: Optional[float] = None
    destination_id: Optional[int] = None
    destination_name: Optional[str] = None
    destination_address: Optional[str] = None
    destination_lat: Optional[float] = None
    destination_lng: Optional[float] = None
    call_type: Optional[str] = 'Routine Transport'
    service_level: Optional[str] = 'BLS'
    mobility_level: Optional[str] = None
    oxygen: Optional[bool] = False
    isolation: Optional[bool] = False
    stretcher: Optional[bool] = False
    wheelchair: Optional[bool] = False
    special_equipment: Optional[dict] = None
    notes: Optional[str] = None
    recurrence: Optional[dict] = None
    active: Optional[bool] = True

class StandingOrderUpdate(BaseModel):
    patient_name: Optional[str] = None
    pickup_address: Optional[str] = None
    pickup_lat: Optional[float] = None
    pickup_lng: Optional[float] = None
    destination_id: Optional[int] = None
    destination_name: Optional[str] = None
    destination_address: Optional[str] = None
    destination_lat: Optional[float] = None
    destination_lng: Optional[float] = None
    call_type: Optional[str] = None
    service_level: Optional[str] = None
    mobility_level: Optional[str] = None
    oxygen: Optional[bool] = None
    isolation: Optional[bool] = None
    stretcher: Optional[bool] = None
    wheelchair: Optional[bool] = None
    special_equipment: Optional[dict] = None
    notes: Optional[str] = None
    recurrence: Optional[dict] = None
    active: Optional[bool] = None

class StandingOrderOut(BaseModel):
    id: int
    agency_id: int
    patient_name: Optional[str] = None
    pickup_address: Optional[str] = None
    pickup_lat: Optional[float] = None
    pickup_lng: Optional[float] = None
    destination_id: Optional[int] = None
    destination_name: Optional[str] = None
    destination_address: Optional[str] = None
    destination_lat: Optional[float] = None
    destination_lng: Optional[float] = None
    call_type: Optional[str] = 'Routine Transport'
    service_level: Optional[str] = 'BLS'
    mobility_level: Optional[str] = None
    oxygen: Optional[bool] = False
    isolation: Optional[bool] = False
    stretcher: Optional[bool] = False
    wheelchair: Optional[bool] = False
    special_equipment: Optional[dict] = None
    notes: Optional[str] = None
    recurrence: Optional[dict] = None
    active: Optional[bool] = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    agency: Optional["AgencyOut"] = None
    destination: Optional[DestinationOut] = None
    class Config:
        from_attributes = True

class StandingOrderGenerate(BaseModel):
    start_date: date
    end_date: date

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

class ScheduledEventDispatch(BaseModel):
    unit_id: int

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
    customer_id: Optional[int] = None
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
    exp = int(time.time()) + SESSION_MAX_AGE
    cid = user.customer_id if user.customer_id is not None else ''
    aid = user.agency_id if user.agency_id is not None else ''
    msg = f'{user.id}:{user.role}:{cid}:{aid}:{exp}'
    sig = hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f'{msg}:{sig}'

def verify_session(token):
    if not token:
        return None
    parts = token.split(':')
    if len(parts) < 5:
        return None
    sig = parts[-1]
    msg = ':'.join(parts[:-1])
    expected = hmac.new(SECRET_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    msg_parts = msg.split(':')
    if len(msg_parts) not in (4, 5):
        return None
    uid = msg_parts[0]
    role = msg_parts[1]
    customer_id = int(msg_parts[2]) if len(msg_parts) > 2 and msg_parts[2].isdigit() else None
    agency_id = int(msg_parts[3]) if len(msg_parts) > 3 and msg_parts[3].isdigit() else None
    exp = msg_parts[-1]
    if time.time() > int(exp):
        return None
    return {'user_id': int(uid), 'role': role, 'customer_id': customer_id, 'agency_id': agency_id, 'exp': int(exp)}

def get_current_user(request: Request):
    session = request.cookies.get('session')
    if INSECURE_DEV and not session:
        db = SessionLocal()
        try:
            u = db.query(User).filter(User.role == 'admin').first()
            if u:
                return {'user_id': u.id, 'role': u.role, 'email': u.email, 'customer_id': u.customer_id, 'agency_id': u.agency_id}
        finally:
            db.close()
        return {'user_id': 0, 'role': 'admin', 'email': 'dev@example.com', 'customer_id': None, 'agency_id': None}
    payload = verify_session(session)
    if not payload:
        raise HTTPException(status_code=401, detail='Not authenticated')
    db = SessionLocal()
    try:
        u = db.query(User).get(payload['user_id'])
        if not u or not u.is_active:
            raise HTTPException(status_code=401, detail='Not authenticated')
        customer_id = payload.get('customer_id') if payload.get('customer_id') is not None else u.customer_id
        agency_id = payload.get('agency_id') if payload.get('agency_id') is not None else u.agency_id
        return {'user_id': u.id, 'role': payload['role'], 'email': u.email, 'customer_id': customer_id, 'agency_id': agency_id, 'first_name': u.first_name, 'last_name': u.last_name, 'exp': payload['exp']}
    finally:
        db.close()

def require_admin(request: Request):
    user = get_current_user(request)
    if user.get('role') not in ('admin', 'superadmin'):
        raise HTTPException(status_code=403, detail='Admin required')
    return user

CALL_TAKER_ROLES = {'call_taker','dispatcher','admin'}
DISPATCHER_ROLES = {'dispatcher','admin'}
FIELD_ROLES = {'mdt','responder','dispatcher','admin'}

def check_role(request: Request, allowed: set):
    user = get_current_user(request)
    if user.get('role') not in allowed and user.get('role') not in ('admin', 'superadmin'):
        raise HTTPException(status_code=403, detail='Role required')
    return user

def require_role(allowed_roles: set):
    def _require(request: Request):
        return check_role(request, allowed_roles)
    return _require

def seed_default_admin():
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            email = os.getenv('ADMIN_EMAIL', 'dustin@dispatchtodiscipleship.net')
            password = os.getenv('ADMIN_PASSWORD', 'Warrior/202601!')
            first_name = os.getenv('ADMIN_FIRST_NAME', 'Dustin')
            last_name = os.getenv('ADMIN_LAST_NAME', '')
            db.add(User(email=email, first_name=first_name, last_name=last_name, customer_id=None, agency_id=None, hashed_password=hash_password(password), role='superadmin', is_active=True))
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
    if request.url.path.startswith('/static/'):
        return _security_headers(await call_next(request))
    if request.url.path in ('/login', '/logout', '/docs', '/openapi.json', '/taip/ingest'):
        return _security_headers(await call_next(request))
    if INSECURE_DEV:
        return _security_headers(await call_next(request))
    # Page-level role guards
    PAGE_ROLES = {
        '/call-entry': CALL_TAKER_ROLES,
        '/console': CALL_TAKER_ROLES,
        '/dispatch': CALL_TAKER_ROLES,
        '/admin': {'admin'},
        '/users': {'admin'},
        '/coverage': DISPATCHER_ROLES,
        '/scheduled': DISPATCHER_ROLES,
        '/scheduled_events': DISPATCHER_ROLES,
        '/mdt': FIELD_ROLES,
        '/mobile-mdt': FIELD_ROLES,
        '/avl': DISPATCHER_ROLES,
        '/hud': DISPATCHER_ROLES,
        '/reports': DISPATCHER_ROLES,
        '/dashboard': DISPATCHER_ROLES,
        '/superadmin': {'superadmin'},
    }
    session = request.cookies.get('session')
    payload = verify_session(session)
    allowed = PAGE_ROLES.get(request.url.path)
    if request.method in ('GET','OPTIONS','HEAD'):
        if allowed:
            if not payload:
                return _security_headers(JSONResponse(status_code=401, content={'detail': 'Not authenticated'}, headers={'Location':'/login'}))
            if payload.get('role') not in allowed and payload.get('role') not in ('admin', 'superadmin'):
                return _security_headers(JSONResponse(status_code=403, content={'detail': 'Role required'}))
        return _security_headers(await call_next(request))
    # Non-GET endpoints
    if not payload:
        return _security_headers(JSONResponse(status_code=401, content={'detail': 'Not authenticated'}))
    if request.url.path == '/config' and request.method in ('POST', 'PUT', 'DELETE') and payload.get('role') not in ('admin', 'superadmin'):
        return _security_headers(JSONResponse(status_code=403, content={'detail': 'Admin required'}))
    return _security_headers(await call_next(request))

def _verify_turnstile(token: Optional[str], remoteip: str) -> bool:
    secret = os.environ.get('TURNSTILE_SECRET')
    if not secret:
        return True
    if not token:
        return False
    try:
        payload = urllib.parse.urlencode({
            'secret': secret,
            'response': token,
            'remoteip': remoteip,
        }).encode()
        req = urllib.request.Request(
            'https://challenges.cloudflare.com/turnstile/v0/siteverify',
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
        return result.get('success') is True
    except Exception:
        return False

@app.post('/login')
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.headers.get('x-forwarded-for') or request.client.host or 'unknown'
    now = time.time()
    attempts = login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < 900]
    if len(attempts) >= 5:
        raise HTTPException(status_code=429, detail='Too many login attempts. Try again later.')

    if not _verify_turnstile(body.cf_turnstile_response, ip):
        raise HTTPException(status_code=403, detail='Turnstile verification failed')

    requested_customer_id = body.customer_id
    if not requested_customer_id and body.customer_slug:
        customer = db.query(Customer).filter(Customer.slug == body.customer_slug).first()
        requested_customer_id = customer.id if customer else None

    q = db.query(User).filter(User.email == body.email)
    if requested_customer_id:
        q = q.filter(User.customer_id == requested_customer_id)
    user = q.first()

    # Superadmin can log in with any customer slug (or none)
    if not user and requested_customer_id:
        user = db.query(User).filter(User.email == body.email, User.role == 'superadmin').first()

    if not user or user.hashed_password != hash_password(body.password):
        attempts.append(now)
        login_attempts[ip] = attempts
        raise HTTPException(status_code=401, detail='Invalid credentials')
    login_attempts.pop(ip, None)

    # Default the user's customer to the requested one if not already set
    if requested_customer_id and user.customer_id is None and user.role != 'superadmin':
        user.customer_id = requested_customer_id
        db.add(user)
        db.commit()

    response.set_cookie(key='session', value=make_session(user), httponly=True, samesite='lax' if INSECURE_DEV else 'strict', secure=not INSECURE_DEV, path='/', max_age=SESSION_MAX_AGE)
    return {'email': user.email, 'role': user.role, 'customer_id': user.customer_id, 'agency_id': user.agency_id}

@app.post('/logout')
def logout(response: Response):
    response.delete_cookie(key='session', path='/')
    return {'ok': True}

def _customer_config_value(db, customer_id, agency_id, category, key):
    # Most specific: agency-level within this customer
    if agency_id and customer_id:
        cfg = db.query(CustomerConfig).filter_by(customer_id=customer_id, agency_id=agency_id, category=category, key=key).first()
        if cfg:
            return cfg.value
    # Customer-level default (agency-agnostic)
    if customer_id:
        cfg = db.query(CustomerConfig).filter_by(customer_id=customer_id, agency_id=None, category=category, key=key).first()
        if cfg:
            return cfg.value
    # Legacy global default (pre-customer data)
    cfg = db.query(CustomerConfig).filter_by(customer_id=None, agency_id=agency_id, category=category, key=key).first()
    return cfg.value if cfg else None

@app.get('/me', response_model=UserMe)
def me(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    u = db.query(User).get(user['user_id'])
    modules = []
    selected = None
    personnel_id = None
    cross_ids = []
    customer_id = user.get('customer_id') if user.get('customer_id') else (u.customer_id if u and u.customer_id else None)
    agency_id = user.get('agency_id') if user.get('agency_id') else (u.agency_id if u and u.agency_id else None)
    customer_name = None
    customer_logo = None
    if customer_id:
        cust = db.query(Customer).get(customer_id)
        if cust:
            customer_name = cust.name
            cfg = cust.config if isinstance(cust.config, dict) else {}
            customer_logo = cfg.get('logo_url') if isinstance(cfg, dict) else None
    if u and customer_id:
        if agency_id:
            modules_val = _customer_config_value(db, customer_id, agency_id, 'modules', 'defaults')
            if modules_val:
                modules = modules_val if isinstance(modules_val, list) else []
            coop_val = _customer_config_value(db, customer_id, agency_id, 'cooperating_agencies', 'defaults')
            if coop_val:
                cross_ids = coop_val if isinstance(coop_val, list) else []
        sel = db.query(CustomerConfig).filter_by(customer_id=customer_id, category='user_module', key=str(u.id)).first()
        if not sel:
            sel = db.query(CustomerConfig).filter_by(customer_id=None, category='user_module', key=str(u.id)).first()
        if sel:
            selected = sel.value
        p = db.query(Personnel).filter(Personnel.user_id == u.id).first()
        if p:
            personnel_id = p.id
    prefs = u.preferences if u and isinstance(u.preferences, dict) else None
    return {'user_id': user['user_id'], 'email': u.email if u else None, 'first_name': u.first_name if u else None, 'last_name': u.last_name if u else None, 'role': user['role'], 'customer_id': customer_id, 'customer_name': customer_name, 'customer_logo': customer_logo, 'agency_id': agency_id, 'modules': modules, 'selected_module': selected, 'personnel_id': personnel_id, 'cross_discipline_agencies': cross_ids, 'preferences': prefs}

@app.put('/me/module')
def set_user_module(body: UserModuleUpdate, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    u = db.query(User).get(user['user_id'])
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    customer_id = u.customer_id or user.get('customer_id')
    if u.agency_id:
        agency_modules = _customer_config_value(db, customer_id, u.agency_id, 'modules', 'defaults') or []
        if not isinstance(agency_modules, list):
            agency_modules = []
        if agency_modules and body.module not in agency_modules and body.module != 'all':
            raise HTTPException(status_code=400, detail='Module not enabled for agency')
        if not agency_modules:
            body.module = 'all'
    sel = db.query(CustomerConfig).filter_by(customer_id=customer_id, category='user_module', key=str(u.id)).first()
    if not sel:
        sel = CustomerConfig(customer_id=customer_id, category='user_module', key=str(u.id), value=body.module)
        db.add(sel)
    else:
        sel.value = body.module
    db.commit()
    return {'selected_module': body.module}

@app.put('/me/preferences')
def set_user_preferences(body: UserUpdate, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    u = db.query(User).get(user['user_id'])
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    if body.preferences is not None:
        u.preferences = body.preferences
    db.commit(); db.refresh(u)
    return u

@app.get('/me/customers')
def me_customers(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request)
    if user.get('role') != 'superadmin':
        raise HTTPException(status_code=403, detail='Superadmin required')
    customers = db.query(Customer).order_by(Customer.name).all()
    agencies = db.query(Agency).order_by(Agency.name).all()
    return {
        'customers': [{'id': c.id, 'name': c.name, 'slug': c.slug} for c in customers],
        'agencies': [{'id': a.id, 'name': a.name, 'customer_id': a.customer_id, 'agency_type': a.agency_type} for a in agencies]
    }

@app.post('/me/switch')
def me_switch(body: SwitchRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = require_admin(request)
    if user.get('role') != 'superadmin':
        raise HTTPException(status_code=403, detail='Superadmin required')
    u = db.query(User).get(user['user_id'])
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    # Validate customer exists if provided
    if body.customer_id is not None:
        customer = db.query(Customer).get(body.customer_id)
        if not customer:
            raise HTTPException(status_code=404, detail='Customer not found')
    # Validate agency exists if provided
    if body.agency_id is not None:
        agency = db.query(Agency).get(body.agency_id)
        if not agency:
            raise HTTPException(status_code=404, detail='Agency not found')
    # Build a session-only user context with selected customer/agency
    session_user = type('SessionUser', (), {
        'id': u.id,
        'role': u.role,
        'customer_id': body.customer_id,
        'agency_id': body.agency_id
    })()
    response.set_cookie(key='session', value=make_session(session_user), httponly=True, samesite='lax' if INSECURE_DEV else 'strict', secure=not INSECURE_DEV, path='/', max_age=86400)
    return {'customer_id': body.customer_id, 'agency_id': body.agency_id}

@app.get('/login')
def login_page():
    return FileResponse('static/login.html')

def _select_home_page(request: Request):
    try:
        user = get_current_user(request)
        if user.get('role') == 'superadmin' and (user.get('customer_id') is None or user.get('agency_id') is None):
            return FileResponse('static/superadmin.html')
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
    return FileResponse('static/dispatch.html', headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0'})

@app.get('/dashboard_v5')
def dashboard_v5():
    return FileResponse('static/dashboard_v5.html', headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0'})

@app.get('/console')
def console():
    return FileResponse('static/dispatch.html', headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0'})

@app.get('/police')
def police_console():
    return FileResponse('static/dispatch.html', headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0'})

@app.get('/fire')
def fire_console():
    return FileResponse('static/dispatch.html', headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0'})

@app.get('/ems')
def ems_console():
    return FileResponse('static/dispatch.html', headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0'})

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

@app.get('/superadmin')
def superadmin():
    return FileResponse('static/superadmin.html')

@app.get('/agency-setup')
def agency_setup():
    return FileResponse('static/agency_setup.html')

@app.get('/agency-build')
def agency_build():
    return FileResponse('static/agency_build.html')

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
def list_config(request: Request, agency_id: Optional[int] = Query(None), category: Optional[str] = Query(None), db: Session = Depends(get_db)):
    user = get_current_user(request)
    customer_id = user.get('customer_id')
    q = db.query(CustomerConfig)
    if customer_id:
        q = q.filter(CustomerConfig.customer_id == customer_id)
    if agency_id:
        q = q.filter(CustomerConfig.agency_id == agency_id)
    if category:
        q = q.filter(CustomerConfig.category == category)
    return q.order_by(CustomerConfig.category, CustomerConfig.key).all()

@app.post('/config', response_model=CustomerConfigOut)
def create_config(body: CustomerConfigCreate, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    customer_id = body.customer_id or current_user.get('customer_id')
    existing = db.query(CustomerConfig).filter_by(customer_id=customer_id, agency_id=body.agency_id, category=body.category, key=body.key).first()
    if existing:
        for k, v in body.model_dump(exclude_unset=True).items():
            setattr(existing, k, v)
        existing.updated_at = tz_now()
        db.commit()
        db.refresh(existing)
        return existing
    cfg = CustomerConfig(customer_id=customer_id, **body.model_dump(exclude={'customer_id'}))
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg

@app.post('/config/seed', response_model=dict)
def seed_config(body: SeedConfigRequest, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    agency = db.query(Agency).get(body.agency_id)
    if not agency:
        raise HTTPException(status_code=404, detail='Agency not found')
    customer_id = agency.customer_id or current_user.get('customer_id')
    templates = {
        'police': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'AK','label':'Dispatched'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'TRP','label':'Transporting'},{'code':'TC','label':'Traffic Control'},{'code':'CT','label':'Citation'},{'code':'ARR','label':'Arrest'},{'code':'BK','label':'Booking'},{'code':'CBY_CALLER','label':'Cancelled by Caller'},{'code':'CBY_OTHER','label':'Cancelled by Other Agency'},{'code':'CBY_DISPATCH','label':'Cancelled by Dispatch'},{'code':'OOS','label':'Out of Service'}],
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
                {'priority':1,'label':'EMERGENT','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Non-Emergent','target_seconds':720},
                {'priority':4,'label':'Scheduled','target_seconds':1200},
                {'priority':5,'label':'Standby','target_seconds':1800}
            ],
            'response_plans': {
                'Traffic Accident': ['patrol','supervisor','rescue'],
                'Theft': ['patrol','detective'],
                'Domestic': ['patrol','supervisor'],
                'Assault': ['patrol','supervisor','k9'],
                'Welfare Check': ['patrol'],
                'Suspicious Person': ['patrol','k9']
            },
            'response_profiles': {
                'Traffic Accident': {'slots':[{'unit_type':'patrol','count':1},{'unit_type':'supervisor','count':1}], 'response_mode':'emergency'},
                'Theft': {'unit_types':['patrol','detective'], 'min_units':1, 'max_units':2, 'response_mode':'routine'},
                'Domestic': {'unit_types':['patrol','supervisor'], 'min_units':2, 'max_units':3, 'response_mode':'emergency'},
                'Assault': {'slots':[{'unit_type':'patrol','count':2},{'unit_type':'supervisor','count':1},{'unit_type':'k9','count':1}], 'response_mode':'emergency'},
                'Welfare Check': {'unit_types':['patrol'], 'min_units':1, 'max_units':1, 'response_mode':'routine'},
                'Suspicious Person': {'unit_types':['patrol','k9'], 'min_units':1, 'max_units':2, 'response_mode':'routine'}
            },
            'dispositions': ['Arrested','Cited','Warned','Referred','Report','No Action','False Alarm']
        },
        'fire': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'AK','label':'Dispatched'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'WATER','label':'Water on Fire'},{'code':'EXT','label':'Extinguished'},{'code':'OVER','label':'Overhaul'},{'code':'TR','label':'Transporting'},{'code':'CBY_CALLER','label':'Cancelled by Caller'},{'code':'CBY_OTHER','label':'Cancelled by Other Agency'},{'code':'CBY_DISPATCH','label':'Cancelled by Dispatch'},{'code':'OOS','label':'Out of Service'}],
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
                {'priority':1,'label':'EMERGENT','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Non-Emergent','target_seconds':720},
                {'priority':4,'label':'Scheduled','target_seconds':1200},
                {'priority':5,'label':'Standby','target_seconds':1800}
            ],
            'response_plans': {
                'Structure Fire': ['engine','ladder','rescue','chief'],
                'Vehicle Fire': ['engine','brush','tanker'],
                'Medical Assist': ['rescue','ambulance'],
                'Alarm': ['engine','ladder'],
                'Vehicle Accident': ['engine','rescue','ambulance'],
                'Brush Fire': ['brush','tanker']
            },
            'response_profiles': {
                'Structure Fire': {'slots':[{'unit_type':'engine','count':2},{'unit_type':'ladder','count':1},{'unit_type':'rescue','count':1},{'unit_type':'chief','count':1}], 'response_mode':'emergency'},
                'Vehicle Fire': {'slots':[{'unit_type':'engine','count':1},{'unit_type':'brush','count':1},{'unit_type':'tanker','count':1}], 'response_mode':'emergency'},
                'Medical Assist': {'slots':[{'unit_type':'rescue','count':1},{'unit_type':'ambulance','count':1}], 'response_mode':'emergency'},
                'Alarm': {'unit_types':['engine','ladder'], 'min_units':1, 'max_units':2, 'response_mode':'routine'},
                'Vehicle Accident': {'slots':[{'unit_type':'engine','count':1},{'unit_type':'rescue','count':1},{'unit_type':'ambulance','count':1}], 'response_mode':'emergency'},
                'Brush Fire': {'slots':[{'unit_type':'brush','count':1},{'unit_type':'tanker','count':1}], 'response_mode':'emergency'}
            },
            'dispositions': ['Extinguished','Controlled','Under Control','False Alarm','No Fire','Cancelled']
        },
        'ems': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'AK','label':'Dispatched'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'TR','label':'Transporting'},{'code':'TH','label':'Transporting to HEMS'},{'code':'AD','label':'Arrived at Destination'},{'code':'CBY_CALLER','label':'Cancelled by Caller'},{'code':'CBY_OTHER','label':'Cancelled by Other Agency'},{'code':'CBY_DISPATCH','label':'Cancelled by Dispatch'},{'code':'NO_TRANSPORT','label':'No Transport'},{'code':'PATIENT_REFUSAL','label':'Patient Refusal'},{'code':'OOS','label':'Out of Service'}],
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
                {'priority':1,'label':'EMERGENT','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Non-Emergent','target_seconds':720},
                {'priority':4,'label':'Scheduled','target_seconds':1200},
                {'priority':5,'label':'Standby','target_seconds':1800}
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
        existing = db.query(CustomerConfig).filter_by(customer_id=customer_id, agency_id=body.agency_id, category=category, key='defaults').first()
        if existing:
            existing.value = value
            existing.updated_at = tz_now()
        else:
            db.add(CustomerConfig(customer_id=customer_id, agency_id=body.agency_id, category=category, key='defaults', value=value))
    db.commit()
    return {'status': 'seeded', 'agency_id': body.agency_id, 'template': body.template}

@app.get('/health')
def health():
    return {'status': 'ok'}

# Pydantic schemas
class AgencyCreate(BaseModel):
    customer_id: Optional[int] = None
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
    customer_id: Optional[int] = None
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
    current_incident_id: Optional[int] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    heading: Optional[float] = None
    speed: Optional[float] = None
    last_seen_at: Optional[datetime] = None
    is_active: Optional[bool] = True
    camera_url: Optional[str] = None
    last_assigned_at: Optional[datetime] = None
    in_service_at: Optional[datetime] = None
    accumulated_call_seconds: Optional[float] = None
    capabilities: Optional[dict] = None
    taip_id: Optional[str] = None
    taip_destination_url: Optional[str] = None
    taip_port: Optional[int] = None
    photo_url: Optional[str] = None

    @computed_field
    @property
    def stale(self) -> bool:
        if not self.last_seen_at:
            return True
        return (tz_now() - _naive_local(self.last_seen_at)).total_seconds() > TAIP_STALE_SECONDS

    @computed_field
    @property
    def offline(self) -> bool:
        if not self.last_seen_at:
            return True
        return (tz_now() - _naive_local(self.last_seen_at)).total_seconds() > TAIP_OFFLINE_SECONDS

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
    camera_url: Optional[str] = None
    photo_url: Optional[str] = None
    is_active: Optional[bool] = None

class PersonnelCreate(BaseModel):
    customer_id: Optional[int] = None
    agency_id: int
    first_name: str
    last_name: str
    radio_id: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    sms_phone: Optional[str] = None
    provider_level: Optional[str] = None
    photo_url: Optional[str] = None
    current_unit_id: Optional[int] = None
    duty_status: str = 'off_duty'
    is_active: Optional[bool] = True

class PersonnelOut(PersonnelCreate):
    id: int
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class PersonnelUpdate(BaseModel):
    customer_id: Optional[int] = None
    agency_id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    radio_id: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    sms_phone: Optional[str] = None
    provider_level: Optional[str] = None
    photo_url: Optional[str] = None
    current_unit_id: Optional[int] = None
    duty_status: Optional[str] = None
    is_active: Optional[bool] = None

class IncidentCreate(BaseModel):
    agency_id: int
    incident_number: Optional[str] = None
    call_number: Optional[str] = None
    call_type: str
    priority: int = 2
    status: Optional[str] = 'open'
    location_text: Optional[str] = None
    extra: Optional[dict] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    caller_name: Optional[str] = None
    callback: Optional[str] = None
    narrative: Optional[str] = None
    call_entry_started_at: Optional[datetime] = None

    @validator('extra', pre=True)
    def _parse_extra(cls, v):
        if isinstance(v, str):
            try: return json.loads(v)
            except Exception: return None
        return v

    @validator('priority')
    def _clamp_priority(cls, v):
        if v is None: return 2
        try: return min(max(int(v), 1), 5)
        except Exception: return 2

class IncidentOut(IncidentCreate):
    id: int
    agency_id: Optional[int] = None
    call_type: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    call_entry_started_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    last_status_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class IncidentUpdate(BaseModel):
    agency_id: Optional[int] = None
    call_type: Optional[str] = None
    priority: Optional[int] = None
    status: Optional[str] = None
    call_status: Optional[str] = None
    location_text: Optional[str] = None
    narrative: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    callback: Optional[str] = None
    caller_name: Optional[str] = None
    extra: Optional[dict] = None
    call_entry_started_at: Optional[datetime] = None

    @validator('priority')
    def _clamp_priority(cls, v):
        if v is None: return None
        try: return min(max(int(v), 1), 5)
        except Exception: return None

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
    destination_name: Optional[str] = None
    mileage: Optional[float] = None
    unit_id: Optional[int] = None
    at: Optional[datetime] = None

class UnitCamera(BaseModel):
    camera_url: str

class UnitShift(BaseModel):
    action: str

class UnitStatus(BaseModel):
    status_code: str
    reason: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    destination_id: Optional[int] = None
    destination_name: Optional[str] = None
    mileage: Optional[float] = None
    passenger_count: Optional[int] = None
    at: Optional[datetime] = None

class PersonnelAssign(BaseModel):
    unit_id: Optional[int] = None

class MDTStart(BaseModel):
    unit_id: int
    personnel_ids: List[int]
    status_code: str
    provider_levels: Optional[dict] = None

class UnitStaff(BaseModel):
    personnel_ids: List[int]
    duty_status: Optional[str] = 'on_duty'
    status_code: Optional[str] = None

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
    r'^>RPV(\d{5})([+-]\d{7})([+-]\d{8})(\d{3})(\d{3})(\d)(\d?)(?:;ID=([A-Z0-9-]{1,20}))?(?:;\*([0-9A-Fa-f]{2}))?<$',
    re.IGNORECASE
)

def _taip_checksum_ok(raw: str, checksum: str, checksum_start: int) -> bool:
    if not checksum:
        return True
    # The checksum is computed over all characters from the leading > through and
    # including the * delimiter that immediately precedes the two checksum digits.
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
    time_text, lat_text, lon_text, speed_text, heading_text, source_text, age_text, taip_id, checksum = m.groups()
    checksum_start = m.start(9) if checksum and m.start(9) is not None else None
    if checksum and checksum_start is not None and not _taip_checksum_ok(text, checksum, checksum_start):
        raise ValueError(f'TAIP checksum failure: expected {checksum}')
    lat = int(lat_text) / 100000.0
    lng = int(lon_text) / 100000.0
    speed = int(speed_text)
    heading = int(heading_text)
    gps_source = int(source_text)
    data_age = int(age_text or 2)
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

def _road_eta_matrix(unit_points, dest_lat, dest_lng):
    """Return list of (duration_seconds, distance_meters) for unit_points using OSRM table API, or (None, None) on failure."""
    if not OSRM_URL or not unit_points or dest_lat is None or dest_lng is None:
        return [(None, None)] * len(unit_points)
    results = []
    chunk_size = 50
    for i in range(0, len(unit_points), chunk_size):
        chunk = unit_points[i:i+chunk_size]
        coords = [f"{dest_lng},{dest_lat}"] + [f"{u[1]},{u[0]}" for u in chunk]
        url = f"{OSRM_URL.rstrip('/')}/table/v1/driving/{';'.join(coords)}?sources=0&destinations=all&annotations=duration,distance"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'D2D-CAD/1.0'})
            with urllib.request.urlopen(req, timeout=OSRM_TIMEOUT) as r:
                data = json.loads(r.read().decode('utf-8'))
                if data.get('code') != 'Ok' or not data.get('durations'):
                    results.extend([(None, None)] * len(chunk))
                    continue
                durations = data['durations'][0]
                distances = data.get('distances', [[]])[0]
                for j in range(1, len(coords)):
                    dur = durations[j] if j < len(durations) else None
                    dist = distances[j] if j < len(distances) else None
                    if dur is None:
                        results.append((None, None))
                    else:
                        results.append((dur, dist))
        except Exception:
            results.extend([(None, None)] * len(chunk))
    return results

def _taip_reported_time(data: dict, received_at: datetime) -> Optional[datetime]:
    if data.get('reported_at'):
        return data['reported_at']
    sod = data.get('gps_seconds_of_day')
    if sod is None:
        return None
    # The GPS time-of-day field is UTC. Build candidates on the UTC day of the
    # received timestamp and the previous UTC day, then keep the one closest to
    # the receive time so the reported wall-clock time is correct across the
    # day boundary and server timezones.
    received_utc = received_at.replace(tzinfo=DEFAULT_TIMEZONE).astimezone(timezone.utc)
    base_utc = received_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [
        base_utc + timedelta(seconds=sod),
        base_utc - timedelta(days=1) + timedelta(seconds=sod),
    ]
    best = None
    best_diff = None
    for c in candidates:
        diff = abs((c - received_utc).total_seconds())
        if best is None or diff < best_diff:
            best = c
            best_diff = diff
    return best.astimezone(DEFAULT_TIMEZONE)

def _taip_out_of_order(unit, reported_at):
    if not unit or not unit.last_seen_at or not reported_at:
        return False
    last = _naive_local(unit.last_seen_at)
    rep = _naive_local(reported_at)
    return rep < last - timedelta(seconds=TAIP_OUT_OF_ORDER_SECONDS)

def _taip_jump_ok(unit, lat, lng, reported_at):
    if not unit or unit.lat is None or unit.lng is None or not reported_at:
        return True
    last = _naive_local(unit.last_seen_at) if unit.last_seen_at else None
    if not last:
        return True
    rep = _naive_local(reported_at)
    distance_m = _haversine_m(unit.lat, unit.lng, lat, lng)
    if distance_m is None or distance_m <= 0:
        return True
    delta_s = (rep - last).total_seconds()
    if delta_s <= 0:
        # Cannot trust time delta; allow if within reasonable distance
        return distance_m <= TAIP_MAX_JUMP_MPS * 10
    speed_mps = distance_m / delta_s
    return speed_mps <= TAIP_MAX_JUMP_MPS

def _taip_stale_state(last_seen_at: Optional[datetime]):
    if not last_seen_at:
        return True, True
    age = (tz_now() - _naive_local(last_seen_at)).total_seconds()
    return age > TAIP_STALE_SECONDS, age > TAIP_OFFLINE_SECONDS

# Active unit status codes keep a unit assigned to an incident.
_CALL_ACTIVE_STATUSES = {'AK','ER','OS','AP','TR','TRP','ED','TH','AD','DECEASED','WATER','EXT','OVER','TC','ARR','CT','BK','PA','INVEST','REPORT','SEARCH','WORK_TRAFFIC','FIRE_ATTACK','EXTRICATION','NO_FIRE'}
_ASSIGNABLE_STATUSES = {'AQ','AFR','POSTING','STAGED','AT_STATION','AVAILABLE_ON_RADIO','IN_SERVICE','ON_DUTY'}
_OUT_OF_SERVICE_STATUSES = {'OOS','LUN','MAINT','OFF_DUTY','MEAL','off_duty'}

# Map a unit status code to a high-level assignment phase.
def map_status(code: str) -> str:
    on_scene = {'OS','AP','AD','WATER','EXT','OVER','TC','ARR','CT','BK','DECEASED','PA','INVEST','REPORT','SEARCH','WORK_TRAFFIC','FIRE_ATTACK','EXTRICATION','NO_FIRE'}
    if code in ('AK','dispatched'):
        return 'assigned'
    if code in ('ER','en_route'):
        return 'en_route'
    if code in on_scene:
        return 'on_scene'
    if code in ('TR','TRP','TH','ED','transport'):
        return 'transport'
    return 'clear'

def refresh_incident_status(db: Session, incident):
    """Close the call when no units remain assigned to it."""
    if not incident or incident.status == 'closed':
        return
    db.flush()
    units = db.query(Unit).filter(Unit.current_incident_id == incident.id).all()
    if not units:
        was_dispatched = db.query(IncidentUnit).filter_by(incident_id=incident.id).first() is not None
        if was_dispatched:
            incident.status = 'closed'
            incident.closed_at = tz_now()
            for iu in db.query(IncidentUnit).filter_by(incident_id=incident.id, cleared_at=None).all():
                iu.cleared_at = tz_now()
                iu.assignment_status = 'cleared'
            _ensure_incident_report(db, incident, {'user_id': None})
            _log_event(db, 'incident_closed', 'incident', incident.id, data={'reason': 'all_units_available'}, agency_id=incident.agency_id)

def _incident_milestone_events(db, incident_id):
    rows = db.query(StatusEvent).filter(StatusEvent.incident_id == incident_id).order_by(StatusEvent.created_at).all()
    first = {}
    on_scene = {'OS','on_scene','AP','WATER','EXT','OVER','DECEASED','PA','INVEST','REPORT','SEARCH','WORK_TRAFFIC','FIRE_ATTACK','EXTRICATION','NO_FIRE'}
    for ev in rows:
        code = ev.status_code
        if code in ('AK','dispatched'):
            first.setdefault('AK', ev.created_at)
        if code in ('ER','en_route'):
            first.setdefault('ER', ev.created_at)
        if code in on_scene:
            first.setdefault('OS', ev.created_at)
        if code in ('TR','transport','ED'):
            first.setdefault('TR', ev.created_at)
    return first

def _response_goals(db, incident):
    agency = db.query(Agency).get(incident.agency_id)
    customer_id = incident.customer_id or (agency.customer_id if agency else None)
    goals = _customer_config_value(db, customer_id, agency.id if agency else None, 'response_goals', 'defaults') or {}
    if not isinstance(goals, dict):
        goals = {}
    if 'on_scene_seconds' not in goals:
        goals['on_scene_seconds'] = goals.get('on_scene_city_seconds') or goals.get('on_scene_county_seconds') or 480
    defaults = {'dispatch_seconds':60,'en_route_seconds':120,'on_scene_seconds':480}
    defaults.update(goals)
    return defaults

def _incident_timers(db, incident):
    start = incident.call_entry_started_at or incident.created_at
    milestones = _incident_milestone_events(db, incident.id)
    goals = _response_goals(db, incident)
    now = tz_now()
    phases = []
    alerts = []
    for key, label, code in [('dispatch','Dispatch','AK'),('en_route','En Route','ER'),('on_scene','On Scene','OS'),('transport','Transport','TR')]:
        target = goals.get(f'{key}_seconds', 0) or 0
        actual = milestones.get(code)
        if actual and start:
            elapsed = (actual - start).total_seconds()
            status = 'met' if (target == 0 or elapsed <= target) else 'late'
        elif start:
            elapsed = (now - start).total_seconds()
            status = 'pending' if (target == 0 or elapsed <= target) else 'alert'
        else:
            elapsed = 0
            status = 'pending'
        if status == 'alert':
            alerts.append(f"{label} target missed ({int(elapsed)}s / {target}s)")
        phases.append({'phase':key,'label':label,'target_seconds':target,'actual_seconds':int(elapsed) if elapsed else 0,'actual_at':actual.isoformat() if actual else None,'status':status})
    last_status_event = db.query(StatusEvent).filter(StatusEvent.incident_id == incident.id).order_by(StatusEvent.created_at.desc()).first()
    last_status = None
    if last_status_event:
        last_status = {
            'status_code': last_status_event.status_code,
            'status_label': _status_label(last_status_event.status_code),
            'at': last_status_event.created_at.isoformat() if last_status_event.created_at else None,
            'elapsed_seconds': int((now - last_status_event.created_at).total_seconds()) if last_status_event.created_at else 0,
            'reason': last_status_event.reason
        }
    else:
        last_status = {'status_code': None, 'status_label': None, 'at': None, 'elapsed_seconds': 0, 'reason': None}
    return {'start_at':start.isoformat() if start else None,'phases':phases,'alerts':alerts,'has_alert':len(alerts)>0,'last_status':last_status}

def _active_incident_alerts(db, agency_id=None):
    q = db.query(Incident).filter(Incident.status != 'closed')
    if agency_id:
        q = q.filter(Incident.agency_id == agency_id)
    result = []
    for incident in q.all():
        timers = _incident_timers(db, incident)
        if timers['has_alert']:
            result.append({'incident_id':incident.id,'call_number':incident.call_number,'call_type':incident.call_type,'priority':incident.priority,'alerts':timers['alerts'],'timers':timers})
    return result

# Endpoints

GEOCODER_TIMEOUT = 5
OSRM_URL = os.getenv('OSRM_URL', 'https://router.project-osrm.org')
OSRM_TIMEOUT = int(os.getenv('OSRM_TIMEOUT', 3))

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
    try:
        g = geojson
        if isinstance(g, str):
            g = json.loads(g)
        if not isinstance(g, dict):
            return False
        if 'geometry' in g:
            g = g['geometry']
            if not isinstance(g, dict):
                return False
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
    except Exception as e:
        print('point_in_geojson error:', e)
    return False

def _find_zone_for_point(db, lat, lng, agency_id=None):
    try:
        q = db.query(PostZone).filter(PostZone.is_active == True)
        if agency_id:
            q = q.filter(or_(PostZone.agency_id == agency_id, PostZone.agency_id == None))
        for z in q.all():
            try:
                if _point_in_geojson(lat, lng, z.geojson):
                    return z
            except Exception as e:
                print(f'Zone {z.id} geometry error:', e)
                continue
        return None
    except Exception as e:
        print('find_zone_for_point error:', e)
        return None

def _resolve_destination_id(db, body, agency_id):
    dest_id = body.destination_id
    if not dest_id and body.destination_name:
        d = Destination(agency_id=agency_id, name=body.destination_name.strip(), category='other', is_active=True)
        db.add(d); db.flush(); db.refresh(d)
        dest_id = d.id
    return dest_id

def _cross_streets_around(db, lat, lng):
    # Placeholder: cross streets require road network data.
    return None

def _load_extra(extra):
    if extra is None:
        return {}
    if isinstance(extra, dict):
        return dict(extra)
    if isinstance(extra, str):
        try:
            return json.loads(extra) or {}
        except Exception:
            return {}
    return {}

def _validate_incident_location(db, incident, force=False):
    loc = db.query(IncidentLocation).filter_by(incident_id=incident.id).first()
    if not loc:
        loc = IncidentLocation(incident_id=incident.id, raw_address=incident.location_text)
        db.add(loc)
    if not force and loc.verification_status == 'verified' and loc.latitude is not None and loc.longitude is not None:
        return loc
    extra = _load_extra(incident.extra)
    if incident.lat is not None and incident.lng is not None and not force:
        # Call taker verified coordinates. Use them rather than re-geocoding the address.
        loc.latitude = incident.lat
        loc.longitude = incident.lng
        loc.verification_status = 'verified'
        loc.geocoded_at = tz_now()
        extra['verification_status'] = 'verified'
    else:
        g = _geocode_structured(incident.location_text) if incident.location_text else None
        if g:
            loc.standardized_address = g.get('display_name')
            a = g.get('address') or {}
            loc.city = a.get('city')
            loc.state = a.get('state')
            loc.postal_code = a.get('postcode')
            loc.latitude = g['lat']
            loc.longitude = g['lng']
            loc.geocoded_at = tz_now()
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
    # Geocode using the address components first; the agency name often confuses Nominatim.
    query = _build_geo_query([agency.address, agency.city, agency.state, agency.zip_code])
    lat, lng = (None, None)
    if query:
        lat, lng = geocode_address(query)
    if lat is None and agency.name:
        query = _build_geo_query([agency.name, agency.city, agency.state])
        if query:
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

@app.get('/version')
def version():
    return APP_VERSION

@app.get('/customers', response_model=List[CustomerOut])
def list_customers(current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    q = db.query(Customer)
    if current_user.get('role') != 'superadmin' and current_user.get('customer_id'):
        q = q.filter(Customer.id == current_user.get('customer_id'))
    return q.order_by(Customer.name).all()

@app.post('/customers', response_model=CustomerOut)
def create_customer(body: CustomerCreate, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    if not body.slug:
        body.slug = re.sub(r'[^a-z0-9]+', '-', body.name.lower()).strip('-')[:100]
    customer = Customer(**body.model_dump())
    db.add(customer); db.commit(); db.refresh(customer)
    return customer

@app.get('/customers/{customer_id}', response_model=CustomerOut)
def get_customer(customer_id: int, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.query(Customer).get(customer_id)
    if not c:
        raise HTTPException(status_code=404, detail='Customer not found')
    return c

@app.put('/customers/{customer_id}', response_model=CustomerOut)
def update_customer(customer_id: int, body: CustomerUpdate, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.query(Customer).get(customer_id)
    if not c:
        raise HTTPException(status_code=404, detail='Customer not found')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit(); db.refresh(c)
    return c

@app.delete('/customers/{customer_id}')
def delete_customer(customer_id: int, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    c = db.query(Customer).get(customer_id)
    if not c:
        raise HTTPException(status_code=404, detail='Customer not found')
    db.delete(c); db.commit()
    return {'deleted': customer_id}

@app.post('/agencies', response_model=AgencyOut)
def create_agency(body: AgencyCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    agency = Agency(**body.model_dump())
    if agency.customer_id is None:
        # Agencies are customers. Each agency gets its own customer tenant.
        slug = (agency.domain or re.sub(r'[^a-z0-9]+', '-', agency.name.lower()).strip('-'))[:100]
        if not slug:
            slug = 'agency-' + str(int(time.time()))
        if db.query(Customer).filter(Customer.slug == slug).first():
            slug = slug + '-' + str(int(time.time() % 10000))
        customer = Customer(name=agency.name, slug=slug, domain=agency.domain, config={}, approved=agency.approved)
        db.add(customer)
        db.commit()
        db.refresh(customer)
        agency.customer_id = customer.id
    fill_agency_lat_lng(agency)
    db.add(agency)
    db.commit()
    db.refresh(agency)
    return agency

@app.get('/agencies', response_model=List[AgencyOut])
def list_agencies(request: Request, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    user = get_current_user(request)
    customer_id = user.get('customer_id')
    q = db.query(Agency)
    if customer_id:
        q = q.filter(Agency.customer_id == customer_id)
    return q.offset(skip).limit(limit).all()

@app.get('/agencies/{agency_id}', response_model=AgencyOut)
def get_agency(agency_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.query(Agency).get(agency_id)
    if not a:
        raise HTTPException(status_code=404, detail='Agency not found')
    return a

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

def _shared_owner_customer_ids(db, customer_id):
    if not customer_id:
        return []
    return [r.customer_id for r in db.query(CustomerShare.customer_id).filter(
        CustomerShare.shared_customer_id == customer_id,
        CustomerShare.share_avl == True
    ).all()]

@app.post('/units', response_model=UnitOut)
def create_unit(body: UnitCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    agency = db.query(Agency).get(body.agency_id)
    customer_id = agency.customer_id if agency else current_user.get('customer_id')
    unit = Unit(**body.model_dump(), customer_id=customer_id)
    db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit

@app.get('/units', response_model=List[UnitOut])
def list_units(request: Request, agency_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    user = get_current_user(request)
    customer_id = user.get('customer_id')
    q = db.query(Unit)
    if customer_id:
        owner_ids = _shared_owner_customer_ids(db, customer_id)
        q = q.filter(or_(Unit.customer_id == customer_id, Unit.customer_id.in_(owner_ids)))
    if agency_id:
        q = q.filter(Unit.agency_id == agency_id)
    return q.all()

@app.get('/units/{unit_id}', response_model=UnitOut)
def get_unit(request: Request, unit_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    customer_id = user.get('customer_id')
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    if customer_id and unit.customer_id != customer_id:
        if unit.customer_id not in _shared_owner_customer_ids(db, customer_id):
            raise HTTPException(status_code=404, detail='Unit not found')
    return unit

@app.put('/units/{unit_id}', response_model=UnitOut)
def update_unit(unit_id: int, body: UnitUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    customer_id = current_user.get('customer_id')
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    if customer_id and unit.customer_id is not None and unit.customer_id != customer_id:
        raise HTTPException(status_code=403, detail='Cross-customer unit modification is not allowed')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(unit, k, v)
    if body.agency_id:
        agency = db.query(Agency).get(body.agency_id)
        if agency:
            unit.customer_id = agency.customer_id
    db.commit(); db.refresh(unit)
    return unit

@app.delete('/units/{unit_id}')
def delete_unit(unit_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    customer_id = current_user.get('customer_id')
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    if customer_id and unit.customer_id is not None and unit.customer_id != customer_id:
        raise HTTPException(status_code=403, detail='Cross-customer unit deletion is not allowed')
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
    msg = DispatchMessage(**body.model_dump(), method=body.channel, sent_at=tz_now())
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
def list_destinations(agency_id: Optional[int] = Query(None), category: Optional[str] = Query(None), include_inactive: Optional[bool] = Query(False), db: Session = Depends(get_db)):
    q = db.query(Destination)
    if not include_inactive:
        q = q.filter(Destination.is_active == True)
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
    ts = body.timestamp or tz_now()
    if status == 'en_route' and not leg.en_route_at:
        leg.en_route_at = ts
    elif status == 'arrived' and not leg.arrived_at:
        leg.arrived_at = ts
    elif status == 'transfer_completed' and not leg.transfer_completed_at:
        leg.transfer_completed_at = ts
    elif status == 'cleared' and not leg.cleared_at:
        leg.cleared_at = ts
    if body.mileage is not None:
        if status == 'arrived' and leg.pickup_mileage is None:
            leg.pickup_mileage = body.mileage
        elif status == 'arrived_destination' and leg.dropoff_mileage is None:
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
    data = body.model_dump()
    if not data.get('customer_id'):
        agency = db.query(Agency).get(data.get('agency_id'))
        data['customer_id'] = agency.customer_id if agency else None
    p = Personnel(**data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p

@app.get('/personnel', response_model=List[PersonnelOut])
def list_personnel(request: Request, agency_id: Optional[int] = Query(None), unit_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    user = get_current_user(request)
    customer_id = user.get('customer_id')
    q = db.query(Personnel)
    if customer_id:
        q = q.filter(Personnel.customer_id == customer_id)
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
    if current_user.get('customer_id') and p.customer_id != current_user.get('customer_id'):
        raise HTTPException(status_code=404, detail='No personnel record linked to this user')
    return p

@app.put('/personnel/{personnel_id}', response_model=PersonnelOut)
def update_personnel(personnel_id: int, body: PersonnelUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.query(Personnel).get(personnel_id)
    if not p:
        raise HTTPException(status_code=404, detail='Personnel not found')
    customer_id = current_user.get('customer_id')
    if customer_id and p.customer_id is not None and p.customer_id != customer_id:
        raise HTTPException(status_code=403, detail='Not authorized')
    # allow self or admin/dispatch
    u = db.query(User).get(current_user['user_id'])
    is_admin = u and u.role in ('admin','super_admin','superadmin')
    is_self = p and p.user_id == current_user['user_id']
    if not is_admin and not is_self:
        raise HTTPException(status_code=403, detail='Not authorized')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    if body.agency_id:
        agency = db.query(Agency).get(body.agency_id)
        if agency:
            p.customer_id = agency.customer_id
    db.commit(); db.refresh(p)
    return p

@app.delete('/personnel/{personnel_id}')
def delete_personnel(personnel_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    customer_id = current_user.get('customer_id')
    p = db.query(Personnel).get(personnel_id)
    if not p:
        raise HTTPException(status_code=404, detail='Personnel not found')
    u = db.query(User).get(current_user['user_id'])
    if not (u and u.role in ('admin','super_admin','superadmin')):
        raise HTTPException(status_code=403, detail='Not authorized')
    db.delete(p); db.commit()
    return {'deleted': personnel_id}

@app.post('/incidents', response_model=IncidentOut)
def create_incident(request: Request, body: IncidentCreate, db: Session = Depends(get_db)):
    check_role(request, CALL_TAKER_ROLES)
    data = body.model_dump()
    agency = db.query(Agency).get(data.get('agency_id'))
    data['customer_id'] = agency.customer_id if agency else None
    if not data.get('incident_number'):
        count = db.query(Incident).filter(Incident.customer_id == data['customer_id'], Incident.agency_id == data['agency_id']).count()
        data['incident_number'] = f"{data['agency_id']}-{count + 1:05d}"
    if not data.get('call_number'):
        data['call_number'] = data['incident_number']
    user = get_current_user(request)
    incident = Incident(**data)
    db.add(incident)
    db.flush()
    _set_incident_destination_from_extra(db, incident)
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
def list_incidents(request: Request, agency_id: Optional[int] = Query(None), status: Optional[str] = Query(None), call_type: Optional[str] = Query(None), search: Optional[str] = Query(None), from_date: Optional[datetime] = Query(None), to_date: Optional[datetime] = Query(None), archived: Optional[bool] = Query(None), start_date: Optional[date] = Query(None), end_date: Optional[date] = Query(None), db: Session = Depends(get_db)):
    user = get_current_user(request)
    customer_id = user.get('customer_id')
    q = db.query(Incident)
    if customer_id:
        q = q.filter(Incident.customer_id == customer_id)
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
    if status == 'closed':
        today = tz_now().date()
        today_start = datetime.combine(today, dt_time.min)
        if start_date and end_date:
            q = q.filter(Incident.closed_at >= datetime.combine(start_date, dt_time.min), Incident.closed_at <= datetime.combine(end_date, dt_time.max))
        elif archived is True:
            q = q.filter(Incident.closed_at < today_start)
        elif archived is False:
            q = q.filter(Incident.closed_at >= today_start)
    return q.order_by(Incident.created_at.desc()).all()

@app.get('/incidents/{incident_id}', response_model=IncidentOut)
def get_incident(request: Request, incident_id: int, db: Session = Depends(get_db)):
    user = get_current_user(request)
    customer_id = user.get('customer_id')
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    if customer_id and incident.customer_id is not None and incident.customer_id != customer_id:
        raise HTTPException(status_code=404, detail='Incident not found')
    return incident

def _get_response_profile(db, customer_id, agency_id, call_type):
    profile = _customer_config_value(db, customer_id, agency_id, 'response_profiles', 'defaults')
    if profile is None:
        profile = _customer_config_value(db, customer_id, agency_id, 'response_plans', 'defaults')
    profiles = profile or {}
    return profiles.get(call_type) if isinstance(profiles, dict) else None

def _profile_slots(profile):
    slots = []
    if not profile:
        return slots
    explicit = profile.get('slots') or []
    if explicit:
        return explicit
    unit_types = profile.get('unit_types') or []
    if unit_types:
        count = profile.get('min_units') or profile.get('max_units') or 1
        for t in unit_types:
            slots.append({'unit_type': t, 'count': 1})
        extra = count - len(unit_types)
        if extra > 0 and unit_types:
            slots.append({'unit_type': unit_types[0], 'count': extra})
    return slots

def _incident_resource_status(db, incident):
    agency = db.query(Agency).get(incident.agency_id)
    customer_id = incident.customer_id or (agency.customer_id if agency else None)
    profile = _get_response_profile(db, customer_id, incident.agency_id, incident.call_type)
    slots = _profile_slots(profile)
    assigned = db.query(IncidentUnit, Unit).join(Unit, IncidentUnit.unit_id == Unit.id).filter(IncidentUnit.incident_id == incident.id).all()
    assigned_by_type = {}
    for iu, u in assigned:
        assigned_by_type[u.unit_type] = assigned_by_type.get(u.unit_type, 0) + 1
    if slots:
        status = []
        for s in slots:
            t = s.get('unit_type')
            c = s.get('count', 1)
            a = assigned_by_type.get(t, 0)
            status.append({'unit_type': t, 'required': c, 'assigned': a, 'needed': max(0, c - a), 'filled': a >= c})
        return {
            'call_type': incident.call_type,
            'profiled': True,
            'slots': status,
            'total_required': sum(s['required'] for s in status),
            'total_assigned': sum(assigned_by_type.values()),
            'total_needed': sum(s['needed'] for s in status)
        }
    total_assigned = sum(assigned_by_type.values())
    return {
        'call_type': incident.call_type,
        'profiled': False,
        'slots': [],
        'total_required': profile.get('min_units') if profile else None,
        'total_assigned': total_assigned,
        'total_needed': (profile.get('min_units') - total_assigned) if profile and profile.get('min_units') else None
    }

@app.get('/incidents/{incident_id}/resource-status')
def get_resource_status(incident_id: int, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    return _incident_resource_status(db, incident)

@app.get('/incidents/{incident_id}/recommend')
def recommend_units(incident_id: int, limit: int = Query(10), db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    agency = db.query(Agency).get(incident.agency_id)
    customer_id = incident.customer_id or (agency.customer_id if agency else None)
    profile = _get_response_profile(db, customer_id, incident.agency_id, incident.call_type)
    plan = _customer_config_value(db, customer_id, incident.agency_id, 'response_plans', 'defaults') or {}
    slots = _profile_slots(profile)
    if slots:
        recommended_types = [s.get('unit_type') for s in slots]
    else:
        recommended_types = plan.get(incident.call_type, []) if isinstance(plan, dict) else []

    # Service level from profile or fallback to keyword heuristics
    call_lower = (incident.call_type or '').lower()
    agency_type = (profile.get('agency_type') if profile and profile.get('agency_type') else None) or (agency.agency_type if agency else 'ems')
    required_level = None
    if agency_type == 'ems':
        if profile and profile.get('service_level'):
            required_level = profile.get('service_level').upper()
        else:
            als_keywords = ['cardiac','chest pain','overdose','respiratory','allergic','stroke','behavioral','choking','seizure','als','trauma','unconscious']
            required_level = 'ALS' if any(k in call_lower for k in als_keywords) else 'BLS'

    required_equipment = set(profile.get('equipment') or []) if profile else set()
    response_mode = profile.get('response_mode') if profile else None
    emergency = (response_mode == 'emergency') or (not response_mode and incident.priority in (1,2))
    speed_mph = 35.0 if emergency else 25.0
    now = tz_now()

    # Resource slot fill tracking
    assigned_units = db.query(IncidentUnit, Unit).join(Unit, IncidentUnit.unit_id == Unit.id).filter(IncidentUnit.incident_id == incident.id, IncidentUnit.assignment_status != 'cleared').all()
    assigned_by_type = {}
    total_assigned = 0
    for iu, unit in assigned_units:
        assigned_by_type[unit.unit_type] = assigned_by_type.get(unit.unit_type, 0) + 1
        total_assigned += 1
    slot_needed = {}
    if slots:
        for s in slots:
            t = s.get('unit_type')
            c = s.get('count', 1)
            a = assigned_by_type.get(t, 0)
            slot_needed[t] = max(0, c - a)
    max_units = profile.get('max_units') if profile and not slots else None

    # Pre-compute active posting counts per zone for coverage-loss penalty
    from sqlalchemy import func
    posting_counts = {}
    for zone_id, cnt in db.query(UnitPosting.post_zone_id, func.count(UnitPosting.id)).filter(UnitPosting.is_current == True).group_by(UnitPosting.post_zone_id).all():
        posting_counts[zone_id] = cnt
    zones = {z.id: z for z in db.query(PostZone).all() if z.minimum_units}
    units = db.query(Unit).filter(Unit.is_active == True).all()
    unit_points = [(u.lat, u.lng) for u in units if u.lat is not None and u.lng is not None]
    road_results = _road_eta_matrix(unit_points, incident.lat, incident.lng)
    road_map = {}
    road_idx = 0
    for u in units:
        if u.lat is not None and u.lng is not None and road_idx < len(road_results):
            road_map[u.id] = road_results[road_idx]
            road_idx += 1
        else:
            road_map[u.id] = (None, None)
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
            reasons.append('no GPS')
        elif (_naive_local(now) - _naive_local(u.last_seen_at)).total_seconds() > TAIP_OFFLINE_SECONDS:
            s -= 300; reasons.append('GPS offline')
        elif (_naive_local(now) - _naive_local(u.last_seen_at)).total_seconds() > TAIP_STALE_SECONDS:
            age = (_naive_local(now) - _naive_local(u.last_seen_at)).total_seconds()
            s -= age * 0.05; reasons.append('GPS stale')

        # Distance and ETA (road-based with OSRM, fallback to Haversine)
        dist_miles = None
        eta_seconds = None
        road_dur, road_dist = road_map.get(u.id, (None, None))
        if incident.lat is not None and incident.lng is not None and u.lat is not None and u.lng is not None:
            if road_dist is not None:
                dist_miles = road_dist / 1609.34
            else:
                dist_m = _haversine_m(u.lat, u.lng, incident.lat, incident.lng)
                if dist_m is not None:
                    dist_miles = dist_m / 1609.34
            if road_dur is not None:
                eta_seconds = road_dur
                s -= eta_seconds * 0.03
                if road_dist is None:
                    dist_miles = max(dist_miles or 0, (eta_seconds / 3600) * speed_mph)
                reasons.append(f"{dist_miles:.1f} mi · {eta_seconds/60:.0f} min (road)")
            elif dist_miles is not None:
                eta_seconds = (dist_miles / speed_mph) * 3600
                s -= eta_seconds * 0.05
                reasons.append(f"{dist_miles:.1f} mi · {eta_seconds/60:.0f} min")
        if dist_miles is None and 'no GPS' not in reasons:
            s -= 500; reasons.append('no GPS')

        # Service level / capability
        unit_level = (caps.get('service_level') or 'BLS').upper() if agency_type == 'ems' else (u.unit_type or '')
        if agency_type == 'ems' and required_level:
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

        # Equipment requirement from response profile
        if required_equipment:
            unit_equipment = set(caps.get('equipment') or [])
            missing = required_equipment - unit_equipment
            if missing:
                eligible = False; reasons.append(f"missing equipment: {', '.join(missing)}")
            else:
                s += 50; reasons.append('equipment match')

        # Agency preference
        if u.agency_id == incident.agency_id:
            s += 100; reasons.append('same agency')

        # Resource slot / run-card match
        if recommended_types:
            if (u.unit_type or '') in recommended_types:
                if slots and slot_needed.get(u.unit_type, 0) <= 0:
                    eligible = False; reasons.append(f"{u.unit_type} slot already filled")
                elif max_units and total_assigned >= max_units:
                    eligible = False; reasons.append('max units reached')
                else:
                    s += 200; reasons.append('resource slot match')
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
            'first_transport': first_event(['TR','TH','ED','TRP']),
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

def _unit_status_time(status_events, codes):
    for e in status_events:
        if e.status_code in codes:
            return e.created_at.isoformat() if e.created_at else None
    return None

def _status_label(code):
    return {
        'AQ':'Available','AFR':'Available for Response','OS':'On Scene','ER':'En Route','TR':'Transport','TRP':'Transport','ED':'En Route to Destination','TH':'Transporting to HEMS','AD':'Arrived at Destination',
        'DEL':'Delivered','NPF':'No Patient Found','NO_TRANSPORT':'No Transport','PATIENT_REFUSAL':'Patient Refusal','CAN':'Cancelled','LUN':'Lunch','MAINT':'Out for Maintenance','OOS':'Out of Service','OFF_DUTY':'Off Duty','off_duty':'Off Duty','IN_SERVICE':'In Service','ON_DUTY':'On Duty',
        'AK':'Dispatched','dispatched':'Dispatched','open':'Open','closed':'Closed','en_route':'En Route','on_scene':'On Scene',
        'TC':'Traffic Control','CT':'Citation','ARR':'Arrest','BK':'Booking','WATER':'Water on Fire','EXT':'Extinguished','OVER':'Overhaul'
    }.get(code, code or '')

def _redact_summary(summary, profile='full'):
    if profile == 'full' or not summary:
        return summary
    out = json.loads(json.dumps(summary))
    if profile in ('operational','billing','public'):
        ci = out.get('call_info') or {}
        if profile in ('operational','public'):
            ci.pop('caller_name', None)
            ci.pop('callback', None)
        if profile in ('billing','public'):
            ci.pop('narrative', None)
        out['call_info'] = ci
    if profile in ('operational','public'):
        for u in out.get('units', []):
            u['crew'] = []
    if profile == 'public':
        out['timeline'] = []
        loc = out.get('location') or {}
        loc.pop('latitude', None)
        loc.pop('longitude', None)
        loc.pop('original_address', None)
        out['location'] = loc
    if profile == 'billing':
        out['timeline'] = []
    return out

def _source_name(user, default='System'):
    if not user:
        return default
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or user.email or default

def _fmt_dur(seconds):
    if seconds is None or seconds < 0:
        return None
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f'{h}:{m:02d}:{s:02d}'

def _build_after_call_summary(db, incident):
    agency = db.query(Agency).get(incident.agency_id) if incident.agency_id else None
    loc = db.query(IncidentLocation).filter_by(incident_id=incident.id).first()
    all_status_events = db.query(StatusEvent).filter_by(incident_id=incident.id).order_by(StatusEvent.created_at.asc()).all()
    call_logs = db.query(CallLog).filter_by(incident_id=incident.id).order_by(CallLog.timestamp.asc()).all()
    messages = db.query(DispatchMessage).filter_by(incident_id=incident.id).order_by(DispatchMessage.sent_at.asc()).all()
    events = db.query(Event).filter_by(entity_type='incident', entity_id=incident.id).order_by(Event.timestamp.asc()).all()

    timeline = []
    for e in all_status_events:
        unit = db.query(Unit).get(e.unit_id)
        timeline.append({'at': e.created_at.isoformat() if e.created_at else None, 'type': 'status', 'unit': unit.call_sign if unit else None, 'event': _status_label(e.status_code), 'notes': e.reason, 'source': 'MDT' if unit else 'CAD'})
    for l in call_logs:
        user = db.query(User).get(l.user_id) if l.user_id else None
        timeline.append({'at': l.timestamp.isoformat() if l.timestamp else None, 'type': 'log', 'unit': None, 'event': l.log_type, 'notes': l.message, 'source': _source_name(user, 'CAD')})
    for m in messages:
        unit = db.query(Unit).get(m.unit_id) if m.unit_id else None
        src = m.channel.upper() if m.channel and m.channel.lower() != 'mdt' else 'MDT'
        if unit and m.channel and m.channel.lower() == 'mdt':
            src = f"MDT ({unit.call_sign})"
        timeline.append({'at': m.sent_at.isoformat() if m.sent_at else None, 'type': 'message', 'unit': unit.call_sign if unit else None, 'event': m.method, 'notes': m.message_text, 'source': src})
    for e in events:
        user = db.query(User).get(e.user_id) if e.user_id else None
        timeline.append({'at': e.timestamp.isoformat() if e.timestamp else None, 'type': 'event', 'unit': None, 'event': e.event_type, 'notes': json.dumps(e.data) if e.data else None, 'source': _source_name(user, 'System')})
    timeline.sort(key=lambda x: x['at'] or '')

    units_summary = []
    incident_units = db.query(IncidentUnit).filter_by(incident_id=incident.id).all()
    for iu in incident_units:
        unit = iu.unit
        if not unit:
            continue
        unit_status_events = [e for e in all_status_events if e.unit_id == unit.id]
        assigned_at = iu.assigned_at.isoformat() if iu.assigned_at else None
        dispatched_at = _unit_status_time(unit_status_events, ['AK','dispatched'])
        en_route_at = _unit_status_time(unit_status_events, ['ER','en_route'])
        arrived_at = _unit_status_time(unit_status_events, ['OS','on_scene','AP','WATER','EXT','OVER'])
        transport_at = _unit_status_time(unit_status_events, ['TR','ED','TRP','TH'])
        dest_arrived_at = _unit_status_time(unit_status_events, ['AD','DEL','delivered','at_destination'])
        clear_at = iu.cleared_at.isoformat() if iu.cleared_at else _unit_status_time(unit_status_events, ['AQ','AFR','Available','CAN','NPF','NO_TRANSPORT','PATIENT_REFUSAL','CBY_CALLER','CBY_OTHER','CBY_DISPATCH'])
        miles = db.query(MileageReading).filter_by(incident_id=incident.id, unit_id=unit.id).order_by(MileageReading.recorded_at.asc()).all()
        mileage = {}
        if miles:
            mileage['beginning'] = miles[0].mileage
            mileage['ending'] = miles[-1].mileage
        scene_reading = next((m for m in miles if m.status_code in ('OS','on_scene')), None)
        dest_reading = next((m for m in miles if m.status_code in ('AD','arrived_at_destination','DEL','delivered','at_destination')), None)
        leg = db.query(TransportLeg).filter_by(incident_id=incident.id, unit_id=unit.id).first()
        if scene_reading:
            mileage['scene'] = scene_reading.mileage
        if dest_reading:
            mileage['destination'] = dest_reading.mileage
        if 'scene' in mileage and 'destination' in mileage:
            mileage['total'] = round(mileage['destination'] - mileage['scene'], 1)
        if leg:
            if leg.pickup_mileage is not None and 'scene' not in mileage:
                mileage['scene'] = leg.pickup_mileage
            if leg.dropoff_mileage is not None and 'destination' not in mileage:
                mileage['destination'] = leg.dropoff_mileage
            if 'scene' in mileage and 'destination' in mileage and 'total' not in mileage:
                mileage['total'] = round(mileage['destination'] - mileage['scene'], 1)
            if leg.destination:
                mileage['destination_name'] = leg.destination.name
                mileage['destination_address'] = leg.destination.address
        crew = db.query(Personnel).filter(Personnel.current_unit_id == unit.id).all()
        units_summary.append({
            'unit_id': unit.id,
            'call_sign': unit.call_sign,
            'unit_type': unit.unit_type,
            'discipline': (agency.agency_type if agency else 'unknown').lower(),
            'agency': agency.name if agency else None,
            'assigned_at': assigned_at,
            'dispatched_at': dispatched_at,
            'en_route_at': en_route_at,
            'arrived_at': arrived_at,
            'transport_at': transport_at,
            'destination_arrived_at': dest_arrived_at,
            'cleared_at': clear_at,
            'mileage': mileage,
            'crew': [{'id': c.id, 'name': f"{c.first_name or ''} {c.last_name or ''}".strip(), 'cert': ''} for c in crew],
            'disposition': iu.disposition
        })

    header = {
        'incident_id': incident.id,
        'incident_number': incident.incident_number,
        'call_number': incident.call_number,
        'call_type': incident.call_type,
        'priority': incident.priority,
        'discipline': agency.agency_type if agency else 'unknown',
        'status': incident.status,
        'report_generated_at': tz_now().isoformat(),
        'call_received_at': incident.created_at.isoformat() if incident.created_at else None,
        'closed_at': incident.closed_at.isoformat() if incident.closed_at else None,
        'incident_date': incident.created_at.date().isoformat() if incident.created_at else None,
        'jurisdiction': loc.jurisdiction.name if loc and loc.jurisdiction else (agency.name if agency else None),
        'primary_agency': agency.name if agency else None,
        'reporting_agency': agency.name if agency else None
    }
    location_info = {
        'original_address': loc.raw_address if loc else incident.location_text,
        'validated_address': loc.standardized_address if loc else None,
        'city': loc.city if loc else None,
        'state': loc.state if loc else None,
        'postal_code': loc.postal_code if loc else None,
        'latitude': loc.latitude if loc else incident.lat,
        'longitude': loc.longitude if loc else incident.lng,
        'cross_streets': loc.cross_streets if loc else None,
        'zone': loc.zone.name if loc and loc.zone else (incident.extra.get('zone_name') if incident.extra else None),
        'verification_status': loc.verification_status if loc else 'unverified'
    }
    duration_seconds = None
    if incident.created_at:
        end = incident.closed_at or tz_now()
        duration_seconds = int((_naive_local(end) - _naive_local(incident.created_at)).total_seconds())
        if duration_seconds < 0:
            duration_seconds = 0
    call_info = {
        'narrative': incident.narrative,
        'caller_name': incident.caller_name,
        'callback': incident.callback,
        'response_mode': (incident.extra or {}).get('response_mode'),
        'call_status': (incident.extra or {}).get('call_status'),
        'total_call_duration': _fmt_dur(duration_seconds),
        'total_call_duration_seconds': duration_seconds
    }
    return {'header': header, 'call_info': call_info, 'location': location_info, 'units': units_summary, 'timeline': timeline}

def _ensure_incident_report(db, incident, user):
    report = db.query(IncidentReport).filter_by(incident_id=incident.id).order_by(IncidentReport.version.desc()).first()
    if not report:
        report = IncidentReport(incident_id=incident.id, report_number=f"RPT-{incident.id}-{1}", status='draft', version=1, summary_json=_build_after_call_summary(db, incident), created_by=user.get('user_id'))
        db.add(report)
    else:
        report.summary_json = _build_after_call_summary(db, incident)
        report.updated_at = tz_now()
    return report

@app.get('/incidents/{incident_id}/report')
def get_after_call_report(request: Request, incident_id: int, profile: str = Query('full'), db: Session = Depends(get_db)):
    user = check_role(request, CALL_TAKER_ROLES)
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    report = db.query(IncidentReport).filter_by(incident_id=incident.id).order_by(IncidentReport.version.desc()).first()
    if not report:
        report = _ensure_incident_report(db, incident, user)
        db.commit(); db.refresh(report)
    summary = report.summary_json or {}
    summary['report'] = {'status': report.status, 'version': report.version, 'report_id': report.id, 'report_number': report.report_number, 'finalized_at': report.finalized_at.isoformat() if report.finalized_at else None, 'amendment_reason': report.amendment_reason, 'created_at': report.created_at.isoformat()}
    return _redact_summary(summary, profile)

@app.get('/incidents/{incident_id}/report/print')
def print_after_call_report(incident_id: int, db: Session = Depends(get_db)):
    return FileResponse('static/after_call_summary.html')

@app.get('/incidents/{incident_id}/report/status')
def get_report_status(request: Request, incident_id: int, db: Session = Depends(get_db)):
    check_role(request, CALL_TAKER_ROLES)
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    report = db.query(IncidentReport).filter_by(incident_id=incident.id).order_by(IncidentReport.version.desc()).first()
    if not report:
        report = _ensure_incident_report(db, incident, {'user_id': None})
        db.commit(); db.refresh(report)
    return {'status': report.status, 'version': report.version, 'report_id': report.id, 'report_number': report.report_number, 'finalized_at': report.finalized_at.isoformat() if report.finalized_at else None, 'amendment_reason': report.amendment_reason, 'created_at': report.created_at.isoformat()}

@app.get('/incidents/{incident_id}/report/versions')
def get_report_versions(request: Request, incident_id: int, db: Session = Depends(get_db)):
    check_role(request, CALL_TAKER_ROLES)
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    reports = db.query(IncidentReport).filter_by(incident_id=incident.id).order_by(IncidentReport.version.desc()).all()
    return [{'version': r.version, 'status': r.status, 'report_number': r.report_number, 'created_at': r.created_at.isoformat(), 'finalized_at': r.finalized_at.isoformat() if r.finalized_at else None, 'amendment_reason': r.amendment_reason} for r in reports]

@app.put('/incidents/{incident_id}/report/finalize')
def finalize_report(request: Request, incident_id: int, db: Session = Depends(get_db)):
    user = check_role(request, DISPATCHER_ROLES)
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    report = db.query(IncidentReport).filter_by(incident_id=incident.id).order_by(IncidentReport.version.desc()).first()
    if not report:
        report = _ensure_incident_report(db, incident, user)
    if report.status == 'finalized':
        raise HTTPException(status_code=400, detail='Report is already finalized. Use amend to make changes.')
    report.status = 'finalized'
    report.finalized_at = tz_now()
    report.finalized_by = user.get('user_id')
    db.commit(); db.refresh(report)
    return {'status': report.status, 'version': report.version}

@app.put('/incidents/{incident_id}/report/ready-for-review')
def ready_for_review_report(request: Request, incident_id: int, db: Session = Depends(get_db)):
    user = check_role(request, CALL_TAKER_ROLES)
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    report = db.query(IncidentReport).filter_by(incident_id=incident.id).order_by(IncidentReport.version.desc()).first()
    if not report:
        report = _ensure_incident_report(db, incident, user)
    if report.status == 'finalized':
        raise HTTPException(status_code=400, detail='Report is finalized.')
    report.status = 'ready_for_review'
    db.commit(); db.refresh(report)
    return {'status': report.status, 'version': report.version}

@app.post('/incidents/{incident_id}/report/amend')
def amend_report(request: Request, incident_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    user = check_role(request, DISPATCHER_ROLES)
    reason = body.get('reason') or ''
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    latest = db.query(IncidentReport).filter_by(incident_id=incident.id).order_by(IncidentReport.version.desc()).first()
    if not latest:
        latest = _ensure_incident_report(db, incident, user)
    parent_version = latest.version
    new_version = parent_version + 1
    new_report = IncidentReport(
        incident_id=incident.id,
        report_number=f"RPT-{incident.id}-{new_version}",
        status='draft',
        version=new_version,
        parent_report_version=parent_version,
        amendment_reason=reason,
        summary_json=_build_after_call_summary(db, incident),
        created_by=user.get('user_id')
    )
    db.add(new_report); db.commit(); db.refresh(new_report)
    return {'status': new_report.status, 'version': new_report.version, 'parent_version': parent_version}

@app.put('/incidents/{incident_id}/report/return-for-correction')
def return_report_for_correction(request: Request, incident_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    user = check_role(request, DISPATCHER_ROLES)
    reason = body.get('reason') or ''
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    report = db.query(IncidentReport).filter_by(incident_id=incident.id).order_by(IncidentReport.version.desc()).first()
    if not report:
        raise HTTPException(status_code=404, detail='No report to return')
    report.status = 'returned_for_correction'
    report.amendment_reason = reason
    db.commit(); db.refresh(report)
    return {'status': report.status, 'version': report.version}

def _build_supplement(db, incident, discipline):
    base = _build_after_call_summary(db, incident)
    if discipline == 'ems':
        ems_base = base.copy()
        legs = db.query(TransportLeg).filter_by(incident_id=incident.id).all()
        mileage = db.query(MileageReading).filter_by(incident_id=incident.id).order_by(MileageReading.recorded_at.asc()).all()
        ems_base['transport_legs'] = [{
            'leg_id': l.id,
            'unit_id': l.unit_id,
            'pickup_address': l.pickup_address,
            'pickup_mileage': l.pickup_mileage,
            'dropoff_address': l.dropoff_address,
            'dropoff_mileage': l.dropoff_mileage,
            'departed_scene_at': l.departed_scene_at.isoformat() if l.departed_scene_at else None,
            'arrived_destination_at': l.arrived_destination_at.isoformat() if l.arrived_destination_at else None,
            'destination_name': l.destination.name if l.destination else None,
            'destination_agency_type': l.destination.agency_type if l.destination else None
        } for l in legs]
        ems_base['mileage_readings'] = [{
            'unit_id': m.unit_id,
            'status_code': m.status_code,
            'mileage': m.mileage,
            'recorded_at': m.recorded_at.isoformat() if m.recorded_at else None
        } for m in mileage]
        epcr = db.query(EpcrExport).filter_by(incident_id=incident.id).order_by(EpcrExport.created_at.desc()).first()
        ems_base['epcr'] = {
            'export_id': epcr.id,
            'nemsis_status': epcr.status,
            'xml_payload': epcr.xml_payload,
            'created_at': epcr.created_at.isoformat()
        } if epcr else None
        ems_base['patient_narrative'] = (incident.extra or {}).get('patient_narrative') or incident.narrative
        return ems_base
    if discipline == 'fire':
        fire = base.copy()
        units = db.query(IncidentUnit).filter_by(incident_id=incident.id).all()
        events = db.query(StatusEvent).filter_by(incident_id=incident.id).order_by(StatusEvent.created_at.asc()).all()
        fire['neris'] = {
            'neris_version': '1.0-draft',
            'submission_status': 'local-draft',
            'incident_type_mapping': (incident.extra or {}).get('neris_incident_type'),
            'alarm_level': (incident.extra or {}).get('alarm_level'),
            'fire_origin_detected': any(e.status_code in ('WATER','EXT','OVER') for e in events),
            'units': [{
                'unit_id': u.unit.id if u.unit else None,
                'call_sign': u.unit.call_sign if u.unit else None,
                'arrived_at': _unit_status_time([e for e in events if e.unit_id == (u.unit.id if u.unit else None)], ['OS','on_scene','WATER','EXT','OVER']),
                'cleared_at': u.cleared_at.isoformat() if u.cleared_at else None
            } for u in units],
            'property_use': (incident.extra or {}).get('property_use'),
            'fire_origin': (incident.extra or {}).get('fire_origin'),
            'flame_description': (incident.extra or {}).get('flame_description')
        }
        fire['nfirs_historical_readonly'] = {
            'note': 'NFIRS is decommissioned. Historical NFIRS records are read-only; current fire reporting is NERIS-native.',
            'legacy_incident_number': incident.incident_number,
            'legacy_module_reference': None
        }
        return fire
    if discipline == 'law':
        law = base.copy()
        law['enforcement'] = {
            'citations': (incident.extra or {}).get('citations') or [],
            'arrests': (incident.extra or {}).get('arrests') or [],
            'warnings': (incident.extra or {}).get('warnings') or [],
            'case_number_assigned': (incident.extra or {}).get('case_number'),
            'officer_summary': (incident.extra or {}).get('officer_summary') or incident.narrative
        }
        return law
    return base

@app.get('/incidents/{incident_id}/supplement/{discipline}')
def get_supplement_report(request: Request, incident_id: int, discipline: str, profile: str = Query('full'), db: Session = Depends(get_db)):
    check_role(request, CALL_TAKER_ROLES)
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    if discipline not in ('ems','fire','law'):
        raise HTTPException(status_code=400, detail='Invalid discipline')
    return _redact_summary(_build_supplement(db, incident, discipline), profile)

@app.get('/incidents/{incident_id}/supplement/{discipline}/view')
def supplement_view(request: Request, incident_id: int, discipline: str, db: Session = Depends(get_db)):
    check_role(request, CALL_TAKER_ROLES)
    if discipline not in ('ems','fire','law'):
        raise HTTPException(status_code=400, detail='Invalid discipline')
    return FileResponse('static/supplement.html')

@app.get('/incidents/{incident_id}/report/export')
def export_report(request: Request, incident_id: int, format: str = 'json', profile: str = Query('full'), db: Session = Depends(get_db)):
    check_role(request, CALL_TAKER_ROLES)
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    report = db.query(IncidentReport).filter_by(incident_id=incident.id).order_by(IncidentReport.version.desc()).first()
    if not report:
        report = _ensure_incident_report(db, incident, {'user_id': None})
        db.commit(); db.refresh(report)
    if format == 'json':
        return Response(json.dumps(_redact_summary(report.summary_json or {}, profile)), media_type='application/json', headers={'Content-Disposition': f'attachment; filename="report-{incident_id}.json"'})
    if format == 'html':
        return FileResponse('static/after_call_summary.html')
    if format == 'pdf':
        try:
            from fpdf import FPDF
            summary = _redact_summary(report.summary_json or {}, profile)
            pdf = FPDF()
            pdf.add_page(); pdf.set_font('Arial', 'B', 14)
            h = summary.get('header', {})
            pdf.cell(0, 10, 'After-Call Summary', ln=True)
            pdf.set_font('Arial', '', 11)
            pdf.cell(0, 8, f"Incident #: {h.get('incident_number','-')}", ln=True)
            pdf.cell(0, 8, f"Call #: {h.get('call_number','-')}", ln=True)
            pdf.cell(0, 8, f"Call Type: {h.get('call_type','-')}", ln=True)
            pdf.cell(0, 8, f"Location: {(summary.get('location') or {}).get('validated_address') or (summary.get('location') or {}).get('original_address','-')}", ln=True)
            pdf.ln(4)
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 10, 'Units', ln=True)
            pdf.set_font('Arial', '', 10)
            for u in summary.get('units', []):
                pdf.cell(0, 6, f"{u.get('call_sign','')}  ({u.get('unit_type','')})  Assigned: {u.get('assigned_at','-')}  Cleared: {u.get('cleared_at','-')}", ln=True)
            pdf.ln(4)
            pdf.set_font('Arial', 'B', 12)
            pdf.cell(0, 10, 'Narrative', ln=True)
            pdf.set_font('Arial', '', 10)
            narrative = (summary.get('call_info') or {}).get('narrative') or 'No narrative'
            for line in narrative.split('\n')[:20]:
                pdf.cell(0, 5, line, ln=True)
            import io
            buf = io.BytesIO(); pdf.output(buf); buf.seek(0)
            return Response(buf.read(), media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="report-{incident_id}.pdf"'})
        except ImportError:
            raise HTTPException(status_code=501, detail='PDF generation requires fpdf2. Install: pip install fpdf2')
    raise HTTPException(status_code=400, detail='Unsupported format')

@app.put('/incidents/{incident_id}', response_model=IncidentOut)
def update_incident(request: Request, incident_id: int, body: IncidentUpdate, db: Session = Depends(get_db)):
    try:
        user = check_role(request, CALL_TAKER_ROLES)
        if body.status == 'closed':
            check_role(request, DISPATCHER_ROLES)
        incident = db.query(Incident).get(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail='Incident not found')
        changes = body.model_dump(exclude_unset=True)
        old_location_text = incident.location_text
        for k, v in changes.items():
            if k == 'agency_id' and v is None:
                continue
            if k == 'extra' and v:
                extra = _load_extra(incident.extra)
                for ek, ev in v.items():
                    if ev is not None:
                        extra[ek] = ev
                setattr(incident, 'extra', extra)
                _set_incident_destination_from_extra(db, incident)
                continue
            if k == 'call_status' and v is not None:
                extra = _load_extra(incident.extra)
                extra['call_status'] = v
                setattr(incident, 'extra', extra)
                continue
            if k == 'call_status':
                continue
            setattr(incident, k, v)
        if 'location_text' in changes and changes['location_text'] != old_location_text:
            try:
                _validate_incident_location(db, incident, force=True)
            except Exception as e:
                print('validate incident location error:', e)
        if body.status == 'closed' and not incident.closed_at:
            incident.closed_at = tz_now()
            for unit in db.query(Unit).filter(Unit.current_incident_id == incident.id).all():
                iu = db.query(IncidentUnit).filter_by(incident_id=incident.id, unit_id=unit.id, cleared_at=None).first()
                if iu:
                    iu.cleared_at = incident.closed_at
                    iu.assignment_status = 'cleared'
                    if iu.assigned_at:
                        duration = (_naive_local(incident.closed_at) - _naive_local(iu.assigned_at)).total_seconds()
                        unit.accumulated_call_seconds = (unit.accumulated_call_seconds or 0) + duration
                agency = db.query(Agency).get(unit.agency_id) if unit.agency_id else None
                unit.current_status = 'AFR' if (agency and agency.agency_type == 'fire') else 'AQ'
                unit.current_incident_id = None
                db.add(StatusEvent(unit_id=unit.id, incident_id=incident.id, status_code=unit.current_status, reason='Call closed'))
            _ensure_incident_report(db, incident, user)
        _log_event(db, 'incident_updated', 'incident', incident.id, user_id=user.get('user_id'), data=changes, agency_id=incident.agency_id)
        db.commit()
        db.refresh(incident)
        return incident
    except HTTPException:
        raise
    except Exception as e:
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

def _set_incident_destination_from_extra(db, incident):
    extra = incident.extra or {}
    if not isinstance(extra, dict):
        extra = {}
    if extra.get('no_transport') or (not extra.get('transport_destination_id') and not extra.get('transport_destination_name')):
        return
    latest = db.query(IncidentDestination).filter_by(incident_id=incident.id).order_by(IncidentDestination.created_at.desc()).first()
    dest_id = None
    if extra.get('transport_destination_id'):
        dest_id = extra['transport_destination_id']
    elif extra.get('transport_destination_name'):
        name = extra['transport_destination_name'].strip()
        dest = db.query(Destination).filter(Destination.agency_id == incident.agency_id, Destination.name.ilike(name)).first()
        if not dest:
            dest = Destination(agency_id=incident.agency_id, name=name, is_active=True)
            db.add(dest); db.flush()
        dest_id = dest.id
    if not dest_id:
        return
    if latest and latest.destination_id == dest_id:
        return
    db.add(IncidentDestination(incident_id=incident.id, destination_id=dest_id, notes={}))

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

def _dispatch_one_unit(db, incident, unit, user, notes=None, reassign=False):
    now = tz_now()
    # If the unit was previously assigned and cleared, reuse the cleared record.
    existing_cleared = db.query(IncidentUnit).filter_by(incident_id=incident.id, unit_id=unit.id, assignment_status='cleared').first()
    if existing_cleared:
        existing_cleared.assignment_status = 'assigned'
        existing_cleared.cleared_at = None
        existing_cleared.assigned_at = now
        existing_cleared.notes = notes or existing_cleared.notes
        unit.current_incident_id = incident.id
        unit.last_assigned_at = now
        unit.current_status = 'AK'
        db.add(existing_cleared); db.flush()
        refresh_incident_status(db, incident)
        _log_event(db, 'unit_dispatched', 'incident', incident.id, user_id=user.get('user_id'), data={'unit_id': unit.id, 'notes': notes}, agency_id=incident.agency_id)
        db.add(StatusEvent(unit_id=unit.id, incident_id=incident.id, status_code='AK', reason='Dispatched to incident'))
        return existing_cleared
    # Re-dispatch to the same incident is only allowed if the previous assignment was cleared,
    # unless we are promoting an existing stacked assignment or reassigning to this incident.
    existing = db.query(IncidentUnit).filter_by(incident_id=incident.id, unit_id=unit.id).filter(IncidentUnit.assignment_status != 'cleared').first()
    if existing:
        if not reassign:
            raise HTTPException(status_code=400, detail='Unit already assigned to incident')
        if existing.assignment_status == 'stacked' or (existing.assignment_status not in ('cleared','stacked') and unit.current_incident_id != incident.id):
            # Promote this record to the active assignment and clear any other active assignment
            active = db.query(IncidentUnit).filter_by(unit_id=unit.id).filter(IncidentUnit.assignment_status.notin_(['cleared','stacked'])).order_by(IncidentUnit.assigned_at.desc()).first()
            if active and active.id != existing.id:
                active.assignment_status = 'cleared'
                active.cleared_at = now
                old_incident = db.query(Incident).get(active.incident_id)
                if old_incident:
                    refresh_incident_status(db, old_incident)
            existing.assignment_status = 'assigned'
            existing.assigned_at = now
            unit.current_incident_id = incident.id
            unit.last_assigned_at = now
            unit.current_status = 'AK'
            db.add(existing); db.flush()
            refresh_incident_status(db, incident)
            _log_event(db, 'unit_dispatched', 'incident', incident.id, user_id=user.get('user_id'), data={'unit_id': unit.id, 'notes': notes}, agency_id=incident.agency_id)
            db.add(StatusEvent(unit_id=unit.id, incident_id=incident.id, status_code='AK', reason='Dispatched to incident'))
            return existing
        # Already actively assigned to this incident
        return existing
    # Check if the unit is currently on an active call (not stacked, not cleared)
    active = db.query(IncidentUnit).filter_by(unit_id=unit.id).filter(IncidentUnit.assignment_status.notin_(['cleared','stacked'])).order_by(IncidentUnit.assigned_at.desc()).first()
    if active and active.incident_id != incident.id:
        if reassign:
            # Clear the previous active assignment and move the unit to the new call
            active.assignment_status = 'cleared'
            active.cleared_at = now
            old_incident = db.query(Incident).get(active.incident_id)
            if old_incident:
                refresh_incident_status(db, old_incident)
        else:
            # Stack the unit onto the new incident without changing current assignment
            iu = IncidentUnit(incident_id=incident.id, unit_id=unit.id, notes=notes, assignment_status='stacked')
            db.add(iu); db.flush()
            _log_event(db, 'unit_stacked', 'incident', incident.id, user_id=user.get('user_id'), data={'unit_id': unit.id, 'notes': notes}, agency_id=incident.agency_id)
            return iu
    iu = IncidentUnit(incident_id=incident.id, unit_id=unit.id, notes=notes)
    unit.current_incident_id = incident.id
    unit.last_assigned_at = now
    unit.current_status = 'AK'
    db.add(iu)
    db.flush()
    refresh_incident_status(db, incident)
    _log_event(db, 'unit_dispatched', 'incident', incident.id, user_id=user.get('user_id'), data={'unit_id': unit.id, 'notes': notes}, agency_id=incident.agency_id)
    db.add(StatusEvent(unit_id=unit.id, incident_id=incident.id, status_code='AK', reason='Dispatched to incident'))
    # Notify crew by SMS/email and create an MDT message
    crew = db.query(Personnel).filter(Personnel.current_unit_id == unit.id).all()
    if crew:
        msg = f"DISPATCH: {incident.call_number or incident.incident_number} - {incident.call_type} at {incident.location_text or 'Unknown'}"
        _record_alert(db, incident.id, unit.id, msg, crew)
        db.add(DispatchMessage(incident_id=incident.id, unit_id=unit.id, message_text=msg, method='mdt', channel='mdt', sent_at=tz_now()))
    return iu

def _advance_stacked_call(db, unit, user):
    stacked = db.query(IncidentUnit).filter_by(unit_id=unit.id, assignment_status='stacked').order_by(IncidentUnit.assigned_at.asc()).first()
    if not stacked:
        return None
    next_incident = db.query(Incident).get(stacked.incident_id)
    if not next_incident or next_incident.status == 'closed':
        stacked.assignment_status = 'cleared'
        stacked.cleared_at = tz_now()
        return None
    stacked.assignment_status = 'en_route'
    unit.current_incident_id = next_incident.id
    unit.current_status = 'ER'
    unit.last_assigned_at = tz_now()
    refresh_incident_status(db, next_incident)
    _log_event(db, 'unit_en_route', 'incident', next_incident.id, user_id=user.get('user_id'), data={'unit_id': unit.id, 'reason': 'Cleared previous call; advancing to stacked call'}, agency_id=next_incident.agency_id)
    db.add(StatusEvent(unit_id=unit.id, incident_id=next_incident.id, status_code='ER', reason='Cleared previous call; en route to stacked call'))
    crew = db.query(Personnel).filter(Personnel.current_unit_id == unit.id).all()
    if crew:
        msg = f"DISPATCH: {next_incident.call_number or next_incident.incident_number} - {next_incident.call_type} at {next_incident.location_text or 'Unknown'}"
        _record_alert(db, next_incident.id, unit.id, msg, crew)
        db.add(DispatchMessage(incident_id=next_incident.id, unit_id=unit.id, message_text=msg, method='mdt', channel='mdt', sent_at=tz_now()))
    return next_incident.id

@app.post('/incidents/{incident_id}/dispatch/{unit_id}')
def dispatch_unit(request: Request, incident_id: int, unit_id: int, notes: Optional[str] = None, reassign: bool = Query(False), db: Session = Depends(get_db)):
    check_role(request, DISPATCHER_ROLES)
    incident = db.query(Incident).get(incident_id)
    unit = db.query(Unit).get(unit_id)
    if not incident or not unit:
        raise HTTPException(status_code=404, detail='Incident or unit not found')
    user = get_current_user(request)
    iu = _dispatch_one_unit(db, incident, unit, user, notes, reassign=reassign)
    db.commit()
    db.refresh(iu)
    return {
        'incident_id': iu.incident_id,
        'unit_id': iu.unit_id,
        'assigned_at': iu.assigned_at,
        'assignment_status': iu.assignment_status
    }

@app.post('/incidents/{incident_id}/dispatch-recommended')
def dispatch_recommended(request: Request, incident_id: int, db: Session = Depends(get_db)):
    check_role(request, DISPATCHER_ROLES)
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    user = get_current_user(request)
    agency = db.query(Agency).get(incident.agency_id)
    customer_id = incident.customer_id or (agency.customer_id if agency else None) or user.get('customer_id')
    profile = _get_response_profile(db, customer_id, incident.agency_id, incident.call_type)
    slots = _profile_slots(profile)
    max_units = profile.get('max_units') if profile and not slots else None
    recs = recommend_units(incident_id=incident_id, limit=25, db=db)
    slot_remaining = {}
    total_needed = 0
    if slots:
        assigned_by_type = {}
        for _, unit in db.query(IncidentUnit, Unit).join(Unit, IncidentUnit.unit_id == Unit.id).filter(IncidentUnit.incident_id == incident_id, IncidentUnit.assignment_status != 'cleared').all():
            assigned_by_type[unit.unit_type] = assigned_by_type.get(unit.unit_type, 0) + 1
        for s in slots:
            t = s.get('unit_type'); c = s.get('count', 1)
            slot_remaining[t] = max(0, c - assigned_by_type.get(t, 0))
        total_needed = sum(slot_remaining.values())
    elif max_units:
        total_assigned = db.query(IncidentUnit).filter_by(incident_id=incident_id).filter(IncidentUnit.assignment_status != 'cleared').count()
        total_needed = max(0, max_units - total_assigned)
    else:
        total_needed = 1
    dispatched = []
    for r in recs:
        if not r.get('eligible') or total_needed <= 0:
            continue
        unit = db.query(Unit).get(r['unit_id'])
        if not unit or unit.current_incident_id:
            continue
        if slots and slot_remaining.get(unit.unit_type, 0) <= 0:
            continue
        iu = _dispatch_one_unit(db, incident, unit, user)
        dispatched.append({'unit_id': unit.id, 'call_sign': unit.call_sign})
        total_needed -= 1
        if slots:
            slot_remaining[unit.unit_type] = slot_remaining.get(unit.unit_type, 0) - 1
    if not dispatched:
        raise HTTPException(status_code=400, detail='No eligible recommended units available')
    db.commit()
    _log_event(db, 'incident_bulk_dispatched', 'incident', incident_id, user_id=user.get('user_id'), data={'unit_ids': [d['unit_id'] for d in dispatched]}, agency_id=incident.agency_id)
    db.commit()
    return {'dispatched': dispatched, 'resource_status': _incident_resource_status(db, incident)}

@app.get('/incidents/{incident_id}/packet/{unit_id}')
def get_dispatch_packet(incident_id: int, unit_id: int, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    unit = db.query(Unit).get(unit_id)
    if not incident or not unit:
        raise HTTPException(status_code=404, detail='Incident or unit not found')
    iu = db.query(IncidentUnit).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    if not iu:
        raise HTTPException(status_code=400, detail='Unit not assigned to incident')
    loc = db.query(IncidentLocation).filter_by(incident_id=incident_id).first()
    agency = db.query(Agency).get(incident.agency_id)
    assigned = db.query(IncidentUnit, Unit).join(Unit, IncidentUnit.unit_id == Unit.id).filter(IncidentUnit.incident_id == incident_id, IncidentUnit.assignment_status != 'cleared').all()
    ack = db.query(IncidentUnitAck).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    idest = db.query(IncidentDestination).filter_by(incident_id=incident_id).first()
    dest = db.query(Destination).get(idest.destination_id) if idest else None
    if not dest and incident.extra:
        extra = incident.extra if isinstance(incident.extra, dict) else {}
        if extra.get('transport_destination_id'):
            dest = db.query(Destination).get(extra['transport_destination_id'])
        elif extra.get('transport_destination_name'):
            dest = {'name': extra['transport_destination_name'], 'address': None, 'lat': None, 'lng': None, 'category': None}
    packet = {
        'incident_id': incident.id,
        'incident_number': incident.incident_number,
        'call_number': incident.call_number,
        'call_type': incident.call_type,
        'priority': incident.priority,
        'narrative': incident.narrative,
        'location_text': incident.location_text,
        'lat': incident.lat,
        'lng': incident.lng,
        'standardized_address': loc.standardized_address if loc else None,
        'cross_streets': loc.cross_streets if loc else None,
        'zone_name': loc.zone.name if loc and loc.zone else None,
        'destination': {'id': getattr(dest, 'id', None), 'name': dest.name, 'address': dest.address, 'lat': dest.lat, 'lng': dest.lng} if dest else None,
        'agency': {'id': agency.id, 'name': agency.name, 'agency_type': agency.agency_type} if agency else None,
        'unit': {'id': unit.id, 'call_sign': unit.call_sign, 'unit_type': unit.unit_type},
        'assigned_units': [{'id': u.id, 'call_sign': u.call_sign, 'unit_type': u.unit_type} for _, u in assigned],
        'resource_status': _incident_resource_status(db, incident),
        'acknowledged_at': ack.acknowledged_at.isoformat() if ack else None,
        # Once a unit has moved past the initial dispatched state (e.g., dispatcher set it en_route from console), the MDT should not be forced to acknowledge before status changes.
        'requires_ack': not ack and unit.current_status in ('AK','AQ','AFR','dispatched','available'),
        'extra': incident.extra
    }
    return packet

@app.post('/incidents/{incident_id}/units/{unit_id}/ack')
def ack_dispatch_packet(request: Request, incident_id: int, unit_id: int, db: Session = Depends(get_db)):
    user = check_role(request, FIELD_ROLES)
    iu = db.query(IncidentUnit).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    if not iu:
        raise HTTPException(status_code=404, detail='Unit not assigned to incident')
    existing = db.query(IncidentUnitAck).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    if not existing:
        existing = IncidentUnitAck(incident_id=incident_id, unit_id=unit_id, acknowledged_by=user.get('email'))
        db.add(existing)
    existing.acknowledged_at = tz_now()
    db.commit()
    _log_event(db, 'packet_acknowledged', 'incident', incident_id, user_id=user.get('user_id'), data={'unit_id': unit_id}, agency_id=iu.incident.agency_id)
    return {'acknowledged': True, 'acknowledged_at': existing.acknowledged_at}

@app.get('/incidents/{incident_id}/acks')
def list_incident_acks(incident_id: int, db: Session = Depends(get_db)):
    acks = db.query(IncidentUnitAck).filter_by(incident_id=incident_id).all()
    return [{'unit_id': a.unit_id, 'acknowledged_at': a.acknowledged_at, 'acknowledged_by': a.acknowledged_by} for a in acks]

@app.get('/incidents/{incident_id}/timers')
def get_incident_timers(incident_id: int, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    return _incident_timers(db, incident)

@app.get('/incidents/alerts')
def list_incident_alerts(agency_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    return _active_incident_alerts(db, agency_id)

@app.post('/incidents/{incident_id}/units/{unit_id}/status')
def update_unit_status(request: Request, incident_id: int, unit_id: int, body: StatusUpdate, db: Session = Depends(get_db)):
    check_role(request, FIELD_ROLES)
    iu = db.query(IncidentUnit).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    if not iu:
        raise HTTPException(status_code=404, detail='Unit not assigned to incident')
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    agency = db.query(Agency).get(unit.agency_id)
    if body.status_code in ('OS','AD') and body.mileage is None and agency and agency.agency_type == 'ems':
        raise HTTPException(status_code=400, detail=f'Mileage is required for {_status_label(body.status_code)}')
    ts = body.at or tz_now()
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
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
            duration = (_naive_local(ts) - _naive_local(iu.assigned_at)).total_seconds()
            if duration > 0:
                unit.accumulated_call_seconds = (unit.accumulated_call_seconds or 0) + duration
        iu.cleared_at = ts
        iu.assignment_status = 'cleared'
        unit.current_incident_id = None
        unit.current_status = 'AQ' if body.status_code in ('CAN','NPF','NO_TRANSPORT','PATIENT_REFUSAL','CBY_CALLER','CBY_OTHER','CBY_DISPATCH') else body.status_code
        _advance_stacked_call(db, unit, get_current_user(request))
    incident = db.query(Incident).get(incident_id)
    refresh_incident_status(db, incident)
    # Transport leg lifecycle
    if incident:
        open_leg = db.query(TransportLeg).filter_by(incident_id=incident.id, unit_id=unit_id).filter(TransportLeg.status != 'cleared').order_by(TransportLeg.created_at.desc()).first()
        if body.status_code in ('TR','TRP','ED','TH'):
            if not open_leg:
                dest_id = _resolve_destination_id(db, body, incident.agency_id)
                if not dest_id:
                    latest = db.query(IncidentDestination).filter_by(incident_id=incident.id).order_by(IncidentDestination.created_at.desc()).first()
                    dest_id = latest.destination_id if latest else None
                open_leg = TransportLeg(incident_id=incident.id, unit_id=unit_id, destination_id=dest_id, status='en_route', en_route_at=ts, passenger_count=body.passenger_count)
                db.add(open_leg); db.flush()
            else:
                open_leg.status = 'en_route'
                if body.at is not None or not open_leg.en_route_at:
                    open_leg.en_route_at = ts
                new_dest = _resolve_destination_id(db, body, incident.agency_id)
                if new_dest:
                    open_leg.destination_id = new_dest
            if body.passenger_count is not None:
                open_leg.passenger_count = body.passenger_count
            if open_leg.arrived_at and (body.at is not None or not open_leg.departed_scene_at):
                open_leg.departed_scene_at = ts
        elif body.status_code == 'OS':
            if not open_leg:
                open_leg = TransportLeg(incident_id=incident.id, unit_id=unit_id, status='arrived', arrived_at=ts)
                db.add(open_leg); db.flush()
            else:
                if open_leg.status == 'en_route':
                    open_leg.status = 'arrived'
                if body.at is not None or not open_leg.arrived_at:
                    open_leg.arrived_at = ts
            if open_leg.pickup_mileage is None:
                open_leg.pickup_mileage = body.mileage
        elif body.status_code == 'AD' and open_leg:
            if open_leg.status in ('en_route','arrived'):
                open_leg.status = 'arrived_destination'
            if body.at is not None or not open_leg.arrived_destination_at:
                open_leg.arrived_destination_at = ts
            if open_leg.dropoff_mileage is None:
                open_leg.dropoff_mileage = body.mileage
        elif body.status_code not in _CALL_ACTIVE_STATUSES and open_leg:
            open_leg.status = 'cleared'
            if body.at is not None or not open_leg.cleared_at:
                open_leg.cleared_at = ts
    user = get_current_user(request)
    db.add(StatusEvent(
        unit_id=unit_id,
        incident_id=incident_id,
        status_code=body.status_code,
        reason=body.reason,
        lat=body.lat,
        lng=body.lng,
        created_at=ts
    ))
    if body.mileage is not None and incident_id and body.status_code in ('OS','AD'):
        db.add(MileageReading(incident_id=incident_id, unit_id=unit_id, status_code=body.status_code, mileage=round(float(body.mileage), 1), recorded_at=ts))
    _log_event(db, 'unit_status_changed', 'incident', incident_id, user_id=user.get('user_id'), data={'unit_id': unit_id, 'status_code': body.status_code, 'disposition': body.disposition, 'lat': body.lat, 'lng': body.lng, 'mileage': body.mileage, 'at': body.at.isoformat() if body.at else None}, agency_id=incident.agency_id)
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
    timestamp = tz_now()
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
    ts = tz_now()
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
        received_at = tz_now()
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
        pos = _record_taip_sentence(db, body.raw, body.taip_id, source=source, received_at=tz_now())
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
                received_at = tz_now()
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
            received_at = tz_now()
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

def _taip_compute_checksum(payload: str) -> str:
    calculated = 0
    for ch in payload:
        calculated ^= ord(ch)
    return f'{calculated:02X}'

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

@app.get('/taip/verify')
def verify_taip(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    results = []
    now = tz_now()
    lat, lng = 40.0, -83.0
    time_str = '00000'
    base_sentence = f'>RPV{time_str}+4000000-0830000000000001;ID=TAIP-VERIFY'
    checksum = _taip_compute_checksum(f'{base_sentence};*')
    valid_sentence = f'{base_sentence};*{checksum}<'
    bad_checksum_sentence = f'{base_sentence};*00<'

    # Parser test
    try:
        parsed = parse_taip_pv(valid_sentence)
        ok = parsed and abs(parsed['lat'] - 40.0) < 0.0001 and abs(parsed['lng'] - (-83.0)) < 0.0001
        results.append({'item': 'PV fixed-width parser', 'pass': ok, 'detail': parsed})
    except Exception as e:
        results.append({'item': 'PV fixed-width parser', 'pass': False, 'detail': str(e)})

    # Checksum test
    try:
        parse_taip_pv(bad_checksum_sentence)
        results.append({'item': 'Checksum rejects invalid', 'pass': False, 'detail': 'No error raised for bad checksum'})
    except ValueError:
        results.append({'item': 'Checksum rejects invalid', 'pass': True, 'detail': 'ValueError raised'})

    # Allowlist test
    try:
        allowed = _taip_source_allowed('1.2.3.4')
        results.append({'item': 'Source IP allowlist', 'pass': True, 'detail': f'allowlist={TAIP_ALLOWLIST}, 1.2.3.4 allowed={allowed}'})
    except Exception as e:
        results.append({'item': 'Source IP allowlist', 'pass': False, 'detail': str(e)})

    # Rate limit test
    try:
        test_id = 'TAIP-VERIFY'
        _taip_rate_limited(test_id)
        limited = _taip_rate_limited(test_id)
        if TAIP_MIN_INTERVAL <= 0:
            results.append({'item': 'Per-source rate limiting', 'pass': True, 'detail': 'TAIP_MIN_INTERVAL=0, rate limit disabled'})
        else:
            results.append({'item': 'Per-source rate limiting', 'pass': limited, 'detail': f'first call accepted, second call limited={limited}, interval={TAIP_MIN_INTERVAL}s'})
    except Exception as e:
        results.append({'item': 'Per-source rate limiting', 'pass': False, 'detail': str(e)})

    # Out-of-order test
    try:
        u = Unit(last_seen_at=now, lat=lat, lng=lng)
        old = now - timedelta(seconds=TAIP_OUT_OF_ORDER_SECONDS + 5)
        out_of_order = _taip_out_of_order(u, old)
        results.append({'item': 'Out-of-order rejection', 'pass': out_of_order, 'detail': f'older than {TAIP_OUT_OF_ORDER_SECONDS}s rejected={out_of_order}'})
    except Exception as e:
        results.append({'item': 'Out-of-order rejection', 'pass': False, 'detail': str(e)})

    # Stale data age test
    try:
        stale_sentence = f'>RPV{time_str}+4000000-0830000000000000;ID=TAIP-VERIFY<'
        parse_taip_pv(stale_sentence)
        results.append({'item': 'Stale data age rejection', 'pass': False, 'detail': 'data_age=0 should raise'})
    except ValueError:
        results.append({'item': 'Stale data age rejection', 'pass': True, 'detail': 'data_age=0 rejected'})

    # Impossible jump test
    try:
        u = Unit(last_seen_at=now - timedelta(seconds=2), lat=lat, lng=lng)
        jump_ok = _taip_jump_ok(u, lat + 1.0, lng, now)
        results.append({'item': 'Impossible jump rejection', 'pass': not jump_ok, 'detail': f'large jump in 2s allowed={jump_ok}'})
    except Exception as e:
        results.append({'item': 'Impossible jump rejection', 'pass': False, 'detail': str(e)})

    # Stale/offline state test
    try:
        stale, offline = _taip_stale_state(now - timedelta(seconds=TAIP_STALE_SECONDS + 1))
        results.append({'item': 'Stale/offline state detection', 'pass': stale, 'detail': f'stale={stale}, offline={offline}'})
    except Exception as e:
        results.append({'item': 'Stale/offline state detection', 'pass': False, 'detail': str(e)})

    # Listener config
    try:
        info = taip_listener_info()
        results.append({'item': 'UDP/TCP listener configuration', 'pass': info.get('udp_port') > 0 or info.get('tcp_port') > 0, 'detail': info})
    except Exception as e:
        results.append({'item': 'UDP/TCP listener configuration', 'pass': False, 'detail': str(e)})

    # End-to-end ingest with DB (uses an existing unit or creates a temporary one)
    try:
        unit = db.query(Unit).filter(Unit.taip_id == 'TAIP-VERIFY').first()
        if not unit:
            # find any unit with a taip_id to reuse
            unit = db.query(Unit).filter(Unit.taip_id != None).first()
        test_taip_id = unit.taip_id if unit and unit.taip_id else 'TAIP-VERIFY'
        test_unit_id = unit.id if unit else None
        old_pos_count = db.query(TaipPosition).filter(TaipPosition.taip_id == test_taip_id).count()
        # save and temporarily bypass allowlist/rate limits for this self-test
        old_allowlist = list(TAIP_ALLOWLIST)
        old_last_packet = taip_last_packet.get(test_taip_id)
        if test_taip_id in taip_last_packet:
            del taip_last_packet[test_taip_id]
        old_unit = None
        if unit:
            old_unit = {'lat': unit.lat, 'lng': unit.lng, 'speed': unit.speed, 'heading': unit.heading, 'last_seen_at': unit.last_seen_at}
        try:
            TAIP_ALLOWLIST.clear()
            pos = _record_taip_sentence(db, valid_sentence, taip_id=test_taip_id, source='127.0.0.1', received_at=now)
        finally:
            TAIP_ALLOWLIST.clear()
            TAIP_ALLOWLIST.extend(old_allowlist)
            if old_last_packet:
                taip_last_packet[test_taip_id] = old_last_packet
            elif test_taip_id in taip_last_packet:
                del taip_last_packet[test_taip_id]
        # clean up the test position and restore unit state
        if pos and pos.id:
            db.delete(pos)
        if unit and old_unit:
            unit.lat = old_unit['lat']
            unit.lng = old_unit['lng']
            unit.speed = old_unit['speed']
            unit.heading = old_unit['heading']
            unit.last_seen_at = old_unit['last_seen_at']
        db.commit()
        results.append({'item': 'End-to-end ingest to database', 'pass': bool(pos and pos.id), 'detail': {'taip_id': test_taip_id, 'unit_id': test_unit_id, 'position_id': pos.id if pos else None}})
    except Exception as e:
        results.append({'item': 'End-to-end ingest to database', 'pass': False, 'detail': str(e)})

    passed = sum(1 for r in results if r['pass'])
    return {'overall': f'{passed}/{len(results)} passed', 'passed': passed, 'total': len(results), 'checks': results, 'timestamp': now.isoformat()}

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
        unit.in_service_at = tz_now()
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
def set_unit_status(request: Request, unit_id: int, body: UnitStatus, db: Session = Depends(get_db)):
    check_role(request, FIELD_ROLES)
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    agency = db.query(Agency).get(unit.agency_id)
    if body.status_code in ('OS','AD') and body.mileage is None and unit.current_incident_id and agency and agency.agency_type == 'ems':
        raise HTTPException(status_code=400, detail=f'Mileage is required for {_status_label(body.status_code)}')
    ts = body.at or tz_now()
    if ts.tzinfo is not None:
        ts = ts.replace(tzinfo=None)
    unit.current_status = body.status_code
    if body.lat is not None: unit.lat = body.lat
    if body.lng is not None: unit.lng = body.lng
    incident_id = None
    if unit.current_incident_id:
        incident = db.query(Incident).get(unit.current_incident_id)
        if incident and incident.status != 'closed':
            iu = db.query(IncidentUnit).filter_by(incident_id=incident.id, unit_id=unit_id, cleared_at=None).first()
            if iu:
                if body.passenger_count is not None:
                    iu.passenger_count = body.passenger_count
                if body.status_code in _CALL_ACTIVE_STATUSES:
                    iu.assignment_status = map_status(body.status_code)
                else:
                    if iu.assigned_at:
                        duration = (_naive_local(ts) - _naive_local(iu.assigned_at)).total_seconds()
                        if duration > 0:
                            unit.accumulated_call_seconds = (unit.accumulated_call_seconds or 0) + duration
                    iu.cleared_at = ts
                    iu.assignment_status = 'cleared'
                    unit.current_incident_id = None
                    unit.current_status = 'AQ' if body.status_code in ('CAN','NPF','NO_TRANSPORT','PATIENT_REFUSAL','CBY_CALLER','CBY_OTHER','CBY_DISPATCH') else body.status_code
                    _advance_stacked_call(db, unit, get_current_user(request))
            refresh_incident_status(db, incident)
            # Transport leg lifecycle
            open_leg = db.query(TransportLeg).filter_by(incident_id=incident.id, unit_id=unit_id).filter(TransportLeg.status != 'cleared').order_by(TransportLeg.created_at.desc()).first()
            if body.status_code in ('TR','ED','TH','TRP'):
                if not open_leg:
                    dest_id = _resolve_destination_id(db, body, incident.agency_id)
                    if not dest_id:
                        latest = db.query(IncidentDestination).filter_by(incident_id=incident.id).order_by(IncidentDestination.created_at.desc()).first()
                        dest_id = latest.destination_id if latest else None
                    open_leg = TransportLeg(incident_id=incident.id, unit_id=unit_id, destination_id=dest_id, status='en_route', en_route_at=ts, passenger_count=body.passenger_count)
                    db.add(open_leg); db.flush()
                else:
                    open_leg.status = 'en_route'
                    if body.at is not None or not open_leg.en_route_at:
                        open_leg.en_route_at = ts
                    new_dest = _resolve_destination_id(db, body, incident.agency_id)
                    if new_dest:
                        open_leg.destination_id = new_dest
                if body.passenger_count is not None:
                    open_leg.passenger_count = body.passenger_count
                if open_leg.arrived_at and (body.at is not None or not open_leg.departed_scene_at):
                    open_leg.departed_scene_at = ts
            elif body.status_code == 'OS':
                if not open_leg:
                    open_leg = TransportLeg(incident_id=incident.id, unit_id=unit_id, status='arrived', arrived_at=ts)
                    db.add(open_leg); db.flush()
                else:
                    if open_leg.status == 'en_route':
                        open_leg.status = 'arrived'
                    if body.at is not None or not open_leg.arrived_at:
                        open_leg.arrived_at = ts
                if open_leg.pickup_mileage is None:
                    open_leg.pickup_mileage = body.mileage
            elif body.status_code == 'AD' and open_leg:
                if open_leg.status in ('en_route','arrived'):
                    open_leg.status = 'arrived_destination'
                if body.at is not None or not open_leg.arrived_destination_at:
                    open_leg.arrived_destination_at = ts
                if open_leg.dropoff_mileage is None:
                    open_leg.dropoff_mileage = body.mileage
            elif body.status_code not in _CALL_ACTIVE_STATUSES and open_leg:
                open_leg.status = 'cleared'
                if body.at is not None or not open_leg.cleared_at:
                    open_leg.cleared_at = ts
            incident_id = incident.id
    if body.mileage is not None and incident_id and body.status_code in ('OS','AD'):
        db.add(MileageReading(incident_id=incident_id, unit_id=unit_id, status_code=body.status_code, mileage=round(float(body.mileage), 1), recorded_at=ts))
    if body.status_code in _ASSIGNABLE_STATUSES and (body.at is not None or not unit.in_service_at):
        unit.in_service_at = ts
    elif body.status_code in _OUT_OF_SERVICE_STATUSES:
        unit.in_service_at = None
    db.add(StatusEvent(unit_id=unit_id, incident_id=incident_id, status_code=body.status_code, reason=body.reason, lat=body.lat, lng=body.lng, created_at=ts))
    db.commit()
    db.refresh(unit)
    return unit

@app.post('/mdt/start', response_model=UnitOut)
def mdt_start(request: Request, body: MDTStart, db: Session = Depends(get_db)):
    user = get_current_user(request)
    unit = db.query(Unit).get(body.unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    u = db.query(User).get(user['user_id'])
    is_admin = u and u.role in ('admin','super_admin','superadmin')
    if not is_admin and unit.agency_id != user.get('agency_id'):
        raise HTTPException(status_code=403, detail='Unit not in your agency')
    # assign selected personnel to unit
    for pid in body.personnel_ids:
        p = db.query(Personnel).get(pid)
        if not p:
            continue
        if not is_admin and p.agency_id != user.get('agency_id'):
            continue
        p.current_unit_id = body.unit_id
        p.duty_status = 'on_duty'
        if body.provider_levels and str(pid) in body.provider_levels:
            p.provider_level = body.provider_levels[str(pid)]
        elif body.provider_levels and str(pid) in (body.provider_levels or {}):
            p.provider_level = body.provider_levels.get(pid)
    # set unit status (IN_SERVICE, ON_DUTY, AQ, etc)
    unit.current_status = body.status_code
    if body.status_code in _ASSIGNABLE_STATUSES and not unit.in_service_at:
        unit.in_service_at = tz_now()
    elif body.status_code in _OUT_OF_SERVICE_STATUSES:
        unit.in_service_at = None
    db.add(StatusEvent(unit_id=unit.id, status_code=body.status_code, reason='MDT start'))
    db.commit(); db.refresh(unit)
    return unit

@app.post('/units/{unit_id}/staff', response_model=UnitOut)
def set_unit_staff(request: Request, unit_id: int, body: UnitStaff, db: Session = Depends(get_db)):
    user = get_current_user(request)
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    u = db.query(User).get(user['user_id'])
    if not (u and u.role in ('admin','super_admin','dispatcher')) and unit.agency_id != user.get('agency_id'):
        raise HTTPException(status_code=403, detail='Not authorized')
    # clear existing crew from this unit if desired? keep simple: only set for listed ids
    for pid in body.personnel_ids:
        p = db.query(Personnel).get(pid)
        if p:
            p.current_unit_id = unit_id
            p.duty_status = body.duty_status or 'on_duty'
    if body.status_code:
        unit.current_status = body.status_code
        if body.status_code in _ASSIGNABLE_STATUSES and not unit.in_service_at:
            unit.in_service_at = tz_now()
        elif body.status_code in _OUT_OF_SERVICE_STATUSES:
            unit.in_service_at = None
        db.add(StatusEvent(unit_id=unit.id, status_code=body.status_code, reason='Dispatcher crew assign'))
    db.commit(); db.refresh(unit)
    return unit

ALLOWED_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}

def _validate_upload_file(file: UploadFile):
    ext = os.path.splitext(file.filename)[1].lower() or '.jpg'
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=f'Only image files are allowed ({", ".join(ALLOWED_IMAGE_EXTS)})')
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f'File too large (max {MAX_UPLOAD_SIZE // (1024*1024)}MB)')
    return ext

@app.post('/units/{unit_id}/photo')
def upload_unit_photo(unit_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    ext = _validate_upload_file(file)
    upload_dir = 'static/uploads'
    os.makedirs(upload_dir, exist_ok=True)
    path = f'{upload_dir}/unit_{unit_id}{ext}'
    with open(path, 'wb') as out:
        shutil.copyfileobj(file.file, out)
    unit.photo_url = f'/static/uploads/unit_{unit_id}{ext}'
    db.commit(); db.refresh(unit)
    return {'photo_url': unit.photo_url}

@app.post('/personnel/{personnel_id}/photo')
def upload_personnel_photo(personnel_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    p = db.query(Personnel).get(personnel_id)
    if not p:
        raise HTTPException(status_code=404, detail='Personnel not found')
    ext = _validate_upload_file(file)
    upload_dir = 'static/uploads'
    os.makedirs(upload_dir, exist_ok=True)
    path = f'{upload_dir}/personnel_{personnel_id}{ext}'
    with open(path, 'wb') as out:
        shutil.copyfileobj(file.file, out)
    p.photo_url = f'/static/uploads/personnel_{personnel_id}{ext}'
    db.commit(); db.refresh(p)
    return {'photo_url': p.photo_url}

@app.post('/units/{unit_id}/location', response_model=UnitOut)
def update_unit_location(unit_id: int, body: LocationUpdate, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    unit.lat = body.lat
    unit.lng = body.lng
    if body.speed is not None: unit.speed = body.speed
    if body.heading is not None: unit.heading = body.heading
    unit.last_seen_at = tz_now()
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
                m = DispatchMessage(incident_id=incident_id, unit_id=unit_id, message_text=msg, method=channel, channel=address, sent_at=tz_now())
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
    customer_id = current_user.get('customer_id')
    def ensure_agency(name, atype, domain, lat, lng):
        a = db.query(Agency).filter(Agency.domain == domain).first()
        if a: return a
        a = Agency(customer_id=customer_id, name=name, agency_type=atype, domain=domain, city='Columbus', state='OH', lat=lat, lng=lng)
        db.add(a); db.flush(); return a
    police = ensure_agency('City Police', 'police', 'pilot.police', CENTER[0]-0.01, CENTER[1]+0.01)
    fire = ensure_agency('Metro Fire', 'fire', 'pilot.fire', CENTER[0]+0.01, CENTER[1]-0.01)
    ems = ensure_agency('County EMS', 'ems', 'pilot.ems', CENTER[0]+0.005, CENTER[1]-0.015)
    db.commit()
    def ensure_config(agency, template):
        for category, value in template.items():
            cfg = db.query(CustomerConfig).filter_by(customer_id=customer_id, agency_id=agency.id, category=category, key='defaults').first()
            if not cfg:
                db.add(CustomerConfig(customer_id=customer_id, agency_id=agency.id, category=category, key='defaults', value=value))
        seeded = db.query(CustomerConfig).filter_by(customer_id=customer_id, agency_id=agency.id, category='__seeded__', key='flag').first()
        if not seeded:
            db.add(CustomerConfig(customer_id=customer_id, agency_id=agency.id, category='__seeded__', key='flag', value=True))
    templates = {
        'police': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'AK','label':'Dispatched'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'TRP','label':'Transporting'},{'code':'TC','label':'Traffic Control'},{'code':'CT','label':'Citation'},{'code':'ARR','label':'Arrest'},{'code':'BK','label':'Booking'},{'code':'CBY_CALLER','label':'Cancelled by Caller'},{'code':'CBY_OTHER','label':'Cancelled by Other Agency'},{'code':'CBY_DISPATCH','label':'Cancelled by Dispatch'},{'code':'OOS','label':'Out of Service'}],
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
                {'priority':1,'label':'EMERGENT','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Non-Emergent','target_seconds':720},
                {'priority':4,'label':'Scheduled','target_seconds':1200},
                {'priority':5,'label':'Standby','target_seconds':1800}
            ],
            'response_plans': {
                'Traffic Accident': ['patrol','supervisor','rescue'],
                'Theft': ['patrol','detective'],
                'Domestic': ['patrol','supervisor'],
                'Assault': ['patrol','supervisor','k9'],
                'Welfare Check': ['patrol'],
                'Suspicious Person': ['patrol','k9']
            },
            'response_profiles': {
                'Traffic Accident': {'slots':[{'unit_type':'patrol','count':1},{'unit_type':'supervisor','count':1}], 'response_mode':'emergency'},
                'Theft': {'unit_types':['patrol','detective'], 'min_units':1, 'max_units':2, 'response_mode':'routine'},
                'Domestic': {'unit_types':['patrol','supervisor'], 'min_units':2, 'max_units':3, 'response_mode':'emergency'},
                'Assault': {'slots':[{'unit_type':'patrol','count':2},{'unit_type':'supervisor','count':1},{'unit_type':'k9','count':1}], 'response_mode':'emergency'},
                'Welfare Check': {'unit_types':['patrol'], 'min_units':1, 'max_units':1, 'response_mode':'routine'},
                'Suspicious Person': {'unit_types':['patrol','k9'], 'min_units':1, 'max_units':2, 'response_mode':'routine'}
            },
            'dispositions': ['Arrested','Cited','Warned','Referred','Report','No Action','False Alarm']
        },
        'fire': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'AK','label':'Dispatched'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'WATER','label':'Water on Fire'},{'code':'EXT','label':'Extinguished'},{'code':'OVER','label':'Overhaul'},{'code':'TR','label':'Transporting'},{'code':'CBY_CALLER','label':'Cancelled by Caller'},{'code':'CBY_OTHER','label':'Cancelled by Other Agency'},{'code':'CBY_DISPATCH','label':'Cancelled by Dispatch'},{'code':'OOS','label':'Out of Service'}],
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
                {'priority':1,'label':'EMERGENT','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Non-Emergent','target_seconds':720},
                {'priority':4,'label':'Scheduled','target_seconds':1200},
                {'priority':5,'label':'Standby','target_seconds':1800}
            ],
            'response_plans': {
                'Structure Fire': ['engine','ladder','rescue','chief'],
                'Vehicle Fire': ['engine','brush','tanker'],
                'Medical Assist': ['rescue','ambulance'],
                'Alarm': ['engine','ladder'],
                'Vehicle Accident': ['engine','rescue','ambulance'],
                'Brush Fire': ['brush','tanker']
            },
            'response_profiles': {
                'Structure Fire': {'slots':[{'unit_type':'engine','count':2},{'unit_type':'ladder','count':1},{'unit_type':'rescue','count':1},{'unit_type':'chief','count':1}], 'response_mode':'emergency'},
                'Vehicle Fire': {'slots':[{'unit_type':'engine','count':1},{'unit_type':'brush','count':1},{'unit_type':'tanker','count':1}], 'response_mode':'emergency'},
                'Medical Assist': {'slots':[{'unit_type':'rescue','count':1},{'unit_type':'ambulance','count':1}], 'response_mode':'emergency'},
                'Alarm': {'unit_types':['engine','ladder'], 'min_units':1, 'max_units':2, 'response_mode':'routine'},
                'Vehicle Accident': {'slots':[{'unit_type':'engine','count':1},{'unit_type':'rescue','count':1},{'unit_type':'ambulance','count':1}], 'response_mode':'emergency'},
                'Brush Fire': {'slots':[{'unit_type':'brush','count':1},{'unit_type':'tanker','count':1}], 'response_mode':'emergency'}
            },
            'dispositions': ['Extinguished','Controlled','Under Control','False Alarm','No Fire','Cancelled']
        },
        'ems': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'AK','label':'Dispatched'},{'code':'ER','label':'En Route'},{'code':'OS','label':'On Scene'},{'code':'TR','label':'Transporting'},{'code':'TH','label':'Transporting to HEMS'},{'code':'AD','label':'Arrived at Destination'},{'code':'CBY_CALLER','label':'Cancelled by Caller'},{'code':'CBY_OTHER','label':'Cancelled by Other Agency'},{'code':'CBY_DISPATCH','label':'Cancelled by Dispatch'},{'code':'NO_TRANSPORT','label':'No Transport'},{'code':'PATIENT_REFUSAL','label':'Patient Refusal'},{'code':'OOS','label':'Out of Service'}],
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
                {'priority':1,'label':'EMERGENT','target_seconds':180},
                {'priority':2,'label':'Urgent','target_seconds':420},
                {'priority':3,'label':'Non-Emergent','target_seconds':720},
                {'priority':4,'label':'Scheduled','target_seconds':1200},
                {'priority':5,'label':'Standby','target_seconds':1800}
            ],
            'response_plans': {
                'Cardiac Arrest': ['medic','supervisor'],
                'Chest Pain': ['medic','ambulance'],
                'Respiratory': ['medic','ambulance'],
                'Fall': ['ambulance','medic'],
                'Motor Vehicle Accident': ['air','ambulance','medic','supervisor'],
                'Overdose': ['ambulance','medic']
            },
            'response_profiles': {
                'Cardiac Arrest': {'slots':[{'unit_type':'medic','count':1},{'unit_type':'supervisor','count':1}], 'service_level':'ALS', 'response_mode':'emergency', 'equipment':['defibrillator','monitor']},
                'Chest Pain': {'slots':[{'unit_type':'ambulance','count':1}], 'service_level':'ALS', 'response_mode':'emergency', 'equipment':['monitor','defibrillator']},
                'Respiratory': {'slots':[{'unit_type':'ambulance','count':1}], 'service_level':'ALS', 'response_mode':'emergency'},
                'Fall': {'slots':[{'unit_type':'ambulance','count':1}], 'service_level':'BLS', 'response_mode':'emergency'},
                'Motor Vehicle Accident': {'slots':[{'unit_type':'air','count':1},{'unit_type':'ambulance','count':1},{'unit_type':'medic','count':1}], 'service_level':'ALS', 'response_mode':'emergency'},
                'Overdose': {'slots':[{'unit_type':'ambulance','count':1}], 'service_level':'ALS', 'response_mode':'emergency'}
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
        now = tz_now()
        u = Unit(name=call_sign, call_sign=call_sign, agency_id=agency_id, unit_type=unit_type, capabilities=capabilities, lat=lat, lng=lng, taip_id=taip_id, in_service_at=now, last_seen_at=now, current_status='AQ', current_incident_id=None, accumulated_call_seconds=0)
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
        u2.current_incident_id = inc.id; u2.current_status = 'AK'; u2.last_assigned_at = tz_now()
        db.add(StatusEvent(unit_id=u2.id, incident_id=inc.id, status_code='AK', reason='Dispatched to incident'))
        inc.status = 'dispatched'
    if not db.query(IncidentUnit).filter_by(incident_id=inc.id, unit_id=u3.id).first():
        db.add(IncidentUnit(incident_id=inc.id, unit_id=u3.id))
        u3.current_incident_id = inc.id; u3.current_status = 'AK'; u3.last_assigned_at = tz_now()
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

def _agency_customer_id(db, agency_id, customer_id):
    if customer_id:
        return customer_id
    if agency_id:
        agency = db.query(Agency).get(agency_id)
        return agency.customer_id if agency else None
    return None

def _coerce_int(v, default=None):
    try:
        if v is None or str(v).strip() == '':
            return default
        return int(float(v))
    except Exception:
        return default

def _coerce_float(v, default=None):
    try:
        if v is None or str(v).strip() == '':
            return default
        return float(v)
    except Exception:
        return default

def _coerce_bool(v, default=False):
    if v is None or str(v).strip() == '':
        return default
    return str(v).lower() in ('true','yes','1','y','on')

def _parse_fields(v):
    if not v:
        return []
    if isinstance(v, list):
        return v
    s = str(v).strip()
    if s.startswith('['):
        try:
            return json.loads(s)
        except Exception:
            pass
    return [x.strip() for x in s.split(',') if x.strip()]

def _resolve_agency(db, customer_id, value):
    if not value:
        return None
    if isinstance(value, int):
        return db.query(Agency).filter(Agency.customer_id == customer_id, Agency.id == value).first()
    if str(value).isdigit():
        return db.query(Agency).filter(Agency.customer_id == customer_id, Agency.id == int(value)).first()
    v = str(value).strip()
    return db.query(Agency).filter(Agency.customer_id == customer_id, or_(Agency.name == v, Agency.domain == v)).first()

def _resolve_unit_by_call_sign(db, customer_id, value):
    if not value:
        return None
    return db.query(Unit).filter(Unit.customer_id == customer_id, Unit.call_sign == str(value).strip()).first()

def _ensure_customer_config(db, customer_id, agency_id, category, value):
    cfg = db.query(CustomerConfig).filter_by(customer_id=customer_id, agency_id=agency_id, category=category, key='defaults').first()
    if cfg:
        cfg.value = value
        cfg.updated_at = tz_now()
    else:
        cfg = CustomerConfig(customer_id=customer_id, agency_id=agency_id, category=category, key='defaults', value=value)
        db.add(cfg)
    return cfg

def _build_csv_template(rows: List[dict]) -> str:
    import csv, io
    if not rows:
        return ''
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=rows[0].keys())
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return out.getvalue()

CSV_TEMPLATES = {
    'agencies': _build_csv_template([
        {'name':'Metro Police','agency_type':'police','city':'Columbus','state':'OH','domain':'police.example.com'},
        {'name':'Metro Fire','agency_type':'fire','city':'Columbus','state':'OH','domain':'fire.example.com'},
    ]),
    'units': _build_csv_template([
        {'agency':'Metro Police','call_sign':'A12','name':'Adam-12','unit_type':'patrol','lat':'39.9608','lng':'-82.998','taip_id':'A12','taip_destination_url':'','taip_port':'','camera_url':''},
        {'agency':'Metro Fire','call_sign':'E1','name':'Engine 1','unit_type':'engine','lat':'39.959','lng':'-83.0','taip_id':'','taip_destination_url':'','taip_port':'','camera_url':''},
    ]),
    'crew-members': _build_csv_template([
        {'agency':'Metro Police','first_name':'John','last_name':'Doe','email':'john.doe@example.com','phone':'+16145551001','sms_phone':'+16145551001','unit':'A12','duty_status':'on_duty','provider_level':'officer','radio_id':'RP001'},
        {'agency':'County EMS','first_name':'Sarah','last_name':'Lee','email':'sarah.lee@example.com','phone':'+16145551004','sms_phone':'+16145551004','unit':'M1','duty_status':'on_duty','provider_level':'paramedic','radio_id':'MED01'},
    ]),
    'personnel': _build_csv_template([
        {'agency':'Metro Police','first_name':'John','last_name':'Doe','email':'john.doe@example.com','phone':'+16145551001','sms_phone':'+16145551001','unit':'A12','duty_status':'on_duty','provider_level':'officer','radio_id':'RP001'},
    ]),
    'incidents': _build_csv_template([
        {'agency':'Metro Police','call_number':'2024-00001','call_type':'Traffic Accident','priority':'2','location_text':'Main St & 1st Ave','lat':'39.961','lng':'-82.999','status':'open','caller_name':'Jane Smith','callback':'+16145551000','narrative':'Two vehicle accident'},
    ]),
    'map-layers': _build_csv_template([
        {'agency_id':'1','name':'Hydrant 1','type':'hydrant','lat':'39.961','lng':'-82.999','geojson':''},
    ]),
    'locations': _build_csv_template([
        {'agency':'Metro Police','name':'Police Headquarters','location_type':'station','address':'1200 Patrol Rd','lat':'39.961','lng':'-82.998','notes':'Admin building'},
        {'agency':'Metro Police','name':'Courthouse','location_type':'address','address':'345 Justice Blvd','lat':'39.960','lng':'-82.997','notes':'Frequent pickup'},
    ]),
    'stations': _build_csv_template([
        {'agency':'Metro Fire','name':'Station 1','address':'1000 Firehouse Ln','lat':'39.959','lng':'-83.001','notes':'Headquarters'},
        {'agency':'County EMS','name':'Medic Station','address':'2000 EMS Way','lat':'39.962','lng':'-82.996','notes':'Bays 1-4'},
    ]),
    'zones': _build_csv_template([
        {'agency':'Metro Police','name':'North Patrol','zone_type':'post','color':'#3b82f6','geojson':'{"type":"Polygon","coordinates":[[[-83.0,39.96],[-82.99,39.96],[-82.99,39.965],[-83.0,39.965],[-83.0,39.96]]]}','display_order':'0','minimum_units':'1','is_active':'true'},
    ]),
    'call-types': _build_csv_template([
        {'agency':'Metro Police','label':'Traffic Accident','priority':'2','fields':'vehicles,injuries'},
        {'agency':'Metro Police','label':'Domestic','priority':'2','fields':'weapons,children'},
    ]),
    'ems-call-types': _build_csv_template([
        {'agency':'County EMS','label':'Cardiac Arrest','priority':'1','fields':'age,conscious'},
        {'agency':'County EMS','label':'Fall','priority':'2','fields':'age,conscious'},
    ]),
    'ems-chief-complaints': _build_csv_template([
        {'agency':'County EMS','label':'Chest Pain','priority':'1','fields':'age,conscious'},
        {'agency':'County EMS','label':'Respiratory Distress','priority':'1','fields':'age,conscious'},
    ]),
}

@app.get('/import/template/{name}')
def download_template(name: str):
    name = name.replace('.csv','')
    template = CSV_TEMPLATES.get(name)
    if not template:
        raise HTTPException(status_code=404, detail='Template not found')
    return Response(template, media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="{name}.csv"'})

@app.post('/import/{entity}', response_model=dict)
def import_csv(entity: str, file: UploadFile = File(...), current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    import csv, io
    if entity == 'crew-members':
        entity = 'personnel'
    valid_entities = ('agencies','units','personnel','incidents','map-layers','locations','stations','zones','call-types','ems-call-types','ems-chief-complaints')
    if entity not in valid_entities:
        raise HTTPException(status_code=400, detail=f'Entity must be one of {valid_entities}')
    content = file.file.read().decode('utf-8')
    reader = csv.DictReader(io.StringIO(content))
    count = 0; errors = []
    default_customer_id = current_user.get('customer_id')
    if not default_customer_id:
        raise HTTPException(status_code=403, detail='Customer not set')

    if entity == 'map-layers':
        layers = []
        for idx, row in enumerate(reader, start=1):
            try:
                geojson = None
                if row.get('geojson'):
                    try: geojson = json.loads(row['geojson'])
                    except Exception: pass
                layers.append({'name': row['name'], 'type': row.get('type', 'hydrant'), 'lat': _coerce_float(row.get('lat')), 'lng': _coerce_float(row.get('lng')), 'agency_id': _coerce_int(row.get('agency_id')), 'geojson': geojson})
            except Exception as e:
                errors.append(f'row {idx}: {e}')
        if layers:
            existing = db.query(CustomerConfig).filter_by(customer_id=default_customer_id, category='map_layers', key='all').first()
            if existing:
                existing.value = (existing.value or []) + layers
            else:
                db.add(CustomerConfig(customer_id=default_customer_id, category='map_layers', key='all', value=layers))
            db.commit()
            _log_event(db, 'csv_imported', 'system', 0, user_id=current_user.get('user_id'), data={'entity': 'map-layers', 'imported': len(layers), 'errors': len(errors)}, agency_id=None)
        return {'imported': len(layers), 'errors': errors[:10]}

    if entity in ('call-types','ems-call-types','ems-chief-complaints'):
        category = {'call-types':'call_types','ems-call-types':'ems_call_types','ems-chief-complaints':'ems_chief_complaints'}[entity]
        groups = {}
        for idx, row in enumerate(reader, start=1):
            try:
                agency = _resolve_agency(db, default_customer_id, row.get('agency') or row.get('agency_id'))
                agency_id = agency.id if agency else None
                customer_id = agency.customer_id if agency else default_customer_id
                group = (customer_id, agency_id)
                groups.setdefault(group, [])
                item = {'label': row.get('label') or row.get('name'), 'priority': _coerce_int(row.get('priority'), 2), 'fields': _parse_fields(row.get('fields'))}
                if not item['label']:
                    raise ValueError('label is required')
                groups[group].append(item)
                count += 1
            except Exception as e:
                errors.append(f'row {idx}: {e}')
        for (customer_id, agency_id), items in groups.items():
            _ensure_customer_config(db, customer_id, agency_id, category, items)
        db.commit()
        _log_event(db, 'csv_imported', 'system', 0, user_id=current_user.get('user_id'), data={'entity': entity, 'imported': count, 'errors': len(errors)}, agency_id=None)
        return {'imported': count, 'errors': errors[:10]}

    for idx, row in enumerate(reader, start=1):
        try:
            if entity == 'agencies':
                domain = row.get('domain')
                name = row['name']
                a = None
                if domain:
                    a = db.query(Agency).filter_by(customer_id=default_customer_id, domain=domain).first()
                if not a and name:
                    a = db.query(Agency).filter_by(customer_id=default_customer_id, name=name).first()
                fields = {
                    'customer_id': default_customer_id,
                    'name': name,
                    'agency_type': row.get('agency_type','fire'),
                    'city': row.get('city'),
                    'state': row.get('state'),
                    'domain': domain,
                    'address': row.get('address'),
                }
                if a:
                    for k,v in fields.items():
                        if v is not None:
                            setattr(a,k,v)
                else:
                    db.add(Agency(**fields))
            elif entity == 'units':
                agency = _resolve_agency(db, default_customer_id, row.get('agency') or row.get('agency_id'))
                if not agency:
                    raise ValueError('agency not found')
                call_sign = row.get('call_sign')
                if not call_sign:
                    raise ValueError('call_sign is required')
                unit = db.query(Unit).filter(Unit.customer_id == default_customer_id, Unit.call_sign == call_sign).first()
                fields = {
                    'customer_id': agency.customer_id,
                    'agency_id': agency.id,
                    'name': row.get('name') or call_sign,
                    'call_sign': call_sign,
                    'unit_type': row.get('unit_type','patrol'),
                    'lat': _coerce_float(row.get('lat')),
                    'lng': _coerce_float(row.get('lng')),
                    'taip_id': row.get('taip_id'),
                    'taip_destination_url': row.get('taip_destination_url'),
                    'taip_port': _coerce_int(row.get('taip_port')),
                    'camera_url': row.get('camera_url'),
                    'current_status': 'AQ',
                    'in_service_at': tz_now(),
                    'accumulated_call_seconds': 0,
                }
                if unit:
                    for k,v in fields.items():
                        if k in ('current_status','in_service_at','accumulated_call_seconds'):
                            continue
                        if v is not None:
                            setattr(unit,k,v)
                else:
                    db.add(Unit(**fields))
            elif entity == 'personnel':
                agency = _resolve_agency(db, default_customer_id, row.get('agency') or row.get('agency_id'))
                if not agency:
                    raise ValueError('agency not found')
                first_name = row.get('first_name')
                last_name = row.get('last_name')
                if not first_name or not last_name:
                    raise ValueError('first_name and last_name are required')
                email = row.get('email')
                unit = _resolve_unit_by_call_sign(db, default_customer_id, row.get('unit'))
                current_unit_id = _coerce_int(row.get('current_unit_id')) or (unit.id if unit else None)
                p = None
                if email:
                    p = db.query(Personnel).filter(Personnel.customer_id == default_customer_id, Personnel.email == email).first()
                if not p:
                    p = db.query(Personnel).filter(Personnel.customer_id == default_customer_id, Personnel.first_name == first_name, Personnel.last_name == last_name, Personnel.agency_id == agency.id).first()
                fields = {
                    'customer_id': agency.customer_id,
                    'agency_id': agency.id,
                    'first_name': first_name,
                    'last_name': last_name,
                    'email': email,
                    'phone': row.get('phone'),
                    'sms_phone': row.get('sms_phone'),
                    'radio_id': row.get('radio_id'),
                    'provider_level': row.get('provider_level'),
                    'current_unit_id': current_unit_id,
                    'duty_status': row.get('duty_status','off_duty'),
                }
                if p:
                    for k,v in fields.items():
                        if v is not None:
                            setattr(p,k,v)
                else:
                    db.add(Personnel(**fields))
            elif entity in ('locations','stations'):
                agency = _resolve_agency(db, default_customer_id, row.get('agency') or row.get('agency_id'))
                if not agency:
                    raise ValueError('agency not found')
                name = row.get('name')
                if not name:
                    raise ValueError('name is required')
                location_type = row.get('location_type','address') if entity == 'locations' else 'station'
                loc = db.query(Location).filter(Location.customer_id == agency.customer_id, Location.agency_id == agency.id, Location.name == name, Location.location_type == location_type).first()
                fields = {
                    'customer_id': agency.customer_id,
                    'agency_id': agency.id,
                    'name': name,
                    'location_type': location_type,
                    'address': row.get('address'),
                    'lat': _coerce_float(row.get('lat')),
                    'lng': _coerce_float(row.get('lng')),
                    'notes': row.get('notes'),
                }
                if loc:
                    for k,v in fields.items():
                        if v is not None:
                            setattr(loc,k,v)
                else:
                    db.add(Location(**fields))
            elif entity == 'zones':
                agency = _resolve_agency(db, default_customer_id, row.get('agency') or row.get('agency_id'))
                if not agency:
                    raise ValueError('agency not found')
                name = row.get('name')
                if not name:
                    raise ValueError('name is required')
                geojson = None
                if row.get('geojson'):
                    try: geojson = json.loads(row['geojson'])
                    except: pass
                pz = db.query(PostZone).filter(PostZone.customer_id == default_customer_id, PostZone.agency_id == agency.id, PostZone.name == name).first()
                fields = {
                    'customer_id': default_customer_id,
                    'agency_id': agency.id,
                    'name': name,
                    'zone_type': row.get('zone_type','post'),
                    'color': row.get('color','#3b82f6'),
                    'geojson': geojson,
                    'display_order': _coerce_int(row.get('display_order'),0),
                    'minimum_units': _coerce_int(row.get('minimum_units'),0),
                    'is_active': _coerce_bool(row.get('is_active'),True),
                }
                if pz:
                    for k,v in fields.items():
                        if v is not None:
                            setattr(pz,k,v)
                else:
                    db.add(PostZone(**fields))
            elif entity == 'incidents':
                agency = _resolve_agency(db, default_customer_id, row.get('agency') or row.get('agency_id'))
                if not agency:
                    raise ValueError('agency not found')
                inc_count = db.query(Incident).filter(Incident.customer_id == agency.customer_id, Incident.agency_id == agency.id).count()
                inc = Incident(
                    customer_id=agency.customer_id,
                    agency_id=agency.id,
                    call_number=row.get('call_number') or f"{agency.id}-{inc_count+1:05d}",
                    incident_number=row.get('incident_number') or f"{agency.id}-{inc_count+1:05d}",
                    call_type=row.get('call_type','Unknown'),
                    priority=_coerce_int(row.get('priority'),2),
                    location_text=row.get('location_text'),
                    lat=_coerce_float(row.get('lat')),
                    lng=_coerce_float(row.get('lng')),
                    status=row.get('status','open'),
                    caller_name=row.get('caller_name'),
                    callback=row.get('callback'),
                    narrative=row.get('narrative'),
                )
                db.add(inc)
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
    customer_id = current_user.get('customer_id')
    q = db.query(User)
    if customer_id:
        q = q.filter(User.customer_id == customer_id)
    return q.order_by(User.created_at.desc()).all()

@app.post('/users', response_model=UserOut)
def create_user(body: UserCreate, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail='Email already exists')
    customer_id = body.customer_id or current_user.get('customer_id')
    if not customer_id and body.agency_id:
        agency = db.query(Agency).get(body.agency_id)
        customer_id = agency.customer_id if agency else None
    u = User(email=body.email, customer_id=customer_id, hashed_password=hash_password(body.password), first_name=body.first_name, last_name=body.last_name, role=body.role, agency_id=body.agency_id, is_active=True)
    db.add(u); db.commit(); db.refresh(u)
    _log_event(db, 'user_created', 'system', 0, user_id=current_user.get('user_id'), data={'new_user': u.id, 'role': u.role}, agency_id=body.agency_id)
    return u

@app.put('/users/{user_id}', response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.query(User).get(user_id)
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    customer_id = current_user.get('customer_id')
    if customer_id and u.customer_id is not None and u.customer_id != customer_id:
        raise HTTPException(status_code=404, detail='User not found')
    data = body.model_dump(exclude_unset=True)
    if 'password' in data:
        u.hashed_password = hash_password(data.pop('password'))
    for k, v in data.items():
        setattr(u, k, v)
    if body.agency_id:
        agency = db.query(Agency).get(body.agency_id)
        if agency:
            u.customer_id = agency.customer_id
    db.commit(); db.refresh(u)
    return u

@app.delete('/users/{user_id}')
def delete_user(user_id: int, current_user: dict = Depends(require_admin), db: Session = Depends(get_db)):
    customer_id = current_user.get('customer_id')
    u = db.query(User).get(user_id)
    if not u:
        raise HTTPException(status_code=404, detail='User not found')
    if customer_id and u.customer_id is not None and u.customer_id != customer_id:
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

def _check_transport_conflicts(db, st, window_minutes=60, exclude_id=None):
    if not st.scheduled_at:
        return []
    window = timedelta(minutes=window_minutes)
    start = st.scheduled_at - window
    end = st.scheduled_at + window
    q = db.query(ScheduledTransport).filter(
        ScheduledTransport.id != st.id,
        ScheduledTransport.id != (exclude_id or -1),
        ScheduledTransport.status != 'cancelled',
        ScheduledTransport.scheduled_at >= start,
        ScheduledTransport.scheduled_at <= end
    )
    if st.agency_id:
        q = q.filter(ScheduledTransport.agency_id == st.agency_id)
    conflicts = []
    for other in q.all():
        if (st.unit_id and other.unit_id == st.unit_id) or (st.patient_name and other.patient_name and st.patient_name.lower() == other.patient_name.lower()):
            conflicts.append({'id': other.id, 'scheduled_at': other.scheduled_at, 'patient_name': other.patient_name, 'unit_id': other.unit_id, 'reason': 'time + resource overlap'})
    return conflicts

def _transport_warnings(db, agency_id=None):
    now = tz_now()
    soon = now + timedelta(minutes=30)
    q = db.query(ScheduledTransport).filter(ScheduledTransport.status.in_(['scheduled','assigned']))
    if agency_id:
        q = q.filter(ScheduledTransport.agency_id == agency_id)
    warnings = []
    for st in q.all():
        if not st.scheduled_at:
            continue
        if st.scheduled_at < now:
            warnings.append({'id': st.id, 'scheduled_at': st.scheduled_at, 'patient_name': st.patient_name, 'unit_id': st.unit_id, 'type': 'late', 'reason': 'scheduled time has passed'})
        elif st.scheduled_at <= soon:
            warnings.append({'id': st.id, 'scheduled_at': st.scheduled_at, 'patient_name': st.patient_name, 'unit_id': st.unit_id, 'type': 'soon', 'reason': 'scheduled within 30 minutes'})
    return warnings

@app.get('/scheduled-transports', response_model=List[ScheduledTransportOut])
def list_scheduled_transports(status: Optional[str] = None, agency_id: Optional[int] = None, date: Optional[date] = Query(None), current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(ScheduledTransport)
    if agency_id:
        q = q.filter(ScheduledTransport.agency_id == agency_id)
    if status:
        q = q.filter(ScheduledTransport.status == status)
    if date:
        start = datetime.combine(date, dt_time.min)
        end = datetime.combine(date, dt_time.max)
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

@app.get('/scheduled-transports/{st_id}/conflicts')
def get_scheduled_transport_conflicts(st_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    st = db.query(ScheduledTransport).get(st_id)
    if not st:
        raise HTTPException(status_code=404, detail='Scheduled transport not found')
    return _check_transport_conflicts(db, st)

@app.get('/scheduled-transports/warnings')
def get_scheduled_transport_warnings(agency_id: Optional[int] = Query(None), current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return _transport_warnings(db, agency_id)

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
    unit.last_assigned_at = tz_now()
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
def list_scheduled_events(status: Optional[str] = None, agency_id: Optional[int] = None, date: Optional[date] = Query(None), window_minutes: Optional[int] = Query(None), current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(ScheduledEvent)
    if agency_id:
        q = q.filter(ScheduledEvent.agency_id == agency_id)
    if status:
        q = q.filter(ScheduledEvent.status == status)
    if date:
        start = datetime.combine(date, dt_time.min)
        end = datetime.combine(date, dt_time.max)
        q = q.filter(ScheduledEvent.scheduled_at >= start, ScheduledEvent.scheduled_at <= end)
    if window_minutes is not None:
        q = q.filter(ScheduledEvent.scheduled_at <= tz_now() + timedelta(minutes=window_minutes))
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

@app.post('/scheduled-events/{se_id}/dispatch')
def dispatch_scheduled_event(request: Request, se_id: int, body: ScheduledEventDispatch, db: Session = Depends(get_db)):
    check_role(request, DISPATCHER_ROLES)
    se = db.query(ScheduledEvent).get(se_id)
    if not se:
        raise HTTPException(status_code=404, detail='Scheduled event not found')
    unit = db.query(Unit).get(body.unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    user = get_current_user(request)
    agency = db.query(Agency).get(se.agency_id)
    customer_id = agency.customer_id if agency else user.get('customer_id')
    # create incident from scheduled event
    count = db.query(Incident).filter(Incident.customer_id == customer_id, Incident.agency_id == se.agency_id).count()
    incident_number = f"{se.agency_id}-{count + 1:05d}"
    incident = Incident(
        customer_id=customer_id,
        agency_id=se.agency_id,
        incident_number=incident_number,
        call_number=incident_number,
        call_type=se.event_type or 'Scheduled Event',
        priority=2,
        status='open',
        location_text=se.location_text,
        lat=se.lat,
        lng=se.lng,
        narrative=se.notes,
        extra={'scheduled_event_id': se.id, 'event_type': se.event_type, 'source': 'scheduled_event'},
        created_by=user.get('user_id')
    )
    db.add(incident)
    db.flush()
    _validate_incident_location(db, incident)
    if incident.lat is None or incident.lng is None:
        if agency and agency.lat is not None and agency.lng is not None:
            incident.lat = agency.lat
            incident.lng = agency.lng
    iu = _dispatch_one_unit(db, incident, unit, user, notes='Dispatched from scheduled event', reassign=True)
    db.flush()
    se.status = 'in_progress'
    se.unit_id = unit.id
    db.commit()
    db.refresh(incident)
    _log_event(db, 'scheduled_event_dispatched', 'scheduled_event', se.id, user_id=user.get('user_id'), data={'incident_id': incident.id, 'unit_id': unit.id}, agency_id=se.agency_id)
    return {
        'incident_id': incident.id,
        'incident_number': incident.incident_number,
        'call_number': incident.call_number,
        'unit_id': unit.id,
        'assigned_at': iu.assigned_at,
        'assignment_status': iu.assignment_status
    }

def _generate_standing_order_instances(db, standing_order, start_date, end_date):
    if not standing_order.active or not standing_order.recurrence or not start_date or not end_date:
        return []
    rec = standing_order.recurrence
    freq = rec.get('frequency','daily')
    interval = max(1, rec.get('interval',1))
    time_str = rec.get('time','08:00')
    try:
        hour, minute = map(int, (time_str or '08:00').split(':'))
    except ValueError:
        hour, minute = 8, 0
    days_of_week = rec.get('days_of_week') or []
    day_of_month = rec.get('day_of_month')
    created = []
    cur = start_date
    import calendar
    while cur <= end_date:
        match = False
        if freq == 'daily':
            delta = (cur - start_date).days
            match = (delta % interval == 0)
        elif freq == 'weekly':
            if not days_of_week or cur.weekday() in days_of_week:
                weeks = (cur - start_date).days // 7
                match = (weeks % interval == 0)
        elif freq == 'monthly':
            months = (cur.year - start_date.year) * 12 + (cur.month - start_date.month)
            if months % interval == 0:
                if day_of_month is None:
                    match = cur.day == start_date.day
                else:
                    last_day = calendar.monthrange(cur.year, cur.month)[1]
                    match = cur.day == min(day_of_month, last_day)
        if match:
            scheduled_at = datetime.combine(cur, time(hour, minute))
            existing = db.query(ScheduledTransport).filter_by(agency_id=standing_order.agency_id, patient_name=standing_order.patient_name, scheduled_at=scheduled_at).first()
            if not existing:
                st = ScheduledTransport(
                    agency_id=standing_order.agency_id,
                    patient_name=standing_order.patient_name,
                    pickup_address=standing_order.pickup_address,
                    pickup_lat=standing_order.pickup_lat,
                    pickup_lng=standing_order.pickup_lng,
                    destination_id=standing_order.destination_id,
                    destination_name=standing_order.destination_name,
                    destination_address=standing_order.destination_address,
                    destination_lat=standing_order.destination_lat,
                    destination_lng=standing_order.destination_lng,
                    call_type=standing_order.call_type,
                    service_level=standing_order.service_level,
                    mobility_level=standing_order.mobility_level,
                    oxygen=standing_order.oxygen,
                    isolation=standing_order.isolation,
                    stretcher=standing_order.stretcher,
                    wheelchair=standing_order.wheelchair,
                    special_equipment=standing_order.special_equipment,
                    notes=standing_order.notes,
                    scheduled_at=scheduled_at,
                    status='scheduled'
                )
                db.add(st); db.flush(); created.append(st)
        if freq == 'monthly':
            year = cur.year + (cur.month // 12)
            month = (cur.month % 12) + 1
            last_day = calendar.monthrange(year, month)[1]
            day = min(day_of_month or cur.day, last_day)
            cur = date(year, month, day)
        else:
            cur += timedelta(days=1)
    return created

@app.get('/standing-orders-page')
def standing_orders_page():
    return FileResponse('static/standing_orders.html')

@app.get('/standing-orders', response_model=List[StandingOrderOut])
def list_standing_orders(agency_id: Optional[int] = None, active: Optional[bool] = None, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(StandingOrder)
    if agency_id:
        q = q.filter(StandingOrder.agency_id == agency_id)
    if active is not None:
        q = q.filter(StandingOrder.active == active)
    return q.order_by(StandingOrder.created_at.desc()).all()

@app.post('/standing-orders', response_model=StandingOrderOut)
def create_standing_order(body: StandingOrderCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    so = StandingOrder(**body.dict())
    db.add(so); db.commit(); db.refresh(so)
    _log_event(db, 'standing_order_created', 'standing_order', so.id, user_id=current_user.get('user_id'), data=body.dict(), agency_id=so.agency_id)
    return so

@app.get('/standing-orders/{so_id}', response_model=StandingOrderOut)
def get_standing_order(so_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    so = db.query(StandingOrder).get(so_id)
    if not so:
        raise HTTPException(status_code=404, detail='Standing order not found')
    return so

@app.put('/standing-orders/{so_id}', response_model=StandingOrderOut)
def update_standing_order(so_id: int, body: StandingOrderUpdate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    so = db.query(StandingOrder).get(so_id)
    if not so:
        raise HTTPException(status_code=404, detail='Standing order not found')
    for k, v in body.dict(exclude_unset=True).items():
        setattr(so, k, v)
    db.commit(); db.refresh(so)
    _log_event(db, 'standing_order_updated', 'standing_order', so.id, user_id=current_user.get('user_id'), data=body.dict(exclude_unset=True), agency_id=so.agency_id)
    return so

@app.delete('/standing-orders/{so_id}')
def delete_standing_order(so_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    so = db.query(StandingOrder).get(so_id)
    if not so:
        raise HTTPException(status_code=404, detail='Standing order not found')
    db.delete(so); db.commit()
    _log_event(db, 'standing_order_deleted', 'standing_order', so.id, user_id=current_user.get('user_id'), data={}, agency_id=so.agency_id)
    return {'deleted': so_id}

@app.post('/standing-orders/{so_id}/generate')
def generate_standing_order_transports(so_id: int, body: StandingOrderGenerate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    so = db.query(StandingOrder).get(so_id)
    if not so:
        raise HTTPException(status_code=404, detail='Standing order not found')
    created = _generate_standing_order_instances(db, so, body.start_date, body.end_date)
    if created:
        db.commit()
    _log_event(db, 'standing_order_generated', 'standing_order', so.id, user_id=current_user.get('user_id'), data={'count': len(created), 'start_date': str(body.start_date), 'end_date': str(body.end_date)}, agency_id=so.agency_id)
    return {'created': len(created), 'scheduled_transport_ids': [st.id for st in created]}

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
    customer_id = current_user.get('customer_id')
    q = db.query(PostZone)
    if customer_id:
        q = q.filter(PostZone.customer_id == customer_id)
    if agency_id:
        q = q.filter(PostZone.agency_id == agency_id)
    return q.order_by(PostZone.display_order.asc(), PostZone.name.asc()).all()

@app.post('/post-zones', response_model=PostZoneOut)
def create_post_zone(body: PostZoneCreate, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    pz = PostZone(customer_id=current_user.get('customer_id'), **body.dict())
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
    customer_id = current_user.get('customer_id')
    pz = db.query(PostZone).get(pz_id)
    if not pz:
        raise HTTPException(status_code=404, detail='Post zone not found')
    if customer_id and pz.customer_id is not None and pz.customer_id != customer_id:
        raise HTTPException(status_code=404, detail='Post zone not found')
    for k, v in body.dict(exclude_unset=True).items():
        setattr(pz, k, v)
    db.commit(); db.refresh(pz)
    _log_event(db, 'post_zone_updated', 'post_zone', pz.id, user_id=current_user.get('user_id'), data=body.dict(exclude_unset=True), agency_id=pz.agency_id)
    return pz

@app.delete('/post-zones/{pz_id}', response_model=PostZoneOut)
def delete_post_zone(pz_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    customer_id = current_user.get('customer_id')
    pz = db.query(PostZone).get(pz_id)
    if not pz:
        raise HTTPException(status_code=404, detail='Post zone not found')
    if customer_id and pz.customer_id is not None and pz.customer_id != customer_id:
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
    db.query(UnitPosting).filter_by(unit_id=body.unit_id, is_current=True).update({'is_current': False, 'removed_at': tz_now()})
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
    up.removed_at = tz_now()
    db.commit(); db.refresh(up)
    _log_event(db, 'unit_posting_removed', 'unit_posting', up.id, user_id=current_user.get('user_id'), data={}, agency_id=up.unit.agency_id)
    return up

def _coverage_analysis(db, agency_id=None):
    since = tz_now() - timedelta(days=30)
    q = db.query(PostZone).filter(PostZone.is_active == True)
    if agency_id:
        q = q.filter(PostZone.agency_id == agency_id)
    zones = q.all()
    result = []
    for z in zones:
        current = db.query(UnitPosting).filter_by(post_zone_id=z.id, is_current=True).count()
        recent_incidents = db.query(IncidentLocation).filter(IncidentLocation.zone_id == z.id).join(Incident).filter(Incident.created_at >= since).count()
        gap = max(0, (z.minimum_units or 0) - current)
        need = max(gap, 0)
        score = need * 100 + recent_incidents
        result.append({
            'zone_id': z.id,
            'zone_name': z.name,
            'zone_type': z.zone_type,
            'color': z.color,
            'agency_id': z.agency_id,
            'minimum_units': z.minimum_units or 0,
            'current_units': current,
            'gap': gap,
            'recent_incidents': recent_incidents,
            'score': score
        })
    result.sort(key=lambda x: -x['score'])
    return result

@app.get('/coverage/analysis')
def get_coverage_analysis(agency_id: Optional[int] = Query(None), current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return _coverage_analysis(db, agency_id)

@app.get('/coverage/recommend-unit/{unit_id}')
def recommend_unit_posting(unit_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    if not unit.lat or not unit.lng:
        raise HTTPException(status_code=400, detail='Unit has no location')
    analysis = _coverage_analysis(db, unit.agency_id)
    if not analysis:
        raise HTTPException(status_code=404, detail='No active post zones')
    best = None
    best_score = None
    for zone in analysis:
        if zone['gap'] <= 0:
            continue
        # zone center fallback: use first coordinate of polygon or unit's current zone
        z = db.query(PostZone).get(zone['zone_id'])
        center = _zone_center(z)
        dist = _haversine_m(unit.lat, unit.lng, center[0], center[1]) if center else 0
        score = zone['score'] - (dist / 1000.0)
        if best is None or score > best_score:
            best = zone; best_score = score
    if not best:
        best = analysis[0]
    return {'recommended_zone_id': best['zone_id'], 'recommended_zone_name': best['zone_name'], 'reason': f"gap {best['gap']} / min {best['minimum_units']}, recent incidents {best['recent_incidents']}"}

def _zone_center(z: PostZone):
    if z and z.geojson and z.geojson.get('type') == 'Polygon' and z.geojson.get('coordinates'):
        coords = z.geojson['coordinates'][0]
        lats = [c[1] for c in coords]
        lngs = [c[0] for c in coords]
        return [sum(lats)/len(lats), sum(lngs)/len(lngs)]
    return None

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
            'turnaround_seconds': int((leg.cleared_at - (leg.arrived_destination_at or leg.arrived_at)).total_seconds()) if leg.cleared_at and (leg.arrived_destination_at or leg.arrived_at) else None
        })
    readings_summary = [{'unit_id': r.unit_id, 'call_sign': unit_map.get(r.unit_id), 'status_code': r.status_code, 'mileage': r.mileage, 'recorded_at': r.recorded_at.isoformat() if r.recorded_at else None} for r in readings]
    return {'incident_id': incident_id, 'total_trip_miles': round(total_miles, 1), 'legs': leg_summaries, 'readings': readings_summary}

@app.get('/transport-legs/summary')
def transport_legs_summary(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    # aggregate recent completed leg stats
    since = tz_now() - timedelta(days=7)
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
            'turnaround_seconds': int((leg.cleared_at - (leg.arrived_destination_at or leg.arrived_at)).total_seconds()) if leg.cleared_at and (leg.arrived_destination_at or leg.arrived_at) else None,
            'cleared_at': leg.cleared_at.isoformat() if leg.cleared_at else None
        })
    return rows

@app.get('/reports/summary')
def reports_summary(days: int = 7, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    since = tz_now() - timedelta(days=max(1, min(days, 90)))
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
        if leg.cleared_at and (leg.arrived_destination_at or leg.arrived_at):
            total_turnaround += int((leg.cleared_at - (leg.arrived_destination_at or leg.arrived_at)).total_seconds())
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
    since = tz_now() - timedelta(hours=max(1, min(hours, 72)))
    events = db.query(StatusEvent).filter(StatusEvent.unit_id == unit_id, StatusEvent.lat != None, StatusEvent.lng != None, StatusEvent.created_at >= since).order_by(StatusEvent.created_at.asc()).all()
    return [{'lat': e.lat, 'lng': e.lng, 'status_code': e.status_code, 'created_at': e.created_at.isoformat() if e.created_at else None} for e in events]

@app.get('/supervisor/summary')
def supervisor_summary(current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    now = tz_now()
    since = now - timedelta(days=1)
    active_incidents = db.query(Incident).filter(Incident.status != 'closed').all()
    alerts = _active_incident_alerts(db)
    stale_units = db.query(Unit).filter(Unit.stale == True, Unit.offline == False).count()
    offline_units = db.query(Unit).filter(Unit.offline == True).count()
    on_duty = db.query(Personnel).filter(Personnel.duty_status == 'on_duty').count()
    recent_events = db.query(Event).filter(Event.timestamp >= since).order_by(Event.timestamp.desc()).limit(50).all()
    avg_dispatch = db.query(func.avg(StatusEvent.created_at - Incident.created_at)).filter(
        StatusEvent.status_code.in_(['AK','dispatched']),
        StatusEvent.incident_id == Incident.id,
        Incident.created_at >= now - timedelta(days=7)
    ).first()[0]
    return {
        'active_incidents_count': len(active_incidents),
        'active_incidents': [{'id': i.id, 'call_number': i.call_number, 'call_type': i.call_type, 'status': i.status, 'priority': i.priority} for i in active_incidents],
        'alerts_count': len(alerts),
        'alerts': alerts[:10],
        'stale_units': stale_units,
        'offline_units': offline_units,
        'on_duty_personnel': on_duty,
        'recent_events': [{'event_type': e.event_type, 'entity_type': e.entity_type, 'entity_id': e.entity_id, 'timestamp': e.timestamp.isoformat() if e.timestamp else None, 'data': e.data} for e in recent_events],
        'avg_dispatch_seconds': int(avg_dispatch.total_seconds()) if avg_dispatch else None
    }

@app.get('/supervisor/playback/{incident_id}')
def supervisor_playback(incident_id: int, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    timeline = db.query(StatusEvent).filter_by(incident_id=incident_id).order_by(StatusEvent.created_at.asc()).all()
    messages = db.query(DispatchMessage).filter_by(incident_id=incident_id).order_by(DispatchMessage.created_at.asc()).all()
    notes = db.query(IncidentDestination).filter_by(incident_id=incident_id).order_by(IncidentDestination.created_at.asc()).all()
    return {
        'incident': {'id': incident.id, 'call_number': incident.call_number, 'call_type': incident.call_type, 'priority': incident.priority, 'status': incident.status, 'narrative': incident.narrative, 'created_at': incident.created_at.isoformat() if incident.created_at else None},
        'timeline': [{'at': e.created_at.isoformat(), 'status_code': e.status_code, 'reason': e.reason, 'unit_id': e.unit_id, 'lat': e.lat, 'lng': e.lng} for e in timeline],
        'messages': [{'at': m.created_at.isoformat() if m.created_at else None, 'channel': m.channel, 'message_text': m.message_text} for m in messages],
        'destinations': [{'at': n.created_at.isoformat() if n.created_at else None, 'destination_id': n.destination_id, 'notes': n.notes} for n in notes]
    }

@app.get('/billing/billable-incidents')
def billable_incidents(days: int = 30, current_user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    since = tz_now() - timedelta(days=max(1, min(days, 90)))
    # EMS transports and scheduled transports that are completed/dispatched are billable
    q = db.query(Incident).filter(Incident.created_at >= since, Incident.status.in_(['closed','cleared']))
    incidents = q.order_by(Incident.created_at.desc()).all()
    rows = []
    for i in incidents:
        legs = db.query(TransportLeg).filter_by(incident_id=i.id).all()
        epcr = db.query(EpcrExport).filter_by(incident_id=i.id).first()
        mileage = 0.0
        for leg in legs:
            if leg.pickup_mileage is not None and leg.dropoff_mileage is not None:
                mileage += max(0, leg.dropoff_mileage - leg.pickup_mileage)
        rows.append({
            'incident_id': i.id,
            'call_number': i.call_number,
            'call_type': i.call_type,
            'patient_name': i.caller_name,
            'agency_id': i.agency_id,
            'created_at': i.created_at.isoformat() if i.created_at else None,
            'mileage': round(mileage, 1),
            'transport_legs_count': len(legs),
            'epcr_exported': bool(epcr),
            'epcr_status': epcr.status if epcr else None
        })
    return rows

@app.get('/supervisor-page')
def supervisor_page():
    return FileResponse('static/supervisor.html')

@app.get('/billing-page')
def billing_page():
    return FileResponse('static/billing.html')

@app.get('/taip-verify-page')
def taip_verify_page():
    return FileResponse('static/taip_verify.html')

# Resolve forward references now that all Pydantic models are defined
for _fwd in (ScheduledTransportOut, UnitPostingOut, EpcrExportOut):
    _fwd.model_rebuild()

def _init_unit_last_seen():
    db = SessionLocal()
    try:
        for u in db.query(Unit).filter(Unit.last_seen_at == None).filter(Unit.lat != None, Unit.lng != None).all():
            u.last_seen_at = tz_now()
            db.add(u)
        db.commit()
    finally:
        db.close()

# Startup: geocode any agencies without lat/lng, init missing unit last_seen times, and start TAIP listeners
_init_unit_last_seen()
geocode_missing_agencies()
start_taip_udp_listener()
start_taip_tcp_listener()
