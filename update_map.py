import os
import requests
import gpxpy
import folium
import logging
import time
import re
import json
from folium.plugins import HeatMap
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# --- CONFIGURATION ---
# Laissez votre lien tel quel, le script va se débrouiller avec
PROFILE_URL = "https://www.komoot.com/fr-fr/user/1366042741035" 
REPO_OWNER = "CyrilHussenet"
REPO_NAME = "squamoot"

def get_real_id(session):
    try:
        logger.info(f"Scan approfondi de la page : {PROFILE_URL}")
        response = session.get(PROFILE_URL)
        
        if response.status_code != 200:
            logger.error(f"Impossible d'accéder au profil (Code {response.status_code})")
            return None
        
        html = response.text

        # METHODE 1 : Recherche dans le JSON Next.js (Le plus probable pour 2024/2025)
        # On cherche une structure du type "user":{"id":123456...
        match_json = re.search(r'"user":\{"id":(\d+),', html)
        if match_json:
            found_id = match_json.group(1)
            logger.info(f"✅ ID trouvé (Méthode JSON) : {found_id}")
            return found_id

        # METHODE 2 : Recherche de l'ID global javascript
        match_global = re.search(r'"crt_user_id":(\d+)', html)
        if match_global:
            found_id = match_global.group(1)
            logger.info(f"✅ ID trouvé (Méthode Globale) : {found_id}")
            return found_id

        # METHODE 3 : Recherche brutale d'un ID numérique différent de celui de l'URL
        # On cherche un nombre de 6 à 10 chiffres qui n'est PAS 1366042741035
        potential_ids = re.findall(r'"id":(\d{6,10})', html)
        for pid in potential_ids:
            if pid != "1366042741035": # On ignore l'ID public s'il est trouvé
                logger.info(f"⚠️ ID potentiel détecté : {pid}")
                return pid # On tente le premier trouvé

        logger.error("❌ Echec critique : Aucun ID technique trouvé dans le code source.")
        # Pour le debug, on affiche un petit bout du HTML si ça plante
        logger.info(f"Début du HTML reçu : {html[:200]}")
        return None

    except Exception as e:
        logger.error(f"Erreur d'analyse : {e}")
        return None

def run_sync():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'fr-FR,fr;q=0.9',
    })
    
    user_id = get_real_id(session)
    if not user_id: return

    all_points = []
    total_dist, total_elev, count = 0.0, 0.0, 0
    
    try:
        page = 0
        while True:
            # On utilise l'ID trouvé pour interroger l'API
            logger.info(f"Récupération page {page} pour l'ID {user_id}...")
            tours_url = f"https://www.komoot.com/api/v1/users/{user_id}/tours/?type=tour_recorded&status=public&limit=50&page={page}"
            resp = session.get(tours_url)
            
            if resp.status_code == 404:
                 logger.error("L'ID trouvé semble invalide pour l'API (404).")
                 break
            
            if resp.status_code != 200:
                logger.error(f"Erreur API ({resp.status_code}).")
                break
            
            tours = resp.json().get('_embedded', {}).get('tours', [])
            if not tours: 
                logger.info("Fin de la liste des sorties.")
                break

            for tour in tours:
                gpx_res = session.get(f"https://www.komoot.com/api/v1/tours/{tour['id']}.gpx")
                if gpx_res.status_code == 200:
                    try:
                        gpx = gpxpy.parse(gpx_res.text)
                        total_dist += gpx.length_2d()
                        total_elev += gpx.get_uphill_downhill().uphill
                        count += 1
                        for track in gpx.tracks:
                            for seg in track.segments:
                                for p in seg.points:
                                    all_points.append([p.latitude, p.longitude])
                    except: continue
                time.sleep(0.05)
            page += 1

        if all_points:
            avg_lat = sum(p[0] for p in all_points) / len(all_points)
            avg_lon = sum(p[1] for p in all_points) / len(all_points)
            m = folium.Map(location=[avg_lat, avg_lon], zoom_start=11, tiles='CartoDB dark_matter')
            HeatMap(all_points, radius=4, blur=2, min_opacity=0.4, gradient={0.4:'blue', 0.7:'cyan', 1:'white'}).add_to(m)
            
            m.get_root().html.add_child(folium.Element(f'''
                <div style="position:fixed; top:10px; left:50px; z-index:1000; background:rgba(20,20,20,0.9); color:white; padding:15px; border-radius:10px; border:1px solid #00f2ff; font-family:sans-serif;">
                    <h2 style="margin:0; font-size:16px; color:#00f2ff;">SQUADRA MAP</h2>
                    <p>{count} sorties | {round(total_dist/1000)} km | {int(total_elev)}m D+</p>
                </div>
                <button onclick="triggerUpdate()" style="position:fixed; top:10px; right:10px; z-index:1000; background:#1a1a1a; color:#00f2ff; border:1px solid #00f2ff; padding:10px; cursor:pointer; border-radius:5px;">🔄 UPDATE</button>
                <script>
                function triggerUpdate() {{
                    const token = prompt("Token GitHub :");
                    if(!token) return;
                    fetch('https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/main.yml/dispatches', {{
                        method:'POST', headers:{{'Authorization':'Bearer '+token}}, body:JSON.stringify({{ref:'main'}})
                    }}).then(r=>alert("Mise à jour lancée !"));
                }}
                </script>
            '''))
            m.save("index.html")
            logger.info("🎉 SUCCÈS : index.html généré !")
        else:
            logger.warning("Connexion OK, ID trouvé, mais aucune sortie publique récupérée.")

    except Exception as e:
        logger.error(f"Erreur : {e}")

if __name__ == "__main__":
    run_sync()
