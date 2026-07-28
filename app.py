from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON, or_, UniqueConstraint
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

if DATABASE_URL.startswith('sqlite'):
    Base.metadata.create_all(bind=engine)

app = FastAPI(title='VolCAD Prototype', version='0.1.0')

app.mount('/static', StaticFiles(directory='static'), name='static')

@app.get('/')
def index():
    return FileResponse('static/dashboard_v5.html')

@app.get('/units-screen')
def units_screen():
    return FileResponse('static/screen.html')

@app.get('/calls-screen')
def calls_screen():
    return FileResponse('static/screen.html')

@app.get('/mdt')
def mdt():
    return FileResponse('static/mdt.html')

@app.get('/avl')
def avl():
    return FileResponse('static/avl.html')

@app.get('/history')
def history():
    return FileResponse('static/history.html')

@app.get('/config', response_model=List[CustomerConfigOut])
def list_config(agency_id: Optional[int] = Query(None), category: Optional[str] = Query(None), db: Session = Depends(get_db)):
    q = db.query(CustomerConfig)
    if agency_id:
        q = q.filter(CustomerConfig.agency_id == agency_id)
    if category:
        q = q.filter(CustomerConfig.category == category)
    return q.order_by(CustomerConfig.category, CustomerConfig.key).all()

@app.post('/config', response_model=CustomerConfigOut)
def create_config(body: CustomerConfigCreate, db: Session = Depends(get_db)):
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
    camera_url: Optional[str] = None
    last_assigned_at: Optional[datetime] = None
    in_service_at: Optional[datetime] = None
    accumulated_call_seconds: Optional[float] = None
    taip_id: Optional[str] = None
    class Config:
        from_attributes = True

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

class IncidentOut(IncidentCreate):
    id: int
    status: str
    created_at: Optional[datetime] = None
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

class CustomerConfigCreate(BaseModel):
    agency_id: Optional[int] = None
    category: str
    key: str
    value: Optional[dict] = None

class CustomerConfigOut(CustomerConfigCreate):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
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

class UnitCamera(BaseModel):
    camera_url: str

class UnitShift(BaseModel):
    action: str

class PersonnelAssign(BaseModel):
    unit_id: Optional[int] = None

class AlertCrew(BaseModel):
    message: Optional[str] = None

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

@app.get('/units/{unit_id}', response_model=UnitOut)
def get_unit(unit_id: int, db: Session = Depends(get_db)):
    unit = db.query(Unit).get(unit_id)
    if not unit:
        raise HTTPException(status_code=404, detail='Unit not found')
    return unit

@app.get('/units/{unit_id}/messages', response_model=List[MessageOut])
def get_unit_messages(unit_id: int, db: Session = Depends(get_db)):
    return db.query(DispatchMessage).filter(DispatchMessage.unit_id == unit_id).order_by(DispatchMessage.sent_at.desc()).limit(50).all()

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

@app.post('/incidents', response_model=IncidentOut)
def create_incident(body: IncidentCreate, db: Session = Depends(get_db)):
    data = body.model_dump()
    if not data.get('incident_number'):
        count = db.query(Incident).filter(Incident.agency_id == data['agency_id']).count()
        data['incident_number'] = f"{data['agency_id']}-{count + 1:05d}"
    if not data.get('call_number'):
        data['call_number'] = data['incident_number']
    incident = Incident(**data)
    db.add(incident)
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
    return {'incident': to_dict(incident), 'events': events, 'logs': logs, 'messages': messages, 'assignments': assignments}

@app.put('/incidents/{incident_id}', response_model=IncidentOut)
def update_incident(incident_id: int, body: IncidentUpdate, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(incident, k, v)
    if body.status == 'closed' and not incident.closed_at:
        incident.closed_at = datetime.utcnow()
    db.commit()
    db.refresh(incident)
    return incident

@app.post('/incidents/{incident_id}/dispatch/{unit_id}')
def dispatch_unit(incident_id: int, unit_id: int, notes: Optional[str] = None, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    unit = db.query(Unit).get(unit_id)
    if not incident or not unit:
        raise HTTPException(status_code=404, detail='Incident or unit not found')
    existing = db.query(IncidentUnit).filter_by(incident_id=incident_id, unit_id=unit_id).first()
    if existing:
        raise HTTPException(status_code=400, detail='Unit already dispatched to incident')
    iu = IncidentUnit(incident_id=incident_id, unit_id=unit_id, notes=notes)
    unit.current_incident_id = incident_id
    unit.last_assigned_at = datetime.utcnow()
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
    if body.disposition is not None:
        iu.disposition = body.disposition
    if body.passenger_count is not None:
        iu.passenger_count = body.passenger_count
    if body.status_code == 'CAN':
        if iu.assigned_at:
            duration = (datetime.utcnow() - iu.assigned_at).total_seconds()
            unit.accumulated_call_seconds = (unit.accumulated_call_seconds or 0) + duration
        iu.cleared_at = datetime.utcnow()
        unit.current_incident_id = None
        unit.current_status = 'AQ'
    else:
        unit.current_status = body.status_code
        unit.current_incident_id = incident_id
        if body.disposition is not None:
            iu.disposition = body.disposition
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

@app.get('/taip/positions', response_model=List[TaipPositionOut])
def list_taip_positions(unit_id: Optional[int] = Query(None), limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(TaipPosition)
    if unit_id:
        q = q.filter(TaipPosition.unit_id == unit_id)
    return q.order_by(TaipPosition.received_at.desc()).limit(limit).all()

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
