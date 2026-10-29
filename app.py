from flask import Flask, render_template, request, jsonify
import sqlite3
from datetime import datetime

app = Flask(__name__)
DB_PATH = "/tmp/transitrescue.db"

SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
ACTIVE_STATUSES = ("REPORTED", "VERIFYING", "ASSIGNED", "EN_ROUTE", "SHUTTLE_DISPATCHED", "ARRIVED")

# These are illustrative demo distances in km, not live GPS measurements.
STOP_DISTANCE_KM = {
    "Central Depot": {"Richmond Circle": 4.0, "Market Junction": 7.0, "Majestic": 8.0, "City Hospital": 5.0},
    "Market Junction": {"Richmond Circle": 3.0, "Market Junction": 0.5, "Majestic": 4.0, "City Hospital": 6.0},
    "Richmond Circle": {"Richmond Circle": 0.5, "Market Junction": 3.0, "Majestic": 5.0, "City Hospital": 2.0},
    "Majestic": {"Richmond Circle": 5.0, "Market Junction": 4.0, "Majestic": 0.5, "City Hospital": 7.0},
    "City Hospital": {"Richmond Circle": 2.0, "Market Junction": 6.0, "Majestic": 7.0, "City Hospital": 0.5},
}


from datetime import datetime
from zoneinfo import ZoneInfo

def now():
    return datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route TEXT NOT NULL,
            bus_id TEXT NOT NULL,
            stop TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'High',
            status TEXT NOT NULL DEFAULT 'REPORTED',
            delay_min INTEGER NOT NULL DEFAULT 20,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            shuttle_id TEXT
        );
        CREATE TABLE IF NOT EXISTS rfid_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            location TEXT NOT NULL,
            created_at TEXT NOT NULL,
            simulated INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS shuttles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shuttle_id TEXT UNIQUE NOT NULL,
            capacity INTEGER NOT NULL,
            status TEXT NOT NULL,
            location TEXT NOT NULL,
            destination TEXT,
            eta_min INTEGER,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS passenger_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_type TEXT NOT NULL,
            stop TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'Normal'
        );
        """)
        # Safe schema upgrades for databases created by the earlier Phase 1 app.
        incident_cols = {r["name"] for r in db.execute("PRAGMA table_info(incidents)").fetchall()}
        if "shuttle_id" not in incident_cols:
            db.execute("ALTER TABLE incidents ADD COLUMN shuttle_id TEXT")
        request_cols = {r["name"] for r in db.execute("PRAGMA table_info(passenger_requests)").fetchall()}
        if "priority" not in request_cols:
            db.execute("ALTER TABLE passenger_requests ADD COLUMN priority TEXT NOT NULL DEFAULT 'Normal'")

        count = db.execute("SELECT COUNT(*) AS n FROM shuttles").fetchone()["n"]
        if count == 0:
            db.executemany("""INSERT INTO shuttles
                (shuttle_id, capacity, status, location, destination, eta_min, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""", [
                ("RS-01", 24, "AVAILABLE", "Central Depot", None, None, now()),
                ("RS-02", 18, "AVAILABLE", "Market Junction", None, None, now()),
                ("RS-03", 20, "MAINTENANCE", "Central Depot", None, None, now()),
            ])
        count = db.execute("SELECT COUNT(*) AS n FROM incidents").fetchone()["n"]
        if count == 0:
            db.execute("""INSERT INTO incidents
                (route,bus_id,stop,severity,status,delay_min,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                ("500", "BUS-5007", "Richmond Circle", "High", "REPORTED", 20, now(), now()))


init_db()


def rows(query, args=()):
    with connect() as db:
        return [dict(r) for r in db.execute(query, args).fetchall()]


def row(query, args=()):
    with connect() as db:
        r = db.execute(query, args).fetchone()
        return dict(r) if r else None


def estimate_distance_km(origin, destination):
    """Return an illustrative demo distance; unknown stops use a conservative default."""
    if origin == destination:
        return 0.5
    return STOP_DISTANCE_KM.get(origin, {}).get(destination, 6.0)


def estimate_eta_min(distance_km, severity):
    # Demo assumption: average 24 km/h plus dispatch preparation time.
    prep = 1 if severity == "Critical" else 2
    return max(1, round((distance_km / 24) * 60 + prep))


def choose_best_shuttle(destination, severity):
    available = rows("SELECT * FROM shuttles WHERE status='AVAILABLE'")
    if not available:
        return None
    for shuttle in available:
        shuttle["distance_km"] = estimate_distance_km(shuttle["location"], destination)
        shuttle["eta_min"] = estimate_eta_min(shuttle["distance_km"], severity)
    return min(available, key=lambda s: (s["eta_min"], s["distance_km"], s["shuttle_id"]))


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/dashboard")
def dashboard():
    incidents = rows("SELECT * FROM incidents ORDER BY CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, id DESC LIMIT 10")
    shuttles = rows("SELECT * FROM shuttles ORDER BY shuttle_id")
    events = rows("SELECT * FROM rfid_events ORDER BY id DESC LIMIT 8")
    requests = rows("SELECT * FROM passenger_requests ORDER BY CASE priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1 ELSE 2 END, id DESC LIMIT 6")
    active = row("SELECT COUNT(*) n FROM incidents WHERE status NOT IN ('RESOLVED','CANCELLED')")["n"]
    scans = row("SELECT COUNT(*) n FROM rfid_events")["n"]
    demand = row("SELECT COUNT(*) n FROM rfid_events WHERE event_type='PASSENGER_BOARDING'")["n"]
    return jsonify({"incidents": incidents, "shuttles": shuttles, "events": events,
                    "requests": requests, "metrics": {"active_incidents": active,
                    "rfid_events": scans, "boarding_scans": demand}})


@app.post("/api/incidents")
def create_incident():
    data = request.get_json(silent=True) or {}
    required = ["route", "bus_id", "stop"]
    if not all(str(data.get(k, "")).strip() for k in required):
        return jsonify({"error": "Route, bus ID and stop are required."}), 400
    severity = data.get("severity", "High")
    if severity not in SEVERITY_RANK:
        return jsonify({"error": "Severity must be Critical, High, Medium, or Low."}), 400
    try:
        delay_min = max(0, min(600, int(data.get("delay_min", 20))))
    except (TypeError, ValueError):
        return jsonify({"error": "Delay must be a whole number."}), 400
    timestamp = now()
    with connect() as db:
        cur = db.execute("""INSERT INTO incidents
            (route,bus_id,stop,severity,status,delay_min,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (str(data["route"]).strip()[:40], str(data["bus_id"]).strip()[:40],
             str(data["stop"]).strip()[:100], severity, "REPORTED", delay_min, timestamp, timestamp))
        incident_id = cur.lastrowid
    return jsonify({"ok": True, "id": incident_id, "message": "Incident reported.", "status": "REPORTED"}), 201


@app.post("/api/incidents/<int:incident_id>/status")
def update_incident(incident_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    allowed = {"REPORTED", "VERIFYING", "ASSIGNED", "EN_ROUTE", "SHUTTLE_DISPATCHED", "ARRIVED", "RESOLVED", "CANCELLED"}
    if status not in allowed:
        return jsonify({"error": "Invalid incident status."}), 400
    with connect() as db:
        incident = db.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not incident:
            return jsonify({"error": "Incident not found."}), 404
        if incident["status"] in ("RESOLVED", "CANCELLED") and status != incident["status"]:
            return jsonify({"error": "Closed incidents cannot be reopened through this action."}), 409
        db.execute("UPDATE incidents SET status=?, updated_at=? WHERE id=?", (status, now(), incident_id))
    return jsonify({"ok": True, "status": status})


@app.post("/api/incidents/<int:incident_id>/auto-dispatch")
def auto_dispatch(incident_id):
    """Pick the best available shuttle using simulated stop distances and severity."""
    incident = row("SELECT * FROM incidents WHERE id=?", (incident_id,))
    if not incident:
        return jsonify({"error": "Incident not found."}), 404
    if incident["status"] in ("RESOLVED", "CANCELLED"):
        return jsonify({"error": "Cannot dispatch to a closed incident."}), 409
    if incident.get("shuttle_id"):
        return jsonify({"error": "A shuttle is already assigned to this incident.", "shuttle_id": incident["shuttle_id"]}), 409
    shuttle = choose_best_shuttle(incident["stop"], incident["severity"])
    if not shuttle:
        return jsonify({"error": "No available rescue shuttles. Reset or free a shuttle first."}), 409
    timestamp = now()
    with connect() as db:
        cur = db.execute("UPDATE shuttles SET status='EN_ROUTE', destination=?, eta_min=?, updated_at=? WHERE shuttle_id=? AND status='AVAILABLE'",
                         (incident["stop"], shuttle["eta_min"], timestamp, shuttle["shuttle_id"]))
        if cur.rowcount != 1:
            return jsonify({"error": "Shuttle availability changed. Refresh and try again."}), 409
        db.execute("UPDATE incidents SET status='EN_ROUTE', shuttle_id=?, updated_at=? WHERE id=?",
                   (shuttle["shuttle_id"], timestamp, incident_id))
    return jsonify({"ok": True, "simulated": True, "incident_id": incident_id,
                    "shuttle_id": shuttle["shuttle_id"], "distance_km": shuttle["distance_km"],
                    "eta_min": shuttle["eta_min"], "message": f"{shuttle['shuttle_id']} assigned and simulated en route. Estimated ETA: {shuttle['eta_min']} min."})


@app.post("/api/incidents/<int:incident_id>/arrived")
def shuttle_arrived(incident_id):
    incident = row("SELECT * FROM incidents WHERE id=?", (incident_id,))
    if not incident:
        return jsonify({"error": "Incident not found."}), 404
    if not incident.get("shuttle_id"):
        return jsonify({"error": "No shuttle is assigned to this incident."}), 409
    if incident["status"] in ("RESOLVED", "CANCELLED"):
        return jsonify({"error": "Incident is already closed."}), 409
    timestamp = now()
    with connect() as db:
        db.execute("UPDATE incidents SET status='ARRIVED', updated_at=? WHERE id=?", (timestamp, incident_id))
        db.execute("UPDATE shuttles SET status='ON_SCENE', eta_min=0, location=destination, updated_at=? WHERE shuttle_id=?",
                   (timestamp, incident["shuttle_id"]))
    return jsonify({"ok": True, "status": "ARRIVED", "message": "Demo shuttle marked as arrived at the incident."})


@app.post("/api/incidents/<int:incident_id>/resolve")
def resolve_incident(incident_id):
    incident = row("SELECT * FROM incidents WHERE id=?", (incident_id,))
    if not incident:
        return jsonify({"error": "Incident not found."}), 404
    if incident["status"] == "CANCELLED":
        return jsonify({"error": "Cancelled incidents cannot be resolved."}), 409
    if incident["status"] == "RESOLVED":
        return jsonify({"ok": True, "status": "RESOLVED", "message": "Incident is already resolved."})
    timestamp = now()
    with connect() as db:
        db.execute("UPDATE incidents SET status='RESOLVED', updated_at=? WHERE id=?", (timestamp, incident_id))
        if incident.get("shuttle_id"):
            db.execute("UPDATE shuttles SET status='AVAILABLE', location=COALESCE(destination, location), destination=NULL, eta_min=NULL, updated_at=? WHERE shuttle_id=?",
                       (timestamp, incident["shuttle_id"]))
    return jsonify({"ok": True, "status": "RESOLVED", "message": "Incident resolved; assigned shuttle returned to available state."})


@app.post("/api/rfid/vehicle")
def vehicle_scan():
    data = request.get_json(silent=True) or {}
    tag = str(data.get("tag_id", "V-BUS-5007"))[:80]
    vehicle = str(data.get("vehicle_id", "BUS-5007"))[:80]
    location = str(data.get("location", "Richmond Circle Checkpoint"))[:100]
    with connect() as db:
        db.execute("""INSERT INTO rfid_events(tag_id,event_type,entity_id,location,created_at,simulated)
                      VALUES(?,?,?,?,?,1)""", (tag, "VEHICLE_CHECKPOINT", vehicle, location, now()))
    return jsonify({"ok": True, "message": f"Vehicle tag {tag} scanned at {location}.", "simulated": True})


@app.post("/api/rfid/passenger")
def passenger_scan():
    data = request.get_json(silent=True) or {}
    tag = str(data.get("tag_id", "P-DEMO-104"))[:80]
    stop = str(data.get("stop", "Richmond Circle"))[:100]
    # Store a pseudonymous demo tag; do not collect passenger names.
    with connect() as db:
        db.execute("""INSERT INTO rfid_events(tag_id,event_type,entity_id,location,created_at,simulated)
                      VALUES(?,?,?,?,?,1)""", (tag, "PASSENGER_BOARDING", "ANON-PASSENGER", stop, now()))
    return jsonify({"ok": True, "message": f"Demo passenger tag scanned at {stop}.", "simulated": True})


@app.post("/api/requests")
def passenger_request():
    data = request.get_json(silent=True) or {}
    kind = str(data.get("request_type", "Alternative route"))[:80]
    stop = str(data.get("stop", "Richmond Circle"))[:100]
    note = str(data.get("note", ""))[:240]
    priority = data.get("priority", "Normal")
    if priority not in ("Urgent", "High", "Normal"):
        return jsonify({"error": "Priority must be Urgent, High, or Normal."}), 400
    with connect() as db:
        db.execute("""INSERT INTO passenger_requests(request_type,stop,note,created_at,priority)
                      VALUES(?,?,?,?,?)""", (kind, stop, note, now(), priority))
    return jsonify({"ok": True, "message": "Passenger request sent to the operator.", "priority": priority}), 201


@app.post("/api/shuttles/<shuttle_id>/dispatch")
def dispatch(shuttle_id):
    """Manual dispatch endpoint retained for compatibility with the Phase 1 dashboard."""
    data = request.get_json(silent=True) or {}
    incident_id = data.get("incident_id")
    if not incident_id:
        return jsonify({"error": "Choose a valid incident first."}), 400
    incident = row("SELECT * FROM incidents WHERE id=?", (incident_id,))
    if not incident:
        return jsonify({"error": "Choose a valid incident first."}), 400
    if incident["status"] in ("RESOLVED", "CANCELLED"):
        return jsonify({"error": "Cannot dispatch to a closed incident."}), 409
    if incident.get("shuttle_id"):
        return jsonify({"error": "A shuttle is already assigned to this incident."}), 409
    shuttle = row("SELECT * FROM shuttles WHERE shuttle_id=?", (shuttle_id,))
    if not shuttle:
        return jsonify({"error": "Shuttle not found."}), 404
    if shuttle["status"] != "AVAILABLE":
        return jsonify({"error": "This shuttle is not available."}), 409
    distance = estimate_distance_km(shuttle["location"], incident["stop"])
    eta = estimate_eta_min(distance, incident["severity"])
    timestamp = now()
    with connect() as db:
        cur = db.execute("UPDATE shuttles SET status='EN_ROUTE', destination=?, eta_min=?, updated_at=? WHERE shuttle_id=? AND status='AVAILABLE'",
                         (incident["stop"], eta, timestamp, shuttle_id))
        if cur.rowcount != 1:
            return jsonify({"error": "Shuttle availability changed. Refresh and try again."}), 409
        db.execute("UPDATE incidents SET status='EN_ROUTE', shuttle_id=?, updated_at=? WHERE id=?",
                   (shuttle_id, timestamp, incident_id))
    return jsonify({"ok": True, "message": f"{shuttle_id} simulated dispatch started. ETA {eta} min.",
                    "eta_min": eta, "distance_km": distance, "shuttle_id": shuttle_id, "simulated": True})


@app.post("/api/shuttles/<shuttle_id>/reset")
def reset_shuttle(shuttle_id):
    with connect() as db:
        shuttle = db.execute("SELECT * FROM shuttles WHERE shuttle_id=?", (shuttle_id,)).fetchone()
        if not shuttle:
            return jsonify({"error": "Shuttle not found."}), 404
        # Do not silently release a shuttle while its incident remains active.
        active_assignment = db.execute("SELECT id FROM incidents WHERE shuttle_id=? AND status NOT IN ('RESOLVED','CANCELLED') LIMIT 1", (shuttle_id,)).fetchone()
        if active_assignment:
            return jsonify({"error": "Resolve or cancel the linked incident before resetting this shuttle."}), 409
        db.execute("UPDATE shuttles SET status='AVAILABLE', destination=NULL, eta_min=NULL, location='Central Depot', updated_at=? WHERE shuttle_id=?",
                   (now(), shuttle_id))
    return jsonify({"ok": True, "message": f"{shuttle_id} returned to available demo state."})


@app.post("/api/demo/reset")
def reset_demo():
    with connect() as db:
        db.execute("DELETE FROM rfid_events")
        db.execute("DELETE FROM passenger_requests")
        db.execute("DELETE FROM incidents")
        db.execute("""UPDATE shuttles SET status='AVAILABLE', destination=NULL, eta_min=NULL,
                      location=CASE shuttle_id WHEN 'RS-01' THEN 'Central Depot' WHEN 'RS-02' THEN 'Market Junction' ELSE 'Central Depot' END,
                      updated_at=? WHERE shuttle_id IN ('RS-01','RS-02')""", (now(),))
        db.execute("UPDATE shuttles SET status='MAINTENANCE', destination=NULL, eta_min=NULL, location='Central Depot', updated_at=? WHERE shuttle_id='RS-03'", (now(),))
        db.execute("""INSERT INTO incidents (route,bus_id,stop,severity,status,delay_min,created_at,updated_at)
                      VALUES (?,?,?,?,?,?,?,?)""", ("500", "BUS-5007", "Richmond Circle", "High", "REPORTED", 20, now(), now()))
    return jsonify({"ok": True, "message": "Demo activity reset. Fleet and sample incident restored."})


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "TransitRescue", "mode": "simulation", "timestamp": now()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
