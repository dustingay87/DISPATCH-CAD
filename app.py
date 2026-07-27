from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import declarative_base, relationship, Session, sessionmaker
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional
import os
import re
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('SUPABASE_DB_URL', 'sqlite:///./volcad.db')

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
    location_text = Column(Text)
    lat = Column(Float)
    lng = Column(Float)
    caller_name = Column(String(255))
    callback = Column(String(50))
    narrative = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
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
    notes = Column(Text)
    incident = relationship('Incident', back_populates='assigned_units')
    unit = relationship('Unit')

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

if DATABASE_URL.startswith('sqlite'):
    Base.metadata.create_all(bind=engine)

app = FastAPI(title='VolCAD Prototype', version='0.1.0')

app.mount('/static', StaticFiles(directory='static'), name='static')

@app.get('/')
def index():
    return FileResponse('static/index.html')

@app.get('/health')
def health():
    return {'status': 'ok'}

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Pydantic schemas
class AgencyCreate(BaseModel):
    name: str
    agency_type: str = 'fire'
    domain: Optional[str] = None

class AgencyOut(AgencyCreate):
    id: int
    approved: bool
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class UnitCreate(BaseModel):
    agency_id: int
    name: str
    call_sign: str
    unit_type: str = 'engine'
    radio_id: Optional[str] = None
    taip_id: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

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
    taip_id: Optional[str] = None
    class Config:
        from_attributes = True

class PersonnelCreate(BaseModel):
    agency_id: int
    first_name: str
    last_name: str
    radio_id: Optional[str] = None
    duty_status: str = 'off_duty'

class PersonnelOut(PersonnelCreate):
    id: int
    current_unit_id: Optional[int] = None
    created_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class IncidentCreate(BaseModel):
    agency_id: int
    incident_number: Optional[str] = None
    call_type: str
    priority: int = 2
    location_text: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    caller_name: Optional[str] = None
    callback: Optional[str] = None
    narrative: Optional[str] = None

class IncidentOut(IncidentCreate):
    id: int
    status: str
    created_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    class Config:
        from_attributes = True

class StatusUpdate(BaseModel):
    status_code: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    reason: Optional[str] = None

class TaipIngest(BaseModel):
    raw: str
    taip_id: Optional[str] = None

# TAIP parser

def parse_taip(raw: str) -> dict:
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

def map_status(code: str) -> str:
    return {
        'AQ': 'assigned', 'AK': 'assigned',
        'ER': 'en_route', 'OS': 'on_scene',
        'TR': 'transport', 'ED': 'transport',
        'CAN': 'clear'
    }.get(code, code)

# Endpoints

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.post('/agencies', response_model=AgencyOut)
def create_agency(body: AgencyCreate, db: Session = Depends(get_db)):
    agency = Agency(**body.model_dump())
    db.add(agency)
    db.commit()
    db.refresh(agency)
    return agency

@app.get('/agencies', response_model=List[AgencyOut])
def list_agencies(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Agency).offset(skip).limit(limit).all()

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

@app.post('/personnel', response_model=PersonnelOut)
def create_personnel(body: PersonnelCreate, db: Session = Depends(get_db)):
    p = Personnel(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p

@app.get('/personnel', response_model=List[PersonnelOut])
def list_personnel(agency_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    q = db.query(Personnel)
    if agency_id:
        q = q.filter(Personnel.agency_id == agency_id)
    return q.all()

@app.post('/incidents', response_model=IncidentOut)
def create_incident(body: IncidentCreate, db: Session = Depends(get_db)):
    data = body.model_dump()
    if not data.get('incident_number'):
        count = db.query(Incident).filter(Incident.agency_id == data['agency_id']).count()
        data['incident_number'] = f"{data['agency_id']}-{count + 1:05d}"
    incident = Incident(**data)
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident

@app.get('/incidents', response_model=List[IncidentOut])
def list_incidents(agency_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    q = db.query(Incident)
    if agency_id:
        q = q.filter(Incident.agency_id == agency_id)
    return q.order_by(Incident.created_at.desc()).all()

@app.post('/incidents/{incident_id}/dispatch/{unit_id}')
def dispatch_unit(incident_id: int, unit_id: int, notes: Optional[str] = None, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    unit = db.query(Unit).get(unit_id)
    if not incident or not unit:
        raise HTTPException(status_code=404, detail='Incident or unit not found')
    if unit.agency_id != incident.agency_id:
        raise HTTPException(status_code=400, detail='Unit and incident must belong to same agency')
    existing = db.query(IncidentUnit).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    if existing:
        raise HTTPException(status_code=400, detail='Unit already dispatched to incident')
    iu = IncidentUnit(incident_id=incident_id, unit_id=unit_id, notes=notes)
    unit.current_incident_id = incident_id
    unit.current_status = 'AK'
    incident.status = 'dispatched'
    db.add(iu)
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
def update_unit_status(incident_id: int, unit_id: int, body: StatusUpdate, db: Session = Depends(get_db)):
    iu = db.query(IncidentUnit).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    if not iu:
        raise HTTPException(status_code=404, detail='Unit not assigned to incident')
    unit = db.query(Unit).get(unit_id)
    iu.assignment_status = map_status(body.status_code)
    if body.status_code == 'CAN':
        iu.cleared_at = datetime.utcnow()
        unit.current_incident_id = None
        unit.current_status = 'AQ'
    else:
        unit.current_status = body.status_code
        unit.current_incident_id = incident_id
    incident = db.query(Incident).get(incident_id)
    if body.status_code == 'OS' and incident.status in ('open', 'dispatched', 'en_route'):
        incident.status = 'on_scene'
    elif body.status_code == 'ER' and incident.status == 'open':
        incident.status = 'en_route'
    db.add(StatusEvent(
        unit_id=unit_id,
        incident_id=incident_id,
        status_code=body.status_code,
        reason=body.reason,
        lat=body.lat,
        lng=body.lng
    ))
    db.commit()
    return {'incident_id': incident_id, 'unit_id': unit_id, 'status': body.status_code}

@app.post('/taip/ingest')
def ingest_taip(body: TaipIngest, db: Session = Depends(get_db)):
    data = parse_taip(body.raw)
    taip_id = body.taip_id or data.get('taip_id')
    if not taip_id:
        raise HTTPException(status_code=400, detail='taip_id not found in sentence or request')
    unit = db.query(Unit).filter(Unit.taip_id == taip_id).first()
    pos = TaipPosition(
        taip_id=taip_id,
        raw_sentence=body.raw,
        lat=data.get('lat'),
        lng=data.get('lng'),
        speed=data.get('speed'),
        heading=data.get('heading'),
        ignition=data.get('ignition'),
        odometer=data.get('odometer'),
        reported_at=data.get('reported_at')
    )
    if unit:
        pos.unit_id = unit.id
        if data.get('lat') is not None:
            unit.lat = data['lat']
        if data.get('lng') is not None:
            unit.lng = data['lng']
        if data.get('speed') is not None:
            unit.speed = data['speed']
        if data.get('heading') is not None:
            unit.heading = data['heading']
        unit.last_seen_at = datetime.utcnow()
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return {'taip_id': taip_id, 'parsed': data, 'unit_id': pos.unit_id}
