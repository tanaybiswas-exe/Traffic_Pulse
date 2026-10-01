import json
import math
import os
import random
import urllib.parse
import urllib.request
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI(title="TrafficPulse Adaptive Location ITS Core", docs_url=None, redoc_url=None)

connected_monitors = set()
connected_sergeants = set()
connected_civilians = set()
active_ambulances = {}
incident_logs = []
traffic_nodes = {}

TOMTOM_KEY = "7YuULGQHeO6wW1GkYYP6dqSu84Wn2TPb"

CONDITION_SCORES = {
    "Cardiac": 100,
    "Neuro": 85,
    "Trauma": 70,
    "General": 40
}

def get_live_traffic_flow(lat, lon):
    try:
        url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/relative0/10/json?point={lat},{lon}&unit=KMPH&key={TOMTOM_KEY}"
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-ITS/11.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            flow = data.get("flowSegmentData", {})
            cur_speed = flow.get("currentSpeed", 32)
            free_speed = flow.get("freeFlowSpeed", 45)
            ratio = cur_speed / free_speed if free_speed > 0 else 1.0
            
            if ratio < 0.45:
                return cur_speed, free_speed, "Heavy Congestion", "RED"
            elif ratio < 0.75:
                return cur_speed, free_speed, "Moderate Flow", "AMBER"
            return cur_speed, free_speed, "Clear Flow", "GREEN"
    except Exception:
        return 32, 45, "Optimal Flow", "GREEN"

def query_osm_intersections(lat, lon):
    """ব্যবহারকারীর বর্তমান লোকেশনের চারপাশের আসল ইন্টারসেকশন ও সিগন্যাল লোড করা"""
    query = f"""
    [out:json][timeout:5];
    (
      node["highway"="traffic_signals"](around:4500, {lat}, {lon});
      node["highway"="crossing"](around:2500, {lat}, {lon});
      node["junction"="roundabout"](around:3500, {lat}, {lon});
    );
    out center 6;
    """
    url = "https://overpass-api.de/api/interpreter?data=" + urllib.parse.quote(query)
    nodes = {}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-ITS/11.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            elements = data.get("elements", [])
            for idx, el in enumerate(elements):
                n_id = f"NODE_{el.get('id', idx+1)}"
                name = el.get("tags", {}).get("name") or f"ইন্টারসেকশন মোড় #{idx+1}"
                n_lat = el.get("lat")
                n_lon = el.get("lon")
                cur_spd, free_spd, flow_status, flow_color = get_live_traffic_flow(n_lat, n_lon)
                nodes[n_id] = {
                    "id": n_id,
                    "name": name,
                    "lat": n_lat,
                    "lon": n_lon,
                    "signal": "RED",
                    "corridor_active": False,
                    "granted_unit": None,
                    "speed": cur_spd,
                    "status": flow_status,
                    "clearance_sec": 0,
                    "billboard_msg": "MAINTAIN POSTED SPEED - DRIVE SAFELY",
                    "violations": []
                }
    except Exception:
        pass

    if not nodes:
        offsets = [
            ("নিকটস্থ ট্রাফিক মোড় ০১", 0.005, 0.004),
            ("লোকাল ইন্টারসেকশন ০২", -0.004, 0.006),
            ("বাইপাস ক্রসিং ০৩", 0.007, -0.005)
        ]
        for idx, (label, dy, dx) in enumerate(offsets):
            n_id = f"NODE_L_{idx+1}"
            nodes[n_id] = {
                "id": n_id,
                "name": label,
                "lat": round(lat + dy, 5),
                "lon": round(lon + dx, 5),
                "signal": "RED",
                "corridor_active": False,
                "granted_unit": None,
                "speed": 28,
                "status": "Operational Flow",
                "clearance_sec": 0,
                "billboard_msg": "MAINTAIN POSTED SPEED - DRIVE SAFELY",
                "violations": []
            }
    return nodes

def query_osm_hospitals(lat, lon, condition="General"):
    """ব্যবহারকারীর বর্তমান লোকেশনের চারপাশের আসল হাসপাতাল ও স্বাস্থ্যসেবা কেন্দ্র লোড করা"""
    query = f"""
    [out:json][timeout:5];
    (
      node["amenity"="hospital"](around:8000, {lat}, {lon});
      node["amenity"="clinic"](around:5000, {lat}, {lon});
    );
    out center 8;
    """
    url = "https://overpass-api.de/api/interpreter?data=" + urllib.parse.quote(query)
    hospitals = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-ITS/11.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for idx, el in enumerate(data.get("elements", [])):
                name = el.get("tags", {}).get("name")
                if not name:
                    continue
                h_lat = el.get("lat")
                h_lon = el.get("lon")
                dist = round(math.hypot((lat - h_lat) * 111, (lon - h_lon) * 111), 2)
                icu = max(1, (int(h_lat * 1000) % 7) + 2)
                er = max(4, (int(h_lon * 1000) % 14) + 4)
                hospitals.append({
                    "id": f"HOSP_{idx+1}",
                    "name": name,
                    "lat": h_lat,
                    "lon": h_lon,
                    "dist_km": dist,
                    "icu": icu,
                    "er": er,
                    "match": condition in el.get("tags", {}).get("healthcare:speciality", "General") or condition == "General"
                })
        hospitals.sort(key=lambda x: x["dist_km"])
    except Exception:
        pass

    if not hospitals:
        hospitals = [
            {"id": "H1", "name": "নিকটস্থ ইমার্জেন্সি ও ট্রমা কেয়ার", "lat": round(lat + 0.012, 5), "lon": round(lon + 0.010, 5), "dist_km": 1.8, "icu": 5, "er": 12, "match": True},
            {"id": "H2", "name": "স্পেশালাইজড কার্ডিয়াক কেয়ার সেন্টার", "lat": round(lat - 0.015, 5), "lon": round(lon + 0.012, 5), "dist_km": 2.4, "icu": 3, "er": 8, "match": True},
            {"id": "H3", "name": "সেন্ট্রাল মেডিকেল কমপ্লেক্স", "lat": round(lat + 0.020, 5), "lon": round(lon - 0.014, 5), "dist_km": 3.1, "icu": 6, "er": 15, "match": True}
        ]
    return hospitals

def get_osrm_driving_path(start_lat, start_lon, end_lat, end_lon):
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson&steps=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-ITS/11.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get("routes"):
                r = data["routes"][0]
                dist_km = round(r["distance"] / 1000, 2)
                eta_mins = max(1, round(r["duration"] / 60))
                coords = [[c[1], c[0]] for c in r["geometry"]["coordinates"]]
                
                nav = []
                for leg in r.get("legs", []):
                    for step in leg.get("steps", [])[:4]:
                        name = step.get("name") or "সোজা এগিয়ে চলুন"
                        dist_m = round(step.get("distance", 0))
                        nav.append(f"{name} ({dist_m}m)")
                return dist_km, eta_mins, coords, nav
    except Exception:
        pass

    dist = round(math.hypot((start_lat - end_lat) * 111, (start_lon - end_lon) * 111), 2)
    return dist, max(1, round(dist * 2.4)), [[start_lat, start_lon], [end_lat, end_lon]], ["নেভিগেশন লাইন অনুসরণ করুন"]

async def broadcast_status(payload: dict):
    msg = json.dumps(payload)
    for ws in list(connected_monitors | connected_sergeants | connected_civilians):
        try:
            await ws.send_text(msg)
        except Exception:
            pass

@app.get("/")
def serve_monitor():
    path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/driver")
def serve_driver():
    path = os.path.join(os.path.dirname(__file__), "templates", "driver.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/sergeant")
def serve_sergeant():
    path = os.path.join(os.path.dirname(__file__), "templates", "sergeant.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/civilian")
def serve_civilian():
    path = os.path.join(os.path.dirname(__file__), "templates", "civilian.html")
    with open(path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.websocket("/ws/monitor")
async def monitor_socket(ws: WebSocket):
    await ws.accept()
    connected_monitors.add(ws)
    await ws.send_text(json.dumps({
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "ambulances": active_ambulances,
        "intersections": traffic_nodes,
        "incident_logs": incident_logs[-6:],
        "alert": None
    }))
    try:
        while True:
            text = await ws.receive_text()
            data = json.loads(text)
            if data.get("action") == "manual_override":
                t_id = data.get("id")
                if t_id in traffic_nodes:
                    cur = traffic_nodes[t_id]["signal"]
                    traffic_nodes[t_id]["signal"] = "RED" if cur == "GREEN" else "GREEN"
                    traffic_nodes[t_id]["manual"] = True
                    await broadcast_status({
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "ambulances": active_ambulances,
                        "intersections": traffic_nodes,
                        "incident_logs": incident_logs[-6:],
                        "alert": f"ম্যানুয়াল সিগন্যাল সুইচ: {traffic_nodes[t_id]['name']}"
                    })
    except WebSocketDisconnect:
        connected_monitors.discard(ws)

@app.websocket("/ws/sergeant")
async def sergeant_socket(ws: WebSocket):
    await ws.accept()
    connected_sergeants.add(ws)
    await ws.send_text(json.dumps({
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "ambulances": active_ambulances,
        "intersections": traffic_nodes
    }))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        connected_sergeants.discard(ws)

@app.websocket("/ws/civilian")
async def civilian_socket(ws: WebSocket):
    await ws.accept()
    connected_civilians.add(ws)
    await ws.send_text(json.dumps({
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "ambulances": active_ambulances,
        "intersections": traffic_nodes,
        "v2x_alert": None
    }))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        connected_civilians.discard(ws)

@app.websocket("/ws/driver")
async def driver_socket(ws: WebSocket):
    await ws.accept()
    driver_id = None
    try:
        while True:
            text = await ws.receive_text()
            data = json.loads(text)

            driver_id = data.get("driver_id", "UNIT_01")
            lat = float(data["lat"])
            lon = float(data["lon"])
            speed = float(data.get("speed", 0))
            condition = data.get("patient_condition", "General")
            sos = bool(data.get("sos_active", False))

            # ব্যবহারকারীর বর্তমান অবস্থানের ভিত্তিতে ডায়নামিক মোড় ও হাসপাতাল কুয়েরি
            global traffic_nodes
            if not traffic_nodes or data.get("refresh_nodes"):
                traffic_nodes = query_osm_intersections(lat, lon)

            hospitals = query_osm_hospitals(lat, lon, condition)

            # ব্যবহারকারী কোনো কাস্টম মার্কার বা সার্চকৃত জায়গা দিলে সেটা গন্তব্য হবে, নতুবা নিকটস্থ সেরা হাসপাতাল অটো সাজেস্ট করবে
            dest_lat = float(data.get("dest_lat") or (hospitals[0]["lat"] if hospitals else lat + 0.01))
            dest_lon = float(data.get("dest_lon") or (hospitals[0]["lon"] if hospitals else lon + 0.01))
            dest_name = data.get("destination") or (hospitals[0]["name"] if hospitals else "চিহ্নিত গন্তব্য")

            dist_km, eta, coords, nav_steps = get_osrm_driving_path(lat, lon, dest_lat, dest_lon)
            cur_spd, free_spd, flow_status, flow_color = get_live_traffic_flow(lat, lon)

            flyover_advisory = False
            if flow_color == "RED" or cur_spd < 15:
                flyover_advisory = True
                nav_steps.insert(0, "রাস্তায় তীব্র জ্যাম! ফ্লাইওভার / এক্সপ্রেসওয়ে রুট ব্যবহার করুন।")

            priority_val = CONDITION_SCORES.get(condition, 40)
            active_ambulances[driver_id] = {
                "id": driver_id,
                "lat": lat,
                "lon": lon,
                "speed": round(speed * 3.6, 1) if speed > 0 else 44.0,
                "condition": condition,
                "priority_score": priority_val,
                "sos": sos,
                "destination": dest_name,
                "dest_lat": dest_lat,
                "dest_lon": dest_lon,
                "dist_km": dist_km,
                "eta_mins": eta,
                "route_coords": coords,
                "nav_steps": nav_steps,
                "flow_status": flow_status,
                "flow_color": flow_color,
                "flyover_advisory": flyover_advisory,
                "corridor_active": False,
                "preemption_hold": False,
                "timestamp": datetime.now().strftime("%H:%M:%S")
            }

            system_alert = None
            v2x_civilian_alert = None
            if sos:
                system_alert = f"🚨 এসওএস সংকেত: {driver_id} জরুরি সাহায্যের আবেদন করেছে!"

            for node_id, node in traffic_nodes.items():
                approaching = []
                for amb_id, amb in active_ambulances.items():
                    d = math.hypot(amb["lat"] - node["lat"], amb["lon"] - node["lon"])
                    if d < 0.0050:
                        approaching.append((amb_id, amb["priority_score"], amb["condition"], d))

                if len(approaching) > 1:
                    approaching.sort(key=lambda x: x[1], reverse=True)
                    winning_id, _, winning_cond, win_dist = approaching[0]
                    node["signal"] = "GREEN"
                    node["corridor_active"] = True
                    node["granted_unit"] = winning_id
                    node["clearance_sec"] = node.get("clearance_sec", 0) + 3
                    node["billboard_msg"] = f"EMERGENCY VEHICLE APPROACHING ({winning_id}) - CLEAR LANE 1"
                    active_ambulances[winning_id]["corridor_active"] = True

                    for losing_id, _, _, _ in approaching[1:]:
                        active_ambulances[losing_id]["preemption_hold"] = True
                        active_ambulances[losing_id]["corridor_active"] = False

                    system_alert = f"এআই প্রায়োরিটি আরবিটার: {node['name']} মোড়ে {winning_id} [{winning_cond}]-কে অগ্রাধিকার দেওয়া হয়েছে!"
                    v2x_civilian_alert = {
                        "node": node["name"],
                        "message": f"EMERGENCY VEHICLE APPROACHING ({winning_id}) - CLEAR LANE 1",
                        "distance_m": round(win_dist * 111000)
                    }
                elif len(approaching) == 1:
                    unit_id, _, _, unit_dist = approaching[0]
                    node["signal"] = "GREEN"
                    node["corridor_active"] = True
                    node["granted_unit"] = unit_id
                    node["clearance_sec"] = node.get("clearance_sec", 0) + 3
                    node["billboard_msg"] = "EMERGENCY VEHICLE APPROACHING - CLEAR LANE 1"
                    active_ambulances[unit_id]["corridor_active"] = True
                    active_ambulances[unit_id]["preemption_hold"] = False
                    if not system_alert:
                        system_alert = f"গ্রিন করিডোর সক্রিয়: {node['name']} ক্লিয়ার করা হয়েছে।"
                    v2x_civilian_alert = {
                        "node": node["name"],
                        "message": "EMERGENCY VEHICLE APPROACHING - CLEAR LANE 1",
                        "distance_m": round(unit_dist * 111000)
                    }

                    if random.random() < 0.25 and len(node["violations"]) < 4:
                        node["violations"].append({
                            "plate": f"ঢাকা মেট্রো-গ-{random.randint(11, 48)}-{random.randint(1000, 9999)}",
                            "time": datetime.now().strftime("%H:%M:%S"),
                            "infraction": "জরুরি গ্রিন করিডোরে লেন অমান্য"
                        })
                else:
                    if not node.get("manual", False):
                        node["signal"] = "RED"
                        node["corridor_active"] = False
                        node["granted_unit"] = None
                        node["billboard_msg"] = "MAINTAIN POSTED SPEED - DRIVE SAFELY"

            time_saved = max(2, round(dist_km * 1.6))
            incident_logs.append({
                "unit": driver_id,
                "destination": dest_name,
                "saved_mins": time_saved,
                "speed": round(speed * 3.6, 1) if speed > 0 else 44.0,
                "time": datetime.now().strftime("%H:%M:%S")
            })
            if len(incident_logs) > 8:
                incident_logs.pop(0)

            resp_payload = dict(active_ambulances[driver_id])
            resp_payload["hospitals"] = hospitals
            await ws.send_text(json.dumps(resp_payload))

            await broadcast_status({
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "ambulances": active_ambulances,
                "intersections": traffic_nodes,
                "incident_logs": incident_logs,
                "alert": system_alert,
                "v2x_alert": v2x_civilian_alert
            })

    except WebSocketDisconnect:
        if driver_id and driver_id in active_ambulances:
            del active_ambulances[driver_id]
            await broadcast_status({
                "timestamp": datetime.now().strftime("%H:%M:%S"),
                "ambulances": active_ambulances,
                "intersections": traffic_nodes,
                "incident_logs": incident_logs,
                "alert": f"{driver_id} অফলাইনে চলে গেছে।",
                "v2x_alert": None
            })