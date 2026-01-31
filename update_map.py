import os
import requests
import gpxpy
import folium
import logging
import json
import time
from folium.plugins import HeatMap

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

USER_ID = os.getenv("KOMOOT_USER_ID")
DATA_FILE = "all_points.json"

def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {"points": [], "tour_ids": [], "stats": {"dist": 0, "elev": 0, "count": 0}}

def run_sync():
    if not USER_ID: return
    
    # On vide le cache de la session pour paraître neuf
    session = requests.Session()
    storage = load_existing_data()
    new_count = 0
    
    # Liste des URLs à tester pour contourner la 403
    api_urls = [
        f"https://www.komoot.com/api/v007/users/{USER_ID}/tours/?type=tour_recorded&sort_field=date&sort_direction=desc&limit=100",
        f"https://www.komoot.com/api/v1/users/{USER_ID}/tours/?type=tour_recorded&status=public&limit=100"
    ]

    for url in api_urls:
        logger.info(f"Tentative : {url}")
        try:
            resp = session.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code == 200:
                tours = resp.json().get('_embedded', {}).get('tours', [])
                for tour in tours:
                    t_id = str(tour['id'])
                    if t_id not in storage["tour_ids"]:
                        # Téléchargement direct du GPX
                        res_gpx = session.get(f"https://www.komoot.com/api/v1/tours/{t_id}.gpx")
                        if res_gpx.status_code == 200:
                            gpx = gpxpy.parse(res_gpx.text)
                            storage["stats"]["dist"] += gpx.length_2d()
                            storage["stats"]["count"] += 1
                            storage["tour_ids"].append(t_id)
                            for track in gpx.tracks:
                                for seg in track.segments:
                                    for p in seg.points:
                                        storage["points"].append([round(p.latitude, 5), round(p.longitude, 5)])
                            new_count += 1
                break # Si une API marche, on s'arrête là
        except: continue

    if storage["points"]:
        with open(DATA_FILE, 'w') as f: json.dump(storage, f)
        m = folium.Map(location=storage["points"][-1], zoom_start=11, tiles='CartoDB dark_matter')
        HeatMap(storage["points"], radius=3, blur=2).add_to(m)
        m.save("index.html")
        logger.info(f"✅ Terminé : {new_count} nouveaux tours ajoutés.")

if __name__ == "__main__":
    run_sync()
