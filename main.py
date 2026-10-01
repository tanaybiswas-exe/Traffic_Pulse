import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI(title="TrafficPulse Real-Time Engine")

connected_monitors = set()
active_ambulances = {}

# ঢাকার প্রধান ট্রাফিক ইন্টারসেকশনসমূহ
intersections = {
    "INT_01": {
        "id": "INT_01",
        "name": "শাহবাগ ইন্টারসেকশন",
        "lat": 23.7383,
        "lon": 90.3956,
        "signal": "RED",
        "corridor_active": False
    },
    "INT_02": {
        "id": "INT_02",
        "name": "কারওয়ান বাজার মোড়",
        "lat": 23.7508,
        "lon": 90.3935,
        "signal": "RED",
        "corridor_active": False
    },
    "INT_03": {
        "id": "INT_03",
        "name": "ফার্মগেট জংশন",
        "lat": 23.7570,
        "lon": 90.3888,
        "signal": "RED",
        "corridor_active": False
    }
}

def get_real_route(start_lat, start_lon, end_lat, end_lon):
    """OSRM দিয়ে লাইভ টার্ন-বাই-টার্ন পথ ও দূরত্ব নির্ধারণ"""
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson&annotations=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-System/4.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get("routes"):
                route = data["routes"][0]
                dist_km = round(route["distance"] / 1000, 2)
                duration_min = max(1, round(route["duration"] / 60))
                coords = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
                return dist_km, duration_min, coords
    except Exception as e:
        print(f"OSRM Route Error: {e}")
    
    # ব্যাকআপ লিনিয়ার ক্যালকুলেশন
    dist = round(math.hypot((start_lat - end_lat) * 111, (start_lon - end_lon) * 111), 2)
    return dist, max(1, round(dist * 2.5)), [[start_lat, start_lon], [end_lat, end_lon]]

def find_nearest_hospitals(lat, lon):
    """ওভারপাস API দিয়ে অবস্থানের ৫ কিমি রেডিয়াসের আসল হাসপাতাল খোঁজা"""
    query = f"""
    [out:json][timeout:4];
    (
      node["amenity"="hospital"](around:5000, {lat}, {lon});
      node["amenity"="clinic"](around:5000, {lat}, {lon});
    );
    out center 6;
    """
    url = "https://overpass-api.de/api/interpreter?data=" + urllib.parse.quote(query)
    hospitals = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-System/4.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for el in data.get("elements", []):
                name = el.get("tags", {}).get("name")
                if not name:
                    continue
                h_lat = el.get("lat")
                h_lon = el.get("lon")
                dist = round(math.hypot((lat - h_lat) * 111, (lon - h_lon) * 111), 2)
                hospitals.append({
                    "name": name,
                    "lat": h_lat,
                    "lon": h_lon,
                    "dist_km": dist
                })
        hospitals.sort(key=lambda x: x["dist_km"])
    except Exception:
        pass

    if not hospitals:
        hospitals = [
            {"name": "ঢাকা মেডিকেল কলেজ হাসপাতাল (DMCH)", "lat": 23.7260, "lon": 90.3976, "dist_km": 2.1},
            {"name": "বিএসএমএমইউ (পিজি হাসপাতাল)", "lat": 23.7398, "lon": 90.3958, "dist_km": 1.4},
            {"name": "স্কয়ার হাসপাতাল লিমিটেড", "lat": 23.7533, "lon": 90.3816, "dist_km": 3.2}
        ]
    return hospitals

async def broadcast_to_monitors(payload: dict):
    disconnected = set()
    msg = json.dumps(payload)
    for ws in list(connected_monitors):
        try:
            await ws.send_text(msg)
        except Exception:
            disconnected.add(ws)
    connected_monitors.difference_update(disconnected)

# ----------------- পেজ রাউটস -----------------

@app.get("/")
def get_monitor_page():
    path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/driver")
def get_driver_page():
    path = os.path.join(os.path.dirname(__file__), "templates", "driver.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# ----------------- লাইভ ওয়েবসকেট -----------------

@app.websocket("/ws/monitor")
async def ws_monitor_route(ws: WebSocket):
    await ws.accept()
    connected_monitors.add(ws)
    
    await ws.send_text(json.dumps({
        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
        "ambulances": active_ambulances,
        "intersections": intersections,
        "alert": None
    }))

    try:
        while True:
            text = await ws.receive_text()
            data = json.loads(text)
            if data.get("action") == "toggle_signal":
                target_id = data.get("id")
                if target_id in intersections:
                    current = intersections[target_id]["signal"]
                    intersections[target_id]["signal"] = "RED" if current == "GREEN" else "GREEN"
                    intersections[target_id]["manual_override"] = True
                    await broadcast_to_monitors({
                        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
                        "ambulances": active_ambulances,
                        "intersections": intersections,
                        "alert": f"ম্যানুয়াল সিগন্যাল পরিবর্তন: {intersections[target_id]['name']}"
                    })
    except WebSocketDisconnect:
        connected_monitors.remove(ws)

@app.websocket("/ws/driver")
async def ws_driver_route(ws: WebSocket):
    await ws.accept()
    driver_id = None
    try:
        while True:
            text = await ws.receive_text()
            data = json.loads(text)

            driver_id = data.get("driver_id", "AMB_01")
            lat = float(data["lat"])
            lon = float(data["lon"])
            speed = float(data.get("speed", 0))
            dest_lat = float(data["dest_lat"])
            dest_lon = float(data["dest_lon"])
            dest_name = data.get("destination", "নিকটস্থ হাসপাতাল")

            dist_km, eta, coords = get_real_route(lat, lon, dest_lat, dest_lon)

            nearby = []
            if data.get("req_nearby"):
                nearby = find_nearest_hospitals(lat, lon)

            # গ্রিন করিডোর সক্রিয়করণ (৩৫০ মিটারের মধ্যে এলে)
            corridor_engaged = False
            active_alert = None
            for i_id, i_info in intersections.items():
                if math.hypot(lat - i_info["lat"], lon - i_info["lon"]) < 0.0035:
                    i_info["signal"] = "GREEN"
                    i_info["corridor_active"] = True
                    corridor_engaged = True
                    active_alert = f"🚨 গ্রিন করিডোর সক্রিয়: {i_info['name']} খালি করা হচ্ছে!"
                else:
                    if not i_info.get("manual_override", False):
                        i_info["signal"] = "RED"
                        i_info["corridor_active"] = False

            active_ambulances[driver_id] = {
                "id": driver_id,
                "lat": lat,
                "lon": lon,
                "speed": round(speed * 3.6, 1) if speed > 0 else 0,
                "destination": dest_name,
                "dest_lat": dest_lat,
                "dest_lon": dest_lon,
                "distance_km": dist_km,
                "eta_mins": eta,
                "route_coords": coords,
                "corridor_active": corridor_engaged,
                "last_seen": datetime.now().strftime("%I:%M:%S %p")
            }

            resp_driver = dict(active_ambulances[driver_id])
            if nearby:
                resp_driver["nearby_hospitals"] = nearby
            await ws.send_text(json.dumps(resp_driver))

            await broadcast_to_monitors({
                "timestamp": datetime.now().strftime("%I:%M:%S %p"),
                "ambulances": active_ambulances,
                "intersections": intersections,
                "alert": active_alert
            })

    except WebSocketDisconnect:
        if driver_id and driver_id in active_ambulances:
            del active_ambulances[driver_id]
            await broadcast_to_monitors({
                "timestamp": datetime.now().strftime("%I:%M:%S %p"),
                "ambulances": active_ambulances,
                "intersections": intersections,
                "alert": f"{driver_id} অফলাইনে চলে গেছে।"
            })