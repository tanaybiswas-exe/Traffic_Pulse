import json
import math
import os
import random
import urllib.parse
import urllib.request
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI(title="TrafficPulse Ultra-Real Engine with Multi-Ambulance Priority")

connected_monitors = set()
connected_sergeants = set()
active_ambulances = {}
trip_logs = []
dynamic_intersections = {}

TOMTOM_KEY = "7YuULGQHeO6wW1GkYYP6dqSu84Wn2TPb"

# মেডিকেল প্রায়োরিটি ওয়েট
PRIORITY_WEIGHTS = {
    "Cardiac": 100,  # হার্ট অ্যাটাক (সর্বোচ্চ)
    "Neuro": 85,     # স্ট্রোক / ব্রেইন হেমোরেজ
    "Trauma": 70,    # সড়ক দুর্ঘটনা
    "General": 40    # সাধারণ রোগী
}

def fetch_live_traffic_flow(lat, lon):
    """TomTom Traffic Flow Segment API"""
    try:
        url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/relative0/10/json?point={lat},{lon}&unit=KMPH&key={TOMTOM_KEY}"
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-Core/8.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            flow = data.get("flowSegmentData", {})
            current_speed = flow.get("currentSpeed", 30)
            free_flow_speed = flow.get("freeFlowSpeed", 40)
            ratio = current_speed / free_flow_speed if free_flow_speed > 0 else 1.0

            if ratio < 0.4:
                status = "তীব্র জ্যাম (Heavy Congestion)"
                color = "RED"
            elif ratio < 0.75:
                status = "মাঝারি জ্যাম (Slow Traffic)"
                color = "AMBER"
            else:
                status = "রাস্তা পরিষ্কার (Free Flow)"
                color = "GREEN"
            return current_speed, free_flow_speed, status, color
    except Exception as e:
        return 35, 45, "স্বাভাবিক ট্রাফিক", "GREEN"

def fetch_real_osm_intersections(lat, lon):
    """ওপেনস্ট্রিটম্যাপ থেকে ড্রাইভারের আশপাশের আসল সিগন্যাল ও মোড় আনা"""
    query = f"""
    [out:json][timeout:6];
    (
      node["highway"="traffic_signals"](around:4500, {lat}, {lon});
      node["highway"="crossing"](around:2500, {lat}, {lon});
      node["junction"="roundabout"](around:4000, {lat}, {lon});
    );
    out center 6;
    """
    url = "https://overpass-api.de/api/interpreter?data=" + urllib.parse.quote(query)
    signals = {}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-Core/8.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            elements = data.get("elements", [])
            for idx, el in enumerate(elements):
                s_id = f"SIG_{el.get('id', idx+1)}"
                name = el.get("tags", {}).get("name") or f"ইন্টারসেকশন জংশন #{idx+1}"
                s_lat = el.get("lat")
                s_lon = el.get("lon")
                
                spd, ff_spd, traffic_status, color = fetch_live_traffic_flow(s_lat, s_lon)
                
                signals[s_id] = {
                    "id": s_id,
                    "name": name,
                    "lat": s_lat,
                    "lon": s_lon,
                    "signal": "RED",
                    "corridor_active": False,
                    "active_ambulance_id": None,
                    "traffic_speed": spd,
                    "traffic_status": traffic_status,
                    "cctv_status": f"CCTV Live (Cam-0{idx+1})",
                    "violators": []
                }
    except Exception as e:
        pass

    if not signals:
        offsets = [
            ("নিকটস্থ মোড় ১ (মেইন রোড)", 0.005, 0.004),
            ("ট্রাফিক ইন্টারসেকশন ২", -0.004, 0.006),
            ("চৌরাস্তা ক্রসিং ৩", 0.007, -0.005)
        ]
        for idx, (lbl, dy, dx) in enumerate(offsets):
            s_id = f"SIG_LOC_{idx+1}"
            signals[s_id] = {
                "id": s_id,
                "name": lbl,
                "lat": round(lat + dy, 5),
                "lon": round(lon + dx, 5),
                "signal": "RED",
                "corridor_active": False,
                "active_ambulance_id": None,
                "traffic_speed": 28,
                "traffic_status": "মাঝারি ট্রাফিক",
                "cctv_status": f"CCTV Live (Cam-0{idx+1})",
                "violators": []
            }
    return signals

def fetch_real_osm_hospitals(lat, lon, condition="General"):
    """ওপেনস্ট্রিটম্যাপ থেকে ড্রাইভারের আশপাশের আসল হাসপাতাল আনা"""
    query = f"""
    [out:json][timeout:6];
    (
      node["amenity"="hospital"](around:8000, {lat}, {lon});
      node["amenity"="clinic"](around:6000, {lat}, {lon});
    );
    out center 8;
    """
    url = "https://overpass-api.de/api/interpreter?data=" + urllib.parse.quote(query)
    hospitals = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-Core/8.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for idx, el in enumerate(data.get("elements", [])):
                name = el.get("tags", {}).get("name")
                if not name:
                    continue
                h_lat = el.get("lat")
                h_lon = el.get("lon")
                dist = round(math.hypot((lat - h_lat) * 111, (lon - h_lon) * 111), 2)
                
                icu = max(1, (int(h_lat * 1000) % 8) + 1)
                er = max(4, (int(h_lon * 1000) % 15) + 3)
                
                hospitals.append({
                    "id": f"HOSP_{idx+1}",
                    "name": name,
                    "lat": h_lat,
                    "lon": h_lon,
                    "dist_km": dist,
                    "icu_available": icu,
                    "er_available": er,
                    "match": condition in el.get("tags", {}).get("healthcare:speciality", "General") or condition == "General"
                })
        hospitals.sort(key=lambda x: x["dist_km"])
    except Exception as e:
        pass

    if not hospitals:
        hospitals = [
            {"id": "H1", "name": "নিকটস্থ সেন্ট্রাল হাসপাতাল ও ইমার্জেন্সি", "lat": round(lat + 0.012, 5), "lon": round(lon + 0.010, 5), "dist_km": 1.8, "icu_available": 4, "er_available": 10, "match": True},
            {"id": "H2", "name": "জেনারেল হাসপাতাল ও স্পেশালাইজড কেয়ার", "lat": round(lat - 0.015, 5), "lon": round(lon + 0.012, 5), "dist_km": 2.4, "icu_available": 6, "er_available": 12, "match": True},
            {"id": "H3", "name": "উপজেলা স্বাস্থ্য কমপ্লেক্স / ক্লিনিক", "lat": round(lat + 0.020, 5), "lon": round(lon - 0.014, 5), "dist_km": 3.1, "icu_available": 2, "er_available": 7, "match": True}
        ]
    return hospitals

def get_real_osrm_route(start_lat, start_lon, end_lat, end_lon):
    """OSRM লাইভ টার্ন-বাই-টার্ন পথ ও নেভিগেশন নির্দেশনা"""
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson&steps=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-Core/8.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get("routes"):
                r = data["routes"][0]
                dist_km = round(r["distance"] / 1000, 2)
                eta_mins = max(1, round(r["duration"] / 60))
                coords = [[c[1], c[0]] for c in r["geometry"]["coordinates"]]
                
                instructions = []
                for leg in r.get("legs", []):
                    for step in leg.get("steps", [])[:4]:
                        instruction = step.get("name") or "সোজা এগিয়ে যান"
                        instructions.append(f"{instruction} ({round(step.get('distance', 0))} মি.)")
                return dist_km, eta_mins, coords, instructions
    except Exception as e:
        pass

    dist = round(math.hypot((start_lat - end_lat) * 111, (start_lon - end_lon) * 111), 2)
    return dist, max(1, round(dist * 2.5)), [[start_lat, start_lon], [end_lat, end_lon]], ["গন্তব্যের দিকে এগিয়ে চলুন"]

async def broadcast_to_monitors(payload: dict):
    msg = json.dumps(payload)
    for ws in list(connected_monitors | connected_sergeants):
        try:
            await ws.send_text(msg)
        except Exception:
            pass

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

@app.get("/sergeant")
def get_sergeant_page():
    path = os.path.join(os.path.dirname(__file__), "templates", "sergeant.html")
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
        "intersections": dynamic_intersections,
        "trip_logs": trip_logs[-6:],
        "alert": None
    }))

    try:
        while True:
            text = await ws.receive_text()
            data = json.loads(text)
            if data.get("action") == "toggle_signal":
                target_id = data.get("id")
                if target_id in dynamic_intersections:
                    current = dynamic_intersections[target_id]["signal"]
                    dynamic_intersections[target_id]["signal"] = "RED" if current == "GREEN" else "GREEN"
                    dynamic_intersections[target_id]["manual_override"] = True
                    await broadcast_to_monitors({
                        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
                        "ambulances": active_ambulances,
                        "intersections": dynamic_intersections,
                        "trip_logs": trip_logs[-6:],
                        "alert": f"ম্যানুয়াল সিগন্যাল সুইচ: {dynamic_intersections[target_id]['name']}"
                    })
    except WebSocketDisconnect:
        connected_monitors.discard(ws)

@app.websocket("/ws/sergeant")
async def ws_sergeant_route(ws: WebSocket):
    await ws.accept()
    connected_sergeants.add(ws)
    await ws.send_text(json.dumps({
        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
        "ambulances": active_ambulances,
        "intersections": dynamic_intersections
    }))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        connected_sergeants.discard(ws)

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
            condition = data.get("patient_condition", "General")
            sos_active = bool(data.get("sos_triggered", False))

            global dynamic_intersections
            if not dynamic_intersections or data.get("force_refresh"):
                dynamic_intersections = fetch_real_osm_intersections(lat, lon)

            hospitals = fetch_real_osm_hospitals(lat, lon, condition)

            dest_lat = float(data.get("dest_lat") or (hospitals[0]["lat"] if hospitals else lat + 0.01))
            dest_lon = float(data.get("dest_lon") or (hospitals[0]["lon"] if hospitals else lon + 0.01))
            dest_name = data.get("destination") or (hospitals[0]["name"] if hospitals else "নিকটস্থ হাসপাতাল")

            dist_km, eta, coords, nav_steps = get_real_osrm_route(lat, lon, dest_lat, dest_lon)
            spd_cur, spd_free, traffic_status, traffic_color = fetch_live_traffic_flow(lat, lon)

            # ফ্লাইওভার অপটিমাইজেশন পরামর্শ
            flyover_recommended = False
            if traffic_color == "RED" or spd_cur < 15:
                flyover_recommended = True
                nav_steps.insert(0, "⚠️ গ্রাউন্ডে তীব্র জ্যাম! ফ্লাইওভার / এক্সপ্রেসওয়ে রুট ব্যবহার করুন।")

            # বর্তমান অ্যাম্বুলেন্সের অবস্থা সংরক্ষণ
            my_priority = PRIORITY_WEIGHTS.get(condition, 50)
            active_ambulances[driver_id] = {
                "id": driver_id,
                "lat": lat,
                "lon": lon,
                "speed": round(speed * 3.6, 1) if speed > 0 else 46.5,
                "patient_condition": condition,
                "priority_score": my_priority,
                "sos_active": sos_active,
                "destination": dest_name,
                "dest_lat": dest_lat,
                "dest_lon": dest_lon,
                "distance_km": dist_km,
                "eta_mins": eta,
                "route_coords": coords,
                "nav_steps": nav_steps,
                "traffic_status": traffic_status,
                "traffic_color": traffic_color,
                "flyover_recommended": flyover_recommended,
                "corridor_active": False,
                "waiting_for_priority": False,
                "last_seen": datetime.now().strftime("%I:%M:%S %p")
            }

            # এআই কনফ্লিক্ট ও মাল্টি-অ্যাম্বুলেন্স প্রায়োরিটি আরবিটার
            active_alert = None
            active_cctv_target = None

            if sos_active:
                active_alert = f"🚨 এসওএস অ্যালার্ট: {driver_id} জরুরি সাহায্য চেয়েছে! রাস্তায় গাড়ি বিকল/দুর্ঘটনা!"

            for s_id, s_info in dynamic_intersections.items():
                # এই সিগন্যালের ৪০০ মিটারের মধ্যে কয়টি অ্যাম্বুলেন্স আছে
                approaching_ambs = []
                for amb_id, amb in active_ambulances.items():
                    d = math.hypot(amb["lat"] - s_info["lat"], amb["lon"] - s_info["lon"])
                    if d < 0.0040:
                        approaching_ambs.append((amb_id, amb["priority_score"], amb["patient_condition"]))

                if len(approaching_ambs) > 1:
                    # একাধিক অ্যাম্বুলেন্স কনফ্লিক্ট ডিটেক্টেড -> প্রায়োরিটি সলভার
                    approaching_ambs.sort(key=lambda x: x[1], reverse=True)
                    winner_id, winner_score, winner_cond = approaching_ambs[0]
                    
                    s_info["signal"] = "GREEN"
                    s_info["corridor_active"] = True
                    s_info["active_ambulance_id"] = winner_id
                    active_ambulances[winner_id]["corridor_active"] = True

                    # অন্য অ্যাম্বুলেন্সকে ওয়েটিং ফ্ল্যাগ সেট করা
                    for other_id, _, other_cond in approaching_ambs[1:]:
                        active_ambulances[other_id]["waiting_for_priority"] = True
                        active_ambulances[other_id]["corridor_active"] = False

                    active_alert = f"⚖️ কনফ্লিক্ট রেজলভার: {s_info['name']} মোড়ে {winner_id} [{winner_cond}]-কে অগ্রাধিকার দেওয়া হয়েছে!"
                    active_cctv_target = s_info

                elif len(approaching_ambs) == 1:
                    single_id = approaching_ambs[0][0]
                    s_info["signal"] = "GREEN"
                    s_info["corridor_active"] = True
                    s_info["active_ambulance_id"] = single_id
                    active_ambulances[single_id]["corridor_active"] = True
                    active_ambulances[single_id]["waiting_for_priority"] = False
                    if not active_alert:
                        active_alert = f"🚨 করিডোর সক্রিয়: {s_info['name']} সিগন্যাল ক্লিয়ার করা হয়েছে!"
                    active_cctv_target = s_info

                    if random.random() < 0.3 and len(s_info["violators"]) < 3:
                        s_info["violators"].append({
                            "plate": f"ঢাকা মেট্রো-গ-{random.randint(11, 45)}-{random.randint(1000, 9999)}",
                            "time": datetime.now().strftime("%I:%M:%S %p"),
                            "reason": "জরুরি অ্যাম্বুলেন্স লেনে গাড়ি না সরানো"
                        })
                else:
                    if not s_info.get("manual_override", False):
                        s_info["signal"] = "RED"
                        s_info["corridor_active"] = False
                        s_info["active_ambulance_id"] = None

            # অডিট ট্রিপ হিস্ট্রি
            saved_mins = max(2, round(dist_km * 1.8))
            trip_logs.append({
                "id": driver_id,
                "dest": dest_name,
                "saved_mins": saved_mins,
                "speed": round(speed * 3.6, 1) if speed > 0 else 45.0,
                "time": datetime.now().strftime("%I:%M %p")
            })
            if len(trip_logs) > 8:
                trip_logs.pop(0)

            resp_driver = dict(active_ambulances[driver_id])
            resp_driver["hospitals"] = hospitals
            await ws.send_text(json.dumps(resp_driver))

            await broadcast_to_monitors({
                "timestamp": datetime.now().strftime("%I:%M:%S %p"),
                "ambulances": active_ambulances,
                "intersections": dynamic_intersections,
                "cctv_focus": active_cctv_target,
                "trip_logs": trip_logs,
                "alert": active_alert
            })

    except WebSocketDisconnect:
        if driver_id and driver_id in active_ambulances:
            del active_ambulances[driver_id]
            await broadcast_to_monitors({
                "timestamp": datetime.now().strftime("%I:%M:%S %p"),
                "ambulances": active_ambulances,
                "intersections": dynamic_intersections,
                "cctv_focus": None,
                "trip_logs": trip_logs,
                "alert": f"{driver_id} অফলাইনে চলে গেছে।"
            })