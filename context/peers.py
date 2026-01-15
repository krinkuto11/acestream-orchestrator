import libtorrent as lt
import time
import sys

def track_acestream_peers(acestream_id):
    # 1. Configurar la sesión de Libtorrent
    ses = lt.session()
    
    # Configuración para optimizar la búsqueda de peers sin descargar agresivamente
    settings = {
        'user_agent': 'AceStream/3.1.74',  # Nos disfrazamos un poco
        'listen_interfaces': '0.0.0.0:6881',
        'alert_mask': lt.alert.category_t.status_notification
    }
    ses.apply_settings(settings)

    print(f"[*] Iniciando motor libtorrent para ID: {acestream_id}")

    # 2. Construir el Magnet Link
    # Los IDs de Acestream son hashes SHA1 (InfoHash).
    # Añadimos los trackers que vimos en tu Wireshark para encontrar gente rápido.
    trackers = [
        "udp://tracker.coppersurfer.tk:6969/announce",
        "udp://tracker.leechers-paradise.org:6969/announce",
        "udp://9.rarbg.me:2710/announce",
        "udp://tracker.opentrackr.org:1337/announce",
        "http://retracker.local/announce"
    ]
    
    magnet_link = f"magnet:?xt=urn:btih:{acestream_id}"
    for tracker in trackers:
        magnet_link += f"&tr={tracker}"

    # 3. Añadir el enlace a la sesión
    params = lt.parse_magnet_uri(magnet_link)
    params.save_path = "."  # No descargaremos nada real, pero necesita una ruta
    
    # Flags importantes:
    # flag_update_only: No descargar datos si es posible
    # flag_auto_managed: Dejar que libtorrent gestione la conexión
    handle = ses.add_torrent(params)

    print("[*] Conectando al enjambre (Swarm)... Espere unos segundos...")
    print("-" * 50)
    print(f"{'IP ADDRESS':<20} | {'CLIENTE':<25} | {'PROGRESO'}")
    print("-" * 50)

    seen_peers = set()

    try:
        while True:
            # Obtenemos la lista de peers conectados
            peers = handle.get_peer_info()
            
            # Estado del enjambre
            status = handle.status()
            
            for p in peers:
                # p.ip devuelve (ip, puerto)
                ip_str = p.ip[0]
                try:
                    client_name = p.client.decode('utf-8', errors='replace')
                except:
                    client_name = str(p.client) # Fallback por si acaso
                
                # Filtrar solo peers nuevos para no spammear la consola
                if ip_str not in seen_peers:
                    # Acestream suele mostrarse como "AceStream/..." o clientes libtorrent genéricos
                    print(f"{ip_str:<20} | {client_name:<25} | {p.progress*100:.1f}%")
                    seen_peers.add(ip_str)

            # Pequeña pausa para no saturar la CPU
            time.sleep(1)
            
            # Información de depuración cada 10 segundos (opcional)
            if int(time.time()) % 10 == 0:
                print(f"[Estado] Peers totales: {status.num_peers} | Seeds: {status.num_seeds}")
                
    except KeyboardInterrupt:
        print("\n[!] Deteniendo rastreador...")

# --- USO ---
# Reemplaza esto con el ID del canal que quieres investigar
# Ejemplo ficticio (debes poner uno real):
ACESTREAM_ID = "c9321006921967d6258df6945f1d598a5c0cbf1e" 

if __name__ == "__main__":
    if len(sys.argv) > 1:
        track_acestream_peers(sys.argv[1])
    else:
        print("Uso: python script.py <ACESTREAM_ID>")
        # O descomenta la línea de abajo para probar directo:
        # track_acestream_peers(ACESTREAM_ID)
