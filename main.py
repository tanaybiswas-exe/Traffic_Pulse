import json
import math
import os
import urllib.request
import urllib.parse
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI(title="TrafficPulse Smart Navigation")

connected_monitors = set()
connected_drivers = set()

# Intersection signals (Dhaka route demo)
intersections = {
    "INT_01": {"id": "INT_01", "name": "Shahbagh Crossing", "lat": 23.7383, "lon": 90.3956, "signal": "RED", "corridor_active": False, "traffic_density": "Heavy (85%)"},
    "INT_02": {"id": "INT_02", "name": "Kawran Bazar Crossing", "lat": 23.7508, "lon": 90.3935, "signal": "RED", "corridor_active": False, "traffic_density": "Moderate (55%)"},
    "INT_03": {"id": "INT_03", "name": "Farmgate Junction", "lat": 23.7570, "lon": 90.3888, "signal": "RED", "corridor_active": False, "traffic_density": "High (78%)"}
}

ambulance_data = {
    "lat": 23.7330,
    "lon": 90.3980,
    "speed": 0,
    "destination": "Selecting nearest...",
    "dest_lat": 23.7260,
    "dest_lon": 90.3976,
    "distance_km": 0,
    "eta_mins": 0,
    "route_coords": [],
    "traffic_status": "Normal",
    "is_live": False,
    "active_corridor": False,
    "last_update": "Waiting..."
}

def find_nearest_hospitals(lat, lon):
    """OpenStreetMap Overpass API diye 5km radius-er hospital/clinic fetch kora"""
    query = f"""
    [out:json][timeout:5];
    (
      node["amenity"="hospital"](around:5000, {lat}, {lon});
      node["amenity"="clinic"](around:5000, {lat}, {lon});
    );
    out center 6;
    """
    url = "https://overpass-api.de/api/interpreter?data=" + urllib.parse.quote(query)
    hospitals = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-Navigator/1.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            for el in data.get("elements", []):
                name = el.get("tags", {}).get("name", "Local Hospital/Clinic")
                h_lat = el.get("lat")
                h_lon = el.get("lon")
                dist = round(math.hypot((lat - h_lat) * 111, (lon - h_lon) * 111), 2)
                hospitals.append({
                    "name": name,
                    "lat": h_lat,
                    "lon": h_lon,
                    "dist_km": dist,
                    "type": el.get("tags", {}).get("amenity", "hospital")
                })
        hospitals.sort(key=lambda x: x["dist_km"])
    except Exception as e:
        print(f"Hospital Fetch Error: {e}")
    
    # Fallback jodi network timeout hoy
    if not hospitals:
        hospitals = [
            {"name": "Dhaka Medical College (DMCH)", "lat": 23.7260, "lon": 90.3976, "dist_km": 2.1, "type": "Govt Hospital"},
            {"name": "BSMMU (PG Hospital)", "lat": 23.7398, "lon": 90.3958, "dist_km": 1.4, "type": "Specialized"},
            {"name": "Square Hospital Ltd.", "lat": 23.7533, "lon": 90.3816, "dist_km": 3.2, "type": "Private"}
        ]
    return hospitals

def get_real_route_with_traffic(start_lat, start_lon, end_lat, end_lon):
    """OSRM real driving route, distance and traffic estimate"""
    try:
        url = f"https://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson&annotations=true"
        req = urllib.request.Request(url, headers={'User-Agent': 'TrafficPulse-Navigator/1.0'})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get("routes"):
                route = data["routes"][0]
                dist_km = round(route["distance"] / 1000, 2)
                duration = route["duration"] / 60
                
                # Traffic estimation based on average city speed vs free-flow speed
                avg_speed = (dist_km / (duration / 60)) if duration > 0 else 30
                if avg_speed < 12:
                    traffic_status = "Heavy Traffic / Road Slow"
                elif avg_speed < 25:
                    traffic_status = "Moderate Traffic"
                else:
                    traffic_status = "Clear Road / Fast Flow"

                coords = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
                return dist_km, max(1, round(duration)), coords, traffic_status
    except Exception:
        pass
    
    # Fallback Linear Calculation
    dist = round(math.hypot((start_lat - end_lat) * 111, (start_lon - end_lon) * 111), 2)
    return dist, max(1, round(dist * 2.8)), [[start_lat, start_lon], [end_lat, end_lon]], "Standard Traffic"

async def broadcast_to_monitors(payload: dict):
    disconnected = set()
    msg = json.dumps(payload)
    for ws in list(connected_monitors):
        try:
            await ws.send_text(msg)
        except Exception:
            disconnected.add(ws)
    connected_monitors.difference_update(disconnected)

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

@app.websocket("/ws/monitor")
async def ws_monitor_route(ws: WebSocket):
    await ws.accept()
    connected_monitors.add(ws)
    await ws.send_text(json.dumps({
        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
        "ambulance": ambulance_data,
        "intersections": intersections,
        "alert": None
    }))
    try:
        while True:
            text = await ws.receive_text()
            data = json.loads(text)
            if data.get("action") == "toggle_signal":
                t_id = data.get("id")
                if t_id in intersections:
                    intersections[t_id]["signal"] = "RED" if intersections[t_id]["signal"] == "GREEN" else "GREEN"
                    intersections[t_id]["manual_override"] = True
                    await broadcast_to_monitors({
                        "timestamp": datetime.now().strftime("%I:%M:%S %p"),
                        "ambulance": ambulance_data,
                        "intersections": intersections,
                        "alert": f"Manual signal override: {intersections[t_id]['name']}"
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

            # Fetch nearby hospitals dynamically if requested or first time
            nearby_hospitals = []
            if data.get("req_nearby"):
                nearby_hospitals = find_nearest_hospitals(lat, lon)

            dest_lat = float(data.get("dest_lat", ambulance_data["dest_lat"]))
            dest_lon = float(data.get("dest_lon", ambulance_data["dest_lon"]))
            dest_name = data.get("destination", ambulance_data["destination"])

            # Real-time traffic path
            dist_km, eta, coords, traffic = get_real_route_with_traffic(lat, lon, dest_lat, dest_lon)

            # Auto Green Wave Corridor Trigger
            corridor_engaged = False
            active_alert = None
            for i_id, i_info in intersections.items():
                if math.hypot(lat - i_info["lat"], lon - i_info["lon"]) < 0.0035:
                    i_info["signal"] = "GREEN"
                    i_info["corridor_active"] = True
                    corridor_engaged = True
                    active_alert = f"GREEN CORRIDOR ENGAGED: {i_info['name']} cleared!"
                else:
                    if not i_info.get("manual_override", False):
                        i_info["signal"] = "RED"
                        i_info["corridor_active"] = False

            ambulance_data.update({
                "lat": lat,
                "lon": lon,
                "speed": round(speed * 3.6, 1) if speed > 0 else 42,
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

            # Send back to Driver with nearby hospital list
            driver_response = dict(ambulance_data)
            if nearby_hospitals:
                driver_response["nearby_hospitals"] = nearby_hospitals
            await ws.send_text(json.dumps(driver_response))

            # Broadcast to Police Monitors
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
                "alert": "Ambulance connection disconnected."
            })