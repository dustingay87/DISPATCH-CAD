from fastapi import FastAPI, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON, or_, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship, Session, sessionmaker
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional, Any
import os
import re
import math
import json
import asyncio
import time
import hmac
import hashlib
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-change-me')
INSECURE_DEV = os.getenv('INSECURE_DEV', 'false').lower() == 'true'

login_attempts = {}

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
    db.add(Event(event_type=event_type, entity_type=entity_type, entity_id=entity_id, user_id=user_id, data=data, agency_id=agency_id))

@app.middleware('http')
def _security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    if not INSECURE_DEV:
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

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
def me(request: Request):
    user = get_current_user(request)
    return {'user_id': user['user_id'], 'role': user['role']}

@app.get('/login')
def login_page():
    return FileResponse('static/login.html')

@app.get('/')
def index():
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
    return FileResponse('static/mdt.html')

@app.get('/avl')
def avl():
    return FileResponse('static/avl.html')

@app.get('/history')
def history():
    return FileResponse('static/history.html')

@app.get('/admin')
def admin():
    return FileResponse('static/admin.html')

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
            'statuses': [{'code':'AQ','label':'Available'},{'code':'OS','label':'On Scene'},{'code':'ER','label':'En Route'},{'code':'TR','label':'Transport'},{'code':'CAN','label':'Cancelled'},{'code':'LUN','label':'Lunch'},{'code':'OOS','label':'Out of Service'},{'code':'MAINT','label':'Maintenance'}],
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
            }
        },
        'fire': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'OS','label':'On Scene'},{'code':'ER','label':'En Route'},{'code':'TR','label':'Transport'},{'code':'CAN','label':'Cancelled'},{'code':'LUN','label':'Lunch'},{'code':'OOS','label':'Out of Service'},{'code':'MAINT','label':'Maintenance'}],
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
            }
        },
        'ems': {
            'statuses': [{'code':'AQ','label':'Available'},{'code':'OS','label':'On Scene'},{'code':'ER','label':'En Route'},{'code':'TR','label':'Transport'},{'code':'CAN','label':'Cancelled'},{'code':'LUN','label':'Lunch'},{'code':'OOS','label':'Out of Service'},{'code':'MAINT','label':'Maintenance'}],
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

class UnitStatus(BaseModel):
    status_code: str
    reason: Optional[str] = None

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
def recommend_units(incident_id: int, limit: int = Query(5), db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    cfg = db.query(CustomerConfig).filter_by(agency_id=incident.agency_id, category='response_plans', key='defaults').first()
    plan = (cfg.value or {}) if cfg else {}
    recommended_types = plan.get(incident.call_type, []) if isinstance(plan, dict) else []
    units = db.query(Unit).filter(Unit.current_status == 'AQ').all()
    scored = []
    for u in units:
        s = 0
        reasons = []
        if u.unit_type in recommended_types:
            s += 100
            reasons.append('run-card')
        if u.agency_id == incident.agency_id:
            s += 20
            reasons.append('same-agency')
        dist = None
        if incident.lat is not None and u.lat is not None and u.lng is not None:
            dy = (u.lat - incident.lat) * 69.0
            dx = (u.lng - incident.lng) * 69.0 * math.cos(math.radians(incident.lat))
            dist = math.sqrt(dx*dx + dy*dy)
            s -= dist * 5
        scored.append({'unit_id': u.id, 'call_sign': u.call_sign, 'unit_type': u.unit_type, 'agency_id': u.agency_id, 'distance_miles': dist, 'score': s, 'reason': ' / '.join(reasons) or 'available'})
    scored.sort(key=lambda x: -x['score'])
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
    return {'incident': to_dict(incident), 'events': events, 'logs': logs, 'messages': messages, 'assignments': assignments}

@app.put('/incidents/{incident_id}', response_model=IncidentOut)
def update_incident(request: Request, incident_id: int, body: IncidentUpdate, db: Session = Depends(get_db)):
    incident = db.query(Incident).get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail='Incident not found')
    user = get_current_user(request)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(incident, k, v)
    if body.status == 'closed' and not incident.closed_at:
        incident.closed_at = datetime.utcnow()
    _log_event(db, 'incident_updated', 'incident', incident.id, user_id=user.get('user_id'), data=changes, agency_id=incident.agency_id)
    db.commit()
    db.refresh(incident)
    return incident

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
    incident.status = 'dispatched'
    db.add(iu)
    db.flush()
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

@app.get('/taip/stream')
async def taip_stream(db: Session = Depends(get_db)):
    async def event_generator():
        last_payload = None
        while True:
            units = db.query(Unit).all()
            data = [{'id': u.id, 'call_sign': u.call_sign, 'lat': u.lat, 'lng': u.lng, 'heading': u.heading, 'speed': u.speed, 'last_seen_at': u.last_seen_at.isoformat() if u.last_seen_at else None, 'current_status': u.current_status} for u in units]
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
    db.add(StatusEvent(unit_id=unit_id, incident_id=None, status_code=body.status_code, reason=body.reason))
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
    def ensure_unit(call_sign, agency_id, unit_type, lat, lng, taip_id):
        u = db.query(Unit).filter(Unit.call_sign == call_sign).first()
        if u: return u
        u = Unit(name=call_sign, call_sign=call_sign, agency_id=agency_id, unit_type=unit_type, lat=lat, lng=lng, taip_id=taip_id, in_service_at=datetime.utcnow(), current_status='AQ', current_incident_id=None, accumulated_call_seconds=0)
        db.add(u); db.flush(); return u
    u1 = ensure_unit('A12', police.id, 'patrol', CENTER[0]-0.008, CENTER[1]+0.012, 'TAIP-A12')
    u2 = ensure_unit('E1', fire.id, 'engine', CENTER[0]+0.012, CENTER[1]-0.012, 'TAIP-E1')
    u3 = ensure_unit('M1', ems.id, 'ambulance', CENTER[0]+0.007, CENTER[1]-0.017, 'TAIP-M1')
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
                db.add(Unit(agency_id=int(row['agency_id']), name=row.get('name', row['call_sign']), call_sign=row['call_sign'], unit_type=row.get('unit_type', 'patrol'), lat=float(row['lat']) if row.get('lat') else None, lng=float(row['lng']) if row.get('lng') else None, taip_id=row.get('taip_id'), camera_url=row.get('camera_url'), current_status='AQ', in_service_at=datetime.utcnow(), accumulated_call_seconds=0))
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

@app.get('/users-page')
def users_page():
    return FileResponse('static/users.html')

@app.get('/events-page')
def events_page():
    return FileResponse('static/events.html')
