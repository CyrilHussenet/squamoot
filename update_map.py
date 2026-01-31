import os
import requests
import gpxpy
import folium
import logging
import json
import time
from folium.plugins import HeatMap
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# --- CONFIGURATION VIA SECRETS ---
USER_ID = os.getenv("KOMOOT_USER_ID")
DATA_FILE = "all_points.json"

def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except:
            logger.error("Erreur lecture JSON, réinitialisation.")
    return {"points": [], "tour_ids": [], "stats": {"dist": 0, "elev": 0, "count": 0}}

def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f)

def run_sync():
    if not USER_ID:
        logger.error("L'ID utilisateur est manquant dans les Secrets GitHub.")
        return

    session = requests.Session()
    session.headers.update({'User-Agent': 'Mozilla/5.0'})
    
    storage = load_existing_data()
    new_tours_found = 0

    try:
        # On scanne les 20 dernières sorties pour l'incrémental
        url = f"https://www.komoot.com/api/v007/users/{USER_ID}/tours/?type=tour_recorded&sort_field=date&sort_direction=desc&limit=20"
        resp = session.get(url)
        
        if resp.status_code != 200:
            logger.error(f"Erreur API {resp.status_code}")
            return

        tours = resp.json().get('_embedded', {}).get('tours', [])

        for tour in tours:
            tour_id = str(tour['id'])
            
            if tour_id not in storage["tour_ids"]:
                logger.info(f"✨ Nouveau tour : {tour_id}")
                gpx_res = session.get(f"https://www.komoot.com/api/v1/tours/{tour_id}.gpx")
                
                if gpx_res.status_code == 200:
                    try:
                        gpx = gpxpy.parse(gpx_res.text)
                        storage["stats"]["dist"] += gpx.length_2d()
                        storage["stats"]["elev"] += gpx.get_uphill_downhill().uphill
                        storage["stats"]["count"] += 1
                        storage["tour_ids"].append(tour_id)
                        
                        for track in gpx.tracks:
                            for seg in track.segments:
                                for p in seg.points:
                                    storage["points"].append([round(p.latitude, 5), round(p.longitude, 5)])
                        
                        new_tours_found += 1
                        time.sleep(0.05)
                    except: continue
            else:
                break

        if new_tours_found > 0:
            save_data(storage)
            logger.info(f"Sync terminée : {new_tours_found} nouveaux tours.")

        if storage["points"]:
            # On centre sur le dernier point connu
            m = folium.Map(location=storage["points"][-1], zoom_start=11, tiles='CartoDB dark_matter')
            HeatMap(storage["points"], radius=3, blur=2, min_opacity=0.3, gradient={0.4:'blue', 0.7:'cyan', 1:'white'}).add_to(m)
            
            # Dashboard
            header = f'''
            <div style="position:fixed; top:10px; left:50px; z-index:1000; background:rgba(0,0,0,0.8); color:white; padding:15px; border-radius:10px; border:1px solid #00f2ff; font-family:sans-serif;">
                <b style="color:#00f2ff;">SQUADRA MAP</b><br>
                {storage["stats"]["count"]} sorties | {round(storage["stats"]["dist"]/1000)} km
            </div>
            '''
            m.get_root().html.add_child(folium.Element(header))
            m.save("index.html")
            logger.info("index.html mis à jour.")

    except Exception as e:
        logger.error(f"Erreur : {e}")

if __name__ == "__main__":
    run_sync()
