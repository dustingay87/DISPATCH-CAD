CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS agencies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    agency_type VARCHAR(50) NOT NULL DEFAULT 'fire',
    domain VARCHAR(100) UNIQUE,
    address TEXT,
    city VARCHAR(100),
    state VARCHAR(2),
    zip_code VARCHAR(20),
    lat NUMERIC(10,8),
    lng NUMERIC(11,8),
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL DEFAULT 'responder',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    agency_id INTEGER REFERENCES agencies(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS personnel (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    agency_id INTEGER NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    radio_id VARCHAR(50),
    phone VARCHAR(50),
    duty_status VARCHAR(50) NOT NULL DEFAULT 'off_duty',
    current_unit_id INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS certifications (
    id SERIAL PRIMARY KEY,
    agency_id INTEGER REFERENCES agencies(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    code VARCHAR(50),
    description TEXT
);

CREATE TABLE IF NOT EXISTS personnel_certifications (
    id SERIAL PRIMARY KEY,
    personnel_id INTEGER NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
    certification_id INTEGER NOT NULL REFERENCES certifications(id) ON DELETE CASCADE,
    issued_at DATE,
    expires_at DATE,
    verified_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (personnel_id, certification_id)
);

CREATE TABLE IF NOT EXISTS locations (
    id SERIAL PRIMARY KEY,
    agency_id INTEGER REFERENCES agencies(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    location_type VARCHAR(50) NOT NULL,
    address TEXT,
    geom GEOMETRY(Point, 4326),
    lat NUMERIC(10,8),
    lng NUMERIC(11,8),
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_locations_geom ON locations USING GIST (geom);

CREATE TABLE IF NOT EXISTS units (
    id SERIAL PRIMARY KEY,
    agency_id INTEGER NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    call_sign VARCHAR(50) NOT NULL,
    unit_type VARCHAR(50),
    capabilities JSONB,
    station_location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    current_status VARCHAR(50) NOT NULL DEFAULT 'AQ',
    current_incident_id INTEGER,
    geom GEOMETRY(Point, 4326),
    lat NUMERIC(10,8),
    lng NUMERIC(11,8),
    heading NUMERIC(5,2),
    speed NUMERIC(6,2),
    last_seen_at TIMESTAMPTZ,
    radio_id VARCHAR(50),
    taip_id VARCHAR(50),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_units_geom ON units USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_units_taip ON units(taip_id);
CREATE INDEX IF NOT EXISTS idx_units_call_sign ON units(call_sign);
CREATE INDEX IF NOT EXISTS idx_units_agency ON units(agency_id);

CREATE TABLE IF NOT EXISTS unit_personnel (
    id SERIAL PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    personnel_id INTEGER NOT NULL REFERENCES personnel(id) ON DELETE CASCADE,
    role VARCHAR(50),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS incidents (
    id SERIAL PRIMARY KEY,
    agency_id INTEGER NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    incident_number VARCHAR(50) NOT NULL,
    call_type VARCHAR(100) NOT NULL,
    priority INTEGER NOT NULL DEFAULT 2,
    status VARCHAR(50) NOT NULL DEFAULT 'open',
    location_text TEXT,
    geom GEOMETRY(Point, 4326),
    lat NUMERIC(10,8),
    lng NUMERIC(11,8),
    caller_name VARCHAR(255),
    callback VARCHAR(50),
    narrative TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    UNIQUE (agency_id, incident_number)
);

CREATE INDEX IF NOT EXISTS idx_incidents_geom ON incidents USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_incidents_agency_status ON incidents(agency_id, status);
CREATE INDEX IF NOT EXISTS idx_incidents_number ON incidents(incident_number);

CREATE TABLE IF NOT EXISTS incident_units (
    id SERIAL PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    cleared_at TIMESTAMPTZ,
    assignment_status VARCHAR(50) NOT NULL DEFAULT 'assigned',
    notes TEXT,
    UNIQUE (incident_id, unit_id)
);

CREATE TABLE IF NOT EXISTS status_events (
    id BIGSERIAL PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
    incident_id INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
    status_code VARCHAR(50) NOT NULL,
    reason TEXT,
    destination_location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
    geom GEOMETRY(Point, 4326),
    lat NUMERIC(10,8),
    lng NUMERIC(11,8),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_status_events_unit ON status_events(unit_id, created_at);

CREATE TABLE IF NOT EXISTS taip_positions (
    id BIGSERIAL PRIMARY KEY,
    unit_id INTEGER REFERENCES units(id) ON DELETE SET NULL,
    taip_id VARCHAR(50) NOT NULL,
    raw_sentence TEXT,
    lat NUMERIC(10,8),
    lng NUMERIC(11,8),
    speed NUMERIC(6,2),
    heading NUMERIC(5,2),
    ignition BOOLEAN,
    odometer NUMERIC(10,2),
    fix_quality VARCHAR(10),
    reported_at TIMESTAMPTZ,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_taip_positions_unit_time ON taip_positions(unit_id, received_at);
CREATE INDEX IF NOT EXISTS idx_taip_positions_taip_time ON taip_positions(taip_id, received_at);

CREATE TABLE IF NOT EXISTS call_logs (
    id BIGSERIAL PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    log_type VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_call_logs_incident ON call_logs(incident_id, timestamp);

CREATE TABLE IF NOT EXISTS dispatch_messages (
    id BIGSERIAL PRIMARY KEY,
    incident_id INTEGER REFERENCES incidents(id) ON DELETE SET NULL,
    unit_id INTEGER REFERENCES units(id) ON DELETE SET NULL,
    channel VARCHAR(100),
    message_text TEXT NOT NULL,
    method VARCHAR(50),
    sent_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ
);

ALTER TABLE units ADD COLUMN IF NOT EXISTS camera_url TEXT;
ALTER TABLE units ADD COLUMN IF NOT EXISTS last_assigned_at TIMESTAMPTZ;
ALTER TABLE units ADD COLUMN IF NOT EXISTS in_service_at TIMESTAMPTZ;
ALTER TABLE units ADD COLUMN IF NOT EXISTS accumulated_call_seconds NUMERIC DEFAULT 0;
ALTER TABLE personnel ADD COLUMN IF NOT EXISTS email VARCHAR(255);
ALTER TABLE personnel ADD COLUMN IF NOT EXISTS sms_phone VARCHAR(50);
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS call_number VARCHAR(50);
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS extra JSONB;
ALTER TABLE incident_units ADD COLUMN IF NOT EXISTS disposition VARCHAR(100);
ALTER TABLE incident_units ADD COLUMN IF NOT EXISTS passenger_count INTEGER;
