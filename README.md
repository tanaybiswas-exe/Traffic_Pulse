# 🚑 TrafficPulse — Intelligent Transportation & Emergency Preemption System (ITS)

[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![WebSockets](https://img.shields.io/badge/RealTime-WebSockets-010101?style=flat-square&logo=socketdotio&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API)
[![Leaflet](https://img.shields.io/badge/Mapping-Leaflet.js-199900?style=flat-square&logo=leaflet&logoColor=white)](https://leafletjs.com/)
[![TomTom](https://img.shields.io/badge/Traffic-TomTom_API-df1b12?style=flat-square)](https://developer.tomtom.com/)
[![TailwindCSS](https://img.shields.io/badge/UI-Tailwind_CSS-38bdf8?style=flat-square&logo=tailwindcss&logoColor=white)](https://tailwindcss.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)

**TrafficPulse** is an automated, real-time Intelligent Transportation System (ITS) designed to eliminate transit delays for emergency responders in dense urban corridors. By bridging responder vehicles, municipal traffic signal networks, and civilian motorists over low-latency WebSockets, TrafficPulse automates green light preemption corridors and helps preserve the critical "Golden Hour."

---

## 🌐 Live Deployment

* 🖥️ **Central Operations Hub & Map:** [https://traffic-pulse-u2z2.onrender.com/](https://traffic-pulse-u2z2.onrender.com/)
* 🚑 **Driver Navigation Console:** `/driver`
* 📡 **Public Civilian V2X Feed:** `/civilian`
* 🛡️ **Sergeant Handheld Terminal:** `/sergeant`

> **Note on Free-Tier Hosting:** Hosted on Render's free tier. If the server is idle, initial cold boot may take 30–50 seconds.

---

## ⚡ Key Highlights & System Architecture

```text
 🚑 Active Ambulance (GPS / Telemetry)
         │  (Bi-directional WebSockets)
         ▼
 ⚙️ TrafficPulse Core Engine (FastAPI)
         ├── 🧠 Dynamic AI Preemption Arbiter (Cardiac > Stroke > Trauma > Standard)
         ├── 🗺️ OSRM Engine (True Road-Geometry Routing)
         ├── 🚦 OSM Overpass (Intersection Querying)
         └── 🛰️ TomTom Traffic API (Live Flow & Congestion Index)
         │
         ├──► 🚥 Municipal Intersections (Automated Green Corridor Preemption)
         ├──► 📱 Public Civilian V2X (500m Doppler Geofence Chime & Notifications)
         ├──► 👮 Sergeant Handheld (Tactical Override & Patrol Advisory)
         └──► 📄 Automated Audit PDF Engine (Trip Duration & Clearance Records)
