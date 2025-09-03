import logging
from flask import Flask, render_template, request, jsonify
import googlemaps
import requests
from dotenv import load_dotenv
import os
from typing import Dict, List, Optional, Any

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")

# Configuration
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
TOMTOM_API_KEY = os.getenv("TOMTOM_API_KEY")
OPENROUTESERVICE_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY")

# Validate API keys
if not all([GOOGLE_MAPS_API_KEY, TOMTOM_API_KEY, OPENROUTESERVICE_API_KEY]):
    raise ValueError("Missing one or more required API keys in environment variables.")

gmaps = googlemaps.Client(key=GOOGLE_MAPS_API_KEY)

def get_route(start: str, end: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch cycling directions from Google Maps API."""
    try:
        directions = gmaps.directions(start, end, mode="bicycling", alternatives=True)
        return directions if directions else None
    except Exception as e:
        logger.error(f"Error fetching Google Maps directions: {e}")
        return None

def get_traffic_flow(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Fetch traffic flow data from TomTom API."""
    url = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
    params = {
        "point": f"{lat},{lon}",
        "unit": "KMPH",
        "key": TOMTOM_API_KEY,
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.json()
    except Exception as e:
        logger.error(f"Error fetching TomTom traffic data: {e}")
        return None

def get_elevation_data(start: Dict[str, float], end: Dict[str, float]) -> float:
    """Fetch elevation data from OpenRouteService API."""
    url = "https://api.openrouteservice.org/v2/directions/cycling-regular"
    headers = {
        "Authorization": OPENROUTESERVICE_API_KEY,
    }
    body = {
        "coordinates": [[start['lng'], start['lat']], [end['lng'], end['lat']]],
        "instructions": "true",
    }
    try:
        response = requests.post(url, headers=headers, json=body, timeout=10)
        response.raise_for_status()  # Raise an exception for HTTP errors
        data = response.json()
        return data['features'][0]['properties']['ascent'] if 'features' in data else 0
    except Exception as e:
        logger.error(f"Error fetching OpenRouteService elevation data: {e}")
        return 0

def rank_routes(directions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rank routes based on safety score."""
    ranked_routes = []
    for route in directions:
        try:
            distance = route['legs'][0]['distance']['value']
            duration = route['legs'][0]['duration']['value']
            start_location = route['legs'][0]['start_location']

            traffic_data = get_traffic_flow(start_location['lat'], start_location['lng'])
            traffic_flow = traffic_data['flowSegmentData']['currentSpeed'] / 20 if traffic_data else 3
            elevation_gain = get_elevation_data(start_location, route['legs'][0]['end_location'])

            safety_score = (
                (traffic_flow * 0.4) +  # Lower traffic flow = safer
                (elevation_gain * 0.3) +  # Lower elevation gain = safer
                (distance * 0.2) +  # Shorter distance = safer
                (duration * 0.1)  # Shorter duration = safer
            )

            ranked_routes.append({
                "route": route,
                "safety_score": safety_score,
                "traffic_flow": traffic_flow,
                "elevation_gain": elevation_gain,
            })
        except Exception as e:
            logger.error(f"Error ranking route: {e}")
            continue

    ranked_routes.sort(key=lambda x: x["safety_score"])
    return ranked_routes

@app.route("/")
def home() -> str:
    """Render the home page."""
    return render_template("index.html", google_maps_api_key=GOOGLE_MAPS_API_KEY)

@app.route("/plan_route", methods=["POST"])
def plan_route() -> jsonify:
    """Plan the safest route based on user input."""
    try:
        data = request.get_json()
        if not data or 'start' not in data or 'end' not in data:
            return jsonify({"error": "Invalid input: 'start' and 'end' are required"}), 400

        start = data["start"]
        end = data["end"]

        if not isinstance(start, str) or not isinstance(end, str):
            return jsonify({"error": "Invalid input: 'start' and 'end' must be strings"}), 400

        directions = get_route(start, end)
        if not directions:
            return jsonify({"error": "Failed to fetch route data"}), 500

        ranked_routes = rank_routes(directions)

        if not ranked_routes:
            return jsonify({"error": "No routes available"}), 500

        safest_route = ranked_routes[0]["route"]
        eta = safest_route['legs'][0]['duration']['value']
        distance = safest_route['legs'][0]['distance']['value']

        return jsonify({
            "safest_route": safest_route,
            "eta": eta,
            "distance": distance,
            "traffic_flow": ranked_routes[0]["traffic_flow"],
            "elevation_gain": ranked_routes[0]["elevation_gain"],
        })
    except Exception as e:
        logger.error(f"Error in plan_route: {e}")
        return jsonify({"error": "An internal server error occurred"}), 500

if __name__ == "__main__":
    app.run(debug=False, port=5001)  # Disable debug mode in production
