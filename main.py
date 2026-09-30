import json
import math
import os
import urllib.parse
import urllib.request
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI(title="TrafficPulse Emergency Core Engine")

connected_monitors = set()
connected_drivers = set()

# ঢাকার প্রধান প্রধান সিগন্যাল ও ইন্টারসেকশন
intersections = {
    "INT_01": {
        "id": "INT_01",
        "name": "শাহবাগ ইন্টারসেকশন",
        "lat": 23.7383,
        "lon": 90.3956,
        "signal": "RED",
        "corridor_active": False,
        "traffic_density": "Heavy (৮৫%)"
    },
    "INT_02": {
        "id": "INT_02",
        "name": "কারওয়ান বাজার মোড়",
        "lat": 23.7508,
        "lon": 90.3935,
        "signal": "RED",
        "corridor_active": False,
        "traffic_density": "Moderate (৫৫%)"
    },
    "INT_03": {
        "id": "INT_03",
        "name": "ফার্মগেট জংশন",
        "lat": 23.7570,
        "lon": 90.3888,
        "signal": "RED",
        "corridor_active": False,
        "traffic_density": "High (৭৮%)"
    }
}

# অ্যাম্বুলেন্সের লাইভ স্টেট
ambulance_data = {
    "lat": 23.7330,
    "lon": 90.3980,
    "speed": 0,
    "destination": "নিকটস্থ হাসপাতাল খোঁজা হচ্ছে...",
    "dest_lat": 23.7260,
    "dest_lon": 90.3976,
    "distance_km": 0,
    "eta_mins": 0,
    "route_coords": [],
    "traffic_status": "স্বাভাবিক রোড ফ্লো",
    "is_live": False,
    "active_corridor": False,
    "ai_confidence": 98.4,
    "last_update": "চালকের সংযোগের অপেক্ষা..."
}

def find_nearest_hospitals(lat, lon):
    """OpenStreetMap Overpass API দিয়ে নিকটস্থ হাসপাতাল ও ক্লিনিক রিকমেন্ডেশন"""
    query = f"""
    [out:json][timeout:4];
    (
      node["amenity"="hospital"](around:6000, {lat}, {lon});
      node["amenity"="clinic"](around:6000, {lat}, {lon});
    );
    out center 7;
    """
    url = "https://overpass-api.de/api/interpreter?data=" + urllib.parse.quote(query)
    hospitals = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-BD/3.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for el in data.get("elements", []):
                name = el.get("tags", {}).get("name", "স্থানীয় হাসপাতাল / ক্লিনিক")
                h_lat = el.get("lat")
                h_lon = el.get("lon")
                dist = round(math.hypot((lat - h_lat) * 111, (lon - h_lon) * 111), 2)
                h_type = el.get("tags", {}).get("amenity", "হাসপাতাল").capitalize()
                hospitals.append({
                    "name": name,
                    "lat": h_lat,
                    "lon": h_lon,
                    "dist_km": dist,
                    "type": h_type
                })
        hospitals.sort(key=lambda x: x["dist_km"])
    except Exception as e:
        print(f"Hospital Discovery Error: {e}")

    # কোনো কারণে টাইম-আউট হলে স্বয়ংক্রিয় ব্যাকআপ তালিকা
    if not hospitals:
        hospitals = [
            {"name": "ঢাকা মেডিকেল কলেজ হাসপাতাল (DMCH)", "lat": 23.7260, "lon": 90.3976, "dist_km": 2.1, "type": "সরকারি"},
            {"name": "বিএসএমএমইউ (পিজি হাসপাতাল)", "lat": 23.7398, "lon": 90.3958, "dist_km": 1.4, "type": "স্পেশালাইজড"},
            {"name": "স্কয়ার হাসপাতাল লিমিটেড", "lat": 23.7533, "lon": 90.3816, "dist_km": 3.2, "type": "বেসরকারি"},
            {"name": "জাতীয় হৃদরোগ ইনস্টিটিউট (NICVD)", "lat": 23.7712, "lon": 90.3685, "dist_km": 4.5, "type": "সরকারি"}
        ]
    return hospitals

def get_real_route_with_traffic(start_lat, start_lon, end_lat, end_lon):
    """OSRM লাইভ টার্ন-বাই-টার্ন পথ এবং ট্রাফিক জ্যাম বিশ্লেষণ"""
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson"
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-BD/3.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get("routes"):
                route = data["routes"][0]
                dist_km = round(route["distance"] / 1000, 2)
                duration = route["duration"] / 60
                
                avg_speed = (dist_km / (duration / 60)) if duration > 0 else 30
                if avg_speed < 12:
                    traffic_status = "তীব্র জ্যাম (Heavy Traffic)"
                elif avg_speed < 24:
                    traffic_status = "মাঝারি ট্রাফিক (Moderate Traffic)"
                else:
                    traffic_status = "রাস্তা পরিষ্কার (Clear Route)"

                coords = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
                return dist_km, max(1, round(duration)), coords, traffic_status
    except Exception:
        pass

    # ফলব্যাক লিনিয়ার রুট (যদি OSRM রেট লিমিট দেয়)
    dist = round(math.hypot((start_lat - end_lat) * 111, (start_lon - end_lon) * 111), 2)
    return dist, max(1, round(dist * 2.8)), [[start_lat, start_lon], [end_lat, end_lon]], "স্বাভাবিক ট্রাফিক"

async def broadcast_to_monitors(payload: dict):
    disconnected = set()
    msg = json.dumps(payload)
    for ws in list(connected_monitors):
        try:
            await ws.send_text(msg)
        except Exception:
            disconnected.add(ws)
    connected_monitors.difference_update(disconnected)

# ----------------- HTML পেজ রাউট -----------------

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

# ----------------- ওয়েবসকেট ইঞ্জিন -----------------

@app.websocket("/ws/monitor")
async def ws_monitor_route(ws: WebSocket):
    await ws.accept()
    connected_monitors.add(ws)
    
    init_payload = {
        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
        "ambulance": ambulance_data,
        "intersections": intersections,
        "alert": None
    }
    await ws.send_text(json.dumps(init_payload))

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
                        "ambulance": ambulance_data,
                        "intersections": intersections,
                        "alert": f"ম্যানুয়াল ওভাররাইড: {intersections[target_id]['name']}"
                    })
    except WebSocketDisconnect:
        connected_monitors.remove(ws)

@app.websocket("/ws/driver")
async def ws_driver_route(ws: WebSocket):
    await ws.accept()
    connected_drivers.add(ws)
    try:
        while True:
            text = await ws.receive_text()
            data = json.loads(text)

            lat = float(data.get("lat", ambulance_data["lat"]))
            lon = float(data.get("lon", ambulance_data["lon"]))
            speed = float(data.get("speed", 0))

            nearby_hospitals = []
            if data.get("req_nearby"):
                nearby_hospitals = find_nearest_hospitals(lat, lon)

            dest_lat = float(data.get("dest_lat", ambulance_data["dest_lat"]))
            dest_lon = float(data.get("dest_lon", ambulance_data["dest_lon"]))
            dest_name = data.get("destination", ambulance_data["destination"])

            # রিয়েল ওএসআরএম রুট এবং জ্যাম অ্যানালাইসিস
            dist_km, eta, coords, traffic = get_real_route_with_traffic(lat, lon, dest_lat, dest_lon)

            # গ্রিন করিডোর সক্রিয়করণ (অ্যাম্বুলেন্স ৪০০ মিটারের ভেতর এলে)
            corridor_engaged = False
            active_alert = None

            for i_id, i_info in intersections.items():
                if math.hypot(lat - i_info["lat"], lon - i_info["lon"]) < 0.0040:
                    i_info["signal"] = "GREEN"
                    i_info["corridor_active"] = True
                    corridor_engaged = True
                    active_alert = f"🚨 করিডোর সক্রিয়: {i_info['name']} খালি করা হচ্ছে!"
                else:
                    if not i_info.get("manual_override", False):
                        i_info["signal"] = "RED"
                        i_info["corridor_active"] = False

            ambulance_data.update({
                "lat": lat,
                "lon": lon,
                "speed": round(speed * 3.6, 1) if speed > 0 else 46,
                "destination": dest_name,
                "dest_lat": dest_lat,
                "dest_lon": dest_lon,
                "distance_km": dist_km,
                "eta_mins": eta,
                "route_coords": coords,
                "traffic_status": traffic,
                "is_live": True,
                "active_corridor": corridor_engaged,
                "last_update": datetime.now().strftime("%I:%M:%S %p")
            })

            # ড্রাইভারকে রেসপন্স পাঠানো
            resp = dict(ambulance_data)
            if nearby_hospitals:
                resp["nearby_hospitals"] = nearby_hospitals
            await ws.send_text(json.dumps(resp))

            # পুলিশ ও সেন্ট্রাল ড্যাশবোর্ডে লাইভ ব্রডকাস্ট
            await broadcast_to_monitors({
                "timestamp": ambulance_data["last_update"],
                "ambulance": ambulance_data,
                "intersections": intersections,
                "alert": active_alert
            })
    except WebSocketDisconnect:
        connected_drivers.remove(ws)
        if len(connected_drivers) == 0:
            ambulance_data["is_live"] = False
            await broadcast_to_monitors({
                "timestamp": datetime.now().strftime("%I:%M:%S %p"),
                "ambulance": ambulance_data,
                "intersections": intersections,
                "alert": "অ্যাম্বুলেন্সের লাইভ সংযোগ বিচ্ছিন্ন হয়েছে।"
            })